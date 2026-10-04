"""pd/geometry.py — `playdate.geometry` as PYTHON objects (userdata in Lua).

Why in Python and not Lua: CoreLibs does

    if type(rect) == "userdata" then x, y, width, height = rect.x, rect.y, ... end

to read a rect metrics. If our rect/point/size were Lua TABLES (which is the
natural thing, and how they were before in `pd/lib/geometry.lua`), that branch is
skipped, width/height stay nil and the game dies with
`attempt to compare string with number` inside CoreLibs/graphics.

On the console those objects are userdata. Exposing them as Python objects, lupa
delivers them with `type() == "userdata"` and the game code takes the right branch.
It is the SAME reason sounds are userdata (see pd/luaobj.py).
"""

from __future__ import annotations

import math

from .luaobj import Permissive


def _xy_of(a, b=None):
    """Accepts (x, y), a Point/Vector2D or a Lua table with x/y."""
    if b is not None:
        return float(a), float(b)
    if a is None:
        return 0.0, 0.0
    try:                                   # Lua table
        return float(a["x"]), float(a["y"])
    except Exception:  # noqa: BLE001
        return float(getattr(a, "x", 0)), float(getattr(a, "y", 0))


class Vector2D(Permissive):
    def __init__(self, x=0, y=0):
        self.x = float(x or 0)
        self.y = float(y or 0)

    def magnitude(self):
        return math.sqrt(self.x ** 2 + self.y ** 2)

    def magnitudeSquared(self):
        return self.x ** 2 + self.y ** 2

    def normalized(self):
        m = self.magnitude()
        return Vector2D(0, 0) if m == 0 else Vector2D(self.x / m, self.y / m)

    def normalize(self):
        m = self.magnitude()
        if m:
            self.x /= m
            self.y /= m
        return self

    def scaledBy(self, s):
        return Vector2D(self.x * s, self.y * s)

    def scale(self, s):
        self.x *= s
        self.y *= s
        return self

    def addVector(self, v):
        return Vector2D(self.x + v.x, self.y + v.y)

    def dotProduct(self, v):
        return self.x * v.x + self.y * v.y

    def angleBetween(self, v):
        m = self.magnitude() * v.magnitude()
        if m == 0:
            return 0.0
        c = max(-1.0, min(1.0, self.dotProduct(v) / m))
        return math.degrees(math.acos(c))

    def leftNormal(self):
        return Vector2D(self.y, -self.x)

    def rightNormal(self):
        return Vector2D(-self.y, self.x)

    def unpack(self):
        return (self.x, self.y)

    def copy(self):
        return Vector2D(self.x, self.y)


class Point(Permissive):
    def __init__(self, x=0, y=0):
        self.x = float(x or 0)
        self.y = float(y or 0)

    def offsetBy(self, dx, dy):
        self.x += dx
        self.y += dy
        return self

    def offset(self, dx, dy):
        return Point(self.x + dx, self.y + dy)

    def distanceToPoint(self, p):
        px, py = _xy_of(p)
        return math.sqrt((px - self.x) ** 2 + (py - self.y) ** 2)

    def squaredDistanceToPoint(self, p):
        px, py = _xy_of(p)
        return (px - self.x) ** 2 + (py - self.y) ** 2

    def unpack(self):
        return (self.x, self.y)

    def copy(self):
        return Point(self.x, self.y)


class Size(Permissive):
    def __init__(self, w=0, h=0):
        self.width = float(w or 0)
        self.height = float(h or 0)

    def unpack(self):
        return (self.width, self.height)

    def copy(self):
        return Size(self.width, self.height)


