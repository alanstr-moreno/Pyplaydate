# Playdate-Pi architecture — component walkthrough

This document explains **how the emulator works**, component by component, so
another developer understands the flow and extends it. Every section follows
the same pattern: *goal → data → diagram → code pointers → traps*.

---

## 1. Game loading (`pd/pdx.py`)

### Goal
Bring the compiled game (`.pdx`) to three usable things: the **bytecode** of
`main.pdz` (and submodules like `dialog.pdz`, `options.pdz`), the **assets**
(images/fonts/audio) and the **metadata** (`pdxinfo`, palette).

### Input data
- `.pdx` = a **folder** with `main.pdz`, `pdxinfo`, and subfolders
  (`images/`, `sounds/`, ...) containing `.pdi/.pdt/.pft/.pda`.
- `.pdz` = binary chunk container. Each chunk has a header with `id`
  (4 bytes), `flags` and `data_size`. Flags:
  - `FLAG_COMPRESSED = 0x80` → payload is compressed (zlib).
  - `FLAG_ENCRYPTED = 0x40000000` → AES-block encrypted (not supported;
    Catalog games carry this bit and are **not attempted**).

### Flow

```text
.pdx game folder
   │  main.pdz  ──► pd/pdx.py: deserialize chunks
   │                ├─ flag COMPRESSED  → zlib.decompress
   │                └─ flag ENCRYPTED   → [unsupported, soft abort]
   │  pdxinfo      ──► name, version, bundle id, content
   │  images/a.pdi ──► decoder (see §4) -> pygame.Surface with
   │                     alpha mask (SRCALPHA)
   │  sounds/x.pda ──► .pda decoder (see §3) -> numpy samples
   │  fonts/f.pft  ──► .pft decoder -> glyphs (poverty/B40)
   └──► FileStore (name -> bytes) resolved WITHOUT EXTENSION
```

