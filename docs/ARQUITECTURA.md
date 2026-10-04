# Arquitectura de Playdate-Pi — esquema por partes

Este documento explica **cómo funciona** el emulador parte por parte, para que
otro desarrollador entienda el flujo y lo extienda. Cada sección tiene el mismo
patrón: *objetivo → datos → diagrama → puntos de código → trampas*.

---

## 1. Carga de juegos (`pd/pdx.py`)

### Objetivo
Llevar el juego compilado (`.pdx`) a tres cosas usables: el **bytecode** de
`main.pdz` (y submódulos como `dialog.pdz`, `options.pdz`), los **assets**
(imágenes/fuentes/audio) y los **metadatos** (`pdxinfo`, paleta).

### Datos de entrada
- `.pdx` = una **carpeta** con `main.pdz`, `pdxinfo`, y subcarpetas
  (`images/`, `sounds/`, ...) con `.pdi/.pdt/.pft/.pda`.
- `.pdz` = contenedor binario de chunks. Cada chunk tiene cabecera con `id`
  (4 bytes), `flags` y `data_size`. Flags:
  - `FLAG_COMPRESSED = 0x80` → el payload va comprimido (zlib).
  - `FLAG_ENCRYPTED = 0x40000000` → cifrado en bloques AES (no soportado;
    los juegos de la Catalog llevan este bit y **no se intentan**).

### Flujo

```text
.jdx/carpeta juego
   │  main.pdz  ──► pd/pdx.py: deserializa chunks
   │                ├─ flag COMPRESSED  → zlib.decompress
   │                └─ flag ENCRYPTED   → [no soportado, aborta suave]
   │  pdxinfo      ──► nombre, versión, bundle id, contenido
   │  images/a.pdi ──► decodificador (ver §4) -> pygame.Surface con
   │                     máscara de opacidad (SRCALPHA)
   │  sounds/x.pda ──► decodificador .pda (ver §3) -> samples numpy
   │  fonts/f.pft  ──► decodificador .pft -> glifos (poverty/B40)
   └──► FileStore (name -> bytes) resuelto por CADENA SIN EXTENSIÓN
```

### Resolución de assets (punto clave)
Los juegos piden `playdate.graphics.image.new("images/fondo")` **sin
extensión**. El `FileStore` de `pd/filestore.py` mantiene un índice
`nombre_sin_extensión → bytes` y devuelve el asset corresponda como sea.
Si no existe el asset exacto, **reporta un aviso** (para que veas qué falta)
y devuelve algo inofensivo (o `nil` según la compilación del juego).

### Cómo correr código compilado
`main.pdz` no es texto: es **bytecode Lua 5.4 de 32 bits**. `pd/luavm.py`
consigue que lupa lo cargue con un deserializador parcheado (`patch_lundump`)
que corrige las diferencias 32/64 bits. La resolución de submódulos con
`import "CoreLibs/*"` se reenvía a las carpetas del SDK cuando el `.pdx` no las
trae. Detalle completo en `docs/JUEGO_COMPILADO.md`.

### Trampas
- Nunca añadas la extensión al pedir un asset (el juego no la pide).
- No leas el `.pdz` como texto; usa el deserializador de chunks.
- Un `.pdz` cifrado NO se corrige "probando a descomprimir": aborta con aviso.

---

## 2. Interpretación del bytecode y bucle (`pd/luavm.py`, `pd/runtime.py`)

### Objetivo
Ejecutar `update`/`draw` del juego 30 veces por segundo, dentro del mismo
intérprete Lua 5.4 que la consola, traduciendo cada llamada `playdate.*` a
Python.

### Datos
- Un `LuaRuntime` de **lupa** con las builtins que piden los juegos
  (`math`, `string`, `table`, `os`, `bit`...).
- El bucle vive en `pd/runtime.py`:
  `call_update()` → ejecuta la corrutina `playdate.update()`;
  `call_draw()` → ejecuta la corrutina `playdate.draw()`.

### Flujo del bucle por frame

```text
for frame in range(N):
    feed botones (input)          # pd/input.py
    feed manivela  (crank)        # si la gira el usuario / joystick
    call_update()                 # ejecuta playdate.update() en Lua (corrutina)
    call_draw()                   # ejecuta playdate.draw()
    screen.render(scale)          # 400x240 -> ventana (nearest-neighbor)
    button.end_frame()            # limpia "pressed this frame"

update() y draw() corren como presiön; lupa continúa la corrutina igual que
Playdate: llaman a sus propios playdate.graphics.* que escribe en el canvas.
```

### Corrutinas
Los juegos de Playdate se escriben como `function playdate.update()` que es
**reanudada** (resumed) por el runtime, no llamada de nuevo. El emulador guarda
la corrutina `update`/`draw` creada la primera vez y la reanuda cada frame.

### Trampas
- Cualquier `LuaError` debe propagarse (no silenciarse) para depurar el
  frame exacto.
- El bytecode es 32 bits; lupa sin `LUA_32BITS` se cuelga o revienta en
  `load_buffer`. Ver `docs/JUEGO_COMPILADO.md`.

