"""emulator.py — inicializa pygame, carga el juego y corre el loop a 30 FPS."""

import os
import sys
import time

import pygame

from . import screen as screen_mod
from .runtime import Runtime

# tecla pygame -> boton Playdate
#   D-pad: flechas o WASD          A: X o ESPACIO          B: Z
#   Manivela: Q / E (girar)        Salir: ESC
KEYMAP = {
    pygame.K_UP: "Up", pygame.K_DOWN: "Down",
    pygame.K_LEFT: "Left", pygame.K_RIGHT: "Right",
    pygame.K_w: "Up", pygame.K_s: "Down",
    pygame.K_a: "Left", pygame.K_d: "Right",
    pygame.K_x: "A", pygame.K_SPACE: "A",
    pygame.K_z: "B",
    pygame.K_LSHIFT: "B",              # zurdo: shift tambien es B
}

# La manivela no es una tecla digital: girar 22.5 grados por frame mientras se
# mantiene. Q = antihorario, E = horario.
CRANK_STEP = 22.5
CRANK_KEYS = {pygame.K_q: -1, pygame.K_e: 1}

CONTROLS = """\
  flechas / WASD ......... D-pad
  X o ESPACIO ........... A (boton derecho)
  Z o SHIFT ............. B (boton izquierdo)
  Q / E ................. manivela (antihorario / horario)
  ESC ................... salir
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
        self.crank_angle = 0.0        # grados, fuente de verdad de la manivela
        self._crank_dir = 0           # -1 / +1 mientras se mantiene Q o E

    def _resolve_game_dir(self):
        # ruta directa: carpeta fuente, bundle .pdx o archivo .pdz
        if os.path.exists(self.game_name):
            return os.path.abspath(self.game_name)
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidate = os.path.join(here, "games", self.game_name)
        if os.path.isdir(candidate):
            return candidate
        raise SystemExit(f"No encuentro el juego '{self.game_name}'")

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

            # La manivela gira mientras se mantiene la tecla; el delta se avisa
            # al juego una vez por frame, como hace la consola.
            crank_delta = self._crank_dir * CRANK_STEP
            if crank_delta:
                self.crank_angle += crank_delta

            # Los errores del juego NO deben cerrar el emulador: se imprimen (con
            # el frame) y se sigue dibujando la ultima pantalla. En la consola un
            # error de script te devuelve al menu, nunca te apaga la ventana.
            try:
                runtime.call_update()
                runtime.call_draw()
            except Exception as e:  # noqa: BLE001
                print(f"[frame {self.frame}] error de juego: {type(e).__name__}: {str(e)[:120]}")
            if crank_delta:
                try:
                    runtime.call_crank(crank_delta, 0)
                except Exception as e:  # noqa: BLE001
                    print(f"[frame {self.frame}] error de crank: {str(e)[:120]}")
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