### Asset resolution (key point)
Games request `playdate.graphics.image.new("images/fondo")` **without an
extension**. The `FileStore` in `pd/filestore.py` keeps an index
`name_without_extension → bytes` and returns whatever asset matches. If the
exact asset is missing, it **reports a warning** (so you see what's missing)
and returns something harmless (or `nil` depending on the game build).

### Running compiled code
`main.pdz` is not text: it is **Lua 5.4 / 32-bit bytecode**. `pd/luavm.py`
makes lupa load it with a patched deserializer (`patch_lundump`) that fixes the
32/64-bit differences. Submodule resolution via `import "CoreLibs/*"` is
forwarded to the SDK folders when the `.pdx` does not ship them. Full detail in
`docs/COMPILED_GAMES.md`.

### Traps
- Never add an extension when requesting an asset (the game doesn't).
- Do not read the `.pdz` as text; use the chunk deserializer.
- An encrypted `.pdz` is NOT "fixed by trying to decompress": soft-abort with a
  warning.

---

## 2. Bytecode interpretation and loop (`pd/luavm.py`, `pd/runtime.py`)

### Goal
Run the game's `update`/`draw` 30 times per second inside the same Lua 5.4
interpreter as the console, translating each `playdate.*` call to Python.

### Data
- A lupa **`LuaRuntime`** with the builtins games request
  (`math`, `string`, `table`, `os`, `bit`...).
- The loop lives in `pd/runtime.py`:
  `call_update()` → runs the coroutine `playdate.update()`;
  `call_draw()` → runs the coroutine `playdate.draw()`.

### Per-frame loop

```text
for frame in range(N):
    feed buttons (input)          # pd/input.py
    feed crank                    # if the user rotates it / joystick
    call_update()                 # runs playdate.update() in Lua (coroutine)
    call_draw()                   # runs playdate.draw()
    screen.render(scale)          # 400x240 -> window (nearest-neighbor)
    button.end_frame()            # clears "pressed this frame"

update() and draw() run as coroutines; lupa resumes them just like Playdate:
they call playdate.graphics.* which writes to the canvas.
```

### Coroutines
Playdate games are written as `function playdate.update()` which is **resumed**
by the runtime, not called anew. The emulator stores the coroutine
`update`/`draw` created the first time and resumes it each frame.

### Traps
- Any `LuaError` must be propagated (not silenced) to debug the exact frame.
- The bytecode is 32-bit; lupa without `LUA_32BITS` hangs or crashes on
  `load_buffer`. See `docs/COMPILED_GAMES.md`.

---

## 3. Audio reading (`pd/pda.py`, `pd/sound.py`)

### Goal
Play **the `.pda` effects** embedded in the game **and the music**.

### `.pda` format (Playdate Audio ~AUDD)
Header: 12-byte marker (`"Playdate AUDD"`) + `rate` (3 bytes little endian) +
`fmt` (1 byte). The format (`fmt` field) can be:
- **PCM 16-bit mono** (e.g. the *confirm* of Fishing Simulator).
- **IMA ADPCM stereo** (e.g. the *pop* of Shrimp Boom).

`pd/pda.py` decodes both to a numpy `float32` sample array (values in
`[-1, 1]`). For ADPCM the standard IMA step table and nibble expansion are
implemented (you must de-interleave the two channels).

### Synthesizer (note-driven music/effects)
Many games (Kickflip, ...) carry no `.pda`: the music is **synthesized** with
`playdate.sound`, creating `Synth` objects and scheduling notes:

```text
Synth:playNote(freq, volume, length, when)
   'when' is ABSOLUTE in seconds since the audio engine started
   (not relative to the call). Scheduled in a heap by
   (absolute_time, synth).

Default waveform = NOISE (the console sounds "crackly"), not sine.
ADSR (attack/sustain/decay) applied over each note.
```

Example of the note structure (provided by the user): each song is
`{id, bpm, name, notes[channels][...], splits, loopFrom, ticks}`. `notes` per
channel are `[duration, frequency]` pairs; `loopFrom` is the tick where the
loop starts; `ticks` the total length in 1/16th ticks.

### Diagram

```text
.pda ──► pd/pda.py ─► PCM strided / IMA ADPCM ─► float32 samples
                                                      │
Synth/playNote ──────────────────────────────────────► heap queue
                                                      │  (absolute when)
                                                      ▼
                                  pygame.mixer.Sound(samples) -> play()
```

### Traps
- **ADPCM stereo**: do not forget the two interleaved channels, or the sound
  comes out slowed down.
- `when` is **absolute**: treating it as relative makes everything play at
  startup.
- The short ADSR (`attack=0.0`, `release=0.02s`) is what gives the typical
  "plop".
- With a dummy mixer on the Pi (no audio out), `play()` must not crash.

---

## 4. Building / reading the API (`pd/runtime.py`, `pd/graphics.py`)

### Goal
The game finds its WHOLE API under `playdate.*`. The pattern is **2 steps**:

1. Implement the method in Python (in the corresponding module).
2. Register it in `Runtime._build_api()`.

### The `playdate` table

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

Each submodule is a **flat Lua table** whose leaves are **Python functions**.
The emulator resolves `playdate.graphics.foo` at call time.

### How to find which API a game uses
In `tools/` there is a debugger (`find_missing_api.py`) that runs the game in
isolation and lists **what needs to be implemented**, sorted by real usage.

### Big trap
- lupa turns **Python functions into `userdata`**, not `function`. Any check
  with `type(f)=="function"` **inside Lua** skips your functions. Use
  `if f ~= nil` or lupa as a bridge (see §5).

---

## 5. Python wrapping — the tricky part (`pd/luaobj.py`)

### Goal / golden rule
Expose the Playdate API to Lua while respecting **the type contract** that
games actually check. Four rules:

**R1. Playdate objects = USERDATA, not tables.**
Games do `if type(sprite) == "userdata" then ... else` (the "user/table" branch
usually handles Lua). If you expose a Python object as a lupa object directly,
lupa delivers it as `userdata` and the flows take the right branch. Examples:
`newSprite()`, `image.new()`, `polygon.new()`, `Synth`.

**R2. Flat tables with functions on the leaves; never nested objects.**
Exposing a Python object nested inside a table sometimes makes lupa lose the
attribute when accessed from Lua after many reads (known lupa bug with chained
objects). Solution: each sub-API is a **Lua table** with Python functions on
the leaves. No `g["playdate"] = python_object`.

**R3. Lua metamethods, not Python operators.**
lupa does **not** map Python `__mul__`, `__index`, `__call` automatically to
Lua metamethods. For `polygon * affineTransform`, `imagetable[i]`,
`synth:playNote` you install metamethods with **`debug.setmetatable`** and a
**Lua** function as `__index`/`__mul`. Preserve the existing lupa metatable (do
not replace it wholesale) or the object's methods are lost.

```lua
-- pattern (in pd/lib/playdate_ext.lua)
local mt = debug.getmetatable(obj) or {}
local old_index = mt.__index
mt.__mul = function(a, b) return playdate_ext.polygon_mul(a, b) end
mt.__index = function(self, k)
    if old_index and old_index ~= mt then return old_index(self, k) end
    return playdate_ext.polygon_field(self, k)   -- bound Python function
end
debug.setmetatable(obj, mt)
```

**R4. A Python function exposed to Lua is `userdata`.** This showed up already
in tests above: `type(polygon.new)=="userdata"`. Any Lua wrapper that checks
`type(f)=="function"` skips yours.

### Lifetime management
An exposed object is registered in a `LuaObjectRegistry` (dict
`id -> python`). Lua keeps `userdata` with the id; on GC the object is marked
to free the Python side.

### lupa gotchas when returning values
- A Python `tuple` is **unpacked** as multiple return values in Lua
  (`unpack_returned_tuples=True`). For ONE value, return a list or wrap it.
- A Python **list/dict is NOT** converted to a Lua table (it is userdata).
  To return a real table use `self.lua.table_from([...])`.
- Python `None` → Lua `nil`.

---

## 6. Sprites, colors and collisions (summary; see modules)

- **`pd/sprite.py`**: `Sprite` in Python with `image`, `center`, `moveTo`,
  `moveWithCollisions`, masks (`groupMask`, `collisionsEnabled`).
  The callback `sprite:draw(x,y,w,h)` receives a translate to the top-left of
  its `bounds()` and the ink color (the game draws in local "0,0").
- **Group collision**: `setGroups({2,3})` receives a **Lua table** → you must
  accept any iterable (not only `list/tuple`), or the masks stay at 0 and the
  sprites pass through the walls.
- **`moveWithCollisions`** returns 4 values (x, y, collided, overlapCount).
- Real constants: `kCollisionType` (`Slide=0, Freeze=1, Overlap=2, Bounce=3`).
- **Color**: `kColorBlack=0` (ink), `kColorWhite=1` (paper). `setColor`
  accepts booleans, 0/1 and literals `0x000000`/`0xffffff` (mapped to
  ink/paper). Default resolution is `400x240`, not 320x320.
- **1-bit images filled with paper** (the cursor hand) are invisible on a
  light background: `draw_sprite` draws them in ink when opaque-paper ≥ 2×
  ink so the cursor is visible.

---

## 7. Tricks and common bugs at a glance

| Symptom | Root cause | Fix |
|---------|-----------|-----|
| `type(polygon.new)=="userdata"` all around | lupa exposes Python functions as userdata | checks `f ~= nil`, not `type(f)=="function"` |
| `attempt to index a nil value (field 'affineTransform')` in vectors | missing geometry metatable on `vector2D` | install a `__index` Lua function over the userdata |
| sprites pass through walls | `setGroups` with a Lua table did not enter `_groups_to_mask` | accept generic iterables |
| sprites don't draw | the game does not call `sprite.update()` or the runtime over-called it | real semantics: only draws if the game calls `update()` |
| fish/score invisible | imagetable without numeric `__index` / global white color | Lua `__index` metatable; reset color per sprite |
| sounds "beepy" | synth defaults to sine | default = **noise** (waveform 4) + short ADSR |
| music doesn't play | notes scheduled with relative `when` | `when` absolute in seconds since engine start |
| cursor (hand) missing | 1-bit paper image invisible on light background | draw in ink when opaque-paper ≥ 2× ink |

---

## Development tools

- `tools/api_coverage.py` — which API your game uses and which is missing.
- `tools/headless_test.py` — runs N frames and reports the first error with
  its frame.
- `tools/find_missing_api.py <game>` — lists pending APIs by usage.
- `tools/build_lua32.sh` — builds lupa against Lua 5.4 with LUA_32BITS.