---

## 3. Lectura de audio (`pd/pda.py`, `pd/sound.py`)

### Objetivo
Reproducir **los efectos `.pda`** que embebe el juego y **la música**.

### Formato `.pda` (Playdate Audio ~AUDD)
Cabecera: marca de 12 bytes (`"Playdate AUDD"`) + `rate` (3 bytes little
endian) + `fmt` (1 byte). El formato (campo `fmt`) puede ser:
- **PCM 16-bit mono** (p.ej. los *confirm* de Fishing Simulator).
- **IMA ADPCM estéreo** (p.ej. los *pop* de Shrimp Boom).

`pd/pda.py` decodifica ambos a un array de samples numpy (`float32` entre -1 y
1). En el caso ADPCM se implementa la tabla de pasos IMA y el nibble expansion
habitual (hay que desintercalar los dos canales).

### Sintetizador (música/efectos por notas)
Muchos juegos (Kickflip, ...) no traen `.pda`: la música se **sintetiza** con
`playdate.sound` creando `Synth` y programando notas:

```text
Synth:playNote(freq, volume, length, when)
   'when' es ABSOLUTO en segundos desde el arranque del motor de audio
   (no relativo a la llamada). Se agenda en un buffer con heapq de
   (tiempo_absoluto, synth).

Onda por defecto = RUIDO (la consola suena "chisporroteante"), no seno.
ADSR (ataque/sostén/decaimiento) aplicado sobre cada nota.
```

Ejemplo de notas (estructura aportada por el usuario): cada canción es
`{id, bpm, name, notes[canales][...], splits, loopFrom, ticks}`. `notes` por
canal son pares `[duración, frecuencia]`; `loopFrom` indica el tick de inicio
del bucle; `ticks` el largo total en tiempos de 1/16.

### Diagrama

```text
.pda ──► pd/pda.py ─► PCM strided / IMA ADPCM ─► samples float32
                                                      │
Synth/playNote ──────────────────────────────────────► cola con heapq
                                                      │  (cuándo absoluto)
                                                      ▼
                                  pygame.mixer.Sound(samples) -> play()
```

### Trampas
- **ADPCM estéreo**: no olvidar los dos canales intercalados, o el sonido
  suena ralentizado.
- `when` **absoluto**: si lo tratas como relativo, todo suena al inicio.
- El ADSR corto (`attack=0.0x, release=0.02x`) es lo que da el "plop" típico.
- Con mixer dummy en la Pi (sin salida), `play()` debe no romper.

---

## 4. Lectura / construcción de la API (`pd/runtime.py`, `pd/graphics.py`)

### Objetivo
Que el juego encuentre TODA la API en `playdate.*`. El patrón es **2 pasos**:

1. Implementa el método en Python (en el módulo correspondiente).
2. Regístralo en `Runtime._build_api()`.

### La tabla `playdate`

```text
runtime.build_api()
   playdate = {
      graphics  = { clear, fillRect, drawText, drawImage, newImage,
                    getImageFromRect, setColor, setDrawOffset, ... },
      sound     = { sound:newSynth, FilePlayer, playNote, ... },
      sprite    = { newSprite, addSprites, update, CollisionType... },
      geometry  = { vector2D, vec2, rect, polygon, affineTransform },
      display   = { setInverted, setFlipped, ... },
      button    = { isPressed, wasPressed, ... },
      file      = { open, read, write, ... },
      ... callbacks: setUpdateCallback, setCrankCallback, update, draw
   }
```

Cada submódulo es una **tabla Lua plana** cuyas hojas son **funciones Python**.
El emulador resuelve `playdate.graphics.foo` en el momento de la llamada.

### Cómo decide qué API usa un juego
En `tools/` hay un depurador (`find_missing_api.py`) que corre el juego aislando
errores y lista **qué hay que implementar**, ordenado por uso real.

### Trampa grande
- Lupa convierte las **funciones Python en `userdata`**, no `function`.
  Cualquier check con `type(f)=="function"` **en Lua** se salta tus funciones.
  Usa `if f ~= nil` o `lupa` como puente (ver §5).

---

## 5. Wrapping en Python — el endiablado (`pd/luaobj.py`)

### Objetivo / regla de oro
Exponer la API de Playdate a Lua respetando **el contrato de tipos** que los
juegos realmente comprueban. Cuatro reglas:

**R1. Objetos Playdate = USERDATA, no tablas.**
Los juegos hacen `if type(sprite) == "userdata" then ... else` (la rama
"usuario/tabla" suele ser para Lua). Si expones un objeto Python como objeto
lupa **directo**, lupa lo entrega como `userdata` y los flujos toman la rama
correcta. Ejemplos: `newSprite()`, `image.new()`, `polygon.new()`, `Synth`.

**R2. Tablas planas con funciones en las hojas; nunca objetos anidados.**
Exponer un objeto Python anidado dentro de una tabla a veces hace que lupa
pierda el atributo al acceder desde Lua tras muchas lecturas (bug conocido de
lupa con objetos encadenados). Solución: cada sub-API es una **tabla Lua** con
funciones Python en hojas. Nada de `g["playdate"] = objeto_python`.

