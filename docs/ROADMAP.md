# Roadmap — pending work

This is the list of known open items, ordered by impact. Each entry says what
is wrong, where the code lives, and **how to verify a fix**. Pick one, follow
`CONTRIBUTING.md`, and validate with the test tools.

Legend: `[open]` = not started · `[wip]` = in progress · `[blocked]` = needs
something else first.

---

## 1. Kickflip background music (sequencer) — `[resolved]`

**Status**: implemented. `Sequence` and `Track` now live in `pd/sound.py` and
are wired into `playdate.sound` in `pd/runtime.py` (`_sound_table`). Kickflip
Coast builds 8 tracks with ~950 notes at tempo 8 steps/sec and `play()` now
schedules them through the tracks' Synths. A single background scheduler thread
(a heap of pending notes) plays them, so hundreds of notes do not spawn hundreds
of threads.

**Notes**: `sequence:setTempo` is in **steps per second** (not BPM);
`addTrack()` with no argument creates and returns a new track; `addNote(step,
note, length, velocity)` accepts a MIDI number or a note name like "Db3".

**Verify**: run `Kickflip Coast Demo.pdx` and confirm the music plays and loops.

---

## 2. FlippyFish score does not increment — `[resolved]`

**Status**: fixed and confirmed by the user — the score now increments when the
fish passes through the gap.

**Where it was**: `pd/sprite.py` (`moveWithCollisions`, `sprite_response_call`,
`alpha_collision`), `pd/runtime.py` (`_spr_move_collisions`). The fish falls
20px/frame, so on first contact its pixels may not touch yet
(`alphaCollision=false`); gameOver/score only fire on a later frame while still
overlapping.

**Verify (regression)**: run `FlippyFish.pdx`, play past the first gap, confirm
the score increments.

---

## 3. Asheteroids `t:rotate()` — `[resolved]`

**Status**: fixed. The geometry metatable install (`pd/runtime.py`,
`LUA_GEOM_MT`) was REPLACING lupa's metatable with one that only had `__mul`,
which removed `__index` and made any `t:rotate()` / `t.x` fail with
"attempt to index a userdata value". It now PRESERVES lupa's metatable and only
ADDS `__mul`, so both the operator and the Python methods work while
`type(t) == "userdata"` is kept.

**Verify (regression)**: run `mijuego2.pdx`, confirm it boots, the ship
rotates, and there is no `vectorsprite.lua:32` error.

---

## 4. `.pda` audio integration — `[wip]`

**Problem**: `.pda` decoding works (PCM + IMA ADPCM) but not all games' audio
is wired through. Some `sound` APIs (sequence, filters, effects) are permissive
stubs.

**Where**: `pd/pda.py`, `pd/sound.py`, `pd/runtime.py` (`_sound_table`,
`_audio_stub`).

**What to do**: wire the decoded `.pda` samples into the games that use them
(Shrimp Boom's bubbles/pop, Fishing Simulator's confirm), and replace the
`_audio_stub` no-ops with real behavior where feasible.

**Verify**: run `shrimpboom004.pdx` and `Fishing Simulator.pdx`, confirm the
sound effects play.

---

## 5. Regression test runner — `[open]`

**Problem**: there is no single command that runs all tested games and reports
errors, so a change can silently break another game.

**What to do**: add a `tools/regression.py` that runs each game in
`docs/ROADMAP.md`'s tested list headless for N frames with injected input and
reports per-game errors. Wire it into `CONTRIBUTING.md`'s "definition of done".

**Verify**: `python tools/regression.py` runs all games and reports 0 errors.

---

## 6. Tested games (regression list)

These are the games used to validate changes. Keep them working:

| Game | Path | Notes |
|------|------|-------|
| Shrimp Boom 004 | `games/shrimpboom004.pdx` | movement, shooting, HUD, sound |
| Playdate Broken Screen | `games/PlaydateBrokenScreen.pdx` | `.pdi` images |
| Fishing Simulator | `games/Fishing Simulator.pdx` | menu, scene, sound |
| Smolitaire 1.0.1 | `games/smolitaire-1.0.1.pdx` | menu, gameplay, cursor |
| Flippy Fish | `games/FlippyFish.pdx` | collisions, score, floor |
| Sprite Collision Masks | `games/SpriteCollisionMasks.pdx` | group masks, bounces |

---

## 7. Nice-to-have (lower priority)

- **`CONTRIBUTING.md` issue templates** — GitHub issue templates for bug / new
  API so reports arrive structured.
- **`LICENSE`** — pick one (suggestion: MIT).
- **More `.pft` font coverage** — the decoder works; validate against more SDK
  fonts (see `docs/PFT_FONTS.md`).
- **`playdate.display.setScale` chunky look** — verify the downscale/upscale
  matches the console for all supported scales.
