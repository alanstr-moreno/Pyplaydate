# 🎮 Playdate-Pi — Emulador de Playdate en Python

Un **runtime/emulador del Playdate** escrito en Python que ejecuta **juegos
compilados reales `.pdx`** (bytecode Lua 5.4 de 32 bits), reimplementando la API
`playdate.*` sobre Lua vía **lupa**, con render y audio por **pygame-ce**.

No es un emulador a nivel de CPU (no QEMU): es un **runtime** que carga el
`.pdx`, decodifica los formatos compilados (`.pdz`, `.pdi`, `.pdt`, `.pft`,
`.pda`) y ejecuta los chunks Lua, traduciendo la API del SDK de Playdate a
Python. Corre igual en tu Mac y en una Raspberry Pi Zero 2 W.

> Funciona con **homebrew / juegos de itch.io sin cifrar** y con los ejemplos
> del SDK. Los juegos cifrados de la Catalog (`bit 0x40000000`) no se ejecutan.

## 📸 Juegos probados

De **itch.io** (homebrew):

| Juego | Captura | Estado |
|-------|---------|--------|
| **Shrimp Boom 004** | ![shrimpboom](assets/screenshots/shrimpboom.png) | ✅ Movimiento 4 direcciones, disparo, HUD, sonido (burbujas/pop/muerte) |
| **Playdate Broken Screen** | ![playdatebrokenscreen](assets/screenshots/playdatebrokenscreen.png) | ✅ Imágenes `.pdi` decodificadas y dibujadas |
| **Fishing Simulator** | ![fishing](assets/screenshots/fishing.png) | ✅ Menú, escena, sonido (catch/select/confirm) |
| **Smolitaire 1.0.1** | ![smolitaire](assets/screenshots/smolitaire.png) | ✅ Menú, juego, cursor (mano) visible |

Del **Playdate SDK** (compilados con `pdc`):

| Juego | Captura | Estado |
|-------|---------|--------|
| **Flippy Fish** | ![flippyfish](assets/screenshots/flippyfish.png) | ✅ Pez animado, colisiones, score, suelo, algas |
| **Sprite Collision Masks** | ![spritecollisionmasks](assets/screenshots/spritecollisionmasks.png) | ✅ Máscaras de colisión por grupos, rebotes, sprites sin salir del cuadro |

## 🧰 Requisitos

- **Python 3.10+** (probado en 3.14)
- **pygame-ce** `>=2.5` (el clásico `pygame` no sirve)
- **lupa** `>=2.0` **enlazada contra Lua 5.4 con `LUA_32BITS`** — requisito
  crítico (Playdate compila para una VM Lua de 32 bits). Ver
  `docs/ARQUITECTURA.md`.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

> **lupa + LUA_32BITS**: Playdate compila los `.pdz` para Lua de 32 bits
> (floats `double`, offsets de 32 bits). lupa debe compilarse/enlazarse con esa
> variante o los chunks compilados no cargan ni revientan. En el Mac de Alan ya
> está el Python de Hermes con todo (`~/.hermes/tools/python-3.14*/bin/python3`).

## ▶️ Cómo ejecutar

```bash
python playdate_pi.py "ruta/al/juego.pdx" [--scale 2] [--palette device|bw|yellow] [--frames N] [--verbose]
```

Ejemplos:

```bash
python playdate_pi.py shrimpboom004.pdx --scale 3
python playdate_pi.py "Fishing Simulator.pdx" --palette device
python playdate_pi.py FlippyFish.pdx --frames 400     # headless (test)
python playdate_pi.py shrimpboom004.pdx --verbose       # logs API/assets
```

### Controles (equivalen al D-pad + 2 botones + manivela)

| Tecla | Acción |
|-------|--------|
| Flechas / WASD | D-pad |
| X / ESPACIO | Botón A (derecho) |
| Z / SHIFT | Botón B (izquierdo) |
| Q / E | Manivela (antihorario / horario) |
| ESC | Salir |

## 🏗️ Cómo funciona (mapa de arquitectura)

Cuatro capas transforman el `.pdx` compilado en píxeles y audio. Cada una tiene
su sección en [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) con esquemas.

```text
┌────────────────────────────────────────────────────────────┐
│ 1. CARGA DE JUEGO   — descomprime y parsea el .pdx (.pdz,  │  pd/pdx.py
│    .pdi, .pft, .pda, pdxinfo) y resuelve assets            │
├────────────────────────────────────────────────────────────┤
│ 2. INTERPRETACIÓN   — lupa ejecuta los chunks Lua 5.4 de   │  pd/luavm.py
│    32 bits; el juego pide la API vía playdate.*            │
├────────────────────────────────────────────────────────────┤
│ 3. API EN PYTHON    — se construye la tabla playdate.*     │  pd/runtime.py
│    ({graphics, sound, sprite, display, button, geometry})  │
├────────────────────────────────────────────────────────────┤
│ 4. WRAPPING         — objetos Playdate = userdata, tablas  │  pd/luaobj.py
│    planas, metamétodos, callbacks, coord./patrones         │
└────────────────────────────────────────────────────────────┘
```

Síntesis de cada parte:

1. **Carga de juegos** — `.pdx` = carpeta con `main.pdz`, `.pdi/.pdt/.pft/.pda`
   y `pdxinfo`. `pdz` = contenedor con chunks posiblemente comprimidos
   (`FLAG_COMPRESSED=0x80`) o cifrados (`FLAG_ENCRYPTED=0x40000000`, no
   soportado). Los assets se resuelven **ignorando la extensión**.
