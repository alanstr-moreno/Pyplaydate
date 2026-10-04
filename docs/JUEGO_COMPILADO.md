# Correr un juego COMPILADO (`.pdx`) — hallazgos y cómo está hecho

Documento de la parte más difícil del emulador: ejecutar un juego **compilado**
(un `.pdx` real, no el código fuente). Todo lo de aquí está verificado contra
`smolitaire-1.0.1.pdx` (juego real de itch.io, sin DRM).

---

## 1. Qué es realmente un juego compilado

```
Smolitaire.pdx/            <- bundle (carpeta)
    pdxinfo                <- metadatos (name, bundleID, version...)
    main.pdz               <- CONTENEDOR con el bytecode Lua compilado
    dialog.pdz, options.pdz, ...
    images/*.pdi  *.pdt    <- imagenes ya compiladas (1-bit)
    audio/*.pda            <- audio compilado
```

- **No hay `main.lua`.** El código está en `main.pdz`.
- `.pdz` = contenedor: cabecera `Playdate PDZ` + flags, luego entradas
  (bytecode, imágenes, fuentes, audio). Las entradas pueden estar zlib-comprimidas.
- **DRM**: los juegos de la **Catalog (tienda)** traen el `main.pdz` **cifrado**
  (bit `0x40000000`, método propietario). Esos **no** se pueden ejecutar, y no se
  intenta. `pd/pdx.py` los detecta y avisa.
- Los juegos **homebrew / itch.io / sideload no están cifrados** y sí se ejecutan.

Lector: **`pd/pdx.py`** (`read_pdx`, `extract_pdx`, `parse_pdz`).

---

## 2. Las 3 barreras técnicas (y cómo se resolvieron)

Ejecutar bytecode de Playdate con un Lua normal **no funciona** por tres motivos
independientes. Los tres costaron tiempo:

### (a) `LUA_32BITS=1`

Playdate usa Lua 5.4.3 con **enteros de 4 bytes y floats de 4 bytes**.
Un Lua estándar (8-byte int) rechaza el bytecode:
`bad binary format (lua_Integer size mismatch)`.

Se verifica leyendo el header del `.luac` extraído:
```
1b4c7561 54 00 ... 04 04 04 78    sig="\x1bLua" ver=0x54 inst=4 INT=4 NUM=4
```

→ Compilar Lua 5.4.3 con `#define LUA_32BITS 1` en `luaconf.h`.

### (b) El enum de opcodes es DISTINTO (la trampa)

Playdate usa el enum de **Lua 5.4 *beta***: `OP_LOADFALSE`/`OP_LFALSESKIP`/
`OP_LOADTRUE` están **al final** (81-83) y hay un hueco en 5; el resto va
desplazado −2 respecto al 5.4.3 final.

**El síntoma es engañoso**: el bytecode **carga sin error** (el header es válido)
pero **se ejecuta mal**. Se detecta mirando el desensamblado: aparecían cosas
imposibles como `TEST` sin su `JMP` y `FORPREP ... ; to = 1042` en una función de
225 instrucciones.

**La trampa**: "arreglarlo" moviendo los opcodes en `lopcodes.h` **rompe la tabla
de dispatch del VM** → **segfault** en Lua normal.

**La solución correcta**: no tocar el enum/VM; **remapear los opcodes AL CARGAR**,
en `lundump.c` (`LoadCode`). Mapa:

| opcode Playdate | opcode Lua 5.4.3 std |
|-----------------|----------------------|
| 0..4            | 0..4 |
| 6..80 (LOADNIL..EXTRAARG) | 8..82 |
| 81/82/83 (LOADFALSE/LFALSESKIP/LOADTRUE) | 5/6/7 |

Script: **`tools/patch_lundump.py`**.

Verificación: el desensamblado pasa a ser coherente —
```
2  GETTABUP 0 0 0  ; _ENV "import"
3  LOADK    1 1    ; "CoreLibs/object"
4  CALL     0 2 1  ; import("CoreLibs/object")
```

### (c) `import()` y los chunks del `.pdz`

