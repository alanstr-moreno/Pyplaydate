"""image.py — `playdate.graphics.image` / `imagetable` objects (Python side).

A compiled game references images by NAME without an extension
(e.g. `image.new("images/title")`), and the real asset is `images/title.pdi`.
This is the object that holds the already-decoded Surface; the Lua "handles"
(the ones the game sees) are built in runtime.py with the `__id` pattern.
"""

import os

from .pdi import decode_pdi, decode_pdt

ASSET_EXTS = (".pdi", ".pdt", ".png", ".gif", "")


def resolve_asset(asset_dir, name):
    """Finds the real file of an asset by name, with or without an extension.

    The API IGNORES the extension: a real game asks `image.new("images/background.png")`
    and the asset is `images/background.pdi`. If you only try `name+ext` the
    lookup fails, `image.new` returns nil and the game dies indexing it. Any
    known extension is stripped first, then each format is tried.
    """
    name = str(name).lstrip("/")
    if not asset_dir:
        return None
    candidatos = [name]
    base, ext = os.path.splitext(name)
    if ext.lower() in (".png", ".pdi", ".pdt", ".psd", ".gif", ".jpg", ".jpeg", ".lua", ".pft", ".fnt"):
        candidatos.insert(0, base)          # the name without extension, first
    for cand in candidatos:
        for e in ASSET_EXTS:
            p = os.path.join(asset_dir, cand + e)
            if os.path.isfile(p):
                return p
        # also the name as-is (assets without an extension)
        p = os.path.join(asset_dir, cand)
        if os.path.isfile(p):
            return p
    return None


class PDImage:
    """Wraps a pygame.Surface. `surface` is the decoded bitmap."""

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
    """spritesheet: list of PDImage + cells per row (for getImage(x,y))."""

    def __init__(self, images, cells_per_row):
        self.images = images
        self.per_row = cells_per_row

    def getLength(self):
        return len(self.images)

    def getImage(self, a, b=None):
        if b is None:
            i = int(a) - 1                     # sequential, 1-based
            return self.images[i] if 0 <= i < len(self.images) else None
        x, y = int(a), int(b)                  # matrix, 0-based
        i = y * self.per_row + x
        return self.images[i] if 0 <= i < len(self.images) else None


def load_image(asset_dir, path):
    """image.new(path) -> PDImage (decodes .pdi/.pdt; supports .png fallback)."""
    real = resolve_asset(asset_dir, path)
    if real is None:
        raise FileNotFoundError(f"asset not found: {path!r} in {asset_dir!r}")
    with open(real, "rb") as f:
        data = f.read()
    return PDImage(decode_pdi(data))


def load_imagetable(asset_dir, path):
    """imagetable.new(path) -> PDImageTable."""
    real = resolve_asset(asset_dir, path)
    if real is None:
        raise FileNotFoundError(f"imagetable not found: {path!r} in {asset_dir!r}")
    with open(real, "rb") as f:
        data = f.read()
    cells, per_row = decode_pdt(data)
    return PDImageTable([PDImage(s) for s in cells], per_row)
