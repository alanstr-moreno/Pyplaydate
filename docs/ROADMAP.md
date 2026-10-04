# Roadmap — pending work

This is the list of known open items, ordered by impact. Each entry says what
is wrong, where the code lives, and **how to verify a fix**. Pick one, follow
`CONTRIBUTING.md`, and validate with the test tools.

Legend: `[open]` = not started · `[wip]` = in progress · `[blocked]` = needs
something else first.

---

## 1. Kickflip background music (sequencer) — `[open]`

**Problem**: Kickflip Coast's background music does not play. The `.pdx` has no
`.pda` files — the music is **synthesized** (a sequence of `Synth` + `playNote`
calls). The current `Synth.playNote` schedules notes but the sequencer structure
is not read.

**Data**: the game's music is a JSON-like structure per song:
`{id, bpm, name, notes[channels][...], splits, loopFrom, ticks}`. `notes` per
channel are `[duration, frequency]` pairs; `loopFrom` is the tick where the
loop starts; `ticks` the total length in 1/16th ticks.

**Where**: `pd/sound.py` (`Synth`, `playNote`), `pd/runtime.py` (`_sound_table`).

**What to do**: implement a sequencer that reads the song structure, schedules
notes at absolute engine time (`getCurrentTime()`), respects `loopFrom`/`ticks`,
and uses the default waveform (noise/sawtooth) with ADSR.

**Verify**: run `Kickflip Coast Demo.pdx`, confirm the music plays and loops.
Check `Synth.playNote` receives `(freq, vol, length, when)` with `when` absolute.

---

## 2. FlippyFish score does not increment — `[open]`

**Problem**: the fish flies and collides, but `score` stays 0. The
`collisionResponse` fires, but the "pass through the gap" logic never calls
`addOne`.

**Where**: `pd/sprite.py` (`moveWithCollisions`, `sprite_response_call`,
`alpha_collision`), `pd/runtime.py` (`_spr_move_collisions`).

**What to do**: trace when the fish crosses the seaweed gap and whether the
score sprite's `collisionResponse` is invoked on the right frame. The fish
falls 20px/frame, so on first contact its pixels may not touch yet
(`alphaCollision=false`); gameOver/score only fire on a later frame while still
overlapping.

**Verify**: run `FlippyFish.pdx`, play past the first gap, confirm the score
increments. Instrument `addOne` / the score sprite's `collisionResponse`.

---

## 3. Asheteroids `t:rotate()` — `[open]`

**Problem**: `mijuego2.pdx` (Asheteroids) reaches `vectorsprite.lua:32` and
fails on `t:rotate()`. Installing the geometry metatable with
`debug.setmetatable` replaces the lupa metatable and loses the object's methods.

**Where**: `pd/runtime.py` (`_install_geometry_metamethods`, `LUA_GEOM_MT`),
`pd/geometry.py` (`AffineTransform.rotate`).

**What to do**: preserve the existing lupa metatable when installing the
geometry metamethods (the lesson already applied to the SCM case but not closed
for Asheteroids).

**Verify**: run `mijuego2.pdx`, confirm it gets past `vectorsprite.lua:32` and
the ship rotates.

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
