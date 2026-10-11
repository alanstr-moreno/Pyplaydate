"""pd/screen.py — Playdate 400x240 framebuffer, rendered like the console.

Console facts (official SDK docs):
  - Screen **400 x 240**, 1 bit per pixel (Sharp Memory LCD, reflective, NO
    backlight). NOTE: it is NOT 320x320 -- many games do
    `fillRect(0, 0, 400, 240)` and with the wrong size everything is off.
  - Two colors, not grays: the real "black" and "white" are two grays
    (Simulator docs: "the display will use two gray colors instead of pure
    black and pure white, to match the Memory LCD display on the hardware").

Colors: taken from an asset of Panic's own Simulator (`pdx-quicklook.png`),
which gives ink `#4a463f` and the case `#ffb400` (Pantone 1235C). The LCD
"white" is a warm light gray.

How it works: Graphics keeps drawing on a normal RGB canvas (so the drawing
code does not change) and HERE, at present time, it is quantized to 2 colors
and colored with the palette. That reproduces the real look: hard 1-bit, no
antialiasing, with the LCD tint.
"""

from __future__ import annotations

import pygame

WIDTH = 400
HEIGHT = 240

# Screens/palettes. `device` mimics the hardware; `bw` is pure black/white (what
# Panic asks for Catalog captures); `yellow` tints the paper yellow.
PALETTES = {
    "device": {"ink": (0x4a, 0x46, 0x3f), "paper": (0xc8, 0xc5, 0xb8)},
    "bw": {"ink": (0x00, 0x00, 0x00), "paper": (0xff, 0xff, 0xff)},
    "yellow": {"ink": (0x1a, 0x16, 0x0e), "paper": (0xff, 0xd0, 0x4a)},
}

CASE_YELLOW = (0xff, 0xb4, 0x00)     # Pantone 1235C, from Panic asset
CASE_SHADOW = (0x2a, 0x27, 0x22)     # inner screen border
BEZEL = 14                           # case frame thickness (game px)


class Screen:
    """Drawing canvas (RGB) + 1-bit presentation with the console palette."""

    def __init__(self, palette="device", bezel=BEZEL):
        self.bezel = bezel
        self.inverted = False
        self.palette_name = palette if palette in PALETTES else "device"
        self.pixel_scale = 1
        self._alloc_canvas()

    def _alloc_canvas(self):
        """Allocates the framebuffer the game draws into.

        `playdate.display.setScale(n)` does NOT shrink the panel: it changes the
        buffer the game draws into to 400/n x 240/n, each buffer pixel being
        presented as n x n. The SDK spells it out for getWidth(): "if the scale
        is 2, the values returned will be based off of a 200 x 120-pixel screen
        rather than the native 400 x 240".

        Getting this wrong drawns everything into the top-left quadrant and the
        Mode7 plane comes out in the wrong place and size -- which is exactly
        what a game calling setScale(2) did.
        """
        s = max(1, int(self.pixel_scale) or 1)
        self.width = WIDTH // s
        self.height = HEIGHT // s
        self.canvas = pygame.Surface((self.width, self.height))
        self.canvas.fill((255, 255, 255))

    def setScale(self, n):
        """playdate.display.setScale(n): only 1, 2, 4 and 8 are accepted."""
        try:
            n = int(n)
        except (TypeError, ValueError):
            return
        if n in (1, 2, 4, 8) and n != self.pixel_scale:
            self.pixel_scale = n
            self._alloc_canvas()

    # --- state ---------------------------------------------------------
    @property
    def palette(self):
        return PALETTES[self.palette_name]

    def setInverted(self, flag):
        self.inverted = bool(flag)

    def getInverted(self):
        return self.inverted

    def setPalette(self, name):
        if name in PALETTES:
            self.palette_name = name

    def reset(self):
        self.canvas.fill((255, 255, 255))

    # --- presentation ---------------------------------------------------
    def to_1bit(self):
        """Quantizes the canvas to 2 colors and paints it with the LCD palette.

        Hard threshold (the stable version). Ordered dithering was tried and
        reverted: it made the game worse while the game state does not draw well yet.
        """
        ink = self.palette["ink"]
        paper = self.palette["paper"]
        if self.inverted:
            ink, paper = paper, ink
        mask = pygame.mask.from_threshold(
            self.canvas, (255, 255, 255), (127, 127, 127))
        return mask.to_surface(setcolor=paper, unsetcolor=ink)

    def render(self, display, scale=2):
        """Draws the case frame + the scaled screen (nearest)."""
        src = self.to_1bit()
        # The game drew into a 400/s x 240/s buffer; presenting it is stretching
        # that back to the native panel and then to the window. Integer factors
        # with nearest-neighbour is what gives the console's chunky look.
        frame = pygame.transform.scale(src, (WIDTH * scale, HEIGHT * scale))
        if display is None:
            return frame

        b = self.bezel * scale
        display.fill(CASE_YELLOW)
        # thin dark border around the panel, like the real bezel
        pygame.draw.rect(display, CASE_SHADOW,
                         (b - 2 * scale, b - 2 * scale,
                          self.width * scale + 4 * scale,
                          self.height * scale + 4 * scale),
                         border_radius=4 * scale)
        display.blit(frame, (b, b))
        return frame

    def window_size(self, scale=2):
        return ((self.width + 2 * self.bezel) * scale,
                (self.height + 2 * self.bezel) * scale)
