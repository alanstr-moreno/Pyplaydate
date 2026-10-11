"""emulator.py — initializes pygame, loads the game and runs the 30 FPS loop."""

import os
import sys
import time
import traceback

import pygame

from . import screen as screen_mod
from .errors import GameIncomplete
from .runtime import Runtime

# pygame key -> Playdate button
#   D-pad: arrows or WASD          A: X or SPACE          B: Z
#   Crank: Q / E (rotate)          Quit: ESC
KEYMAP = {
    pygame.K_UP: "Up", pygame.K_DOWN: "Down",
    pygame.K_LEFT: "Left", pygame.K_RIGHT: "Right",
    pygame.K_w: "Up", pygame.K_s: "Down",
    pygame.K_a: "Left", pygame.K_d: "Right",
    pygame.K_x: "A", pygame.K_SPACE: "A",
    pygame.K_z: "B",
    pygame.K_LSHIFT: "B",              # left-handed: shift is also B
}

# The crank is not a digital key: rotate 22.5 degrees per frame while held.
# Q = counter-clockwise, E = clockwise.
CRANK_STEP = 22.5
CRANK_KEYS = {pygame.K_q: -1, pygame.K_e: 1}


class _FrameLimiter:
    """Precise frame limiter driven by the game's refresh rate.

    The Playdate calls `playdate.update()` 30 times per second by default and
    lets the game change that with `playdate.display.setRefreshRate()` (50 is
    the maximum). The limiter follows whatever the game asked for, so the
    emulator speaks the same clock as the console.

    It schedules against an absolute deadline rather than pygame's
    `Clock.tick()`: that sleeps through SDL_Delay, which on macOS overshoots by
    ~4 ms per frame, so asking for 30 Hz actually produced ~27 Hz and a game
    animating by frame count drifted slower than the console. The deadline
    keeps the long-run rate exact even when an individual sleep overshoots.
    """

    def __init__(self, fps=None, spin=0.0015):
        self.spin = spin
        self._period = 0.0
        self._deadline = time.perf_counter()
        if fps:
            self.set_rate(fps)

    def set_rate(self, fps):
        """fps <= 0 (or None) means 'as fast as possible', like rate 0."""
        period = (1.0 / float(fps)) if fps and fps > 0 else 0.0
        if period != self._period:
            self._period = period
            self._deadline = time.perf_counter()

    def tick(self, fps=None):
        if fps is not None:
            self.set_rate(fps)
        if self._period <= 0:
            return
        self._deadline += self._period
        now = time.perf_counter()
        if now - self._deadline > self._period:
            self._deadline = now          # fell behind: resync, never spin
            return
        while True:
            d = self._deadline - time.perf_counter()
            if d <= 0:
                return
            if d > self.spin:
                time.sleep(d - self.spin)


CONTROLS = """\
  arrows / WASD ......... D-pad
  X or SPACE ........... A (right button)
  Z or SHIFT ........... B (left button)
  Q / E ................. crank (counter-clockwise / clockwise)
  ESC ................... quit
"""