Playdate usa `import "CoreLibs/graphics"` (no `require`): carga el chunk
compilado del propio `.pdz` y lo ejecuta **una sola vez**.

**`pd/runtime.py`** lo implementa: `_load_pdx` indexa las entradas de tipo 1
(bytecode) en `self.chunks`, define el global `import`, y ejecuta el chunk `main`.
`import(name)` carga bajo demanda y cachea.

---

## 3. Build reproducible

Todo junto en **`tools/build_lua32.sh`** → produce `vendor/lupa/` (lupa compilado
contra la Lua parcheada, autocontenido: solo `libSystem`).

```sh
sh tools/build_lua32.sh          # macOS o Linux (incluida la Pi Zero 2 W)
```

`pd/luavm.py` usa `vendor/lupa` si existe (`lupa.lua`); si no, cae al lupa del
sistema (que solo sirve para juegos en fuente).

---

## 4. Formato de imagen `.pdi` / `.pdt` y su decodificador

Un juego compilado **no trae PNGs**: `pdc` convierte a `.pdi` (imagen 1-bit) y
`.pdt` (spritesheet). Cabecera `Playdate IMG`/`Playdate IMT` + flags (bit 31 =
comprimido → zlib).

Datos = **celdas**: los bordes transparentes se recortan y hay que reconstruirlos.

```
Cell header (uint16 LE): clipW, clipH, stride, clipL, clipR, clipT, clipB, flags
  bitmap de color: stride*clipH bytes, 1 bit/px, 0=negro 1=blanco, MSB primero
  si flags&0x3:    bitmap de alpha (1=opaco)
  ancho final = clipL + clipW + clipR
```

Implementado en **`pd/pdi.py`** (`decode_pdi`, `decode_pdt`, `decode_cell`).
Verificado contra assets reales: `title.pdi` decodifica a **"Smolitaire"** con
sombra dithered, legible y sin espejo (confirma el bit-order MSB).

---

## 5. Patrón "handle" para objetos

Por el **bug de lupa** (ver `README`/`GUIA`), NUNCA se exponen objetos Python
anidados a Lua. Los "objetos" de Playdate son **tablas Lua con `__id`**, y el
estado real vive en Python:

```python
self._registry[id] = objeto_python     # pd/runtime.py
```

La tabla handle lleva además sus métodos como campos (`:draw`, `:getSize`,
`...`), porque el juego hace `img:draw(x,y)` (que pasa `img` como 1er argumento).

Ejemplo completo en `pd/runtime.py::_image_handle` / `_imagetable_handle`, con la
implementación del objeto en `pd/image.py` y el dibujo en `pd/graphics.py`.

---

## 6. El depurador de APIs faltantes

Cuando al juego le falta una API, Lua muere con un mensaje opaco
(`attempt to index a nil value`) y arreglas de a una por vez. **`pd/apidbg.py`**
+ **`tools/find_missing_api.py`** lo resuelven:

```sh
python tools/find_missing_api.py smolitaire-1.0.1.pdx -n 120
```

Qué hace:
- Instala un **proxy** sobre el árbol `playdate` que registra cada acceso a un
  campo **inexistente** (con su ruta completa) y devuelve un **stub encadenable**,
  para que el juego **siga corriendo** en vez de morir.
- Aísla los errores con `pcall` (`__pd_safe`) → descubre **muchas** APIs faltantes
  en una sola corrida.
- Devuelve un informe agrupado por módulo y ordenado por uso.

Salida típica:
```
=== APIs FALTANTES (55) ===
  playdate.graphics.sprite
      playdate.graphics.sprite.new().setZIndex   x2
      playdate.graphics.sprite.new().moveTo      x1
  playdate.sound.sampleplayer
      playdate.sound.sampleplayer.new()          x10
```

Uso desde código:
```python
from pd import apidbg
rt.on_ready = apidbg.install          # antes de correr el juego
...
missing, errors = apidbg.report(rt)
```

---

## 7. Estado actual (verificado)

