# Contributing — written for human AND AI contributors

This project is designed so that **both human developers and AI coding agents**
can contribute safely. If you are an AI agent, read this file fully before
touching any code: it contains the rules that keep the emulator from breaking
in ways that are hard to debug.

---

## 1. What this project is (30 seconds)

A **runtime emulator for the Playdate** in Python. It loads **compiled `.pdx`
games** (Lua 5.4 / 32-bit bytecode), decodes the compiled formats (`.pdz`,
`.pdi`, `.pft`, `.pda`) and reimplements the `playdate.*` API on top of Lua via
**lupa**, rendering with **pygame-ce**.

The hard part is the **Lua <-> Python bridge**. Most bugs come from violating
the rules in section 3. Read them.

---

## 2. Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sh tools/build_lua32.sh        # builds the Lua 5.4 / 32-bit lupa (one time)
```

On a Raspberry Pi Zero 2 W, `sh install_pi.sh` does all of the above (system
packages included) in one command.

Run a game:

```bash
python playdate_pi.py "games/<game>.pdx" --scale 3
```

Headless test (no window, reports the first error with its frame):

```bash
python tools/headless_test.py "games/<game>.pdx" -n 300
```

Find which APIs a game uses that the emulator is missing:

```bash
python tools/find_missing_api.py "games/<game>.pdx" -n 120
```

---

## 3. The golden rules (violating these = hard-to-debug bugs)

These are the hard-won lessons. An AI agent MUST follow them.

### R1. Playdate objects are USERDATA, not Lua tables
Games do `if type(x) == "userdata" then ... else ... end`. If you expose a
Playdate object (sprite, image, polygon, synth...) as a **Lua table**, the game
takes the wrong branch and breaks. Expose them as **Python objects** — lupa
delivers them as `type() == "userdata"`. See `pd/luaobj.py`.

### R2. Never expose NESTED Python objects to Lua
lupa corrupts attribute access on nested Python objects after ~77 reads. The
API is built as **flat Lua tables** (`lua.table_from`) with **Python functions
on the leaves**. Never do `g["playdate"] = python_object`.

### R3. lupa does NOT map Python operators to Lua metamethods
`poly * transform`, `imagetable[i]`, `sprite:update()` defaults need real Lua
metamethods installed with `debug.setmetatable` and a **Lua function** (not a
Python function) as `__index`/`__mul`. Preserve the existing lupa metatable.

### R4. A Python function exposed to Lua is `userdata`, not `function`
Any Lua wrapper that checks `type(f) == "function"` will skip your functions.
Use `if f ~= nil`.

### R5. Never READ Lua table fields from Python that could pass through metamethods
Reading a field with `__index` from Python runs Lua code inside a lupa call and
**segfaults**. Writing fields is safe. If you must read, protect against the
coroutine tuple `(thread, id)` (see `Runtime._deref`).

### R6. The 2-step API pattern
To add any API:
1. Implement the method in the Python class (`Graphics`, `Button`, `Sprite`...).
2. Register it in `Runtime._build_api()` with its Playdate name.

### R7. `sprite.update()` semantics
Sprites only move and draw when the **game** calls
`playdate.graphics.sprite.update()`. Do NOT auto-call it — it erases direct
drawing (e.g. Kickflip's splash).

### R8. Real SDK constants
`kColorBlack=0` (ink), `kColorWhite=1` (paper). `kCollisionType`:
`Slide=0, Freeze=1, Overlap=2, Bounce=3`. `setColor` must accept literals
`0x000000`/`0xffffff`, not only booleans.

---

## 4. How to add an API (worked example)

Say a game calls `playdate.graphics.fillCircle(x, y, r)` and it is missing.

**Step 1** — implement in `pd/graphics.py`:

```python
def fillCircle(self, x, y, r):
    pygame.draw.circle(self._canvas, self._ink(), self._xy(x, y), int(r))
```

**Step 2** — register in `pd/runtime.py`, inside the `gfx` dict of
`_build_api()`:

```python
"fillCircle": g.fillCircle,
```

**Step 3** — validate:

```bash
python tools/headless_test.py "games/<game>.pdx" -n 300
python tools/find_missing_api.py "games/<game>.pdx" -n 120
```

If it draws, save a frame and look at it:

```bash
python tools/headless_test.py "games/<game>.pdx" -n 300 --save /tmp/frame.png
```

---

## 5. What NOT to touch (unless you know why)

- **`pd/luavm.py`** and the `vendor/lupa/` build — the 32-bit Lua bridge. Only
  change via `tools/build_lua32.sh` / `tools/patch_lundump.py`.
- **The `_deref` / `_register` handle system** in `pd/runtime.py` — it exists
  because of the lupa bug (R2). Do not "simplify" it.
- **The `__index` metamethods** on imagetables and sprites — they must be Lua
  functions (R3), not Python.
- **`_sprite_update_once`** — the background-clearing semantics (R7).

---

## 6. Definition of "done" for a change

1. `python tools/headless_test.py "games/<game>.pdx" -n 300` passes with 0 errors.
2. `python tools/find_missing_api.py "games/<game>.pdx" -n 120` no longer lists
   the API you added.
3. If it draws: save a frame and **look at it** (don't assume).
4. If it has state (clip, offset, color): test that it draws *and* redraws after
   `popContext`, to catch state leaks.
5. Run the regression across the tested games (see `docs/ROADMAP.md` for the
   list) to confirm nothing else broke.

---

## 7. For AI agents specifically

- **Read `docs/ARCHITECTURE.md`** before starting — it has the per-component
  diagrams and a "common bugs" table.
- **Read `docs/ROADMAP.md`** — it lists the pending work with status, so you
  know what to pick up.
- **Make one focused change at a time** and validate it before moving on.
- **Never guess an API signature** — check `docs/IMPLEMENTING_APIS.md` (verified
  against Inside Playdate) or the SDK headers.
- **Report what you changed and how you verified it** (the exact test command
  and its output), not just "it should work".
- If a test fails, **read the error and the frame number** — the emulator prints
  `[frame N] ...` so you can reproduce exactly.

---

## 8. Code style

- English comments and docstrings only.
- Follow the existing style (4-space indent, no type hints unless already used).
- Keep the golden rules in section 3 as comments where they explain a non-obvious
  decision.
