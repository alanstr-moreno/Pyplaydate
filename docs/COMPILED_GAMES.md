# Running a COMPILED game (`.pdx`) — findings and how it's done

Document for the hardest part of the emulator: running a **compiled** game
(a real `.pdx`, not source code). Everything here is verified against
`smolitaire-1.0.1.pdx` (a real itch.io game, no DRM).

---

## 1. What a compiled game really is

```
Smolitaire.pdx/            <- bundle (folder)
    pdxinfo                <- metadata (name, bundleID, version...)
    main.pdz               <- CONTAINER with the compiled Lua bytecode
    dialog.pdz, options.pdz, ...
    images/*.pdi  *.pdt    <- images already compiled (1-bit)
    audio/*.pda            <- compiled audio
```

- **There is no `main.lua`.** The code is in `main.pdz`.
- `.pdz` = container: `Playdate PDZ` header + flags, then entries
  (bytecode, images, fonts, audio). Entries can be zlib-compressed.
- **DRM**: **Catalog (store)** games ship the `main.pdz` **encrypted**
  (bit `0x40000000`, proprietary method). Those **cannot** be run, and are not
  attempted. `pd/pdx.py` detects them and warns.
- **homebrew / itch.io / sideloaded games are NOT encrypted** and do run.

Reader: **`pd/pdx.py`** (`read_pdx`, `extract_pdx`, `parse_pdz`).

---

## 2. The 3 technical barriers (and how they were solved)

Running Playdate bytecode with a normal Lua **does not work**, for three
independent reasons. All three cost time:

### (a) `LUA_32BITS=1`

Playdate uses Lua 5.4.3 with **4-byte integers and 4-byte floats**.
A standard Lua (8-byte int) rejects the bytecode:
`bad binary format (lua_Integer size mismatch)`.

Verified by reading the header of the extracted `.luac`:
```
1b4c7561 54 00 ... 04 04 04 78    sig="\x1bLua" ver=0x54 inst=4 INT=4 NUM=4
```

→ Compile Lua 5.4.3 with `#define LUA_32BITS 1` in `luaconf.h`.

### (b) The opcode enum is DIFFERENT (the trap)

Playdate uses the **Lua 5.4 *beta*** enum: `OP_LOADFALSE`/`OP_LFALSESKIP`/
`OP_LOADTRUE` are **at the end** (81–83) and there's a hole at 5; the rest is
shifted −2 relative to final 5.4.3.

**The symptom is misleading**: the bytecode **loads without error** (the header
is valid) but **executes incorrectly**. Detected by looking at the
disassembly: impossible things appeared, like `TEST` without its `JMP` and
`FORPREP ... ; to = 1042` in a 225-instruction function.

**The trap**: "fixing it" by moving the opcodes in `lopcodes.h` **breaks the VM
dispatch table** → **segfault** on normal Lua.

**The correct solution**: don't touch the enum/VM; **remap the opcodes AT LOAD
TIME**, in `lundump.c` (`LoadCode`). Map:

| Playdate opcode | Lua 5.4.3 std opcode |
|-----------------|----------------------|
| 0..4            | 0..4 |
| 6..80 (LOADNIL..EXTRAARG) | 8..82 |
| 81/82/83 (LOADFALSE/LFALSESKIP/LOADTRUE) | 5/6/7 |

Script: **`tools/patch_lundump.py`**.

Verification: the disassembly becomes coherent —
```
2  GETTABUP 0 0 0  ; _ENV "import"
3  LOADK    1 1    ; "CoreLibs/object"
4  CALL     0 2 1  ; import("CoreLibs/object")
```

### (c) `import()` and the `.pdz` chunks

Playdate uses `import "CoreLibs/graphics"` (not `require`): it loads the
compiled chunk from the `.pdz` itself and executes it **once**.

**`pd/runtime.py`** implements it: `_load_pdx` indexes the type-1 entries
(bytecode) in `self.chunks`, defines the global `import`, and runs the `main`
chunk. `import(name)` loads on demand and caches.

---

## 3. Reproducible build

Everything together in **`tools/build_lua32.sh`** → produces `vendor/lupa/`
(lupa compiled against the patched Lua, self-contained: only `libSystem`).

```sh
sh tools/build_lua32.sh          # macOS or Linux (including the Pi Zero 2 W)
```

`pd/luavm.py` uses `vendor/lupa` if it exists (`lupa.lua`); otherwise it falls
back to the system lupa (which only works for source games).

---

## 4. `.pdi` / `.pdt` image format and its decoder

A compiled game **ships no PNGs**: `pdc` converts to `.pdi` (1-bit image) and
`.pdt` (spritesheet). Header `Playdate IMG`/`Playdate IMT` + flags (bit 31 =
compressed → zlib).

Data = **cells**: transparent borders are cropped and must be reconstructed.

```
Cell header (uint16 LE): clipW, clipH, stride, clipL, clipR, clipT, clipB, flags
  color bitmap: stride*clipH bytes, 1 bit/px, 0=black 1=white, MSB first
  if flags&0x3:    alpha bitmap (1=opaque)
  final width = clipL + clipW + clipR
```

Implemented in **`pd/pdi.py`** (`decode_pdi`, `decode_pdt`, `decode_cell`).
Verified against real assets: `title.pdi` decodes to **"Smolitaire"** with a
dithered shadow, legible and not mirrored (confirms the MSB bit-order).