class Emulator:
    def __init__(self, game, scale=2, fps=None, headless=False, palette="device",
                 verbose=False):
        self.game_name = game
        self.scale = scale
        self.fps = fps
        self.headless = headless
        self.frame = 0
        self.running = False
        # Set when the game asks for a file that is not there: the Lua loop
        # stops and the error screen is painted instead of the game dying.
        self.incomplete = None
        self.screen = screen_mod.Screen(palette=palette)
        self.crank_angle = 0.0        # degrees, the crank's source of truth
        self._crank_dir = 0           # -1 / +1 while Q or E is held

    def _resolve_game_dir(self):
        # direct path: source folder, .pdx bundle or .pdz file
        if os.path.exists(self.game_name):
            return os.path.abspath(self.game_name)
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidate = os.path.join(here, "games", self.game_name)
        if os.path.isdir(candidate):
            return candidate
        raise SystemExit(f"Game not found: '{self.game_name}'")

    def run(self, max_frames=None):
        if self.headless:
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
            os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

        pygame.init()
        pygame.display.init()
        flags = 0
        win = pygame.display.set_mode(self.screen.window_size(self.scale), flags)
        pygame.display.set_caption(f"Playdate Pi — {self.game_name}")
        clock = _FrameLimiter(self.fps)

        runtime = Runtime(self)
        runtime.verbose = bool(getattr(self, "verbose", False))
        self._runtime = runtime
        self._button = runtime.playdate.button
        # A game whose bundle is missing files must NOT take the process down
        # with a traceback: it is caught here and turned into the error screen.
        try:
            runtime.load(self._resolve_game_dir())
        except GameIncomplete as e:
            self._mark_incomplete(runtime, e.missing)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            if getattr(self, "verbose", False):
                traceback.print_exc()      # --verbose: full trace for debugging
            self._mark_incomplete(runtime, runtime.incomplete,
                                  why=f"{type(e).__name__}: {e}")

        if not self.headless:
            print(f"Playdate Pi — {self.game_name}\n{CONTROLS}")

        self.running = True
        while self.running:
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    self.running = False
                elif e.type == pygame.KEYDOWN:
                    self._key(e.key, True)
                elif e.type == pygame.KEYUP:
                    self._key(e.key, False)

            # The crank rotates while the key is held; the delta is reported to
            # the game once per frame, like the console does.
            crank_delta = self._crank_dir * CRANK_STEP
            if crank_delta:
                self.crank_angle += crank_delta

            if self.incomplete:
                # The game asked for a missing file: the Lua loop is stopped and
                # the error screen painted, but the window stays open (QUIT/ESC
                # still work) exactly so the user can see what happened.
                self._draw_incomplete()
            else:
                # Game errors must NOT close the emulator: they are printed (with
                # the frame) and the last screen keeps being drawn. On the console
                # a script error returns you to the menu, it never shuts the window.
                # A missing file is different: it means the bundle is incomplete,
                # so the loop stops and the error screen takes over.
                try:
                    runtime.call_update()
                    runtime.call_draw()
                except Exception as e:  # noqa: BLE001
                    self._game_error(runtime, e)
                if crank_delta and not self.incomplete:
                    try:
                        runtime.call_crank(crank_delta, 0)
                    except Exception as e:  # noqa: BLE001
                        self._game_error(runtime, e)
            self.screen.render(win, self.scale)
            pygame.display.flip()

            self.frame += 1
            runtime.playdate.button.end_frame()
            # The console clock: 30 Hz by default, whatever the game asked for
            # via playdate.display.setRefreshRate() (50 max). --fps overrides.
            rate = (self.fps if self.fps is not None
                    else runtime.playdate.display.getRefreshRate())
            clock.tick(rate)
            if max_frames and self.frame >= max_frames:
                self.running = False

        pygame.quit()

    # --- error screen ----------------------------------------------------
    def _mark_incomplete(self, runtime, missing=None, why=None):
        """Records that the game is incomplete; the Lua loop stops from here on."""
        if self.incomplete:
            return
        name = missing or (runtime.missing_assets[0] if runtime.missing_assets else None)
        self.incomplete = name or "archivo desconocido"
        parts = [why] if why else []
        if runtime.missing_assets:
            parts.append("ausentes: " + ", ".join(sorted(set(runtime.missing_assets))))
        print(f"[juego incompleto] {self.incomplete} :: " + " | ".join(parts or ["?"]))

    def _game_error(self, runtime, exc):
        """A game error is only printed -- unless the game had already asked for
        a file that is not there, in which case it is reported as incomplete."""
        if runtime.incomplete or runtime.missing_assets:
            first = str(exc).splitlines()[0]
            self._mark_incomplete(runtime, runtime.incomplete,
                                  why=f"{type(exc).__name__}: {first}")
            return
        print(f"[frame {self.frame}] game error: {type(exc).__name__}: {str(exc)[:120]}")

    def _draw_incomplete(self):
        """Paints "juego incompleto" centered, keeping the window alive."""
        scr = self.screen
        scr.canvas.fill((255, 255, 255))
        try:
            # Relative to the buffer, which shrinks with display.setScale(n).
            font = pygame.font.Font(None, max(12, scr.height // 7))
            text = font.render("juego incompleto", True, (0, 0, 0))
            scr.canvas.blit(text, text.get_rect(
                center=(scr.width // 2, scr.height // 2)))
        except Exception:  # noqa: BLE001  (no font available: leave it blank)
            pass

    def _key(self, key, down):
        if key == pygame.K_ESCAPE:
            self.running = False
            return
        if key in CRANK_KEYS:
            self._crank_dir = CRANK_KEYS[key] if down else 0
            return
        name = KEYMAP.get(key)
        if not name:
            return
        if down:
            self._button._press(name)
        else:
            self._button._release(name)