**R3. Metamétodos Lua, no operadores Python.**
lupa **no** mapea `__mul__`, `__index`, `__call` de Python a metamétodos Lua
automáticamente. Para `polygon * affineTransform`, `imagetable[i]`,
`synth:playNote` se instalan metamétodos con **`debug.setmetatable`** y una
función **Lua** como `__index`/`__mul`. Preservar el metatable existente de lupa
(no reemplazarlo entero) o se pierden los métodos del objeto.

```lua
-- patrón (en pd/lib/playdate_ext.lua)
local mt = debug.getmetatable(obj) or {}
local old_index = mt.__index
mt.__mul = function(a, b) return playdate_ext.polygon_mul(a, b) end
mt.__index = function(self, k)
    if old_index and old_index ~= mt then return old_index(self, k) end
    return playdate_ext.polygon_field(self, k)   -- función Python enlazada
end
debug.setmetatable(obj, mt)
```

**R4. Una función Python expuesta a Lua es `userdata`.** En tests de màs
arriba esto ya apareció: `type(polygon.new)=="userdata"`. Cualquier wrapper Lua
que compruebe `type(f)=="function"` se salta las tuyas.

### Administración de objetos (lifetime)
Un objecto expuesto se registra en un `LuaObjectRegistry` (diccionario
`id -> python`). Lua guarda `userdata` con el id; al recoger el objeto (GC) se
marca para liberar el lado Python.

### Gotchas de lupa al devolver valores
- Un `tuple` Python se **desempaqueta** como varios valores de retorno en Lua
  (`unpack_returned_tuples=True`). Para UN valor, devuelve lista o envuelve.
- Una **lista/dict de Python NO** se convierte en tabla Lua (es userdata).
  Para devolver una tabla real usa `self.lua.table_from([...])`.
- `None` Python → `nil` Lua.

---

## 6. Sprites, colores y colisiones (resumen, ver módulos)

- **`pd/sprite.py`**: `Sprite` en Python con `image`, `center`, `moveTo`,
  `moveWithCollisions`, máscaras (`groupMask`, `collisionsEnabled`).
  El callback `sprite:draw(x,y,w,h)` recibe translate en la esquina sup-izq de
  sus `bounds()` y color de tinta (el juego dibuja en "0,0" local).
- **Colisión por grupos**: `setGroups({2,3})` recibe una **tabla Lua** → hay que
  aceptar cualquier iterable (no solo `list/tuple`), o las máscaras quedan a 0
  y los sprites atraviesan las paredes.
- **`moveWithCollisions`** devuelve 4 valores (x, y, collided, overlapCount).
- Constantes reales: `kCollisionType` (`Slide=0, Freeze=1, Overlap=2,
  Bounce=3`).
- **Color**: `kColorBlack=0` (tinta), `kColorWhite=1` (papel). `setColor`
  acepta booleans, 0/1 y literales `0x000000`/`0xffffff` (que mapa a tinta/
  papel). La resolución por defecto es `400x240`, no 320x320.
- **Imágenes 1-bit precursor de papel** (la mano del cursor) son invisibles
  sobre fondo claro: `draw_sprite` las dibuja en tinta cuando opacos papel ≥ 2×
  tinta para que el cursor se vea.

---

## 7. Trucos y errores comunes en un vistazo

| Síntoma | Causa raíz | Fix |
|---------|-----------|-----|
| `type(polygon.new)=="userdata"` alrededor | lupa expone funciones Python como userdata | checks `f ~= nil`, no `type(f)=="function"` |
| `attempt to index a nil value (field 'affineTransform')` en vectores | falta el metatable de geometría en los `vector2D` | instalar `__index` Lua sobre el userdata |
| sprites atraviesan las paredes | `setGroups` con tabla Lua no entraba en `_groups_to_mask` | aceptar iterables genéricos |
| los sprites no pintan | el juego no llama `sprite.update()` o el runtime la llamaba de más | la semántica real: solo pinta si el juego llama `update()` |
| pez/score invisibles | imagetable sin `__index` numérico / color blanco global | metatable Lua `__index`; reset de color por sprite |
| sonidos "pitidos" | synth por defecto seno | default = **ruido** (waveform 4) + ADSR corto |
| música no suena | notas programadas con `when` relativo | `when` absoluto en segundos desde el arranque |
| cursor (mano) no aparece | imagen 1-bit de papel invisible sobre fondo claro | dibujar en tinta cuando opacos papel ≥ 2×tinta |

---

## Herramientas de desarrollo

- `tools/api_coverage.py` — qué API usa tu juego y cuál falta.
- `tools/headless_test.py — corre N frames y reporta el primer error con frame.
- `tools/find_missing_api.py <juego>` — lista las APIs pendientes por uso.
- `tools/build_lua32.sh` — compila lupa contra Lua 5.4 con LUA_32BITS.
