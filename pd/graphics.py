"""playdate.graphics — Python implementation of the drawing context.

State: ink color, draw-offset, clip, pushContext/popContext stack and
lockFocus (drawing onto an image). All drawing goes through `self._canvas`,
which respects lockFocus.

Images (.pdi/.pdt) are drawn here; the object holding the Surface lives in
pd/image.py and the Lua "handle" is built by runtime.py (`__id` pattern).
"""

import pygame

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)

# Sentinel: "this context does not hold the lock" (see pushContext/popContext).
_NO_LOCK = object()


class Graphics:
    def __init__(self, screen):
        self.screen = screen
        self.width = screen.width
        self.height = screen.height
        self.asset_dir = None
        self.deref = None                  # set by Runtime (handle -> Python object)
        self._font_size = 16
        self._fonts = {}
        # 0 = ink (black), 1 = paper (white) -- REAL values of
        # kColorBlack/kColorWhite from the SDK. The console default is ink.
        self._color = 0
        self._ox = 0                       # draw offset
        self._tx = self._ty = 0                # translate (sprites.lua uses it for the draw callback)
        self._dox = self._doy = 0               # display offset (playdate.display.setOffset)
        self._oy = 0
        self._lock = None                  # Surface set by lockFocus
        self._stack = []
        self._line_width = 1
        self._line_cap = 0
        self._stroke = 0
        self._font_handle = None
        self.font_factory = None           # set by Runtime (for the default getFont)

    # --- internals -------------------------------------------------------
    @property
    def _canvas(self):
        return self._lock if self._lock is not None else self.screen.canvas

    def _ink(self):
        # `_color`: 0 = ink (black), 1 = paper (white). Matches the real
        # kColorBlack/kColorWhite values of the SDK.
        return WHITE if self._color else BLACK

    def _mode_ink(self):
        """Effective ink for text/images under the current image draw mode.

        kDrawModeFillWhite (3) draws opaque pixels white, kDrawModeFillBlack (4)
        black and kDrawModeInverted (7) the opposite of the current color. A
        source game draws a black rect then `setImageDrawMode(kDrawModeFillWhite)`
        + drawText to get white text on it; ignoring the mode left the text
        invisible (black on black).
        """
        mode = getattr(self, "_image_draw_mode", 0)
        if mode == 3:      # kDrawModeFillWhite
            return WHITE
        if mode == 4:      # kDrawModeFillBlack
            return BLACK
        if mode == 7:      # kDrawModeInverted
            return WHITE if self._color else BLACK
        return self._ink()

    def _xy(self, x, y):
        return (int(x) + self._ox + self._tx + self._dox, int(y) + self._oy + self._ty + self._doy)

    def _get_font(self):
        size = self._font_size
        if size not in self._fonts:
            self._fonts[size] = pygame.font.Font(None, size)
        return self._fonts[size]

    # --- clearing / pixels --------------------------------------------
    def setBackgroundColor(self, color=None):
        """Color that clear() fills with. Real API (used at startup)."""
        self._bg_color = color

    def getBackgroundColor(self):
        return getattr(self, "_bg_color", None)

    def clear(self, color=0):
        self._canvas.fill(BLACK if color else WHITE)

    def setPixel(self, x, y, color=1):
        px, py = self._xy(x, y)
        if 0 <= px < self.width and 0 <= py < self.height:
            self._canvas.set_at((px, py), BLACK if color else WHITE)

    def getPixel(self, x, y):
        px, py = self._xy(x, y)
        if 0 <= px < self.width and 0 <= py < self.height:
            return 0 if self._canvas.get_at((px, py))[:3] == (255, 255, 255) else 1
        return 0

    # --- color ----------------------------------------------------------
    def setColor(self, color=None):
        """setColor(color): 0/kColorBlack -> ink, 1/kColorWhite -> paper.

        Accepts the THREE forms games use: the constant
        (`kColorBlack = 0`, `kColorWhite = 1`, REAL SDK values), the literal
        `0x000000`/`0xffffff` and nil (back to ink). Before it did
        `1 if color else 0`, which turned `setColor(0xffffff)` (WHITE) into
        BLACK: any game asking for white with the literal came out black.
        """
        if color is None:
            color = 0
        c = int(color)
        if c in (0xFFFFFF, 0xFFFFFFFF):
            self._color = 1          # white (paper)
        elif c == 0x000000:
            self._color = 0          # black (ink)
        else:
            self._color = 1 if c == 1 else 0

    def getColor(self):
        return self._color

    # --- draw offset / clip / context ---------------------------------
    def setDrawOffset(self, x, y):
        self._ox, self._oy = int(x), int(y)

    def getDrawOffset(self):
        return (self._ox, self._oy)

    # --- translate ------------------------------------------------------
    # sprites.lua really calls the sprite's `draw(s, x, y, w, h)` callback with
    # the TRANSLATE set to the sprite's corner: that's why the game can draw at
    # (0,0) and see its text at the sprite's position. Without translate,
    # FlippyFish's Score:draw painted the "0" in the canvas corner.
    def setTranslate(self, x, y):
        self._tx, self._ty = int(x), int(y)

    def getTranslate(self):
        return (self._tx, self._ty)

    def translate(self, dx, dy):
        self._tx += int(dx)
        self._ty += int(dy)

    def setDisplayOffset(self, x, y):
        self._dox, self._doy = int(x), int(y)

    def getDisplayOffset(self):
        return (self._dox, self._doy)

    def setClipRect(self, x=None, y=None, w=None, h=None):
        """setClipRect([x, y, w, h]) — with NO arguments, clears the clip.

        The API allows calling it with no arguments to reset the clip rect, and
        games do it IN THE MIDDLE of their draw callback. If it crashes here,
        the callback dies halfway: the game "runs without errors" but the menu
        and cursor never get drawn. That was exactly it.
        """
        if x is None or y is None or w is None or h is None:
            self.clearClipRect()
            return
        self._canvas.set_clip(pygame.Rect(*self._xy(x, y), int(w), int(h)))

    def clearClipRect(self):
        self._canvas.set_clip(None)

    def copyFrameBufferToImage(self, image=None):
        """playdate.graphics.copyFrameBufferToImage(image): copies the screen to
        that image.

        It is the API a game uses to capture the framebuffer (transitions,
        screen caches). It was missing, and the game calls it.
        """
        if image is None:
            return
        obj = self.deref(image) if self.deref else None
        surf = getattr(obj, "surface", None)
        if surf is not None:
            surf.blit(self._canvas, (0, 0))

    def pushContext(self, *args):
        """pushContext([image]): saves the state and, if given an image, starts
        drawing ONTO IT.

        CAREFUL with the lock: `pushContext()` WITHOUT a target must NOT
        save/restore `_lock`, because the lock is controlled by
        `lockFocus`/`unlockFocus` separately. Before it always saved and
        `popContext` restored it: CoreLibs does dozens of internal push/pop on
        import and one of them OVERWROTE the lock the game had already set.
        Without the lock, the game scene was drawn to the framebuffer, its
        screen buffer stayed empty and its blit erased everything -> invisible
        intro and menu, blank screen.
        """
        target = args[0] if args else None
        self._stack.append((self._color, self._ox, self._oy,
                            self._lock if target is not None else _NO_LOCK,
                            getattr(self, "_dither", 0.0), (self._tx, self._ty)))
        if target is not None and self.deref is not None:
            obj = self.deref(target)
            surf = getattr(obj, "surface", None)
            if surf is not None:
                self._lock = surf
                self._ox = self._oy = 0     # a new context starts with no offset
                self._canvas.set_clip(None)

    def popContext(self):
        if self._stack:
            color, ox, oy, lock, dither, (tx, ty) = self._stack.pop()
            self._color, self._ox, self._oy = color, ox, oy
            self._dither = dither
            self._tx, self._ty = tx, ty
            if lock is not _NO_LOCK:
                self._lock = lock           # only if the push had a target
            if self._lock is None:
                self.screen.canvas.set_clip(None)

    def lockFocus(self, img=None):
        if img is not None and self.deref:
            obj = self.deref(img)
            self._lock = obj.surface if obj is not None else None
        else:
            self._lock = None

    def unlockFocus(self):
        self._lock = None

    def getWorkingImage(self):
        return None

    def setDitherPattern(self, pattern=0, x=None, y=None):
        # no-op: ordered dithering was tried and reverted (documented in screen.py)
        pass

    def setImageDrawMode(self, mode=0):
        self._image_draw_mode = int(mode) if isinstance(mode, (int, float)) else 0

    def getImageDrawMode(self, *a):
        return getattr(self, "_image_draw_mode", 0)

    def setPattern(self, *args):
        pass                                # no-op

    def setStencilImage(self, *args):
        pass

    def setStencilPattern(self, *args):
        pass

    def clearStencil(self):
        pass

    def setLineWidth(self, w):
        self._line_width = max(1, int(w))

    def getLineWidth(self):
        return self._line_width

    def setLineCapStyle(self, style):
        self._line_cap = int(style)

    def getLineCapStyle(self):
        return self._line_cap

    def setStrokeLocation(self, loc):
        self._stroke = int(loc)

    def getStrokeLocation(self):
        return self._stroke

    # --- primitives -----------------------------------------------------
    def fillRect(self, x, y, w, h):
        pygame.draw.rect(self._canvas, self._ink(), (*self._xy(x, y), int(w), int(h)))

    def drawRect(self, x, y, w, h):
        pygame.draw.rect(self._canvas, self._ink(), (*self._xy(x, y), int(w), int(h)), self._line_width)

    def fillRoundRect(self, x, y, w, h, radius):
        pygame.draw.rect(self._canvas, self._ink(), (*self._xy(x, y), int(w), int(h)),
                         border_radius=int(radius))

    def drawRoundRect(self, x, y, w, h, radius):
        pygame.draw.rect(self._canvas, self._ink(), (*self._xy(x, y), int(w), int(h)), 1,
                         border_radius=int(radius))

    def drawLine(self, x1, y1, x2, y2):
        pygame.draw.line(self._canvas, self._ink(), self._xy(x1, y1), self._xy(x2, y2),
                         self._line_width)

    def fillCircleInRect(self, x, y, w, h):
        pygame.draw.ellipse(self._canvas, self._ink(), (*self._xy(x, y), int(w), int(h)))

    def drawCircleInRect(self, x, y, w, h):
        pygame.draw.ellipse(self._canvas, self._ink(), (*self._xy(x, y), int(w), int(h)), self._line_width)

    def fillEllipseInRect(self, x, y, w, h, *a):
        pygame.draw.ellipse(self._canvas, self._ink(), (*self._xy(x, y), int(w), int(h)))

    def drawEllipseInRect(self, x, y, w, h, *a):
        pygame.draw.ellipse(self._canvas, self._ink(), (*self._xy(x, y), int(w), int(h)), self._line_width)

    def fillCircleAtPoint(self, x, y, radius):
        pygame.draw.circle(self._canvas, self._ink(), self._xy(x, y), int(radius))

    def drawCircleAtPoint(self, x, y, radius):
        pygame.draw.circle(self._canvas, self._ink(), self._xy(x, y), int(radius), self._line_width)

    # --- text ----------------------------------------------------------
    def setFont(self, name=None, size=None):
        if isinstance(name, (int, float)) and size is None:
            self._font_size = int(name)
        elif name is not None and size is None:
            self._font_handle = name       # font handle (from font.new/getSystemFont)
        elif size:
            self._font_size = int(size)

    def getFont(self, *a):
        if self._font_handle is None and self.font_factory is not None:
            self._font_handle = self.font_factory()
        return self._font_handle

    def getTextWidth(self, text):
        return self._get_font().size(str(text))[0]

    def getTextSize(self, text, *a):
        """Width/height of the text with the CURRENT font.

        NOTE: if the game did setFont(font.new(".pft")), you must measure with
        THAT font. Measuring with the pygame font returned half widths (48
        instead of 95 for "Klondike"), and CoreLibs uses this function to size
        text rects: Smolitaire's menu items came out truncated in half and the
        UI boxes were mis-measured.
        """
        t = str(text)
        pft = self._pft_font()
        if pft is not None:
            try:
                w, h = pft.getTextSize(t)
                return (w, h)
            except Exception:
                pass
        return (self._get_font().size(t)[0], self._font_size)

    def _pft_font(self):
        """The current .pft font, if the game did setFont(font.new(...))."""
        h = self._font_handle
        if h is None or self.deref is None:
            return None
        try:
            obj = self.deref(h)
        except Exception:
            return None
        return obj if obj is not None and hasattr(obj, "getGlyph") else None

    def _draw_pft(self, text, x, y):
        """Draws with the .pft font (the game's real one).

        The glyphs are TINTED with the current color using their mask: the .pft
        bitmap carries the ink in a fixed color, so blitting it as-is would
        always draw the same color -- and on a black background white letters
        would not show. Before, the default pygame font was used and the text
        came out in another typeface (overlapping/illegible).
        """
        font = self._pft_font()
        if font is None:
            return False
        ink = self._mode_ink()
        cx, cy = self._xy(x, y)
        track = 0
        try:
            track = int(font.getTracking())
        except Exception:
            track = 0
        for ch in str(text):
            g = font.getGlyph(ord(ch))
            if g is None:
                continue
            s = getattr(g, "surface", None)
            if s is not None:
                try:
                    m = pygame.mask.from_surface(s)
                    self._canvas.blit(
                        m.to_surface(setcolor=(ink[0], ink[1], ink[2], 255),
                                     unsetcolor=(0, 0, 0, 0)),
                        (cx, cy))
                except Exception:
                    self._canvas.blit(s, (cx, cy))
            cx += int(getattr(g, "advance", 0) or 0) + track
        return True

    def drawText(self, text, x, y, *a):
        if self._draw_pft(text, x, y):
            return
        surf = self._get_font().render(str(text), True, self._mode_ink())
        self._canvas.blit(surf, self._xy(x, y))

    def drawTextAligned(self, text, x, y, align=0, *a):
        """drawTextAligned(text, x, y, alignment): the text is aligned ON x.

        With kTextAlignment.center, x is the CENTER (and with right, the right
        edge). Before, `alignment` was ignored and it always drew from x, which
        left the text shifted half a width to the right: that's why the game
        title came out cut and off-center vs the official emulator.

        Values per CoreLibs: left=0, right=1, center=2.
        """
        try:
            al = int(align or 0)
        except (TypeError, ValueError):
            al = 0
        w = self.getTextWidth(str(text))
        if al == 1:              # right: x is the right edge
            x = x - w
        elif al == 2:            # center: x is the center
            x = x - w // 2
        self.drawText(text, x, y)

    def drawCenteredText(self, text, y):
        w = self.getTextWidth(text)
        self.drawText(text, (self.width - w) // 2, y)

    # --- images -------------------------------------------------------
    def _image_surface(self, handle):
        if self.deref is None:
            return None
        obj = self.deref(handle)
        return obj.surface if obj is not None else None

    def _poly_pts(self, *args):
        """Normalizes drawPolygon/fillPolygon arguments to [(x,y), ...].

        The API accepts BOTH forms:
          fillPolygon(x1, y1, x2, y2, ...)        -> flat coordinates
          drawPolygon(polygon)                    -> polygon object / point list
        A real game (Kickflip Coast) uses both, and without this it crashes with
        "field 'fillPolygon' is not callable".
        """
        pts = []
        # form 1: a single object/point list
        if len(args) == 1:
            first = args[0]
            try:
                if hasattr(first, "getPoints"):
                    first = first.getPoints()
            except Exception:
                pass
            try:
                if hasattr(first, "__iter__") and not isinstance(first, (str, bytes)):
                    for item in first:
                        try:
                            x, y = item
                        except Exception:
                            x, y = getattr(item, "x", 0), getattr(item, "y", 0)
                        pts.append(self._xy(x, y))
                    if pts:
                        return pts
            except Exception:
                pass
        # form 2: flat coordinates
        vals = []
        for a in args:
            try:
                vals.append(float(a))
            except Exception:
                return []
        for i in range(0, len(vals) - 1, 2):
            pts.append(self._xy(vals[i], vals[i + 1]))
        return pts

    def fillTriangle(self, x1=None, y1=None, x2=None, y2=None, x3=None, y3=None, *a):
        """fillTriangle(x1,y1,x2,y2,x3,y3): filled triangle (particles)."""
        try:
            pts = [self._xy(x1, y1), self._xy(x2, y2), self._xy(x3, y3)]
            pygame.draw.polygon(self._canvas, self._ink(), pts, 0)
        except Exception:
            pass

    def drawTriangle(self, x1=None, y1=None, x2=None, y2=None, x3=None, y3=None, *a):
        try:
            pts = [self._xy(x1, y1), self._xy(x2, y2), self._xy(x3, y3)]
            pygame.draw.polygon(self._canvas, self._ink(), pts,
                                max(1, int(self._line_width)))
        except Exception:
            pass

    def drawPolygon(self, *args):
        pts = self._poly_pts(*args)
        if len(pts) >= 2:
            try:
                pygame.draw.polygon(self._canvas, self._ink(), pts,
                                    max(1, int(self._line_width)))
            except Exception:
                pass

    def fillPolygon(self, *args):
        pts = self._poly_pts(*args)
        if len(pts) >= 3:
            try:
                pygame.draw.polygon(self._canvas, self._ink(), pts, 0)
            except Exception:
                pass

    def image_draw(self, handle, x, y, flip=None, *a):
        surf = self._image_surface(handle)
        if surf is None:
            return
        if flip:
            surf = pygame.transform.flip(surf, True, bool(flip in (2, 3)))
        self._canvas.blit(surf, self._xy(x, y))

    def image_draw_inkfill(self, handle, x, y, *a):
        """Draws ALL the image's opaque pixels in INK (dark).

        For "light-filled" images (a hand cursor that is almost all white) that
        would be invisible on a light background. Builds a shape using the
        image's opacity mask.
        """
        surf = self._image_surface(handle)
        if surf is None:
            return
        w, h = surf.get_size()
        # solid surface of the ink color, clipped with the shape
        import pygame as _pg
        shape = _pg.Surface((w, h), _pg.SRCALPHA)
        shape.fill((0, 0, 0, 0))
        for px in range(w):
            for py in range(h):
                if surf.get_at((px, py))[3] > 0:
                    shape.set_at((px, py), self._ink() + (255,))
        self._canvas.blit(shape, self._xy(x, y))

    def drawImage(self, handle, x, y, flip=None, *a):
        self.image_draw(handle, x, y, flip)

    def image_draw_centered(self, handle, x, y, flip=None):
        surf = self._image_surface(handle)
        if surf is None:
            return
        self.image_draw(handle, int(x) - surf.get_width() // 2,
                        int(y) - surf.get_height() // 2, flip)

    def image_draw_anchored(self, handle, x, y, ax=0.0, ay=0.0):
        """Draws the image anchored: the point (ax*width, ay*height) lands at (x, y).

        CoreLibs exposes it as `img:drawAnchored(x, y, ax, ay)`; a game uses it
        to center (ax=0.5) or align to the top (ay=0) without computing the size.
        """
        surf = self._image_surface(handle)
        if surf is None:
            return
        self.image_draw(handle,
                        int(x) - int(surf.get_width() * (ax or 0)),
                        int(y) - int(surf.get_height() * (ay or 0)))

    def image_draw_faded(self, handle, x, y, alpha=1.0, flip=None):
        """drawFaded: on a 1-bit screen there is no translucency, there is DITHER.

        Piecewise approximation (no dithering engine): >=0.66 solid,
        0.33-0.66 checkerboard at 50%, <0.33 checkerboard at 25%. That is what
        the hardware does conceptually; real dithering would give finer mid-tones.
        """
        surf = self._image_surface(handle)
        if surf is None:
            return
        try:
            a = float(alpha)
        except Exception:  # noqa: BLE001
            a = 1.0
        if a <= 0.0:
            return
        if a >= 0.66:
            self.image_draw(handle, x, y, flip)
            return
        step = 2 if a >= 0.33 else 4
        faded = surf.copy()
        for yy in range(faded.get_height()):
            for xx in range(yy % step, faded.get_width(), step):
                if faded.get_at((xx, yy))[3] > 0:
                    faded.set_at((xx, yy), (0, 0, 0, 0))
        self._canvas.blit(faded, self._xy(x, y))

    def image_draw_scaled(self, handle, x, y, sx, sy=None):
        surf = self._image_surface(handle)
        if surf is None:
            return
        sy = sx if sy is None else sy
        s = pygame.transform.scale(surf, (max(1, int(surf.get_width() * sx)),
                                          max(1, int(surf.get_height() * sy))))
        self._canvas.blit(s, self._xy(x, y))

    def image_draw_rotated(self, handle, x, y, angle, scale=1, yscale=None):
        surf = self._image_surface(handle)
        if surf is None:
            return
        s = pygame.transform.rotozoom(surf, angle, scale)
        if yscale is not None:
            s = pygame.transform.scale(s, (s.get_width(), int(s.get_height() * yscale)))
        self._canvas.blit(s, self._xy(x, y))

    def getGraphics(self):
        return self
