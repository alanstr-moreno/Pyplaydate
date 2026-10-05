# Playdate-Pi — Playdate emulator in Python

A **runtime/emulator for the Playdate** written in Python that runs **real
compiled games `.pdx`** (Lua 5.4 / 32-bit bytecode), reimplementing the
`playdate.*` API on top of Lua via **lupa**, with rendering and audio via
**pygame-ce**.

It is not a CPU-level emulator (no QEMU): it is a **runtime** that loads the
`.pdx`, decodes the compiled formats (`.pdz`, `.pdi`, `.pdt`, `.pft`, `.pda`)
and executes the Lua chunks, translating the Playdate SDK API to Python. It
runs the same on your Mac and on a Raspberry Pi Zero 2 W.

> It works with **homebrew / itch.io unencrypted games** and SDK examples.
> Encrypted Catalog games (`bit 0x40000000`) are not executed.

## Introduction

This HLE (High-Level Emulation) emulator is still in development and has been
created strictly for educational and research purposes. Its main goal is to
provide a lightweight execution environment for titles created and compiled in
Lua using the Playdate SDK. Architecturally, the project uses Pygame to manage
the wrappers.

The system is optimized to operate efficiently on low-resource hardware,
designed with a particular focus on the Raspberry Pi Zero 2W for its
integration into custom handheld consoles. For now, its compatibility is
strictly limited to homebrew software and unofficial catalog games.

### Español / Introducción

Este emulador HLE (High-Level Emulation) sigue en fase de desarrollo y ha sido
creado estrictamente con fines educativos y de investigación. Su objetivo
principal es ofrecer un entorno de ejecución ligero para títulos creados y
compilados en Lua con el SDK de Playdate. A nivel arquitectónico, el proyecto
hace uso de Pygame para gestionar los wrappers.

El sistema está optimizado para operar eficientemente en hardware de bajos
recursos, diseñado con un enfoque particular en la Raspberry Pi Zero 2W para su
integración en consolas portátiles personalizadas. Por ahora, su compatibilidad
está limitada de forma exclusiva a software homebrew y juegos de catálogos no
oficiales.

## Quick start (no Playdate SDK, no console needed)

You do **not** need the Playdate SDK, a Playdate console, or any game source
code. Just install the Python requirements and run a compiled `.pdx`:

```bash
# 1. Install the requirements (Python 3.10+)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Build the Lua 5.4 / 32-bit bridge (one time; see note below)
sh tools/build_lua32.sh

# 3. Run a compiled game
python playdate_pi.py "games/<game>.pdx" --scale 3
```

**On a Raspberry Pi Zero 2 W, steps 1 and 2 are one command:**

```bash
sh install_pi.sh          # installs system packages, venv, requirements and the Lua bridge
source .venv/bin/activate
python playdate_pi.py "games/<game>.pdx" --scale 1
```

That's it. Drop any unencrypted `.pdx` (from itch.io, homebrew, or the SDK
examples) into `games/` and run it. The emulator decodes the compiled formats
and reimplements the `playdate.*` API itself — the SDK is only needed if you
want to *compile* your own games, not to run them.

> **Why step 2?** Playdate compiles `.pdz` for a 32-bit Lua VM. The standard
> `pip install lupa` cannot load that bytecode, so the repo vendors the lupa
> source (`vendor/lupa/`) and `tools/build_lua32.sh` builds it against Lua 5.4
> with `LUA_32BITS`. It is a one-time build (macOS or Linux, including the Pi).

## Tested games

From **itch.io** (homebrew):

| Game | Screenshot | Status |
|------|------------|--------|
| **Shrimp Boom 004** | ![shrimpboom](assets/screenshots/shrimpboom.png) |  4-direction movement, shooting, HUD, sound (bubbles/pop/death) |
| **Playdate Broken Screen** | ![playdatebrokenscreen](assets/screenshots/playdatebrokenscreen.png) |  `.pdi` images decoded and drawn |
| **Fishing Simulator** | ![fishing](assets/screenshots/fishing.png) |  Menu, scene, sound (catch/select/confirm) |
| **Smolitaire 1.0.1** | ![smolitaire](assets/screenshots/smolitaire.png) |  Menu, gameplay, visible cursor (hand) |