---

## 5. "Handle" pattern for objects

Because of the **lupa bug** (see `README`/`GUIA`), NEVER expose nested Python
objects to Lua. Playdate "objects" are **Lua tables with `__id`**, and the real
state lives in Python:

```python
self._registry[id] = python_object     # pd/runtime.py
```

The handle table also carries its methods as fields (`:draw`, `:getSize`,
`...`), because the game does `img:draw(x,y)` (which passes `img` as the 1st
argument).

Full example in `pd/runtime.py::_image_handle` / `_imagetable_handle`, with the
object implementation in `pd/image.py` and the drawing in `pd/graphics.py`.

---

## 6. The missing-API debugger

When the game lacks an API, Lua dies with an opaque message
(`attempt to index a nil value`) and you fix them one at a time.
**`pd/apidbg.py`** + **`tools/find_missing_api.py`** solve it:

```sh
python tools/find_missing_api.py smolitaire-1.0.1.pdx -n 120
```

What it does:
- Installs a **proxy** over the `playdate` tree that records every access to a
  **missing** field (with its full path) and returns a **chainable stub**, so
  the game **keeps running** instead of dying.
- Isolates errors with `pcall` (`__pd_safe`) → discovers **many** missing APIs
  in a single run.
- Returns a report grouped by module and ordered by usage.

Typical output:
```
=== MISSING APIs (55) ===
  playdate.graphics.sprite
      playdate.graphics.sprite.new().setZIndex   x2
      playdate.graphics.sprite.new().moveTo      x1
  playdate.sound.sampleplayer
      playdate.sound.sampleplayer.new()          x10
```

Use from code:
```python
from pd import apidbg
rt.on_ready = apidbg.install          # before running the game
...
missing, errors = apidbg.report(rt)
```

---

## 7. Current state (verified)

| Stage | State |
|---|---|
| Read `.pdx`/`.pdz`, detect DRM | ✅ |
| Extract bytecode and assets | ✅ |
| Patched Lua 5.4.3 (LUA_32BITS + opcode remap) | ✅ |
| Load all 25 Smolitaire chunks | ✅ |
| Run bytecode (`import()` actually works) | ✅ |
| Decode `.pdi`/`.pdt` (visually verified) | ✅ |
| `graphics.image` / `graphics.imagetable` | ✅ |
| `graphics.sprite` (+ AABB collisions) | ✅ |
| `playdate.geometry` (pure Lua) | ✅ |
| `playdate.sound` (shim) / menu / `metadata` / `kButton*` | ✅ |
| **First rendered frame of the compiled game** | ✅ |
| Full playable game | ⬜ missing API + 1 snag |

**Progress measured with the debugger** (Smolitaire): 88 → 55 → 34 → 8 → 5 → **0
missing APIs and 0 errors**. The game **boots fully**, runs **900 frames with
injected input without a single error**, and renders its identity screen (the
"Twenty Minute Mile" logo, drawn with `drawLine` + `setLineWidth`).

**Next iteration**: the game no longer crashes; what's missing is *fidelity*
(that the logo animation advances, the menu, the cards) and the APIs the game
hasn't touched yet because it never reached that screen. The cycle stays the
same: run the debugger, implement the block above, repeat.

**Snag resolved** (the subtlest of all): the game's volume closure walks
`sounds` recursively —
`if type(v)=="table" then <recurse> else v:setVolume(0.5) end`.
Our sampleplayers were **Lua tables**, so the game *entered* them and ended up
calling `setVolume` on one of our methods (a Python function) →
`AttributeError: 'function' object has no attribute 'setVolume'`.

On the console those objects are **userdata**, not tables: `type(v) ~= "table"`
→ the correct branch. The solution was the central lesson below.

---

## 7-bis. The 4 lessons that cost time

1. **Playdate objects are USERDATA, not tables.** Exposing them as Lua tables
   breaks any game that does `if type(x) == "table"`. They are resolved as
   Python objects (lupa delivers them as `type() == "userdata"`), with a
   *permissive* `__getattr__` that returns no-ops for missing methods —
   **excluding the dunders**, because lupa probes `__getitem__`/`__len__` to
   decide how to access the object. See `pd/luaobj.py`.
   (Verified stable at 20 000 reads with the patched lupa.)

2. **`playdate.file.*` reads from the BUNDLE, not a sandbox.** `file.open("common/images/x.dat")`
   resolves against the `.pdx` root. Only `datastore` is a separate writable
   sandbox (`.pd_data/`). Symptom if you get it wrong: `file.open` returns nil
   and the game dies with "attempt to index a nil value" on `f:read()`.

3. **`lua_Integer` is 32-bit** (`LUA_32BITS`). Returning a large integer
   (e.g. `time.time()*1000` ≈ 1.7e12) crashes with
   *"value too large to convert to lua_Integer"*. You must return **float**.

4. **`lua_Number` is 4-byte float.** Epoch-ms precision is not representable:
   time deltas lose resolution. Acceptable to boot; keep in mind if a game
   measures precise times.

---

## 8. References

- Formats (`.pdz`, `.luac`, `.pdi`, `.pdt`, `.pft`):
  `github.com/cranksters/playdate-reverse-engineering`
- `build_lua32.sh`, `patch_lundump.py`, `find_missing_api.py`, `api_coverage.py`
- General implementation guide: `docs/IMPLEMENTANDO_APIS.md`