class Rect(Permissive):
    def __init__(self, x=0, y=0, w=0, h=0):
        self.x = float(x or 0)
        self.y = float(y or 0)
        self.width = float(w or 0)
        self.height = float(h or 0)

    def offsetBy(self, dx, dy):
        self.x += dx
        self.y += dy
        return self

    def offset(self, dx, dy):
        # The API MOVES THE RECT IN PLACE (does not return a new one): the game
        # does `childRects[i]:offset(x, y)` and assumes the rect is already moved.
        # Returning a new rect left ALL the menu children at (0,0) -- Smolitaire
        # items piled up in the corner and were not visible.
        self.x += dx
        self.y += dy
        return self

    def insetBy(self, dx, dy):
        self.x += dx
        self.y += dy
        self.width -= 2 * dx
        self.height -= 2 * dy
        return self

    def inset(self, dx, dy):
        return self.insetBy(dx, dy)

    def moveTo(self, x, y):
        self.x = float(x or 0)
        self.y = float(y or 0)
        return self

    def moveBy(self, dx, dy):
        return self.offsetBy(dx, dy)

    def getCenter(self):
        return Point(self.x + self.width / 2.0, self.y + self.height / 2.0)

    def center(self):
        return self.getCenter()

    def containsPoint(self, a, b=None):
        px, py = _xy_of(a, b)
        return (self.x <= px <= self.x + self.width
                and self.y <= py <= self.y + self.height)

    def intersects(self, r2):
        return not (self.x + self.width <= r2.x or r2.x + r2.width <= self.x
                    or self.y + self.height <= r2.y or r2.y + r2.height <= self.y)

    def intersection(self, r2):
        x1, y1 = max(self.x, r2.x), max(self.y, r2.y)
        x2 = min(self.x + self.width, r2.x + r2.width)
        y2 = min(self.y + self.height, r2.y + r2.height)
        if x2 <= x1 or y2 <= y1:
            return None
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def union(self, r2):
        x1, y1 = min(self.x, r2.x), min(self.y, r2.y)
        x2 = max(self.x + self.width, r2.x + r2.width)
        y2 = max(self.y + self.height, r2.y + r2.height)
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def centerPoint(self):
        return Point(self.x + self.width / 2, self.y + self.height / 2)

    def isEmpty(self):
        return self.width <= 0 or self.height <= 0

    def isEqual(self, r2):
        return (self.x == r2.x and self.y == r2.y
                and self.width == r2.width and self.height == r2.height)

    def unpack(self):
        return (self.x, self.y, self.width, self.height)

    def copy(self):
        return Rect(self.x, self.y, self.width, self.height)


