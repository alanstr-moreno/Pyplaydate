"""playdate.button — button state with edge detection.

Keyboard mapping (for dev on Mac / Pi with a keyboard):
  arrows or WASD  -> D-pad
  X or Space      -> A
  Z               -> B
A gamepad is added later in emulator._poll_gamepad().
"""

# Valid Playdate button names.
BUTTONS = ("A", "B", "Up", "Down", "Left", "Right")


class Button:
    def __init__(self):
        self._down = set()   # pressed this frame (held)
        self._prev = set()   # pressed the previous frame

    def _press(self, name):
        self._down.add(name)

    def _release(self, name):
        self._down.discard(name)

    def end_frame(self):
        self._prev = set(self._down)

    # --- Playdate API ---------------------------------------------------
    def isPressed(self, name):
        return name in self._down

    def wasPressed(self, name):
        # edge: pressed now and not in the previous frame
        return name in self._down and name not in self._prev

    def up(self, name):
        # released this frame
        return name in self._prev and name not in self._down

    def isPressedAny(self):
        return bool(self._down)

    def wasPressedAny(self):
        return bool(self._down - self._prev)
