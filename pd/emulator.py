"""emulator.py — initializes pygame, loads the game and runs the 30 FPS loop."""

import os
import sys
import time

import pygame

from . import screen as screen_mod
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

CONTROLS = """\
  arrows / WASD ......... D-pad
  X or SPACE ........... A (right button)
  Z or SHIFT ........... B (left button)
  Q / E ................. crank (counter-clockwise / clockwise)
  ESC ................... quit
"""


class Emulator:
    def __init__(self, game, scale=2, fps=30, headless=False, palette="device",
                 verbose=False):
        self.game_name = game
        self.scale = scale
        self.fps = fps
        self.headless = headless
        self.frame = 0
        self.running = False
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
        clock = pygame.time.Clock()

        runtime = Runtime(self)
        runtime.verbose = bool(getattr(self, "verbose", False))
        self._runtime = runtime
        self._button = runtime.playdate.button
        runtime.load(self._resolve_game_dir())

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

            # Game errors must NOT close the emulator: they are printed (with
            # the frame) and the last screen keeps being drawn. On the console a
            # script error returns you to the menu, it never shuts the window.
            try:
                runtime.call_update()
                runtime.call_draw()
            except Exception as e:  # noqa: BLE001
                print(f"[frame {self.frame}] game error: {type(e).__name__}: {str(e)[:120]}")
            if crank_delta:
                try:
                    runtime.call_crank(crank_delta, 0)
                except Exception as e:  # noqa: BLE001
                    print(f"[frame {self.frame}] crank error: {str(e)[:120]}")
            self.screen.render(win, self.scale)
            pygame.display.flip()

            self.frame += 1
            runtime.playdate.button.end_frame()
            clock.tick(self.fps)
            if max_frames and self.frame >= max_frames:
                self.running = False

        pygame.quit()

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
