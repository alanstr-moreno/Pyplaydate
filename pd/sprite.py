"""sprite.py — playdate.graphics.sprite (Python side).

Games do NOT use "stock" sprites: their own `CoreLibs/sprites` (compiled inside
the .pdz) is a Lua layer that calls THIS C API. That's why you must implement
exactly what CoreLibs/sprites invokes.

Position model (Playdate):
  * the sprite has an ANCHOR (`center`), by default the center of its image;
  * `moveTo(x, y)` places the anchor at (x, y);
  * drawing happens at `pos - center`.

The Lua object the game sees is a "handle" (table with `__id`, see runtime.py).
`spr.handle` is kept so we can read fields the game adds to the sprite
(e.g. its own `:update()` function).
"""


class Sprite:
    def __init__(self, image_obj=None, image_handle=None, size=None):
        self.image_obj = image_obj          # PDImage (for the size)
        self.image_handle = image_handle    # Lua table (for drawing)
        self.size = size                    # (w,h) if there is no image
        self.pos = [0.0, 0.0]               # anchor on screen
        self.center = None                  # anchor inside the image (None = center)
        self.z = 0
        self.visible = True
        self.updates_enabled = True
        self.ignores_offset = False
        self.flip = 0
        self.rotation = 0
        self.scale = 1
        self.tag = None
        self.opaque = False
        self.collide = None                 # (x, y, w, h) relative to the sprite
        self.tilemap = None
        self.groups = 0
        # Collision mask system (real API):
        #   group_mask     = layers the sprite BELONGS to  (setGroups/setGroupMask)
        #   collides_mask  = layers it COLLIDES with       (setCollidesWithGroups...)
        #   collision_response = 0 free / 1 bounce / 2 slide (kCollisionType*)
        self.group_mask = 0
        self.collides_mask = 0
        self.collision_response = 0
        self.collisions_enabled = True
        self.handle = None                  # Lua table (back-ref)
        self.system = None

    # --- geometry ------------------------------------------------------
    def image_size(self):
        if self.image_obj is not None:
            return self.image_obj.getSize()
        return self.size or (0, 0)

    def center_offset(self):
        """The sprite's anchor IN PIXELS.

        NOTE: Playdate's `sprite:setCenter(x, y)` takes FRACTIONS 0..1 of the
        sprite size (0.5, 0.5 = the center). Before, the fraction was stored and
        subtracted as-is, as if it were pixels: everything a sprite drew came out
        shifted right and down (half width / half height). That made the game
        title off-center and the menus out of place.
        """
        w, h = self.image_size()
        if self.center is not None:
            cx, cy = self.center
            return (cx * w, cy * h)          # fraction -> pixels
        return (w // 2, h // 2)              # default: the center

    def getCenter(self):
        """Returns the fraction (same units as setCenter)."""
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
        """Writes `x`/`y` to the Lua handle. Call after EVERY movement.

        Games read `sprite.x`/`sprite.y` as FIELDS and compute on them: a real
        game does `plane:moveWithCollisions(plane.x + dx, plane.y + dy)`. If the
        field stays frozen, the delta is ALWAYS computed from the initial
        position and the sprite never advances — with input arriving perfectly
        (it looks like the arrows don't work, and if they are the player's,
        "it doesn't move"). Writing Lua table fields from Python is safe; the
        dangerous part is READING them.
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
        """Rectangle (x, y, w, h) in screen coords where the image is drawn."""
        w, h = self.image_size()
        cx, cy = self.center_offset()
        return (int(self.pos[0] - cx), int(self.pos[1] - cy), int(w), int(h))

    def alpha_collision(self, other):
        """PIXEL-level collision (API sprite:alphaCollision): besides the
        rectangles intersecting, at least one non-empty pixel of one image
        touches the other. FlippyFish uses it to decide if the fish really hit
        the floor/seaweed tip (without it, it flagged gameOver in mid-air).
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
            return True   # no pixel data: assume opaque (bounds already overlap)
        # sample the intersection: any pixel with non-empty alpha in both
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

    # --- collisions (AABB) ----------------------------------------------
    def collide_rect_at(self, x, y):
        if self.collide is None:
            return None
        w, h = self.image_size()
        cx, cy = self.center_offset()
        tl_x, tl_y = x - cx, y - cy
        rx, ry, rw, rh = self.collide
        return (int(tl_x + rx), int(tl_y + ry), int(rw), int(rh))

    def moveWithCollisions(self, gx, gy):
        """Moves resolving collisions. Returns (x, y, collisions) where each
        collision is (other_sprite, normal_x, normal_y).

        The `normal` is NOT decorative: games use it to bounce
        (`if collision.normal.x ~= 0 then s.velocityX = -s.velocityX end`, as
        the SpriteCollisionMasks example does). Without it, the game dies with
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

        # Collision list: the contacts of both axes (whether they blocked or
        # only Overlap). A sprite that "passes through" (Overlap) is also
        # reported: FlippyFish sets score/gameOver inside its collisionResponse,
        # which already ran in sprite_response_blocks.
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
                # response effects even if it does not block (Overlap): the game
                # decides gameOver/score here, every frame while overlapping.
                sys.sprite_response_call(self, other)
                ndx = -1.0 if other.pos[0] < self.pos[0] else 1.0
                cols.append((other, ndx, 0.0))
        return (self.pos[0], self.pos[1], cols)


class SpriteSystem:
    """Global sprite list + update/draw phase (playdate.graphics.sprite.*)."""

    def __init__(self, graphics, lua=None):
        self.g = graphics
        self.lua = lua                     # the Lua state, for the callbacks
        self.sprites = []
        self.background_cb = None

    # --- management --------------------------------------------------------
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

    # --- collisions -----------------------------------------------------
    def would_collide(self, spr, x, y):
        """True if the sprite would collide moving to (x, y).

        IMPORTANT: sprites it is ALREADY overlapping at its current position are
        IGNORED. Otherwise a sprite that spawns or ends up overlapping something
        (e.g. stuck against a wall) has ALL its movements blocked forever: it
        freezes in place. That is the "not all of them move" bug in
        SpriteCollisionMasks. The real console also lets you leave a pre-existing
        overlap; it only blocks NEW collisions.
        """
        rect = spr.collide_rect_at(x, y)
        if rect is None:
            return False
        here = spr.collide_rect_at(spr.pos[0], spr.pos[1])
        for other in self.sprites:
            if other is spr or not other.collisions_enabled:
                continue
            # Collision masks: two sprites collide if one's group mask
            # AND-ed with the other's "collides with" mask is non-zero.
            # Without this filter a layered game (SpriteCollisionMasks) sees
            # collisions that don't exist and its player bounces off everything.
            if not _masks_collide(spr, other):
                continue
            ore = other.collide_rect_at(other.pos[0], other.pos[1])
            if ore is None:
                continue
            if not _aabb(rect, ore):
                continue
            # already overlapping? then this hit is not new: it does not block.
            if here is not None and _aabb(here, ore):
                continue
            return other
        return None

    def sprite_response_blocks(self, spr, other):
        """Asks the MOVING sprite's collisionResponse and says whether the hit
        with `other` BLOCKS the movement.

        moveWithCollisions on the real console asks the sprite
        `self:collisionResponse(other)` and respects the kCollisionType it
        returns. FlippyFish overrides the method to ALWAYS return Overlap (the
        fish PASSES THROUGH the seaweed and score/gameOver are decided in the
        callback itself); SpriteCollisionMasks sets collisionResponse =
        kCollisionTypeBounce. Without this a game neither bounced nor scored.
        """
        lua = self.lua
        OVERLAP = 2
        if lua is None or spr.handle is None or other.handle is None:
            return True
        try:
            t = lua.eval("__pd_sprite_response_type")(spr.handle, other.handle)
        except Exception:
            t = None
        return t != OVERLAP   # nil -> default Slide (blocks)

    def sprite_response_call(self, spr, other):
        """Invokes the MOVING sprite's collisionResponse against `other` ONLY for
        its effects (gameOver, score...), without blocking anything.

        The console calls collisionResponse EVERY FRAME while the sprite
        overlaps (Overlap type). FlippyFish depends on that: the fish falls
        20px/frame, so on first contact its pixels don't touch the floor yet
        (alphaCollision=false) and gameOver only fires on a later frame while
        still overlapping. Without this continuous call, the fish passed through
        the floor and fell into the void without dying or scoring.
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
        """playdate.graphics.sprite.update(): updates and then draws."""
        for s in list(self.sprites):
            if not s.updates_enabled or s.handle is None:
                continue
            self._cb("__pd_sprite_update_cb", s)
        if self.background_cb is not None:
            self.background_cb()
        for s in sorted([s for s in self.sprites if s.visible], key=lambda s: s.z):
            self.draw_sprite(s)

    def _cb(self, which, s):
        """Invokes a sprite callback (update/draw) entirely in Lua.

        NOTE: Graphics is pure Python and has NO Lua state; the state lives in
        the Runtime, which injects it here.

        Errors are NOT silenced: a callback that crashes leaves the game drawing
        nothing (or with no logic) and the symptom is invisible -- "the game runs,
        gives no errors and draws nothing". It is reported once per distinct
        message.
        """
        lua = self.lua
        if lua is None or s.handle is None:
            return
        try:
            if which == "__pd_sprite_draw_cb":
                # f(sprite, x, y, w, h): the dirty rect. We pass the sprite's
                # bounds (that's what the background callback needs for its clip).
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
                # real sprites.lua/C: the `draw(s, x, y, w, h)` callback receives
                # the dirty rect in coordinates RELATIVE to the sprite (0,0 = the
                # sprite's top-left corner) with the TRANSLATE set to that corner,
                # and the DRAW COLOR reset to ink -- documented in
                # "Inside Playdate" (sprite:draw) and needed for FlippyFish,
                # whose Score:draw paints the number at (0,0) with the global
                # white color left by the intro (white on white = invisible).
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
        # The cursor hand (pointer 24x24) is PREDOMINANTLY PAPER (white, bit=1):
        # drawn as-is it is invisible on the game's light board (Smolitaire's
        # hand has 293 whites + 97 blacks). The original shows it on a dark
        # background that is not drawn here. It is drawn in INK (all opaque
        # pixels dark) so it is the visible hand the user expects ("a hand with
        # the index finger pointing right").
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
            # NO IMAGE: the sprite can only be drawn by its `draw` callback,
            # which is how games draw cursors, menus and effects. Before it
            # returned here and those sprites were invisible.
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
    """True if the groups of `a` and `b` say they can collide.

    API rule: they collide if (one's group_mask) AND (the other's collides_mask)
    is != 0 in either direction. If NEITHER has masks set (0/0) the collision is
    allowed: that is the default case for a game that does not use the group
    system.
    """
    am, ac = getattr(a, "group_mask", 0) or 0, getattr(a, "collides_mask", 0) or 0
    bm, bc = getattr(b, "group_mask", 0) or 0, getattr(b, "collides_mask", 0) or 0
    if am == 0 and ac == 0 and bm == 0 and bc == 0:
        return True
    return bool((am & bc) or (bm & ac))


def _groups_to_mask(groups):
    """`setGroups({2,3})` -> mask. Group N occupies bit N-1 (1<<(N-1)).

    The official example says it: `setGroups({2})` equals `setGroupMask(2)` and
    `setCollidesWithGroups({3})` equals `setCollidesWithGroupsMask(4)`.
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