class LineSegment(Permissive):
    def __init__(self, x1=0, y1=0, x2=0, y2=0):
        self.x1, self.y1, self.x2, self.y2 = float(x1 or 0), float(y1 or 0), float(x2 or 0), float(y2 or 0)

    def length(self):
        return math.sqrt((self.x2 - self.x1) ** 2 + (self.y2 - self.y1) ** 2)

    def midPoint(self):
        return Point((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    def unpack(self):
        return (self.x1, self.y1, self.x2, self.y2)

    def copy(self):
        return LineSegment(self.x1, self.y1, self.x2, self.y2)


class AffineTransform(Permissive):
    """playdate.geometry.affineTransform: 2D affine matrix.

        | m11 m12 tx |
        | m21 m22 ty |

    `rotate`/`scale`/`translate` MUTATE the matrix (as the Asheteroids example
    uses it: create one, rotate, scale, and multiply the polygon). `*` between
    two transforms COMPOSES. It is a Python object (userdata) so that
    `type(t) == "userdata"` and so lupa maps `__mul__` as a metamethod.
    """

    def __init__(self, m11=1.0, m12=0.0, m21=0.0, m22=1.0, tx=0.0, ty=0.0):
        self.m11, self.m12, self.m21, self.m22 = float(m11), float(m12), float(m21), float(m22)
        self.tx, self.ty = float(tx), float(ty)

    # --- query ---
    def transformPoint(self, x, y=None):
        if y is None:
            x, y = _xy_of(x)
        return Point(self.m11 * x + self.m21 * y + self.tx,
                     self.m12 * x + self.m22 * y + self.ty)

    def copy(self):
        return AffineTransform(self.m11, self.m12, self.m21, self.m22, self.tx, self.ty)

    # --- mutators (return self, like the API) ---
    def reset(self):
        self.m11, self.m12, self.m21, self.m22 = 1.0, 0.0, 0.0, 1.0
        self.tx = self.ty = 0.0
        return self

    def translate(self, dx=0, dy=0):
        self.tx += dx
        self.ty += dy
        return self

    def translatedBy(self, dx=0, dy=0):
        return self.copy().translate(dx, dy)

    def scale(self, sx=1, sy=None):
        if sy is None:
            sy = sx
        self.m11 *= sx
        self.m12 *= sx
        self.m21 *= sy
        self.m22 *= sy
        return self

    def scaledBy(self, sx=1, sy=None):
        return self.copy().scale(sx, sy)

    def rotate(self, angle=0, x=None, y=None):
        a = math.radians(angle or 0)
        c, s_ = math.cos(a), math.sin(a)
        # rotate around (x, y) if given
        if x is not None and y is not None:
            self.translate(-x, -y)
        m11, m12, m21, m22 = self.m11, self.m12, self.m21, self.m22
        self.m11 = c * m11 + s_ * m21
        self.m12 = c * m12 + s_ * m22
        self.m21 = -s_ * m11 + c * m21
        self.m22 = -s_ * m12 + c * m22
        if x is not None and y is not None:
            self.translate(x, y)
        return self

    def rotatedBy(self, angle=0, x=None, y=None):
        return self.copy().rotate(angle, x, y)

    def skew(self, sx=0, sy=0):
        self.m21 += math.tan(math.radians(sx or 0)) * self.m11
        self.m12 += math.tan(math.radians(sy or 0)) * self.m22
        return self

    def concat(self, other):
        """self = self * other (applies `other` first, like the API)."""
        o = other
        m11 = self.m11 * o.m11 + self.m12 * o.m21
        m12 = self.m11 * o.m12 + self.m12 * o.m22
        m21 = self.m21 * o.m11 + self.m22 * o.m21
        m22 = self.m21 * o.m12 + self.m22 * o.m22
        tx = self.tx * o.m11 + self.ty * o.m21 + o.tx
        ty = self.tx * o.m12 + self.ty * o.m22 + o.ty
        self.m11, self.m12, self.m21, self.m22, self.tx, self.ty = m11, m12, m21, m22, tx, ty
        return self

    def invert(self):
        det = self.m11 * self.m22 - self.m12 * self.m21
        if det == 0:
            return self
        i11 = self.m22 / det
        i12 = -self.m12 / det
        i21 = -self.m21 / det
        i22 = self.m11 / det
        itx = -(i11 * self.tx + i21 * self.ty)
        ity = -(i12 * self.tx + i22 * self.ty)
        self.m11, self.m12, self.m21, self.m22, self.tx, self.ty = i11, i12, i21, i22, itx, ity
        return self

    def __mul__(self, other):
        # transform * transform -> new composition
        return self.copy().concat(other)


class Polygon(Permissive):
    def __init__(self, points=None):
        self.points = list(points or [])

    def count(self):
        return len(self.points) // 2

    def unpack(self):
        return tuple(self.points)

    def copy(self):
        return Polygon(self.points)

    def setPointAt(self, i, x=None, y=None):
        if y is None:
            x, y = _xy_of(x)
        idx = (int(i) - 1) * 2
        if 0 <= idx < len(self.points) - 1:
            self.points[idx], self.points[idx + 1] = x, y
        return self

    def getPointAt(self, i):
        idx = (int(i) - 1) * 2
        if 0 <= idx < len(self.points) - 1:
            return Point(self.points[idx], self.points[idx + 1])
        return Point(0, 0)

    def translate(self, dx=0, dy=0):
        self.points = [v + (dx if i % 2 == 0 else dy)
                       for i, v in enumerate(self.points)]
        return self

    def getBounds(self):
        """(x, y, w, h) of the box that wraps the polygon. Used by Asheteroids."""
        if not self.points:
            return (0.0, 0.0, 0.0, 0.0)
        xs = self.points[0::2]
        ys = self.points[1::2]
        x0, y0 = min(xs), min(ys)
        return (float(x0), float(y0), float(max(xs) - x0), float(max(ys) - y0))

    def getBoundsRect(self):
        x, y, w, h = self.getBounds()
        return Rect(x, y, w, h)

    def __mul__(self, t):
        """polygon * affineTransform -> transformed polygon (__mul metamethod)."""
        if isinstance(t, AffineTransform):
            pts = []
            for i in range(0, len(self.points) - 1, 2):
                x, y = self.points[i], self.points[i + 1]
                pts.append(t.m11 * x + t.m21 * y + t.tx)
                pts.append(t.m12 * x + t.m22 * y + t.ty)
            return Polygon(pts)
        return NotImplemented


# ----------------------------------------------------------------------
def build_geometry(table_from):
    """Builds `playdate.geometry` with factories that return Python userdata."""
    def _v2(x=0, y=0):
        return Vector2D(x, y)

    def _v2_polar(angle=0, radius=0):
        r = math.radians(angle or 0)
        return Vector2D((radius or 0) * math.sin(r), -(radius or 0) * math.cos(r))

    def _point(x=0, y=0):
        return Point(x, y)

    def _size(w=0, h=0):
        return Size(w, h)

    def _rect(x=0, y=0, w=0, h=0):
        return Rect(x, y, w, h)

    def _line(x1=0, y1=0, x2=0, y2=0):
        return LineSegment(x1, y1, x2, y2)

    def _poly(*pts):
        return Polygon(pts)

    def _affine(m11=1.0, m12=0.0, m21=0.0, m22=1.0, tx=0.0, ty=0.0):
        return AffineTransform(m11, m12, m21, m22, tx, ty)

    def _distance(x1, y1, x2, y2):
        return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)

    def _sq_distance(x1, y1, x2, y2):
        return (x2 - x1) ** 2 + (y2 - y1) ** 2

    return table_from({
        "vector2D": table_from({"new": _v2, "newPolar": _v2_polar}),
        "point": table_from({"new": _point}),
        "size": table_from({"new": _size}),
        "rect": table_from({"new": _rect}),
        "lineSegment": table_from({"new": _line}),
        "polygon": table_from({"new": _poly}),
        "affineTransform": table_from({"new": _affine}),
        "distanceToPoint": _distance,
        "squaredDistanceToPoint": _sq_distance,
        "kUnflipped": 0, "kFlippedX": 1, "kFlippedY": 2, "kFlippedXY": 3,
    })
