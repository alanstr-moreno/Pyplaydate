"""playdate.button — estado de botones con detección de borde.

Mapeo de teclado (para dev en Mac / Pi con teclado):
  flechas o WASD  -> D-pad
  X o Espacio     -> A
  Z               -> B
Un gamepad se agrega luego en emulator._poll_gamepad().
"""

# Nombres válidos de botón en Playdate.
BUTTONS = ("A", "B", "Up", "Down", "Left", "Right")


class Button:
    def __init__(self):
        self._down = set()   # presionados este frame (held)
        self._prev = set()   # presionados el frame anterior

    def _press(self, name):
        self._down.add(name)

    def _release(self, name):
        self._down.discard(name)

    def end_frame(self):
        self._prev = set(self._down)

    # --- API Playdate ---------------------------------------------------
    def isPressed(self, name):
        return name in self._down

    def wasPressed(self, name):
        # borde: presionado ahora y no en el frame anterior
        return name in self._down and name not in self._prev

    def up(self, name):
        # se soltó este frame
        return name in self._prev and name not in self._down

    def isPressedAny(self):
        return bool(self._down)

    def wasPressedAny(self):
        return bool(self._down - self._prev)
