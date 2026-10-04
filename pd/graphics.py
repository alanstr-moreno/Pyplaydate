"""playdate.graphics — implementacion Python del contexto de dibujo.

Estado: color de tinta, draw-offset, clip, pila pushContext/popContext y
lockFocus (dibujar sobre una imagen). Todo dibujo pasa por `self._canvas`, que
respeta el lockFocus.

Las imagenes (.pdi/.pdt) se dibujan aqui; el objeto que guarda la Surface vive
en pd/image.py y el "handle" Lua lo construye runtime.py (patron `__id`).
"""

import pygame

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)

# Centinela: "este contexto no guarda el lock" (ver pushContext/popContext).
_NO_LOCK = object()


class Graphics:
    def __init__(self, screen):
        self.screen = screen
        self.width = screen.width
        self.height = screen.height
        self.asset_dir = None
        self.deref = None                  # lo setea Runtime (handle -> objeto Python)
        self._font_size = 16
        self._fonts = {}
        # 0 = tinta (negro), 1 = papel (blanco) -- valores REALES de
        # kColorBlack/kColorWhite del SDK. El default de la consola es tinta.
        self._color = 0
        self._ox = 0                       # draw offset
        self._tx = self._ty = 0                # translate (sprites.lua lo usa para el callback draw)
        self._dox = self._doy = 0               # display offset (playdate.display.setOffset)
        self._oy = 0
        self._lock = None                  # Surface fijada por lockFocus
        self._stack = []
        self._line_width = 1
        self._line_cap = 0
        self._stroke = 0
        self._font_handle = None
        self.font_factory = None           # lo setea Runtime (para getFont por defecto)

    # --- internos -------------------------------------------------------
    @property
    def _canvas(self):
        return self._lock if self._lock is not None else self.screen.canvas

    def _ink(self):
        # `_color`: 0 = tinta (negro), 1 = papel (blanco). Coincide con los
        # valores reales de kColorBlack/kColorWhite del SDK.
        return WHITE if self._color else BLACK

    def _xy(self, x, y):
        return (int(x) + self._ox + self._tx + self._dox, int(y) + self._oy + self._ty + self._doy)

    def _get_font(self):
        size = self._font_size
        if size not in self._fonts:
            self._fonts[size] = pygame.font.Font(None, size)
        return self._fonts[size]

    # --- limpieza / pixeles --------------------------------------------
    def setBackgroundColor(self, color=None):
        """Color con el que clear() rellena. API real (usada al arrancar)."""
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
        """setColor(color): 0/kColorBlack -> tinta, 1/kColorWhite -> papel.

        Acepta las TRES formas que usan los juegos: la constante
        (`kColorBlack = 0`, `kColorWhite = 1`, valores REALES del SDK), el
        literal `0x000000`/`0xffffff` y nil (vuelve a tinta). Antes hacia
        `1 if color else 0`, que convertia `setColor(0xffffff)` (BLANCO) en
        NEGRO: cualquier juego que pidiera blanco con el literal salia negro.
        """
        if color is None:
            color = 0
        c = int(color)
        if c in (0xFFFFFF, 0xFFFFFFFF):
            self._color = 1          # blanco (papel)
        elif c == 0x000000:
            self._color = 0          # negro (tinta)
        else:
            self._color = 1 if c == 1 else 0

    def getColor(self):
        return self._color

    # --- draw offset / clip / contexto ---------------------------------
    def setDrawOffset(self, x, y):
        self._ox, self._oy = int(x), int(y)

    def getDrawOffset(self):
        return (self._ox, self._oy)

    # --- translate ------------------------------------------------------
    # sprites.lua de verdad llama al callback `draw(s, x, y, w, h)` del sprite
    # con el TRANSLATE puesto en la esquina del sprite: por eso el juego puede
    # dibujar en (0,0) y ver su texto en la posicion del sprite. Sin translate,
    # Score:draw de FlippyFish pintaba el "0" en la esquina del canvas.
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
        """setClipRect([x, y, w, h]) — SIN argumentos, quita el recorte.

        La API permite llamarla sin argumentos para resetear el clip rect, y los
        juegos lo hacen EN MEDIO de su callback de dibujado. Si aqui revienta,
        el callback muere a medias: el juego "corre sin errores" pero el menu y
        el cursor nunca llegan a pintarse. Era exactamente eso.
        """
        if x is None or y is None or w is None or h is None:
            self.clearClipRect()
            return
        self._canvas.set_clip(pygame.Rect(*self._xy(x, y), int(w), int(h)))

    def clearClipRect(self):
        self._canvas.set_clip(None)

    def copyFrameBufferToImage(self, image=None):
        """playdate.graphics.copyFrameBufferToImage(image): Copia la pantalla a
        esa imagen.

        Es la API con la que un juego captura el framebuffer (transiciones,
        caches de pantalla). Faltaba, y el juego la llama.
        """
        if image is None:
            return
        obj = self.deref(image) if self.deref else None
        surf = getattr(obj, "surface", None)
        if surf is not None:
            surf.blit(self._canvas, (0, 0))

    def pushContext(self, *args):
        """pushContext([image]): guarda el estado y, si recibe una imagen, pasa a
        dibujar SOBRE ELLA.

        CUIDADO con el lock: `pushContext()` SIN destino NO debe guardar/restaurar
        `_lock`, porque el lock lo controla `lockFocus`/`unlockFocus` por separado.
        Antes se guardaba siempre y `popContext` lo restauraba: CoreLibs hace
        decenas de push/pop internos al importarse y uno de ellos PISABA el lock
        que el juego ya tenia puesto. Sin lock, la escena del juego se dibujaba en
        el framebuffer, su buffer de pantalla quedaba vacio y su blit lo borraba
        todo -> intro y menu invisibles, pantalla en blanco.
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
                self._ox = self._oy = 0     # un contexto nuevo empieza sin offset
                self._canvas.set_clip(None)

    def popContext(self):
        if self._stack:
            color, ox, oy, lock, dither, (tx, ty) = self._stack.pop()
            self._color, self._ox, self._oy = color, ox, oy
            self._dither = dither
            self._tx, self._ty = tx, ty
            if lock is not _NO_LOCK:
                self._lock = lock           # solo si el push era CON destino
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
        # no-op: el dither ordenado se probo y se revirtio (documentado en screen.py)
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

    # --- primitivas -----------------------------------------------------
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

    # --- texto ----------------------------------------------------------
    def setFont(self, name=None, size=None):
        if isinstance(name, (int, float)) and size is None:
            self._font_size = int(name)
        elif name is not None and size is None:
            self._font_handle = name       # handle de fuente (de font.new/getSystemFont)
        elif size:
            self._font_size = int(size)

    def getFont(self, *a):
        if self._font_handle is None and self.font_factory is not None:
            self._font_handle = self.font_factory()
        return self._font_handle

    def getTextWidth(self, text):
        return self._get_font().size(str(text))[0]

    def getTextSize(self, text, *a):
        """Ancho/alto del texto con la fuente ACTUAL.

        OJO: si el juego hizo setFont(font.new(".pft")), hay que medir con ESA
        fuente. Medir con la fuente de pygame devolvia anchos a la mitad (48 en
        vez de 95 para "Klondike"), y CoreLibs usa esta funcion para dimensionar
        los rects de texto: los items del menu de Smolitaire salian truncados a
        la mitad y las cajas del UI, mal medidas.
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
        """La fuente .pft actual, si el juego hizo setFont(font.new(...))."""
        h = self._font_handle
        if h is None or self.deref is None:
            return None
        try:
            obj = self.deref(h)
        except Exception:
            return None
        return obj if obj is not None and hasattr(obj, "getGlyph") else None

    def _draw_pft(self, text, x, y):
        """Dibuja con la fuente .pft (la real del juego).

        Los glifos se TINEN con el color actual usando su mascara: el bitmap del
        .pft trae la tinta de un color fijo, asi que blitearlo tal cual dibujaria
        siempre del mismo color -- y sobre un fondo negro las letras blancas no
        se verian. Antes se usaba la fuente por defecto de pygame y el texto
        salia con otra tipografia (solapado/ilegible).
        """
        font = self._pft_font()
        if font is None:
            return False
        ink = self._ink()
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
        surf = self._get_font().render(str(text), True, self._ink())
        self._canvas.blit(surf, self._xy(x, y))

    def drawTextAligned(self, text, x, y, align=0, *a):
        """drawTextAligned(text, x, y, alignment): el texto se alinea SOBRE x.

        Con kTextAlignment.center, x es el CENTRO (y con right, el borde derecho).
        Antes se ignoraba `alignment` y se dibujaba siempre desde x, lo que dejaba
        el texto desplazado media anchura a la derecha: por eso el titulo del
        juego salia cortado y descentrado respecto al emulador oficial.

        Valores segun CoreLibs: left=0, right=1, center=2.
        """
        try:
            al = int(align or 0)
        except (TypeError, ValueError):
            al = 0
        w = self.getTextWidth(str(text))
        if al == 1:              # right: x es el borde derecho
            x = x - w
        elif al == 2:            # center: x es el centro
            x = x - w // 2
        self.drawText(text, x, y)

    def drawCenteredText(self, text, y):
        w = self.getTextWidth(text)
        self.drawText(text, (self.width - w) // 2, y)

    # --- imagenes -------------------------------------------------------
    def _image_surface(self, handle):
        if self.deref is None:
            return None
        obj = self.deref(handle)
        return obj.surface if obj is not None else None

    def _poly_pts(self, *args):
        """Normaliza los argumentos de drawPolygon/fillPolygon a [(x,y), ...].

        La API admite las DOS formas:
          fillPolygon(x1, y1, x2, y2, ...)        -> coordenadas planas
          drawPolygon(polygon)                    -> objeto polygon / lista de puntos
        Un juego real (Kickflip Coast) usa las dos, y sin esto revienta con
        "field 'fillPolygon' is not callable".
        """
        pts = []
        # forma 1: un unico objeto/lista de puntos
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
        # forma 2: coordenadas planas
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
        """fillTriangle(x1,y1,x2,y2,x3,y3): triangulo relleno (particulas)."""
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
        """Dibuja TODOS los pixeles opacos de la imagen en TINTA (oscuro).

        Para imagenes "relleno claro" (cursor de mano casi todo blanco) que sobre
        un fondo claro serian invisibles. Genera un rectangulo usando la mascara
        de opacidad de la imagen.
        """
        surf = self._image_surface(handle)
        if surf is None:
            return
        w, h = surf.get_size()
        # superficie solida del color de tinta, recortada con el shape
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
        """Dibuja la imagen anclada: el punto (ax*ancho, ay*alto) cae en (x, y).

        CoreLibs lo expone como `img:drawAnchored(x, y, ax, ay)`; un juego lo usa
        para centrar (ax=0.5) o alinear arriba (ay=0) sin calcular el tamano.
        """
        surf = self._image_surface(handle)
        if surf is None:
            return
        self.image_draw(handle,
                        int(x) - int(surf.get_width() * (ax or 0)),
                        int(y) - int(surf.get_height() * (ay or 0)))

    def image_draw_faded(self, handle, x, y, alpha=1.0, flip=None):
        """drawFaded: en una pantalla de 1 bit no hay translucidez, hay TRAMA.

        Aproximacion por tramos (sin motor de dithering): >=0.66 solido,
        0.33-0.66 damero al 50%, <0.33 damero al 25%. Es lo que hace el hardware
        conceptualmente; con dithering real daria tonos intermedios mas finos.
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