From the **Playdate SDK** (compiled with `pdc`):

| Game | Screenshot | Status |
|------|------------|--------|
| **Flippy Fish** | ![flippyfish](assets/screenshots/flippyfish.png) |  Animated fish, collisions, score, floor, seaweed |
| **Sprite Collision Masks** | ![spritecollisionmasks](assets/screenshots/spritecollisionmasks.png) |  Group collision masks, bounces, sprites stay inside the box |

The game files themselves live in `games/` (see `.gitignore` — they are not
committed to the repo). Download the `.pdx` from itch.io / the SDK and drop
them there.

## Requirements

- **Python 3.10+** (tested on 3.14)
- **pygame-ce** `>=2.5` (the classic `pygame` won't do)
- **lupa** `>=2.0` **linked against Lua 5.4 with `LUA_32BITS`** — a critical
  requirement (Playdate compiles for a 32-bit Lua VM). See
  `docs/ARCHITECTURE.md`.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

> **lupa + LUA_32BITS**: Playdate compiles `.pdz` for 32-bit Lua (floats are
> `double`, but offsets are 32-bit). lupa must be compiled/linked with that
> variant, or compiled chunks won't load and crash. The vendored source is in
> `vendor/lupa/` and `tools/build_lua32.sh` builds it.

## How to run

```bash
python playdate_pi.py "games/<game>.pdx" [--scale 2] [--palette device|bw|yellow] [--frames N] [--verbose]
```

Examples:

```bash
# Run a game at 3x window size
python playdate_pi.py "games/peru.pdx" --scale 3

# 2x window (default)
python playdate_pi.py "games/peru.pdx"

# Headless: run 400 frames without a window and report errors (for testing)
python playdate_pi.py "games/peru.pdx" --frames 400

# With the game's logs and warnings about missing assets
python playdate_pi.py "games/peru.pdx" --verbose

# Other tested games
python playdate_pi.py games/shrimpboom004.pdx --scale 3
python playdate_pi.py "games/Fishing Simulator.pdx" --palette device
python playdate_pi.py games/FlippyFish.pdx --frames 400   # headless (test)
python playdate_pi.py games/shrimpboom004.pdx --verbose     # log API/assets
```

> The game must be at `games/peru.pdx`. If it is elsewhere, use the full path,
> e.g. `python playdate_pi.py "/path/to/peru.pdx" --scale 3`.

### Controls (map to D-pad + 2 buttons + crank)

| Key | Action |
|-----|--------|
| Arrows / WASD | D-pad |
| X / SPACE | A button (right) |
| Z / SHIFT | B button (left) |
| Q / E | Crank (counter-clockwise / clockwise) |
| ESC | Quit |

## Raspberry Pi Zero 2 W

The emulator is written to run on the Pi with no code changes.

**One-command setup (recommended):**

```bash
sh install_pi.sh
source .venv/bin/activate
python playdate_pi.py "games/peru.pdx" --scale 1
```

`install_pi.sh` runs all of the following for you; do it by hand only if you
prefer:

```bash
# 1. System packages (compiler + Python headers + curl)
sudo apt update
sudo apt install -y build-essential python3-dev python3-venv python3-pip curl

# 2. Python packages
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Build the 32-bit Lua bridge (same script as on the Mac)
sh tools/build_lua32.sh

# 4. Run a game (use --scale 1 or 2 on the Pi)
python playdate_pi.py "games/peru.pdx" --scale 1
```

Notes for the Pi:

- **Use `--scale 1` or `2`.** Scaling the 400×240 framebuffer to a big window is
  the main CPU cost; on the Zero 2 W keep it small. The emulator already renders
  the frame once and scales with nearest-neighbor.
- **Framebuffer / DRM (no desktop):** set `SDL_VIDEODRIVER=kmsdrm` before running.
- **No audio device:** set `SDL_AUDIODRIVER=dummy`. The game still runs; sound
  calls become no-ops (the mixer init already falls back gracefully).
- **Sound**: the note scheduler uses a single background thread with an adaptive
  sleep, so it stays near idle when there is no music. Hundreds of scheduled
  notes do not spawn hundreds of threads (important on the Pi).
- **Performance**: the Lua interpreter and the 1-bit quantization are the other
  costs. 400×240 at 30 FPS is comfortable on the Zero 2 W.

## How it works (architecture map)

Four layers turn the compiled `.pdx` into pixels and audio. Each has its own
section in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) with diagrams.

