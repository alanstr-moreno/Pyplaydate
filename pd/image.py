"""image.py — objetos `playdate.graphics.image` / `imagetable` (lado Python).

Un juego compilado referencia imagenes por NOMBRE sin extension
(p.ej. `image.new("images/title")`), y el asset real es `images/title.pdi`.
Aqui esta el objeto que guarda la Surface ya decodificada; los "handles" Lua
(los que ve el juego) se construyen en runtime.py con el patron `__id`.
"""

import os

from .pdi import decode_pdi, decode_pdt

ASSET_EXTS = (".pdi", ".pdt", ".png", ".gif", "")


def resolve_asset(asset_dir, name):
    """Encuentra el archivo real de un asset por nombre, con o sin extension.

    La API IGNORA la extension: un juego real pide `image.new("images/background.png")`
    y el asset es `images/background.pdi`. Si solo se prueba `nombre+ext` la
    busqueda falla, `image.new` devuelve nil y el juego muere indexandolo. Se
    quita primero cualquier extension conocida y luego se prueba cada formato.
    """
    name = str(name).lstrip("/")
    if not asset_dir:
        return None
    candidatos = [name]
    base, ext = os.path.splitext(name)
    if ext.lower() in (".png", ".pdi", ".pdt", ".psd", ".gif", ".jpg", ".jpeg", ".lua", ".pft", ".fnt"):
        candidatos.insert(0, base)          # el nombre sin extension, primero
    for cand in candidatos:
        for e in ASSET_EXTS:
            p = os.path.join(asset_dir, cand + e)
            if os.path.isfile(p):
                return p
        # tambien el nombre tal cual (assets sin extension)
        p = os.path.join(asset_dir, cand)
        if os.path.isfile(p):
            return p
    return None


class PDImage:
    """Envuelve una pygame.Surface. `surface` es el bitmap decodificado."""

    def __init__(self, surface):
        self.surface = surface

    @property
    def width(self):
        return self.surface.get_width()

    @property
    def height(self):
        return self.surface.get_height()

    def getSize(self):
        return (self.width, self.height)


class PDImageTable:
    """spritesheet: lista de PDImage + celdas por fila (para getImage(x,y))."""

    def __init__(self, images, cells_per_row):
        self.images = images
        self.per_row = cells_per_row

    def getLength(self):
        return len(self.images)

    def getImage(self, a, b=None):
        if b is None:
            i = int(a) - 1                     # secuencial, 1-based
            return self.images[i] if 0 <= i < len(self.images) else None
        x, y = int(a), int(b)                  # matriz, 0-based
        i = y * self.per_row + x
        return self.images[i] if 0 <= i < len(self.images) else None


def load_image(asset_dir, path):
    """image.new(path) -> PDImage (decodifica .pdi/.pdt; soporta .png de fallback)."""
    real = resolve_asset(asset_dir, path)
    if real is None:
        raise FileNotFoundError(f"asset no encontrado: {path!r} en {asset_dir!r}")
    with open(real, "rb") as f:
        data = f.read()
    return PDImage(decode_pdi(data))


def load_imagetable(asset_dir, path):
    """imagetable.new(path) -> PDImageTable."""
    real = resolve_asset(asset_dir, path)
    if real is None:
        raise FileNotFoundError(f"imagetable no encontrada: {path!r} en {asset_dir!r}")
    with open(real, "rb") as f:
        data = f.read()
    cells, per_row = decode_pdt(data)
    return PDImageTable([PDImage(s) for s in cells], per_row)