2. **Interpretación** — `LuaRuntime` de lupa + `patch_lundump` para cargar
   bytecode 32 bits. `playdate.update()` corre en corrutina (como la consola).
3. **APIs** — el patrón: implementa el método en Python y regístralo en
   `Runtime._build_api()`. Guía completa y herramientas en
   `docs/IMPLEMENTANDO_APIS.md` (incluye `tools/api_coverage.py` y
   `tools/headless_test.py`, que corren tu juego y listan qué API falta).
4. **Wrapping** — los objetos Playdate se exponen como **userdata** (no tablas)
   porque los juegos distinguen `type(v)=="userdata"`. Las tablas son **planas**
   (funciones Python en hojas, nunca objetos anidados). Metamétodos Lua reales
   para operadores (`polygon * transform`, `imagetable[i]`, defaults de
   `sprite:update()`). Jugadas de lupa documentadas en `ARQUITECTURA.md`.

## 📁 Estructura del proyecto

```text
playdate_pi/
├── playdate_pi.py          # entry point (CLI)
├── requirements.txt
├── README.md
├── docs/
│   ├── ARQUITECTURA.md     # esquemas por partes + wrapping + errores comunes
│   ├── IMPLEMENTANDO_APIS.md  # patrón para añadir APIs + herramientas
│   ├── JUEGO_COMPILADO.md  # .pdx compilado (LUA_32BITS, opcodes, import)
│   └── PFT_FUENTES.md      # decodificación de fuentes .pft
├── assets/screenshots/     # capturas de los juegos probados
├── tools/                  # utilidades de depuración/cobertura de API
├── pd/
│   ├── pdx.py              # contenedor .pdz + formato
│   ├── pdi.py pft.py pda.py  # decodificadores .pdi/.pft/.pda
│   ├── luavm.py            # LuaRuntime (lupa, 32 bits)
│   ├── runtime.py          # API playdate.* + bucle (update/draw)
│   ├── graphics.py         # render (400x240, paletas 1-bit)
│   ├── sprite.py           # sistema de sprites + colisiones/rebotes
│   ├── sound.py            # audio (pygame.mixer) + synth ADSR
│   ├── geometry.py image.py input.py screen.py emulator.py
│   ├── luaobj.py filestore.py
│   └── lib/                # puentes Lua (playdate_ext.lua, etc.)
```

## ✅ Estado y limitaciones

**Funciona**: carga `pdz` sin cifrar; decodifica `.pdi/.pdt/.pft/.pda`; API de
`graphics`, `sound`, `sprite`, `geometry`, `display`, `button`, `file`.
Colisiones por grupos/máscaras con `collisionResponse`. Audio `.pda` (PCM + IMA
ADPCM) y synth (ruido/sawtooth/square/sine/triangle con ADSR y notas en tiempo
absoluto del motor). Fondo/estado por el sistema de sprites con semántica de la
consola real.

**Limitaciones**: no cifrado (Catalog); `CoreLibs/assets/*` que no vienen en el
`.pdx` emiten avisos; algunos efectos/APIs de `sound` (sequence, filters) son
stubs permisivos. El puente lupa escribe campos Lua (nunca los lee con
metamétodos por Corrutinas).

## 🤝 Contribuir / avanzar juntos

Está diseñado para que otros desarrolladores lo extiendan. Para añadir una API o
corregir fidelidad: lee `docs/ARQUITECTURA.md` y `docs/IMPLEMENTANDO_APIS.md`,
sigue los patrones (método Python + registro en `_build_api()`, userdata,
tablas planas, callbacks) y registra la lección aprendida. Los juegos de la
lista son buenos casos de regresión (el runner `tools/headless_test.py` pisa
cada juego y reporta el primer error).

## ⚠️ Jugadas clave descubiertas (resumen)

1. **lupa no mapea `__mul__`** de Python a metamétodos Lua: usar
   `debug.setmetatable` con una función **Lua** (no Python) como `__index`.
2. **Los objetos Playdate deben ser userdata**, no tablas: los juegos
   distinguen `type(v)=="userdata"` y si es `"table"` toman la rama equivocada.
3. **`sprite.update()` / fondo**: los sprites solo se dibujan si el JUEGO llama
   a `sprite.update()` (semántica de la consola); el fondo = color o
   `background_cb`.
4. **Color**: `kColorBlack=0` (tinta), `kColorWhite=1` (papel); `setColor` debe
   interpretar literales `0x000000`/`0xffffff`, no solo booleans.
5. **Audio**: `synth:playNote(freq, vol, length, when)` usa `when` **absoluto**
   (segundos desde que arrancó el motor, no relativo); default de la onda =
   **ruido**. `.pda` incluye IMA ADPCM.
6. **Cursor/hand**: las imágenes 1-bit con relleno por papel (`bit=1`) son
   invisibles sobre fondo claro; dibujar en tinta cuando el sprite es
   predominantemente papel.

Cada una está detallada (causa + fix) en `docs/ARQUITECTURA.md`.

## Licencia

*(elige la que prefieras — sugerencia: MIT)*

---

Construido con el [Playdate SDK](https://play.date/dev/), [lupa](https://github.com/scoder/lupa)
y [pygame-ce](https://pyga.me/).