```text
┌─────────────────────────────────────────────────────────────┐
│ 1. GAME LOADING   — uncompress/parse the .pdx (.pdz, .pdi,  │  pd/pdx.py
│    .pft, .pda, pdxinfo) and resolve assets                  │
├─────────────────────────────────────────────────────────────┤
│ 2. INTERPRETATION — lupa executes the Lua 5.4 32-bit chunks │  pd/luavm.py
│    ; the game asks for the API via playdate.*               │
├─────────────────────────────────────────────────────────────┤
│ 3. PYTHON API     — the playdate.* table is built           │  pd/runtime.py
│    ({graphics, sound, sprite, display, button, geometry})   │
├─────────────────────────────────────────────────────────────┤
│ 4. WRAPPING       — Playdate objects = userdata, flat       │  pd/luaobj.py
│    tables, metamethods, callbacks, gotchas                   │
└─────────────────────────────────────────────────────────────┘
```

Short version of each part:

1. **Game loading** — `.pdx` is a folder with `main.pdz`, `.pdi/.pdt/.pft/.pda`
   and `pdxinfo`. `pdz` is a container with chunks that may be compressed
   (`FLAG_COMPRESSED=0x80`) or encrypted (`FLAG_ENCRYPTED=0x40000000`, not
   supported). Assets are resolved **ignoring the extension**.
2. **Interpretation** — a lupa `LuaRuntime` plus `patch_lundump` loads 32-bit
   bytecode. `playdate.update()` runs in a coroutine (like the console).
3. **APIs** — the pattern: implement the method in Python and register it in
   `Runtime._build_api()`. Full guide and tooling in
   `docs/IMPLEMENTING_APIS.md` (including `tools/api_coverage.py` and
   `tools/headless_test.py`, which run your game and list the missing API).
4. **Wrapping** — Playdate objects are exposed as **userdata** (not tables)
   because games check `type(v)=="userdata"`. Tables are **flat** (Python
   functions on the leaves, never nested objects). Real Lua metamethods for
   operators (`polygon * transform`, `imagetable[i]`, `sprite:update()`
   defaults). lupa gotchas documented in `ARCHITECTURE.md`.

## Project structure

```text
playdate_pi/
├── playdate_pi.py          # entry point (CLI)
├── install_pi.sh           # one-shot Raspberry Pi setup
├── requirements.txt
├── README.md
├── docs/
│   ├── ARCHITECTURE.md     # per-component diagrams + wrapping + common bugs
│   ├── IMPLEMENTING_APIS.md  # how to add APIs + tooling
│   ├── COMPILED_GAMES.md  # compiled .pdx (LUA_32BITS, opcodes, import)
│   ├── ROADMAP.md            # pending work, ordered by impact
│   └── PFT_FONTS.md      # .pft font decoding
├── assets/screenshots/     # screenshots of the tested games
├── games/                  # the .pdx games (gitignored)
├── tools/                  # debug / API-coverage utilities
├── vendor/lupa/            # vendored lupa source (for LUA_32BITS build)
├── pd/
│   ├── pdx.py              # .pdz container + format
│   ├── pdi.py pft.py pda.py  # decoders for .pdi/.pft/.pda
│   ├── luavm.py            # LuaRuntime (lupa, 32-bit)
│   ├── runtime.py          # playdate.* API + loop (update/draw)
│   ├── graphics.py         # rendering (400x240, 1-bit palettes)
│   ├── sprite.py           # sprite system + collisions/bounces
│   ├── sound.py            # audio (pygame.mixer) + ADSR synth
│   ├── geometry.py image.py input.py screen.py emulator.py
│   ├── luaobj.py filestore.py
│   └── lib/                # Lua bridges (playdate_ext.lua, etc.)
```

