"""pd/screen.py — framebuffer 400x240 de Playdate, renderizado como la consola.

Datos de la consola (doc oficial del SDK):
  - Pantalla **400 x 240**, 1 bit por pixel (Sharp Memory LCD, reflectiva, SIN
    retroiluminacion). OJO: NO son 320x320 -- muchos juegos hacen
    `fillRect(0, 0, 400, 240)` y con el tamaño equivocado se les descuadra todo.
  - Dos colores, no grises: el "negro" y el "blanco" reales son dos grises
    (la doc del Simulator: "the display will use two gray colors instead of pure
    black and pure white, to match the Memory LCD display on the hardware").

Colores: sacados de un asset del propio Simulator de Panic
(`pdx-quicklook.png`), que da la tinta `#4a463f` y la carcasa `#ffb400`
(Pantone 1235C). El "blanco" del LCD es un gris calido claro.

Como funciona: Graphics sigue dibujando en un canvas RGB normal (asi el codigo de
dibujo no cambia) y AQUI, al presentar, se cuantiza a 2 colores y se colorea con
la paleta. Eso reproduce el look real: 1 bit duro, sin antialiasing, con el tinte
del LCD.
"""

from __future__ import annotations

import pygame

WIDTH = 400
HEIGHT = 240

# Pantallas/paletas. `device` imita el hardware; `bw` es negro/blanco puro (lo que
# pide Panic para capturas de la Catalog); `yellow` tinta el papel de amarillo.
PALETTES = {
    "device": {"ink": (0x4a, 0x46, 0x3f), "paper": (0xc8, 0xc5, 0xb8)},
    "bw": {"ink": (0x00, 0x00, 0x00), "paper": (0xff, 0xff, 0xff)},
    "yellow": {"ink": (0x1a, 0x16, 0x0e), "paper": (0xff, 0xd0, 0x4a)},
}

CASE_YELLOW = (0xff, 0xb4, 0x00)     # Pantone 1235C, del asset de Panic
CASE_SHADOW = (0x2a, 0x27, 0x22)     # borde interior de la pantalla
BEZEL = 14                           # grosor del marco de la carcasa (px de juego)


class Screen:
    """Canvas de dibujo (RGB) + presentacion 1-bit con la paleta de la consola."""

    def __init__(self, palette="device", bezel=BEZEL):
        self.width = WIDTH
        self.height = HEIGHT
        self.bezel = bezel
        self.inverted = False
        self.palette_name = palette if palette in PALETTES else "device"
        self.canvas = pygame.Surface((WIDTH, HEIGHT))
        self.canvas.fill((255, 255, 255))

    # --- estado ---------------------------------------------------------
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

    # --- presentacion ---------------------------------------------------
    def to_1bit(self):
        """Cuantiza el canvas a 2 colores y lo pinta con la paleta del LCD.

        Umbral duro (la version estable). El dither ordenado se probo y se revirtio:
        dejaba el juego peor cuando el estado del juego aun no dibuja bien.
        """
        ink = self.palette["ink"]
        paper = self.palette["paper"]
        if self.inverted:
            ink, paper = paper, ink
        mask = pygame.mask.from_threshold(
            self.canvas, (255, 255, 255), (127, 127, 127))
        return mask.to_surface(setcolor=paper, unsetcolor=ink)

    def render(self, display, scale=2):
        """Dibuja el marco de la carcasa + la pantalla escalada (nearest)."""
        src = self.to_1bit()
        # playdate.display.setScale(n): la consola dibuja a 400/n x 240/n y luego
        # cada pixel ocupa n x n -> look "chunky". Se reproduce bajando y subiendo
        # con nearest, que es exactamente eso.
        s = max(1, int(getattr(self, "pixel_scale", 1) or 1))
        if s > 1:
            small = (WIDTH // s, HEIGHT // s)
            src = pygame.transform.scale(
                pygame.transform.scale(src, small), (WIDTH, HEIGHT))
        frame = pygame.transform.scale(src, (self.width * scale, self.height * scale))
        if display is None:
            return frame

        b = self.bezel * scale
        display.fill(CASE_YELLOW)
        # borde oscuro fino alrededor del panel, como el bisel real
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
