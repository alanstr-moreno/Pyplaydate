"""errors.py — exceptions shared across the runtime.

`GameIncomplete` is raised when a game asks for a file that is not there: the
`import` of a script, an image or a sound the bundle never shipped.

On the console a broken game simply shows whatever it managed to draw (or
returns to the menu); it never takes the OS down. Here the same idea: the
exception is caught by the emulator, which STOPS the Lua loop and paints
"juego incompleto" on the Pygame screen, leaving the window open instead of
letting the Python process die with a traceback.
"""


class GameIncomplete(Exception):
    """A file the game asked for does not exist in the game folder."""

    def __init__(self, missing, kind="import"):
        super().__init__(f"{kind}: {missing!r} not found in the game folder")
        self.missing = missing
        self.kind = kind

    def label(self):
        """Text painted by the emulator on the error screen."""
        return "juego incompleto"