| Etapa | Estado |
|---|---|
| Leer `.pdx`/`.pdz`, detectar DRM | ✅ |
| Extraer bytecode y assets | ✅ |
| Lua 5.4.3 parcheada (LUA_32BITS + remapeo de opcodes) | ✅ |
| Cargar los 25 chunks de Smolitaire | ✅ |
| Ejecutar bytecode (`import()` real funcionando) | ✅ |
| Decodificar `.pdi`/`.pdt` (verificado visualmente) | ✅ |
| `graphics.image` / `graphics.imagetable` | ✅ |
| `graphics.sprite` (+ colisiones AABB) | ✅ |
| `playdate.geometry` (Lua puro) | ✅ |
| `playdate.sound` (shim) / menú / `metadata` / `kButton*` | ✅ |
| **Primer frame renderizado del juego compilado** | ✅ |
| Juego completo jugable | ⬜ falta API + 1 snag |

**Progreso medido con el depurador** (Smolitaire): 88 → 55 → 34 → 8 → 5 → **0 APIs
faltantes y 0 errores**. El juego **arranca completo**, corre **900 frames con input
inyectado sin un solo error**, y renderiza su pantalla de identidad (el logo
"Twenty Minute Mile", dibujado con `drawLine` + `setLineWidth`).

**Siguiente iteración**: el juego ya no se cae; lo que falta es *fidelidad*
(que la animación del logo avance, menú, cartas) y las APIs que el juego aún no
tocó porque no llegó a esa pantalla. El ciclo sigue igual: correr el depurador,
implementar el bloque de arriba, repetir.

**Snag resuelto** (era el más sutil de todos): la clausura de volúmenes del juego
recorre `sounds` recursivamente —
`if type(v)=="table" then <recorrer> else v:setVolume(0.5) end`.
Nuestros sampleplayers eran **tablas Lua**, así que el juego *entraba* a ellos y
terminaba llamando `setVolume` sobre un método nuestro (una función Python) →
`AttributeError: 'function' object has no attribute 'setVolume'`.

En la consola esos objetos son **userdata**, no tablas: `type(v) ~= "table"` → la
rama correcta. La solución fue la lección central de abajo.

---

## 7-bis. Las 4 lecciones que costaron tiempo

1. **Los objetos de Playdate son USERDATA, no tablas.** Exponerlos como tablas Lua
   rompe cualquier juego que haga `if type(x) == "table"`. Se resuelven como
   objetos Python (lupa los entrega como `type() == "userdata"`), con un
   `__getattr__` *permisivo* que devuelve no-ops para métodos inexistentes —
   **excluyendo los dunders**, porque lupa sondea `__getitem__`/`__len__` para
   decidir cómo acceder al objeto. Ver `pd/luaobj.py`.
   (Verificado estable a 20 000 lecturas con el lupa parcheado.)

2. **`playdate.file.*` lee del BUNDLE, no de un sandbox.** `file.open("common/images/x.dat")`
   resuelve contra la raíz del `.pdx`. Solo `datastore` es un sandbox escribible
   aparte (`.pd_data/`). Síntoma si te equivocas: `file.open` devuelve nil y el
   juego muere con "attempt to index a nil value" al hacer `f:read()`.

3. **`lua_Integer` es de 32 bits** (`LUA_32BITS`). Devolver un entero grande
   (p.ej. `time.time()*1000` ≈ 1.7e12) revienta con
   *"value too large to convert to lua_Integer"*. Hay que devolver **float**.

4. **`lua_Number` es float de 4 bytes.** La precisión de ms en epoch no se
   representa: los deltas de tiempo pierden resolución. Aceptable para arrancar;
   a tener en cuenta si un juego mide tiempos finos.

---

## 8. Referencias

- Formatos (`.pdz`, `.luac`, `.pdi`, `.pdt`, `.pft`):
  `github.com/cranksters/playdate-reverse-engineering`
- `build_lua32.sh`, `patch_lundump.py`, `find_missing_api.py`, `api_coverage.py`
- Guía general de implementación: `docs/IMPLEMENTANDO_APIS.md`
