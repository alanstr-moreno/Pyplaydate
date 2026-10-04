"""sprite.py — playdate.graphics.sprite (lado Python).

Los juegos NO usan sprites "de fabrica": su propio `CoreLibs/sprites` (viene
compilado dentro del .pdz) es una capa Lua que llama a ESTA API C. Por eso hay
que implementar exactamente lo que CoreLibs/sprites invoca.

Modelo de posicion (Playdate):
  * el sprite tiene un ANCLA (`center`), por defecto el centro de su imagen;
  * `moveTo(x, y)` coloca el ancla en (x, y);
  * el dibujo ocurre en `pos - center`.

El objeto Lua que ve el juego es un "handle" (tabla con `__id`, ver runtime.py).
Se guarda `spr.handle` para poder leer campos que el juego agregue al sprite
(p.ej. su propia funcion `:update()`).
"""


class Sprite:
    def __init__(self, image_obj=None, image_handle=None, size=None):
        self.image_obj = image_obj          # PDImage (para el tamano)
        self.image_handle = image_handle    # tabla Lua (para dibujar)
        self.size = size                    # (w,h) si no hay imagen
        self.pos = [0.0, 0.0]               # ancla en pantalla
        self.center = None                  # ancla dentro de la imagen (None = centro)
        self.z = 0
        self.visible = True
        self.updates_enabled = True
        self.ignores_offset = False
        self.flip = 0
        self.rotation = 0
        self.scale = 1
        self.tag = None
        self.opaque = False
        self.collide = None                 # (x, y, w, h) relativo al sprite
        self.tilemap = None
        self.groups = 0
        # Sistema de mascaras de colision (API real):
        #   group_mask     = capas a las que PERTENECE el sprite  (setGroups/setGroupMask)
        #   collides_mask  = capas CON las que choca              (setCollidesWithGroups...)
        #   collision_response = 0 free / 1 bounce / 2 slide (kCollisionType*)
        self.group_mask = 0
        self.collides_mask = 0
        self.collision_response = 0
        self.collisions_enabled = True
        self.handle = None                  # tabla Lua (back-ref)
        self.system = None

    # --- geometria ------------------------------------------------------
    def image_size(self):
        if self.image_obj is not None:
            return self.image_obj.getSize()
        return self.size or (0, 0)

    def center_offset(self):
        """Ancla del sprite EN PIXELES.

        OJO: `sprite:setCenter(x, y)` de Playdate toma FRACCIONES 0..1 del tamano
        del sprite (0.5, 0.5 = el centro). Antes se guardaba la fraccion y se
        restaba tal cual, como si fueran pixeles: todo lo que dibujaba un sprite
        salia desplazado a la derecha y hacia abajo (media anchura / media altura).
        Con eso el titulo del juego salia descentrado y los menus, fuera.
        """
        w, h = self.image_size()
        if self.center is not None:
            cx, cy = self.center
            return (cx * w, cy * h)          # fraccion -> pixeles
        return (w // 2, h // 2)              # por defecto: el centro

    def getCenter(self):
        """Devuelve la fraccion (mismas unidades que setCenter)."""
        if self.center is not None:
            return self.center
        return (0.5, 0.5)

    def bounds(self):
        w, h = self.image_size()
        cx, cy = self.center_offset()
        return (int(self.pos[0] - cx), int(self.pos[1] - cy), w, h)

    def getPosition(self):
        return (self.pos[0], self.pos[1])

    def sync_handle(self):
        """Escribe `x`/`y` en el handle Lua. Llamar tras CADA movimiento.

        Los juegos leen `sprite.x`/`sprite.y` como CAMPOS y calculan sobre ellos:
        un juego real hace `plane:moveWithCollisions(plane.x + dx, plane.y + dy)`.
        Si el campo se queda congelado, el delta se calcula SIEMPRE sobre la
        posicion inicial y el sprite no avanza nunca — con el input llegando
        perfectamente (parece que las flechas no funcionan, y si son las del
        jugador, "no se mueve"). Escribir campos de tablas Lua desde Python es
        seguro; lo peligroso es LEERLOS.
        """
        h = self.handle
        if h is None:
            return
        try:
            h["x"] = self.pos[0]
            h["y"] = self.pos[1]
        except Exception:  # noqa: BLE001
            pass

    def moveTo(self, x, y):
        self.pos = [float(x), float(y)]
        self.sync_handle()

    def moveBy(self, dx, dy):
        self.pos[0] += dx
        self.pos[1] += dy
        self.sync_handle()

    def setCenter(self, cx, cy):
        self.center = (cx, cy)

    def getCenter(self):
        return self.center_offset()

    def setSize(self, w, h):
        self.size = (int(w), int(h))

    def getSize(self):
        return self.image_size()

    def setBounds(self, x, y, w, h):
        self.center = None
        self.size = (int(w), int(h))
        cx, cy = self.center_offset()
        self.pos = [x + cx, y + cy]

    def getBounds(self):
        return self.bounds()

    def setImage(self, image_obj=None, image_handle=None, flip=0, scale=1):
        self.image_obj = image_obj
        self.image_handle = image_handle
        self.flip = flip or 0
        if scale:
            self.scale = scale

    def bounds(self):
        """Rectangulo (x, y, w, h) en coords de pantalla donde se dibuja la imagen."""
        w, h = self.image_size()
        cx, cy = self.center_offset()
        return (int(self.pos[0] - cx), int(self.pos[1] - cy), int(w), int(h))

    def alpha_collision(self, other):
        """Colision a nivel de PIXEL (API sprite:alphaCollision): ademas de
        intersectar los rectangulos, al menos un pixel no vacio de la imagen de
        uno toca a otro. FlippyFish lo usa para decidir si el pez choco de verdad
        con el suelo/la punta del alga (sin esto marcaba gameOver por aire).
        """
        ax, ay, aw, ah = self.bounds()
        bx, by, bw, bh = other.bounds()
        ox0, oy0 = max(ax, bx), max(ay, by)
        ox1, oy1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        if ox0 >= ox1 or oy0 >= oy1:
            return False
        a = getattr(self.image_obj, "surface", None)
        b = getattr(other.image_obj, "surface", None)
        if a is None or b is None:
            return True   # sin datos de pixel: asumir opaco (bounds ya chocan)
        # muestrear la interseccion: cualquier pixel con alpha no vacia en ambas
        for px in range(int(ox0), int(ox1), 2):
            for py in range(int(oy0), int(oy1), 2):
                try:
                    ca = a.get_at((px - ax, py - ay))
                    cb = b.get_at((px - bx, py - by))
                except Exception:
                    continue
                if ca[3] > 0 and cb[3] > 0:
                    return True
        return False

    # --- colisiones (AABB) ----------------------------------------------
    def collide_rect_at(self, x, y):
        if self.collide is None:
            return None
        w, h = self.image_size()
        cx, cy = self.center_offset()
        tl_x, tl_y = x - cx, y - cy
        rx, ry, rw, rh = self.collide
        return (int(tl_x + rx), int(tl_y + ry), int(rw), int(rh))

    def moveWithCollisions(self, gx, gy):
        """Mueve resolviendo choques. Devuelve (x, y, colisiones) donde cada
        colision es (otro_sprite, normal_x, normal_y).

        El `normal` NO es decorativo: los juegos lo usan para rebotar
        (`if collision.normal.x ~= 0 then s.velocityX = -s.velocityX end`, asi lo
        hace el ejemplo SpriteCollisionMasks). Sin el, el juego muere con
        "attempt to index a nil value (field 'normal')".
        """
        sys = self.system
        nx, ny = float(gx), float(gy)
        hit_x = hit_y = None
        if sys is not None:
            hit_x = sys.would_collide(self, nx, self.pos[1])
        bx = (hit_x is not None and sys.sprite_response_blocks(self, hit_x))
        if bx:
            nx = self.pos[0]
        self.pos[0] = nx
        if sys is not None:
            hit_y = sys.would_collide(self, self.pos[0], ny)
        by = (hit_y is not None and sys.sprite_response_blocks(self, hit_y))
        if by:
            ny = self.pos[1]
        self.pos[1] = ny
        self.sync_handle()

        # Lista de colisiones: los contactos de ambos ejes (da igual si bloquearon
        # o solo en Overlap). Un sprite que "atraviesa" (Overlap) tambien se
        # reporta: FlippyFish marca la puntuacion/gameOver dentro de su
        # collisionResponse, que ya se ejecuto en sprite_response_blocks.
        cols = []
        seen = set()
        for other, blocked_x in ((hit_x, True), (hit_y, False)):
            if other is None or id(other) in seen:
                continue
            seen.add(id(other))
            ndx = (-1.0 if gx < self.pos[0] else 1.0) if blocked_x else 0.0
            ndy = 0.0 if blocked_x else (-1.0 if gy < self.pos[1] else 1.0)
            cols.append((other, ndx, ndy))
        if sys is not None:
            for other in sys.overlapping(self):
                if id(other) in seen:
                    continue
                seen.add(id(other))
                # efectos de la response aunque no bloquee (Overlap): el juego
                # decide gameOver/puntuacion aqui, cada frame mientras se solapa.
                sys.sprite_response_call(self, other)
                ndx = -1.0 if other.pos[0] < self.pos[0] else 1.0
                cols.append((other, ndx, 0.0))
        return (self.pos[0], self.pos[1], cols)


class SpriteSystem:
    """Lista global de sprites + fase de update/draw (playdate.graphics.sprite.*)."""

    def __init__(self, graphics, lua=None):
        self.g = graphics
        self.lua = lua                     # el estado Lua, para los callbacks
        self.sprites = []
        self.background_cb = None

    # --- gestion --------------------------------------------------------
    def new(self, image_obj=None, image_handle=None):
        s = Sprite(image_obj, image_handle)
        s.system = self
        return s

    def add(self, s):
        if s not in self.sprites:
            self.sprites.append(s)

    def remove(self, s):
        if s in self.sprites:
            self.sprites.remove(s)

    def all(self):
        return list(self.sprites)

    def remove_all(self):
        self.sprites.clear()

    # --- colisiones -----------------------------------------------------
    def would_collide(self, spr, x, y):
        """True si el sprite chocaria moviendose a (x, y).

        IMPORTANTE: se IGNORAN los sprites con los que YA esta solapando en su
        posicion actual. Si no, un sprite que nace o acaba solapando algo (p.ej.
        pegado a un muro) queda con TODOS sus movimientos bloqueados para
        siempre: se congela en el sitio. Es el bug de "no todos se mueven" en
        SpriteCollisionMasks. La consola real tambien permite salir de un solape
        preexistente; solo bloquea las colisiones NUEVAS.
        """
        rect = spr.collide_rect_at(x, y)
        if rect is None:
            return False
        here = spr.collide_rect_at(spr.pos[0], spr.pos[1])
        for other in self.sprites:
            if other is spr or not other.collisions_enabled:
                continue
            # Mascaras de colision: dos sprites chocan si la mascara de grupo de
            # uno AND-eada con la de "choca con" del otro es distinta de cero.
            # Sin este filtro un juego de capas (SpriteCollisionMasks) ve
            # colisiones que no existen y su jugador rebota contra todo.
            if not _masks_collide(spr, other):
                continue
            ore = other.collide_rect_at(other.pos[0], other.pos[1])
            if ore is None:
                continue
            if not _aabb(rect, ore):
                continue
            # ¿ya estaba solapando? entonces este choque no es nuevo: no bloquea.
            if here is not None and _aabb(here, ore):
                continue
            return other
        return None

    def sprite_response_blocks(self, spr, other):
        """Consulta la collisionResponse del sprite MOVIL y dice si el choque
        con `other` BLOQUEA el movimiento.

        moveWithCollisions en la consola real pregunta al sprite
        `self:collisionResponse(other)` y respeta el kCollisionType que este
        devuelve. FlippyFish sobreescribe el metodo para devolver SIEMPRE
        Overlap (el pez ATRAVIESA las algas y la puntuacion/gameOver se deciden
        en el propio callback); SpriteCollisionMasks pone collisionResponse =
        kCollisionTypeBounce. Sin esto un juego no rebotaba ni puntuaba.
        """
        lua = self.lua
        OVERLAP = 2
        if lua is None or spr.handle is None or other.handle is None:
            return True
        try:
            t = lua.eval("__pd_sprite_response_type")(spr.handle, other.handle)
        except Exception:
            t = None
        return t != OVERLAP   # nil -> default Slide (bloquea)

    def sprite_response_call(self, spr, other):
        """Invoca la collisionResponse del sprite MOVIL ante `other` SOLO por sus
        efectos (gameOver, puntuacion...), sin bloquear nada.

        La consola llama a collisionResponse CADA FRAME mientras el sprite se
        solapa (tipo Overlap). FlippyFish depende de eso: el pez cae 20px/frame,
        asi que en el primer contacto sus pixeles aun no tocan el suelo
        (alphaCollision=false) y gameOver solo se dispara en un frame posterior
        mientras sigue solapado. Sin esta llamada continua, el pez atravesaba el
        suelo y caia al vacio sin morir ni puntuar.
        """
        lua = self.lua
        if lua is None or spr.handle is None or other.handle is None:
            return
        try:
            lua.eval("__pd_sprite_response_type")(spr.handle, other.handle)
        except Exception:
            pass

    def alpha_collide(self, spr, other):
        try:
            return spr.alpha_collision(other)
        except Exception:
            return False

    def overlapping(self, spr):
        rect = spr.collide_rect_at(spr.pos[0], spr.pos[1])
        out = []
        if rect is None:
            return out
        for other in self.sprites:
            if other is spr:
                continue
            if not _masks_collide(spr, other):
                continue
            ore = other.collide_rect_at(other.pos[0], other.pos[1])
            if ore is not None and _aabb(rect, ore):
                out.append(other)
        return out

    # --- frame ----------------------------------------------------------
    def update(self):
        """playdate.graphics.sprite.update(): actualiza y luego dibuja."""
        for s in list(self.sprites):
            if not s.updates_enabled or s.handle is None:
                continue
            self._cb("__pd_sprite_update_cb", s)
        if self.background_cb is not None:
            self.background_cb()
        for s in sorted([s for s in self.sprites if s.visible], key=lambda s: s.z):
            self.draw_sprite(s)

    def _cb(self, which, s):
        """Invoca un callback del sprite (update/draw) enteramente en Lua.

        OJO: Graphics es Python puro y NO tiene estado Lua; el estado vive en el
        Runtime, que se lo inyecta aqui.

        Los errores NO se silencian: un callback que revienta deja el juego sin
        dibujar nada (o sin logica) y el sintoma es invisible -- "el juego corre,
        no da errores y no pinta nada". Se avisa una vez por mensaje distinto.
        """
        lua = self.lua
        if lua is None or s.handle is None:
            return
        try:
            if which == "__pd_sprite_draw_cb":
                # f(sprite, x, y, w, h): el rect sucio. Pasamos los bounds del
                # sprite (es lo que el callback del fondo necesita para su clip).
                try:
                    x, y = s.bounds()[0], s.bounds()[1]
                except Exception:
                    try:
                        x, y = s.pos
                    except Exception:
                        x = y = 0
                try:
                    w, hh = s.size if s.size else (0, 0)
                except Exception:
                    w = hh = 0
                # sprites.lua/C real: el callback `draw(s, x, y, w, h)` recibe el
                # dirty rect en coordenadas RELATIVAS al sprite (0,0 = esquina
                # sup. izq. del sprite) y con el TRANSLATE puesto en esa esquina,
                # y el COLOR DE DIBUJO reseteado a tinta -- documentado en
                # "Inside Playdate" (sprite:draw) y necesario para FlippyFish,
                # cuyo Score:draw pinta el numero en (0,0) con el color global
                # blanco que dejo el intro (blanco sobre blanco = invisible).
                g = self.g
                tx0, ty0 = g._tx, g._ty
                col0 = g._color
                g._tx, g._ty = int(x), int(y)
                g._color = 0
                try:
                    lua.eval(which)(s.handle, 0, 0, int(w), int(hh))
                finally:
                    g._tx, g._ty = tx0, ty0
                    g._color = col0
            else:
                lua.eval(which)(s.handle)
        except Exception as exc:  # noqa: BLE001
            msg = f"{which}: {type(exc).__name__}: {str(exc)[:160]}"
            if not hasattr(self, "_cb_errors"):
                self._cb_errors = set()
            if msg not in self._cb_errors:
                self._cb_errors.add(msg)
                print(f"[sprite callback] {msg}")

    def default_update(self, s):
        """Default per-sprite `update` callback, for plain sprites (API ref:
        'the sprite system calls this function every frame... Override it').
        Does nothing, but must EXIST so `spr:update()` never dies. Lives on the
        handle's __index, NOT as a raw key, so class sprites' own update() wins.
        """
        return None

    def draw_sprite(self, s):
        # La mano del cursor (pointer 24x24) es PREDOMINANTEMENTE PAPEL (blanca,
        # bit=1): al dibujarla tal cual es invisible sobre el tablero claro del
        # juego (la mano de Smolitaire tiene 293 blancos + 97 negros). El original
        # la muestra sobre un fondo oscuro que aqui no se pinta. Se dibuja en
        # TINTA (todos los opacos oscuros) para que sea la mano visible que el
        # usuario espera ("una mano con el indice apuntando a la derecha").
        if s.image_handle is not None and s.flip == 0:
            im = getattr(s.image_obj, "surface", None)
            if im is not None:
                w, h = im.get_size()
                if w * h <= 32 * 32:
                    white = black = 0
                    try:
                        for px in range(w):
                            for py in range(h):
                                p = im.get_at((px, py))
                                if p[3] > 0:
                                    if p[0] > 128:
                                        white += 1
                                    else:
                                        black += 1
                    except Exception:
                        white = black = 0
                    if white >= black * 2:
                        saved = self.g.getDrawOffset()
                        self.g.setDrawOffset(0, 0)
                        self.g.image_draw_inkfill(s.image_handle, s.bounds()[0], s.bounds()[1])
                        self.g.setDrawOffset(*saved)
                        return
        if s.image_handle is None:
            # SIN IMAGEN: el sprite solo puede pintarse por su callback `draw`,
            # que es como los juegos dibujan cursores, menus y efectos. Antes se
            # salia aqui y esos sprites eran invisibles.
            self._cb("__pd_sprite_draw_cb", s)
            return
        if s.ignores_offset:
            saved = self.g.getDrawOffset()
            self.g.setDrawOffset(0, 0)
            self.g.image_draw(s.image_handle, s.bounds()[0], s.bounds()[1], s.flip)
            self.g.setDrawOffset(*saved)
        else:
            self.g.image_draw(s.image_handle, s.bounds()[0], s.bounds()[1], s.flip)


def _masks_collide(a, b):
    """True si los grupos de `a` y `b` dicen que pueden chocar.

    Regla de la API: chocan si (group_mask de uno) AND (collides_mask del otro)
    es != 0 en cualquiera de los dos sentidos. Si NINGUNO de los dos tiene
    mascaras puestas (0/0) se permite el choque: es el caso por defecto de un
    juego que no usa el sistema de grupos.
    """
    am, ac = getattr(a, "group_mask", 0) or 0, getattr(a, "collides_mask", 0) or 0
    bm, bc = getattr(b, "group_mask", 0) or 0, getattr(b, "collides_mask", 0) or 0
    if am == 0 and ac == 0 and bm == 0 and bc == 0:
        return True
    return bool((am & bc) or (bm & ac))


def _groups_to_mask(groups):
    """`setGroups({2,3})` -> mascara. El grupo N ocupa el bit N-1 (1<<(N-1)).

    El ejemplo oficial lo dice: `setGroups({2})` equivale a `setGroupMask(2)` y
    `setCollidesWithGroups({3})` a `setCollidesWithGroupsMask(4)`.
    """
    mask = 0
    try:
        if isinstance(groups, (list, tuple)):
            for g in groups:
                mask |= 1 << (int(g) - 1)
        else:
            mask |= 1 << (int(groups) - 1)
    except Exception:  # noqa: BLE001
        pass
    return mask


def _aabb(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)