## Status and limitations

**Works**: loads unencrypted `pdz`; decodes `.pdi/.pdt/.pft/.pda`; API of
`graphics`, `sound`, `sprite`, `geometry`, `display`, `button`, `file`.
Group/mask collisions with `collisionResponse`. Audio `.pda` (PCM + IMA ADPCM)
and synth (noise/sawtooth/square/sine/triangle with ADSR and notes at absolute
engine time). Background/appearance handled by the sprite system with real
console semantics.

**Limitations**: no encryption (Catalog); `CoreLibs/assets/*` that are not in
the `.pdx` emit warnings; some `sound` effects/APIs (sequence, filters) are
permissive stubs. The lupa bridge writes Lua fields (it never reads fields
that could pass through metamethods because coroutines).

## Contribute / move forward together

It is designed so other developers **and AI coding agents** can extend it.
Start with [`CONTRIBUTING.md`](CONTRIBUTING.md) (the rules and the API pattern)
and [`docs/ROADMAP.md`](docs/ROADMAP.md) (the pending work, ordered by impact).
To add an API or fix fidelity: read `docs/ARCHITECTURE.md` and
`docs/IMPLEMENTING_APIS.md`, follow the patterns (Python method + registration
in `_build_api()`, userdata, flat tables, callbacks) and record the lesson
learned. The games in the list are good regression cases (the
`tools/headless_test.py` runner steps every game and reports the first error).

## Key discoveries (summary)

1. **lupa does not map Python `__mul__` to Lua metamethods**: use
   `debug.setmetatable` with a **Lua** function (not Python) as `__index`.
2. **Playdate objects must be userdata**, not tables: games check
   `type(v)=="userdata"`, and if it is `"table"` they take the wrong branch.
3. **`sprite.update()` / background**: sprites are only drawn if the GAME
   calls `sprite.update()` (console semantics); foreground = color or
   `background_cb`.
4. **Color**: `kColorBlack=0` (ink), `kColorWhite=1` (paper); `setColor` must
   interpret literals `0x000000`/`0xffffff`, not only booleans.
5. **Audio**: `synth:playNote(freq, vol, length, when)` uses `when` **absolute**
   (seconds since the audio engine started, not relative); default waveform =
   **noise**. `.pda` includes IMA ADPCM.
6. **Cursor/hand**: 1-bit images filled with paper (`bit=1`) are invisible on
   a light background; draw in ink when the sprite is predominantly paper.

Each is detailed (cause + fix) in `docs/ARCHITECTURE.md`.

## Support / Donate

If this project helps you, consider a small donation to support the ongoing
research and development (electronics research, emulation, reverse-engineering):

[<img src="https://www.paypalobjects.com/paypal-ui/logos/svg/paypal-mark-color.svg" alt="Donate with PayPal" width="120">](https://www.paypal.com/donate/?business=59PBMU7RG4CBS&no_recurring=0&currency_code=USD)

**Donate via PayPal:** <https://www.paypal.com/donate/?business=59PBMU7RG4CBS&no_recurring=0&currency_code=USD>

Any amount is appreciated. Thank you!

## License

Released under the **MIT License**. See [LICENSE](LICENSE).

---

Built with the [Playdate SDK](https://play.date/dev/), [lupa](https://github.com/scoder/lupa)
and [pygame-ce](https://pyga.me/).

---

**LIMA - PERU**
**PHC**
