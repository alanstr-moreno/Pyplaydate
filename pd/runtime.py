"""runtime.py — Lua <-> Python bridge.

Two important notes:

1) LUPA BUG AVOIDED: lupa 2.8 + Lua 5.5 + Python 3.14 corrupts access to
   ATTRIBUTES of NESTED Python objects from Lua after ~77 reads (we saw
   `playdate.button.isPressed` returning `Graphics.drawText`). That is why we
   do NOT expose nested Python objects: the API is built as Lua TABLES
   (`rt.table_from`) with Python functions on the leaves.

2) COMPILED BYTECODE: a real game is a `.pdx` with `main.pdz` (Lua 5.4.3
   bytecode with LUA_32BITS=1). Only the lupa in `vendor/lupa` can load it
   (see pd/luavm.py). In that mode we define `import(name)` which loads the
   chunks from the `.pdz` itself, just like Playdate does.

PATTERN TO ADD AN API:
  1) implement the method in the corresponding Python class (Graphics, Button...),
  2) add it to the `_build_api()` dict with its Playdate name.
"""

import os
import time

from . import graphics as gfx_mod
from . import input as input_mod
from .luavm import HAS_PLAYDATE_LUA, LUA_KIND, lupa  # noqa: F401  (lupa reexportado)


def _groups_to_mask(groups):
    """`setGroups({2,3})` -> bit mask (group N occupies bit N-1).

    Defined here (module level) because the `_build_api` lambdas resolve it as a
    global: an import inside a method does not reach them.
    The official example confirms it: `setGroups({2})` == `setGroupMask(2)` and
    `setCollidesWithGroups({3})` == `setCollidesWithGroupsMask(4)`.
    """
    mask = 0
    if groups is None:
        return 0

    def _add(g):
        nonlocal mask
        try:
            mask |= 1 << (int(g) - 1)
        except Exception:  # noqa: BLE001
            pass

    # NOTE: from Lua `setGroups({2, 3})` arrives a LUA TABLE, not a Python
    # list/tuple. Checking only list/tuple left the mask at 0 silently, and a
    # layered game stopped colliding with the walls (the sprites left the
    # screen). You must accept any iterable (including the Lua table).
    if isinstance(groups, (list, tuple, set)):
        for g in groups:
            _add(g)
        return mask
    if hasattr(groups, "__iter__") and not isinstance(groups, (str, bytes)):
        try:
            for g in groups.values() if hasattr(groups, "values") else groups:
                _add(g)
            return mask
        except Exception:  # noqa: BLE001
            pass
    _add(groups)
    return mask

LUA_GEOM_MT = "__pd_poly_mt = { __mul = function(a, b) return __pd_poly_mul(a, b) end }\nlocal function __pd_wrap(tbl)\n  local f = tbl['new']\n  if f == nil then return end\n  tbl['new'] = function(...)\n    local o = f(...)\n    if o ~= nil then debug.setmetatable(o, __pd_poly_mt) end\n    return o\n  end\nend\n__pd_wrap(playdate.geometry.polygon)\n__pd_wrap(playdate.geometry.affineTransform)\n"


__all__ = ["Runtime", "Playdate", "Display", "HAS_PLAYDATE_LUA", "LUA_KIND"]

_LOAD_HELPER = """
function __pd_load(data, name)
  local f, err = load(data, name)
  if not f then error(err, 0) end
  return f
end
"""


class Display:
    """playdate.display (Python implementation)."""

    def __init__(self, screen):
        self._screen = screen
        self._refresh = 30

    def getWidth(self):
        return self._screen.width

    def getHeight(self):
        return self._screen.height

    def setInverted(self, flag):
        """playdate.display.setInverted(flag): draws the framebuffer inverted."""
        self._screen.setInverted(flag)

    def getInverted(self):
        return self._screen.getInverted()

    def getScale(self):
        return getattr(self._screen, "pixel_scale", 1)

    def setScale(self, s):
        """playdate.display.setScale(n): only 1, 2, 4 and 8 are accepted."""
        try:
            n = int(s)
        except Exception:  # noqa: BLE001
            return
        if n in (1, 2, 4, 8):
            self._screen.pixel_scale = n

    def getScreenWidth(self):
        return self._screen.width

    def getScreenHeight(self):
        return self._screen.height

    def getRefreshRate(self):
        return self._refresh

    def setRefreshRate(self, hz):
        self._refresh = int(hz)


class Playdate:
    """State and implementation of the `playdate` object (Python side)."""

    def __init__(self, emulator):
        self._emu = emulator
        self._screen = emulator.screen
        self.graphics = gfx_mod.Graphics(emulator.screen)
        self.button = input_mod.Button()
        self.display = Display(emulator.screen)
        self._update_cb = None
        self._draw_cb = None
        self._crank_cb = None
        self._t0 = time.time()

    # --- time / frame -------------------------------------------------
    def getTimer(self):
        return time.time() - self._t0

    def getElapsedTime(self):
        return time.time() - self._t0

    def resetElapsedTime(self):
        self._t0 = time.time()

    def getTime(self):
        # SECONDS since startup (as the API defines it: "seconds since the app
        # started"). Before it returned milliseconds -- a 1000x error, a game
        # animating with getTime() would run a thousand times fast. It stays a
        # float: an epoch (1.7e12) fits neither in lua_Integer (32-bit) nor in
        # lua_Number.
        return time.time() - self._t0

    def getCurrentTimeMilliseconds(self):
        return int((time.time() - self._t0) * 1000.0)

    def getSecondsSinceEpoch(self):
        """Seconds since the epoch AND milliseconds (TWO values).

        The API returns `seconds, milliseconds` and games use BOTH: a real game
        does `local s, ms = playdate.getSecondsSinceEpoch();
        math.randomseed(ms, s)`. Returning only the seconds, the second value
        arrives nil and `math.randomseed` crashes at LOAD
        ("bad argument #1 to 'randomseed' (number expected, got nil)").
        """
        now = time.time()
        return (int(now), int((now - int(now)) * 1000))

    def getFPS(self):
        return self._emu.fps

    def drawFPS(self, x=0, y=0):
        """playdate.drawFPS(x, y): draws the FPS counter (games use it)."""
        try:
            self.graphics.drawText(str(self._emu.fps), int(x), int(y))
        except Exception:  # noqa: BLE001
            pass

    def getFrame(self):
        return self._emu.frame

    def getGameName(self):
        return self._emu.game_name

    def getSystemVersion(self):
        return "1.12.0"

    # --- game callbacks -------------------------------------------
    def setUpdateCallback(self, fn):
        self._update_cb = fn

    def setDrawCallback(self, fn):
        self._draw_cb = fn

    def getCrankPosition(self):
        """Crank angle (degrees). The keyboard moves it with the crank keys;
        default 0."""
        return getattr(self._emu, "crank_angle", 0.0)

    def getCrankChange(self):
        """Angle change since the last call (an "accumulated delta")."""
        cur = self.getCrankPosition()
        prev = getattr(self, "_crank_last", cur)
        self._crank_last = cur
        return cur - prev

    def getCrankTicks(self, ticks=1):
        """Crank clicks; approximated with the 360/ticks degree jumps."""
        try:
            per = max(1, int(ticks))
        except Exception:  # noqa: BLE001
            per = 1
        return int(abs(self.getCrankChange()) / (360.0 / per))

    def isCrankDocked(self):
        return bool(getattr(self._emu, "crank_docked", False))

    def setCrankSoundsDisabled(self, flag=True):
        pass

    def getButtonState(self):
        """Bitmask of pressed buttons (classic API, still in use).

        Bits: kButtonLeft=1, kButtonRight=2, kButtonUp=4, kButtonDown=8,
        kButtonA=16, kButtonB=32. Without it, a game that calls it gets nil and
        its input logic can get stuck.
        """
        b = self.playdate.button
        v = 0
        for name, bit in (("Left", 1), ("Right", 2), ("Up", 4), ("Down", 8),
                          ("A", 16), ("B", 32)):
            try:
                if b.isPressed(name):
                    v |= bit
            except Exception:  # noqa: BLE001
                pass
        return v

    def buttonIsPressedAny(self):
        """True if any button is pressed (used by some CoreLib/game)."""
        try:
            return self.playdate.button.isPressedAny()
        except Exception:  # noqa: BLE001
            return False

    def setCrankCallback(self, fn):
        self._crank_cb = fn

    # --- emulator control (useful for dev) ---------------------------
    def exit(self):
        self._emu.running = False


SDK_CORELIBS_DEFAULT = os.environ.get(
    "PLAYDATE_SDK_PATH", os.path.expanduser("~/Developer/PlaydateSDK/CoreLibs")
)

# The SDK uses Playdate Lua DIALECT, which adds compound assignment
# operators (`a -= 1`, `t.x += y`, `t[i] *= 2`). lupa Lua is standard 5.4,
# so they must be expanded. frameTimer.lua:182 is `timer.remainingDelay -= 1`
# and without this it does not compile ("syntax error near '-'").
_COMPOUND_RE = None


def _expand_compound_assign(src):
    """Expands `lhs op= rhs` to `lhs = lhs op (rhs)` (Playdate dialect)."""
    global _COMPOUND_RE
    if _COMPOUND_RE is None:
        import re
        _COMPOUND_RE = re.compile(r"^(\s*)(.+?)\s*([-+*/%])=(?!=)\s*(.+?)(\s*--.*)?$")
    out = []
    for line in src.splitlines():
        m = _COMPOUND_RE.match(line)
        if m:
            ind, lhs, op, rhs, comment = (m.group(1), m.group(2), m.group(3),
                                          m.group(4), m.group(5) or "")
            # discard comparisons (==, <=, >=, ~=) and odd lines
            if lhs.strip() and "=" not in lhs and not lhs.endswith(
                    ("<", ">", "~", "+", "-", "*", "/", "%", "=")):
                out.append(f"{ind}{lhs} = {lhs} {op} ({rhs}){comment}")
                continue
        out.append(line)
    return "\n".join(out)


def _sdk_corelib_source(name):
    """Source of an installed SDK CoreLib, or None if it does not exist.

    CoreLibs are part of the console, not the game: they are resolved here so
    `import "CoreLibs/nineslice"` works even if the .pdx does not ship it.
    """
    import os

    # `name` arrives as "CoreLibs/nineslice"; the file is <base>/nineslice.lua.
    rel = name[len("CoreLibs/"):] if name.startswith("CoreLibs/") else name
    if ".." in rel or rel.startswith("/"):
        return None                       # no leaving the directory
    bases = []
    if os.environ.get("PLAYDATE_SDK_PATH"):
        bases.append(os.path.join(os.environ["PLAYDATE_SDK_PATH"], "CoreLibs"))
    bases.append(SDK_CORELIBS_DEFAULT)
    for base in bases:
        p = os.path.join(base, rel + ".lua")
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    return _expand_compound_assign(fh.read())
            except OSError:
                return None
    return None


class Runtime:
    def __init__(self, emulator):
        self.verbose = False          # --verbose: game prints and asset warnings
        self._asset_warned = set()   # assets already warned (no repeats)
        self.emu = emulator
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(_LOAD_HELPER)
        self._pd_load = self.lua.globals()["__pd_load"]
        self.playdate = Playdate(emulator)
        self.playdate.graphics.deref = self._deref   # Lua handle -> Python object
        from .sprite import SpriteSystem
        self.sprites = SpriteSystem(self.playdate.graphics, self.lua)
        self.api = None
        self.chunks = None          # .pdx mode: name -> bytecode bytes
        self.asset_dir = None
        self.on_ready = None        # optional hook: called before running the game
        self._registry = {}         # id -> Python object (images, imagetables, fonts)
        self._next_id = 1
        self.meta = {}              # game pdxinfo
        self.fs = None              # file sandbox (playdate.file/datastore)
        self._input_stack = []      # playdate.inputHandlers
        self._timer_update = None   # playdate.timer.updateTimers (from CoreLibs)
        self._frametimer_update = None
        self._updater = None        # persistent playdate.update coroutine

    # ------------------------------------------------------------------
    def _build_api(self):
        """Builds the global `playdate` as Lua TABLES (see note 1 above)."""
        rt = self.lua
        T = rt.table_from
        pd = self.playdate
        g = pd.graphics
        g.font_factory = self._make_font
        b = pd.button

        gfx = T({
            "width": g.width,
            "height": g.height,
            "clear": g.clear,
            "setColor": g.setColor,
            "getColor": g.getColor,
            # setBackgroundColor: the color `clear()` fills the screen with.
            # The real API exposes it on `playdate.graphics` (NOT only on
            # `graphics.sprite`), and games call it at startup:
            # FlippyFish does `gfx.setBackgroundColor(gfx.kColorWhite)` on its
            # first line. If missing, the load dies with
            # "field 'setBackgroundColor' is not callable".
            "setBackgroundColor": lambda c=None, *a: g.setBackgroundColor(c),
            "getBackgroundColor": lambda *a: g.getBackgroundColor(),
            "setPixel": g.setPixel,
            "getPixel": g.getPixel,
            "fillRect": g.fillRect,
            # NOTE: getOrCreateImage/copyFrameBufferToImage are NOT exposed on
            # purpose. The game checks if they exist: if it gets a valid image,
            # it switches to its cached-screen path (which does not work yet)
            # and the intro disappears. With nil it uses the direct path, which
            # does draw. When the cache path is solved, expose them.
            "drawRect": g.drawRect,
            "fillRoundRect": g.fillRoundRect,
            "drawRoundRect": g.drawRoundRect,
            "drawLine": g.drawLine,
            "drawPolygon": g.drawPolygon,
            "fillPolygon": g.fillPolygon,
            "fillTriangle": g.fillTriangle,
            "drawTriangle": g.drawTriangle,
            "fillCircleInRect": g.fillCircleInRect,
            "drawCircleInRect": g.drawCircleInRect,
            "fillCircleAtPoint": g.fillCircleAtPoint,
            "drawCircleAtPoint": g.drawCircleAtPoint,
            "fillEllipseInRect": g.fillEllipseInRect,
            "drawEllipseInRect": g.drawEllipseInRect,
            "setDrawOffset": g.setDrawOffset,
            "setTranslate": g.setTranslate,
            "getTranslate": g.getTranslate,
            "translate": g.translate,
            "getDrawOffset": g.getDrawOffset,
            "setClipRect": g.setClipRect,
            "clearClipRect": g.clearClipRect,
            "pushContext": g.pushContext,
            "popContext": g.popContext,
            "lockFocus": g.lockFocus,
            "unlockFocus": g.unlockFocus,
            "setDitherPattern": g.setDitherPattern,
            "setImageDrawMode": g.setImageDrawMode,
            "getImageDrawMode": g.getImageDrawMode,
            "setPattern": g.setPattern,
            "setStencilImage": g.setStencilImage,
            "setStencilPattern": g.setStencilPattern,
            "clearStencil": g.clearStencil,
            "setLineWidth": g.setLineWidth,
            "getLineWidth": g.getLineWidth,
            "setLineCapStyle": g.setLineCapStyle,
            "getLineCapStyle": g.getLineCapStyle,
            "setStrokeLocation": g.setStrokeLocation,
            "getStrokeLocation": g.getStrokeLocation,
            "setFont": g.setFont,
            "getFont": g.getFont,
            "getTextWidth": g.getTextWidth,
            "getTextSize": g.getTextSize,
            "getSystemFont": lambda *a: self._make_font(),
            "drawText": g.drawText,
            "drawTextAligned": g.drawTextAligned,
            "drawCenteredText": g.drawCenteredText,
            "drawImage": g.drawImage,
            # object sub-tables (__id pattern, see pd/image.py)
            "image": self._image_table(),
            "imagetable": self._imagetable_table(),
            "font": self._font_table(),
            "sprite": self._sprite_table(),
            # constants (so the game does not get nil)
            # REAL SDK values (pd_api_gfx.h: enum LCDSolidColor
            # { kColorBlack=0, kColorWhite=1 }). They were inverted (1/0).
            "kColorBlack": 0,
            "kColorWhite": 1,
            "kColorClear": 0,
            "kColorXOR": 2,
            "kImageUnflipped": 0,
            "kImageFlippedX": 1,
            "kImageFlippedY": 2,
            "kImageFlippedXY": 3,
            "kDitherTypeBayer4x4": 0,
            "kLineCapStyleButt": 0, "kLineCapStyleRound": 1, "kLineCapStyleSquare": 2,
            "kStrokeCentered": 0, "kStrokeInside": 1, "kStrokeOutside": 2,
            "kPolygonFillNonZero": 0, "kPolygonFillEvenOdd": 1,
            "kDrawModeCopy": 0, "kDrawModeWhiteTransparent": 1,
            "kDrawModeBlackTransparent": 2, "kDrawModeFillWhite": 3,
            "kDrawModeFillBlack": 4, "kDrawModeXOR": 5, "kDrawModeNXOR": 6,
            "kDrawModeInverted": 7,
            "kAlignLeft": 0, "kAlignCenter": 1, "kAlignRight": 2,
            "kWrapWord": 0, "kWrapCharacter": 1, "kWrapClip": 2,
            "kVariantNormal": 0,
            "kVariantBold": 1,
        })
        gfx["getGraphics"] = lambda: gfx

        button = T({
            "isPressed": b.isPressed,
            "wasPressed": b.wasPressed,
            "up": b.up,
            "isPressedAny": b.isPressedAny,
            "wasPressedAny": b.wasPressedAny,
        })

        display = T({
            "width": pd.display.getWidth(),
            "height": pd.display.getHeight(),
            "getWidth": pd.display.getWidth,
            "getHeight": pd.display.getHeight,
            "getSize": lambda *a: (pd.display.getWidth(), pd.display.getHeight()),
            "getRect": lambda *a: (0, 0, pd.display.getWidth(), pd.display.getHeight()),
            "getScreenWidth": pd.display.getScreenWidth,
            "getScreenHeight": pd.display.getScreenHeight,
            "getRefreshRate": pd.display.getRefreshRate,
            "setRefreshRate": pd.display.setRefreshRate,
            "setInverted": pd.display.setInverted,
            "getInverted": pd.display.getInverted,
            "getScale": pd.display.getScale,
            "setScale": pd.display.setScale,
            # setOffset(x,y): shifts the WHOLE screen (ScreenShake uses it for
            # the shake). Applied as a global draw offset in graphics.
            "setOffset": lambda x=0, y=0, *a: g.setDisplayOffset(x, y),
            "getOffset": lambda *a: g.getDisplayOffset(),
        })

        api = T({
            "graphics": gfx,
            "button": button,
            "display": display,
            "getGraphics": lambda: gfx,
            "setUpdateCallback": pd.setUpdateCallback,
            "setDrawCallback": pd.setDrawCallback,
            "setCrankCallback": pd.setCrankCallback,
            "getButtonState": pd.getButtonState,
            "buttonIsPressedAny": pd.buttonIsPressedAny,
            "getCrankPosition": pd.getCrankPosition,
            "getCrankChange": pd.getCrankChange,
            "getCrankTicks": pd.getCrankTicks,
            "isCrankDocked": pd.isCrankDocked,
            "setCrankSoundsDisabled": pd.setCrankSoundsDisabled,
            "getTimer": pd.getTimer,
            "getTime": pd.getTime,
            "getCurrentTimeMilliseconds": pd.getCurrentTimeMilliseconds,
            "getSecondsSinceEpoch": pd.getSecondsSinceEpoch,
            "getElapsedTime": pd.getElapsedTime,
            "resetElapsedTime": pd.resetElapsedTime,
            "getFPS": pd.getFPS,
            "getSystemLanguage": lambda *a: "en",
            "getReduceFlashing": lambda *a: False,
            "getBatteryPercentage": lambda *a: 1.0,
            "getBatteryVoltage": lambda *a: 4.0,
            "getPowerStatus": lambda *a: 0,
            "shouldDisplay24HourTime": lambda *a: True,
            "inputHandlers": self._input_handlers_table(),
            "frameTimer": self.lua.table_from({
                "new": lambda *a: None,
                "performAfterDelay": lambda *a: None,
                "updateTimers": lambda *a: None,
                "allTimers": lambda *a: self.lua.table_from([]),
            }),
            "getElapsedTime": pd.getElapsedTime,
            "getFrame": pd.getFrame,
            "getGameName": pd.getGameName,
            "getSystemVersion": pd.getSystemVersion,
            "exit": pd.exit,
            # button constants (at ROOT level)
            "kButtonLeft": "left", "kButtonRight": "right",
            "kButtonUp": "up", "kButtonDown": "down",
            "kButtonA": "a", "kButtonB": "b",
            # direct button API (games use it a lot in update()).
            "buttonIsPressed": lambda b=None: self._btn_state(b, "pressed"),
            "buttonJustPressed": lambda b=None: self._btn_state(b, "justpressed"),
            "buttonJustReleased": lambda b=None: self._btn_state(b, "justreleased"),
            "drawFPS": lambda x=0, y=0: pd.drawFPS(x, y),
            "metadata": self.lua.table_from(self.meta or {}),
            "getSystemMenu": self._menu_table,
            "sound": self._sound_table(),
            "file": self._file_table(),
            "datastore": self._datastore_table(),
        })
        self.api = api
        return api

    # ------------------------------------------------------------------
    def _load_libs(self):
        """Runs the pure-Lua libraries of pd/lib/*.lua (they define __pd_* globals)."""
        if getattr(self, "_libs_loaded", False):
            return
        libdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
        if os.path.isdir(libdir):
            for fn in sorted(os.listdir(libdir)):
                if fn.endswith(".lua"):
                    with open(os.path.join(libdir, fn), encoding="utf-8") as f:
                        self.lua.execute(f.read())
        self._libs_loaded = True

    def _setup_globals(self, with_import=False):
        self._load_libs()
        g = self.lua.globals()
        api = self._build_api()
        # playdate.geometry is built in PYTHON (objects = userdata), not with
        # pd/lib/geometry.lua: CoreLibs checks `type(rect) == "userdata"` to
        # read x/y/width/height, and with Lua tables it skipped that branch.
        from .geometry import build_geometry, Polygon, AffineTransform

        api["geometry"] = build_geometry(self.lua.table_from)
        g["playdate"] = api
        # The geometry metamethods are installed AFTER publishing `playdate`:
        # the helper applies them with Lua (`debug.setmetatable` on the userdata),
        # and that Lua needs to resolve `playdate.geometry.polygon`.
        self._install_geometry_metamethods(api["geometry"], Polygon, AffineTransform)

        def _print(*args):
            # The game `print`s are its own (often author debug, one per
            # frame). The real console shows them; here they are behind
            # --verbose so they don't flood the output.
            if self.verbose:
                print("[lua]", *args)

        g["print"] = _print
        if with_import:
            g["import"] = self._make_import()

    # ------------------------------------------------------------------
    # Handles: Playdate "objects" (image, imagetable, font...) are Lua tables
    # with __id; the real state lives here (because of the lupa bug).
    def _register(self, obj):
        hid = self._next_id
        self._next_id += 1
        self._registry[hid] = obj
        return hid

    def _install_geometry_metamethods(self, geom, Polygon, AffineTransform):
        """Gives `__mul` to polygon/affineTransform keeping type()=="userdata".

        NOTE: lupa does NOT translate Python `__mul__` to Lua metamethods — a
        `poly * t` crashes with "attempt to perform arithmetic on a POBJECT
        value", which is exactly what the Asheteroids example does. The way
        that works is `debug.setmetatable` on the userdata with a Lua table:
        the object STAYS userdata (type() == "userdata", which is what CoreLibs
        checks) and Lua runs the metamethod, calling back into a Python
        function through the bridge.
        """
        lua = self.lua
        try:
            lua.globals()["__pd_poly_mul"] = lambda a, b: (
                a * b if isinstance(a, (Polygon, AffineTransform)) else b * a)
            lua.execute(LUA_GEOM_MT)
        except Exception as e:  # noqa: BLE001
            # Do NOT silence: if this fails, `poly * t` crashes far from here
            # with "attempt to perform arithmetic on a POBJECT value" and the
            # cause is lost.
            print(f"[geometry metamethods] {type(e).__name__}: {str(e)[:200]}")

    def _deref(self, tbl):
        """Lua handle -> Python object, robust inside COROUTINES.

        NOTE (root cause of "sprites don't draw"): reading `tbl["__id"]` from
        Python when the active Lua thread is a COROUTINE returns the pair
        `(<Lua thread>, id)` instead of the id. `int(tuple)` raises and this
        function returned None silently -> `image_draw` returned without
        drawing and the screen stayed blank, WITHOUT a single error. It only
        happened in games that call `playdate.graphics.sprite.update()` FROM
        their own `playdate.update()` (which runs in a coroutine); those that
        do it from draw never saw it. We extract the integer from the tuple if
        lupa wraps it that way.
        """
        try:
            v = tbl["__id"]
        except Exception:  # noqa: BLE001
            return None
        if isinstance(v, tuple):
            # lupa returns (thread, value) when reading inside a coroutine
            for item in reversed(v):
                if isinstance(item, int):
                    v = item
                    break
            else:
                return None
        try:
            return self._registry.get(int(v))
        except Exception:  # noqa: BLE001
            return None

    def _get_or_create_image(self, name=None, w=None, h=None, *a):
        """playdate.graphics.getOrCreateImage(name, w, h): cached image.

        The game uses it to avoid recreating images every frame (and as a
        screen canvas). It was missing entirely.
        """
        key = str(name) if name is not None else f"{w}x{h}"
        cache = getattr(self, "_image_cache", None)
        if cache is None:
            cache = self._image_cache = {}
        if key in cache:
            return cache[key]
        import pygame as _pg

        from .image import PDImage

        if w is None or h is None:                 # by name: load from the asset
            try:
                from .image import load_image

                img = load_image(self.playdate.graphics.asset_dir, key)
                handle = self._image_handle(img)
                cache[key] = handle
                return handle
            except Exception:  # noqa: BLE001
                return None
        surf = _pg.Surface((int(w), int(h)), _pg.SRCALPHA)
        surf.fill((0, 0, 0, 0))
        handle = self._image_handle(PDImage(surf))
        cache[key] = handle
        return handle

    def _audio_stub(self, kind="audio", *a):
        """A fake audio object that accepts ANY method.

        A real game (Kickflip Coast) uses the whole sequencer
        (`sequence.new`, `track.new`, `synth.new`, `seq:addTrack/play/stop/
        setTempo`, `track:addNote/setInstrument`, `synth:setADSR/playNote`...).
        Implementing it for real is an audio engine; what the game CANNOT
        tolerate is the constructor returning nil, because then it indexes nil
        and crashes (`attempt to index a nil value (upvalue 'seq')`) and stops
        drawing. We return a handle that answers everything with a no-op.

        The metatable is made IN LUA (from Python you only WRITE fields; reading
        a field with __index runs Lua code inside a lupa call and segfaults --
        see the golden rule in the skill).
        """
        hid = self._register(None)
        handle = self.lua.table_from({"__id": hid})
        try:
            return self.lua.eval("__pd_audio_stub")(handle)
        except Exception:
            return handle

    def _image_clear(self, handle, color=None):
        """image:clear([color]): empties the image.

        kColorClear (0) and no argument = TRANSPARENT (truly empty). An explicit
        color = opaque fill. Without this method, a game UI system
        `img:clear(...)` crashes and the drawing never reaches the image.
        """
        obj = self._deref(handle) if handle is not None else None
        surf = getattr(obj, "surface", None) if obj is not None else None
        if surf is None:
            return
        if color is None or color == 0:
            surf.fill((0, 0, 0, 0))
        else:
            surf.fill((0, 0, 0, 255))

    def _image_handle(self, img):
        from .image import PDImage

        hid = self._register(img)
        g = self.playdate.graphics
        w, h = img.getSize()
        return self.lua.table_from({
            "__id": hid,
            "width": w,
            "height": h,
            "getSize": lambda _h=None, *a: img.getSize(),
            "draw": lambda _h, x, y, *a: g.image_draw(_h, x, y, *a),
            "drawCentered": lambda _h, x, y, *a: g.image_draw_centered(_h, x, y, *a),
            # drawFaded(x, y, alpha, ditherType, flip): on a 1-bit screen
            # "translucent" can only be a DITHER. Without a full dithering
            # engine it is approximated in steps (solid / 50% checkerboard / none).
            "drawFaded": lambda _h, x, y, alpha=1.0, dt=None, flip=None:
                g.image_draw_faded(_h, x, y, alpha, flip),
            "drawAnchored": lambda _h, x, y, ax=0, ay=0, *a: g.image_draw_anchored(
                _h, x, y, ax, ay),
            # clear([color]): a game UI uses it to empty its cached image
            # before redrawing it. It was missing entirely.
            "clear": lambda _h=None, color=None, *a: self._image_clear(_h, color),
            "fill": lambda _h=None, color=None, *a: self._image_clear(_h, color),
            "drawScaledText": lambda _h, *a: None,
            "drawScaled": lambda _h, x, y, s, ys=None: g.image_draw_scaled(_h, x, y, s, ys),
            "drawRotated": lambda _h, x, y, an, sc=1, ys=None: g.image_draw_rotated(_h, x, y, an, sc, ys),
            "copy": lambda _h=None, *a: self._image_handle(PDImage(img.surface.copy())),
        })

    def _imagetable_handle(self, tbl):
        hid = self._register(tbl)
        g = self.playdate.graphics

        def _get_image(_h, a, b=None):
            im = tbl.getImage(a, b)
            return self._image_handle(im) if im is not None else None

        def _draw_image(_h, n, x, y, *a):
            im = tbl.getImage(n)
            if im is not None:
                g.image_draw(self._image_handle(im), x, y)

        h = self.lua.table_from({
            "__id": hid,
            "getLength": lambda _h=None, *a: tbl.getLength(),
            "getImage": _get_image,
            "drawImage": _draw_image,
        })
        # Numeric indexing alias: `t[7]` == `t:getImage(7)`. Games use it a lot
        # (FlippyFish: self.swimImages[fishAngle + 7*frame] to change the fish
        # frame). NOTE: the __index must be a LUA FUNCTION, not a Python
        # function: lupa does not route metamethod calls on Python userdata
        # (it gives "attribute name must be string").
        _apply = self.lua.eval("""
            function(h)
              setmetatable(h, { __index = function(t, k)
                if type(k) == 'number' then return t:getImage(math.tointeger(k)) end
                return nil
              end })
              return h
            end
        """)
        _apply(h)
        return h

    def _image_table(self):
        import pygame as _pg

        from .image import PDImage, load_image

        def _new(a=None, b=None, c=None, *rest):
            # The API is image.new(width, height, [bgcolor]): the third argument
            # (background color) is optional and a real game passes it -> without
            # accepting it, it crashes with "takes from 0 to 2 positional
            # arguments but 3 were given".
            try:
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    surf = _pg.Surface((int(a), int(b)), _pg.SRCALPHA)
                    # TRANSPARENT: on the console image.new() creates an empty
                    # (transparent) image until you draw on it. Filling it with
                    # opaque white made a blit of an "empty" image COVER everything
                    # already drawn (that is why the intro, which the game paints
                    # to the framebuffer, disappeared when blitting its empty buffer).
                    surf.fill((0, 0, 0, 0))
                    return self._image_handle(PDImage(surf))
                return self._image_handle(load_image(self.playdate.graphics.asset_dir, a))
            except Exception as e:  # noqa: BLE001
                # `image.new` returns nil when the asset does not exist and the
                # game usually handles it (it asks for extra frames, e.g.
                # explosion/9-11 over 8 real ones). Warning EVERY TIME floods the
                # console of a game that calls this every frame; it warns once per
                # asset and only in verbose mode.
                if self.verbose and a not in self._asset_warned:
                    self._asset_warned.add(a)
                    print(f"[image.new] {a!r}: {e}")
                return None

        def _new_from_rect(src, x, y, w, h):
            obj = self._deref(src)
            if obj is None:
                return None
            return self._image_handle(PDImage(obj.surface.subsurface((x, y, w, h)).copy()))

        return self.lua.table_from({
            "new": _new, "newFromRect": _new_from_rect,
            "kDitherTypeNone": 0, "kDitherTypeDiagonalLine": 1,
            "kDitherTypeVerticalLine": 2, "kDitherTypeHorizontalLine": 3,
            "kDitherTypeScreen": 4, "kDitherTypeBayer2x2": 5,
            "kDitherTypeBayer4x4": 6, "kDitherTypeBayer8x8": 7,
            "kDitherTypeFloydSteinberg": 8, "kDitherTypeBurkes": 9,
            "kDitherTypeAtkinson": 10,
        })

    def _imagetable_table(self):
        from .image import load_imagetable

        def _new(path=None, *a):
            # imagetable.new(path) | new(cellsWide, cellsHigh) | new(table):
            # we accept extra arguments so we don't crash (some games pass cells
            # in addition to the path).
            try:
                return self._imagetable_handle(
                    load_imagetable(self.playdate.graphics.asset_dir, path))
            except Exception as e:  # noqa: BLE001
                print(f"[imagetable.new] {path!r}: {e}")
                return None

        return self.lua.table_from({"new": _new})

    def _btn_name(self, b):
        """Normalizes a button: accepts 'a'/'A', or the old bitmask (kButtonA=16...).

        In the modern API `playdate.kButtonA` is the string "a"; older games
        pass the bitmask number, so both forms are accepted.
        """
        if isinstance(b, str):
            m = {"a": "A", "b": "B", "up": "Up", "down": "Down",
                 "left": "Left", "right": "Right"}
            return m.get(b.lower(), b)
        if isinstance(b, (int, float)):
            m = {16: "A", 32: "B", 4: "Up", 8: "Down", 1: "Left", 2: "Right"}
            return m.get(int(b))
        return None

    def _btn_state(self, b, kind):
        btn = self.playdate.button
        if b is None:                       # no argument = "any"
            from .input import BUTTONS

            if kind == "pressed":
                return btn.isPressedAny()
            if kind == "justpressed":
                return btn.wasPressedAny()
            return any(btn.up(n) for n in BUTTONS)
        name = self._btn_name(b)
        if name is None:
            return False
        if kind == "pressed":
            return btn.isPressed(name)
        if kind == "justpressed":
            return btn.wasPressed(name)
        return btn.up(name)

    def _make_font(self, pft_obj=None):
        """Font handle.

        With a `PDFont` (already-decoded .pft) it exposes REAL metrics and
        glyphs; without it, a stub with plausible numbers (the game does not
        break, but the metrics are fake). The PDFont is stored in the registry
        under `__id`, like images and sprites, so Graphics can retrieve it.
        """
        if pft_obj is None:
            return self.lua.table_from({
                "getHeight": lambda _h=None, *a: 16,
                "getTextWidth": lambda _h, t, *a: 8 * len(str(t)),
                "getTextSize": lambda _h, t, *a: (8 * len(str(t)), 16),
                "getLeading": lambda _h=None, *a: 0,
                "getTracking": lambda _h=None, *a: 0,
                "getGlyph": lambda _h=None, *a: None,
                "setLeading": lambda _h, *a: None,
                "setTracking": lambda _h, *a: None,
                # Games draw text with `font:drawText(...)` and
                # `font:drawTextAligned(...)` (Fishing Simulator uses the second
                # in a sprite callback). Without these methods, the callback dies
                # with "method 'drawTextAligned' is not callable".
                "drawText": lambda _h, text, x, y, *a: self.playdate.graphics.drawText(text, x, y),
                "drawTextAligned": lambda _h, text, x, y, align=0, *a:
                    self.playdate.graphics.drawTextAligned(text, x, y, align),
                "__id": self._register(None),
            })

        f = pft_obj
        hid = self._register(f)

        def _glyph(_h=None, cp=0, *a):
            gl = f.getGlyph(int(cp) if cp is not None else 0)
            if gl is None:
                return None
            img = PDImage(gl.surface)
            return self.lua.table_from({
                "advance": gl.advance,
                "width": gl.advance,
                "getAdvance": lambda *a: gl.advance,
                "getWidth": lambda *a: gl.advance,
                "image": self._image_handle(img),
                "getImage": lambda *a: self._image_handle(img),
                "getKerning": lambda _g=None, c=0: gl.kerning.get(int(c), 0),
            })

        return self.lua.table_from({
            "__id": hid,
            "getHeight": lambda _h=None, *a: f.getHeight(),
            "getLeading": lambda _h=None, *a: f.getLeading(),
            "getTracking": lambda _h=None, *a: f.getTracking(),
            "getTextWidth": lambda _h, t=None, *a: f.getTextWidth(t or ""),
            "getTextSize": lambda _h, t=None, *a: f.getTextSize(t or ""),
            "getGlyph": _glyph,
            "setLeading": lambda _h, *a: None,
            "setTracking": lambda _h, *a: None,
            # `font:drawText(...)` / `font:drawTextAligned(...)`: games draw
            # text with the font handle (Fishing Simulator uses drawTextAligned
            # in a sprite callback). Without this, the callback dies with
            # "method 'drawTextAligned' is not callable".
            "drawText": lambda _h, text, x, y, *a: self.playdate.graphics.drawText(text, x, y),
            "drawTextAligned": lambda _h, text, x, y, align=0, *a:
                self.playdate.graphics.drawTextAligned(text, x, y, align),
        })

    def _font_table(self):
        from .image import resolve_asset

        def _new(path=None, *a):
            name = str(path) if path is not None else ""
            for cand in (name, name + ".pft"):
                if not cand:
                    continue
                try:
                    real = resolve_asset(self.asset_dir, cand)
                except Exception:  # noqa: BLE001
                    real = None
                if real and os.path.exists(real):
                    try:
                        from .pft import load_font

                        return self._make_font(load_font(real))
                    except Exception as e:  # noqa: BLE001
                        print(f"[font.new] {name!r}: {type(e).__name__}: {e}")
                        break
            return self._make_font()          # stub if there is no .pft

        return self.lua.table_from({
            "new": _new,
            "kVariantNormal": 0, "kVariantBold": 1, "kVariantItalic": 2,
        })

    def _sprite_table(self):
        sysr = self.sprites
        T = self.lua.table_from

        def _new(image=None):
            obj = self._deref(image) if image is not None else None
            return self._sprite_handle(sysr.new(obj, image))

        def _all():
            return T([s.handle for s in sysr.all()])

        def _add_empty(x, y, w, h):
            s = sysr.new()
            s.setBounds(x, y, w, h)
            s.collide = (0, 0, int(w), int(h))
            return self._sprite_handle(s)

        def _new_with_text(text, *a):
            return _new(None)

        def _module_update(*a):
            # The module is called with NO arguments (the game sprite loop).
            # Some games also invoke `spr:update()` on sprites that do NOT
            # define their own update: the __index chain brings them here; with
            # a sprite as the first argument it is ignored (on the console that
            # sprite simply has no callback and does nothing).
            if a:
                return sysr.default_update(a[0])
            return sysr.update()

        # REAL SDK values (C_API/pd_api_sprite.h): Slide=0, Freeze=1,
        # Overlap=2, Bounce=3. Games read them (SCM: collisionResponse =
        # gfx.sprite.kCollisionTypeBounce); without this it was nil and they
        # did not bounce.
        return T({
            "new": _new,
            "update": _module_update,
            "kCollisionTypeSlide": 0,
            "kCollisionTypeFreeze": 1,
            "kCollisionTypeOverlap": 2,
            "kCollisionTypeBounce": 3,
            "getAllSprites": _all,
            "removeAll": sysr.remove_all,
            "setBackgroundDrawingCallback": lambda fn: setattr(sysr, "background_cb", fn),
            # setBackgroundColor: the color clear() fills the screen with. It is
            # a REAL API and the game calls it at startup; if it does not exist,
            # the call crashes and takes the initialization with it (and the
            # symptom is "the game runs but draws nothing").
            "setBackgroundColor": lambda c=None, *a: g.setBackgroundColor(c),
            "getBackgroundColor": lambda *a: g.getBackgroundColor(),
            # setAlwaysRedraw: sprites that always redraw, not only when marked
            # dirty. Our system already redraws them all; the flag is remembered.
            "setAlwaysRedraw": lambda flag=True, *a: setattr(sysr, "always_redraw", bool(flag)),
            "getAlwaysRedraw": lambda *a: bool(getattr(sysr, "always_redraw", False)),
            "redrawBackground": lambda *a: None,
            "addEmptyCollisionSprite": _add_empty,
            "addWallSprites": lambda *a: None,
            "update": self._api_sprite_update,
            "newSpriteWithText": _new_with_text,
        })

    def _spr_move_collisions(self, spr, x, y=None):
        """sprite:moveWithCollisions(x,y) -> actualX, actualY, collisions, numCollisions.

        The API returns FOUR values and games use the FOURTH as a loop limit:
        `local ax,ay,cols,n = spr:moveWithCollisions(x,y); for i=1,n do`.
        Returning three, n arrives nil and the update dies with
        "bad 'for' limit (number expected, got nil)". The third is a collision
        table, each with `other` (the hit sprite) and `type`.
        """
        ax, ay, cols = spr.moveWithCollisions(x, y)
        lua = self.lua
        t = lua.table() if lua is not None else None
        n = 0
        for item in (cols or []):
            # each collision is (other_sprite, normal_x, normal_y). The Lua table
            # carries `other` (the sprite handle), `normal` (a vector with x/y,
            # which games read to bounce) and `type`.
            try:
                other, nx, ny = item
            except Exception:  # noqa: BLE001
                other, nx, ny = item, 0.0, 0.0
            n += 1
            if t is None:
                continue
            try:
                t[n] = lua.table_from({
                    "other": getattr(other, "handle", None),
                    "normal": lua.table_from({"x": float(nx), "y": float(ny)}),
                    "type": 1,
                })
            except Exception:  # noqa: BLE001
                pass
        return (ax, ay, t, n)

    def _sprite_handle(self, spr):
        sysr = self.sprites
        hid = self._register(spr)

        def _move_to(_h, x, y=None):
            if y is not None:
                spr.moveTo(x, y)
            _sync_pos()

        def _set_collide(_h, x, y=None, w=None, h=None):
            spr.collide = (0, 0, int(x), int(y)) if w is None else (int(x), int(y), int(w), int(h))

        def _set_image(_h, im=None, flip=0, scale=1):
            obj = self._deref(im) if im is not None else None
            spr.setImage(obj, im, flip, scale)
            self._refresh_size(spr)

        def _init(_h=None, image=None, *a):
            """CoreLibs/sprites calls `o:init(image)` when creating the sprite.

            NOTE: the BUNDLE sprites.luac does `local o = newfunc()` (no image)
            and then `o:init(image)`. If init ignores the argument, the sprite
            stays WITHOUT an image and draws nothing -- that is how Fishing
            Simulator LOADING (a sprite with an image) disappeared.
            """
            if image is not None:
                obj = self._deref(image)
                spr.setImage(obj, image, 0, 1)
                self._refresh_size(spr)

        def _overlap(_h=None):
            return self.lua.table_from([s.handle for s in sysr.overlapping(spr)])

        t = self.lua.table_from({
                    "__id": hid,
                    # `x`/`y`: many games read `sprite.x`/`sprite.y` directly (e.g.
                    # Fishing Simulator does `sprites.hook.x - 3`). Without these
                    # fields, the value is nil and the game crashes with "attempt to
                    # perform arithmetic on a nil value (field 'x')". They are synced
                    # with the position.
                    "x": 0, "y": 0,
                    "init": _init,                        # CoreLibs/sprites invokes it on create
            "moveTo": _move_to,
            "moveBy": lambda _h, x, y: spr.moveBy(x, y),
            "setCenter": lambda _h, x, y: spr.setCenter(x, y),
            "getCenter": lambda _h=None, *a: spr.getCenter(),
            "getPosition": lambda _h=None, *a: spr.getPosition(),
            "setSize": lambda _h, w, h: (spr.setSize(w, h), self._refresh_size(spr)),
            "getSize": lambda _h=None, *a: spr.getSize(),
            "setBounds": lambda _h, x, y, w, h: (spr.setBounds(x, y, w, h),
                                                 self._refresh_size(spr)),
            "getBounds": lambda _h=None, *a: spr.getBounds(),
            "getBoundsRect": lambda _h=None, *a: spr.getBounds(),
            "setZIndex": lambda _h, z: setattr(spr, "z", int(z)),
            "getZIndex": lambda _h=None, *a: spr.z,
            "setVisible": lambda _h, v: setattr(spr, "visible", bool(v)),
            "isVisible": lambda _h=None, *a: spr.visible,
            "setUpdatesEnabled": lambda _h, v: setattr(spr, "updates_enabled", bool(v)),
            "updatesEnabled": lambda _h=None, *a: spr.updates_enabled,
            "setIgnoresDrawOffset": lambda _h, v: setattr(spr, "ignores_offset", bool(v)),
            "setImageFlip": lambda _h, f, *a: setattr(spr, "flip", int(f)),
            "getImageFlip": lambda _h=None, *a: spr.flip,
            "setRotation": lambda _h, a, s=1, ys=None: self._spr_set_rotation(spr, a, s, ys),
            "getRotation": lambda _h=None, *a: spr.rotation,
            "setScale": lambda _h, s, ys=None: setattr(spr, "scale", s),
            "getScale": lambda _h=None, *a: spr.scale,
            "setTag": lambda _h, v: setattr(spr, "tag", v),
            "getTag": lambda _h=None, *a: spr.tag,
            "setOpaque": lambda _h, v: setattr(spr, "opaque", bool(v)),
            "isOpaque": lambda _h=None, *a: spr.opaque,
            "markDirty": lambda _h, *a: None,
            "add": lambda _h=None, *a: sysr.add(spr),
            "remove": lambda _h=None, *a: sysr.remove(spr),
            # LEGACY API: the SDK example games (FlippyFish) call
            # `sprite:addSprite()` / `sprite:removeSprite()`. In the real SDK they
            # live on the module table (`playdate.graphics.sprite.addSprite(s)`),
            # which is the SAME table as the class, so `s:addSprite()` resolves
            # them as methods. Without these aliases the game dies at load with
            # "method 'addSprite' is not callable".
            "addSprite": lambda _h=None, *a: sysr.add(spr),
            "removeSprite": lambda _h=None, *a: sysr.remove(spr),
            "setImage": _set_image,
            "getImage": lambda _h=None, *a: spr.image_handle,
            "setCollideRect": _set_collide,
            "getCollideRect": lambda _h=None, *a: spr.collide,
            "clearCollideRect": lambda _h=None, *a: setattr(spr, "collide", None),
            "setCollisionsEnabled": lambda _h, v: setattr(spr, "collisions_enabled", bool(v)),
            "collisionsEnabled": lambda _h=None, *a: spr.collisions_enabled,
            "moveWithCollisions": lambda _h, x, y=None: self._spr_move_collisions(spr, x, y),
            "checkCollisions": lambda _h=None, *a: bool(sysr.overlapping(spr)),
            "overlappingSprites": _overlap,
            "setTilemap": lambda _h, tm: setattr(spr, "tilemap", tm),
            "setGroups": lambda _h, g: setattr(spr, "group_mask", _groups_to_mask(g)),
            # Collision masks. `setGroups({n})` sets bit n-1 (same as
            # setGroupMask(1<<(n-1))); `setCollidesWithGroups({n})` the same for
            # "collides with". Without `setGroupMask` a layered game dies at load
            # with "method 'setGroupMask' is not callable".
            "setGroupMask": lambda _h, m: setattr(spr, "group_mask", int(m or 0)),
            "getGroupMask": lambda _h=None, *a: getattr(spr, "group_mask", 0),
            "setCollidesWithGroups": lambda _h, g: setattr(spr, "collides_mask", _groups_to_mask(g)),
            "setCollidesWithGroupsMask": lambda _h, m: setattr(spr, "collides_mask", int(m or 0)),
            "getCollidesWithGroupsMask": lambda _h=None, *a: getattr(spr, "collides_mask", 0),
            "resetGroupMask": lambda _h=None, *a: setattr(spr, "group_mask", 0),
            "resetCollidesWithGroupsMask": lambda _h=None, *a: setattr(spr, "collides_mask", 0),
            "getCollideBounds": lambda _h=None, *a: (spr.collide_rect_at(spr.pos[0], spr.pos[1])
                                                     or (0, 0, 0, 0)),
            "setCollisionResponse": lambda h, fn: setattr(h, "collisionResponse", fn),
            "collisionResponse": None,
            "alphaCollision": lambda h, other: sysr.alpha_collide(spr, self._deref(other)),
            "setImageDrawMode": lambda _h, m: None,
            "getImageDrawMode": lambda _h=None, *a: 0,
            "setDrawMode": lambda _h, m: None,
            "setClipRect": lambda _h, *a: None,
            "clearClipRect": lambda _h: None,
            "setStencilImage": lambda _h, *a: None,
            "setStencilPattern": lambda _h, *a: None,
            "clearStencil": lambda _h: None,
            "setUpdateCallback": lambda _h, fn: None,
        })

        # `spr:update()` must exist on EVERY sprite (CoreLibs calls it every
        # frame if it exists; Smolitaire invokes it explicitly on plain
        # sprites). BEFORE it was set as a raw key of the handle and COVERED the
        # game Fish:update (FlippyFish: the fish never fell). Now it goes on
        # the metatable __index: CLASS sprites have their own metatable
        # (class() puts it on the handle and overrides this one), so their
        # update wins; plain ones fall here and get the default no-op.
        _apply_mt = self.lua.eval("""
            function(h, fallback)
              if getmetatable(h) == nil then
                setmetatable(h, { __index = function(_, k)
                  if k == 'update' then return fallback end
                  return nil
                end })
              end
              return h
            end
        """)
        _apply_mt(t, sysr.default_update)
        # Syncs the handle x/y with the sprite position (games read
        # `sprite.x`/`sprite.y` directly).
        def _sync_pos():
            try:
                px, py = spr.getPosition()
                t["x"] = px
                t["y"] = py
            except Exception:  # noqa: BLE001
                pass
        _sync_pos()
        # `width`/`height` as REAL FIELDS (not via metatable). Before, a
        # metatable __index resolved them, and that is a trap: ANY read of a
        # missing field from Python ran Lua code -> reentry into lupa -> SIGSEGV.
        # They are refreshed in _refresh_size when the image/size changes.
        w0, h0 = spr.getSize()
        t["width"] = w0
        t["height"] = h0
        spr.handle = t
        return t

    @staticmethod
    def _spr_set_rotation(spr, angle, scale=1, yscale=None):
        spr.rotation = angle
        if scale:
            spr.scale = scale

    def _menu_table(self):
        """playdate.getSystemMenu() — system menu (functional stub).

        The items the game registers are stored in `self.menu_items` so they can
        be inspected (and so the menu can really be shown).
        """
        T = self.lua.table_from
        # Cumulative: the game may call getSystemMenu() several times; the items
        # must accumulate so they can be inspected.
        if getattr(self, "menu_items", None) is None:
            self.menu_items = []
        items = self.menu_items

        def _mk(title, kind="item"):
            it = T({
                "__title": title,
                "__kind": kind,
                "getTitle": lambda _h=None, *a: title,
                "setTitle": lambda _h=None, t=None, *a: None,
                "getValue": lambda _h=None, *a: None,
                "setValue": lambda _h=None, v=None, *a: None,
                "setCallback": lambda _h=None, cb=None, *a: None,
                "setCheckmark": lambda _h=None, v=None, *a: None,
            })
            items.append(it)
            return it

        return T({
            "addMenuItem": lambda *a: _mk(a[0] if a else "", "item"),
            "addCheckmarkMenuItem": lambda *a: _mk(a[0] if a else "", "checkmark"),
            "addOptionsMenuItem": lambda *a: _mk(a[0] if a else "", "options"),
            "getMenuItems": lambda *a: T(items),
            "removeMenuItem": lambda *a: None,
            "removeAllMenuItems": lambda *a: items.clear(),
        })

    def _sound_table(self):
        """playdate.sound — USERDATA objects (see pd/sound.py), not tables."""
        from .sound import Channel, Instrument, SoundPlayer, Synth, Sequence, Track
        from .sound import get_current_time as _sound_get_current_time

        T = self.lua.table_from
        adir = lambda: getattr(self.playdate.graphics, "asset_dir", None)

        def _sp(*a):
            # sampleplayer/fileplayer/sample: the constructor receives the path
            # and the asset_dir to resolve the real .pda.
            return SoundPlayer(*(a + (adir(),)))

        return T({
            "sampleplayer": T({"new": _sp}),
            "fileplayer": T({"new": _sp}),
            "sample": T({"new": _sp}),
            "synth": T({"new": Synth}),
            "instrument": T({"new": Instrument}),
            "channel": T({"new": Channel}),
            # Synth waveform constants (SDK: pd_api_sound.h, enum SoundWaveform:
            # Square=0, Triangle=1, Sine=2, Noise=3, Sawtooth=4). Games pass
            # these to synth.new()/synth:setWaveform(); Synth._osc interprets the
            # same numbering.
            "kWaveSquare": 0,
            "kWaveTriangle": 1,
            "kWaveSine": 2,
            "kWaveNoise": 3,
            "kWaveSawtooth": 4,
            "effect": T({"new": lambda *a: None}),
            "source": T({"new": lambda *a: None}),
            "sequence": T({"new": Sequence}),
            "track": T({"new": Track}),
            "controlsignal": T({"new": self._audio_stub}),
            "lfo": T({"new": self._audio_stub}),
            "envelope": T({"new": self._audio_stub}),
            "twoPoleFilter": T({"new": self._audio_stub}),
            "onePoleFilter": T({"new": self._audio_stub}),
            "bitcrusher": T({"new": self._audio_stub}),
            "ringmod": T({"new": self._audio_stub}),
            "delayline": T({"new": self._audio_stub}),
            "overdrive": T({"new": self._audio_stub}),
            "playFile": lambda *a: None,
            "playTone": lambda *a: None,
            "setVolume": lambda *a: None,
            "getVolume": lambda: 1.0,
            "stopAll": lambda: None,
            "getCurrentTime": lambda *a: _sound_get_current_time(),
            "setOutputsActive": lambda *a: None,
        })

    def _file_table(self):
        from .filestore import FileSystem

        fs = self.fs if self.fs is not None else FileSystem(None)
        return self.lua.table_from({
            "open": fs.open,
            "exists": fs.exists,
            "isdir": fs.isdir,
            "listFiles": fs.listFiles,
            "mkdir": fs.mkdir,
            "delete": fs.delete,
            "getSize": fs.getSize,
            "modtime": fs.modtime,
            "getType": fs.getType,
            "rename": fs.rename,
            "kFileRead": 1, "kFileWrite": 2, "kFileAppend": 3,
            "kSeekSet": 0, "kSeekFromCurrent": 1, "kSeekFromEnd": 2,
        })

    def _to_lua(self, v, depth=0):
        """Python dict/list -> Lua table, recursive.

        Needed because the datastore is stored as JSON and `json.loads` returns
        Python dict/list, which Lua does NOT see as tables: the game tries to
        index them and fails with "attempt to index a string value" (or sees
        them as userdata).
        """
        if depth > 12 or v is None:
            return None
        if isinstance(v, dict):
            return self.lua.table_from(
                {k: self._to_lua(x, depth + 1) for k, x in v.items()})
        if isinstance(v, (list, tuple)):
            return self.lua.table_from([self._to_lua(x, depth + 1) for x in v])
        return v

    def _datastore_table(self):
        from .filestore import DataStore, FileSystem

        # the datastore IS a separate writable sandbox
        ds = DataStore(FileSystem(os.path.join(self.asset_dir or ".", ".pd_data")))

        def _read(filename="data", *a):
            return self._to_lua(ds.read(filename))

        return self.lua.table_from({
            "read": _read,
            "write": ds.write,
            "delete": ds.delete,
            "writeImage": lambda *a: False,
            "readImage": lambda *a: None,
        })

    def _restore_bg(self):
        pass # Deprecated: the background is now cleared in _sprite_update_once

    def _api_sprite_update(self, *a):
        """`playdate.graphics.sprite.update()` called BY THE GAME.

        Many games invoke it themselves at the end of their update. If we also
        call it in `call_update`, the sprites are drawn TWICE per frame: the
        first at their old position and the second at the new one, which
        produces "thick" letters and a trail where they pass. We set the flag
        so it is not repeated.
        """
        self._sprite_updated = True
        return self._sprite_update_once()

    def _sprite_update_once(self):
        """playdate.graphics.sprite.update(): updates and draws the sprites.

        Idempotent per frame: the docs recommend the game call it from its
        update(), and the system also calls it; without this guard the sprites
        would update and draw twice per frame.
        """
        f = self.emu.frame
        if self._sprite_frame == f:
            return
        self._sprite_frame = f
        # Per the official Playdate SDK, when calling `playdate.graphics.sprite.update()`:
        # "First, the background is drawn. If a background drawing callback is provided,
        # the sprite system calls it to draw the background. Otherwise, the sprite system
        # clears the background to the color specified by setBackgroundColor()."
        # Since our emulator does not use "dirty rects" (it redraws the whole screen
        # every frame), we simply fill the whole canvas with the background color or
        # call the callback, BEFORE the sprites are drawn.
        # This removes the need for the "snapshot" hack, which left drawings made
        # directly to the canvas stuck from earlier states (e.g. the Play button
        # in FlippyFish intro).
        cv = self.playdate.graphics._canvas
        bg_cb = getattr(self.sprites, "background_cb", None)
        if bg_cb:
            try:
                bg_cb(0, 0, cv.get_width(), cv.get_height())
            except Exception as e:
                print(f"[background_cb error] {e}")
        else:
            bg_color = self.playdate.graphics.getBackgroundColor()
            if bg_color is None:
                bg_color = 1 # White default
            c = (255, 255, 255) if bg_color == 1 else (0, 0, 0)
            cv.fill(c)

        self.sprites.update()

    def _input_handlers_table(self):
        """playdate.inputHandlers: stack of input handlers.

        CoreLibs registers a handler here with methods AButtonDown/upButtonHeld/
        cranked... and the runtime dispatches them every frame.
        """

        def _push(h, *a):
            self._input_stack.append(h)

        def _pop(*a):
            if self._input_stack:
                self._input_stack.pop()

        return self.lua.table_from({"push": _push, "pop": _pop})

    def _refresh_size(self, spr):
        """Updates the handle width/height after setImage/setSize."""
        h = getattr(spr, "handle", None)
        if h is None:
            return
        try:
            w, hh = spr.getSize()
            h["width"] = w          # writing IS safe (does not trigger __index)
            h["height"] = hh
        except Exception:  # noqa: BLE001
            pass

    def _dispatch_to_globals(self):
        """Dispatch to the globals `playdate.AButtonDown` etc. (no handler)."""
        b = self.playdate.button
        try:
            self.lua.eval("__pd_dispatch_input")(
                None,
                lambda name: b.isPressed(self._btn_name(name) or name),
                lambda name: b.wasPressed(self._btn_name(name) or name),
                lambda name: b.up(self._btn_name(name) or name),
            )
        except Exception:  # noqa: BLE001
            pass

    def _dispatch_input(self):
        """Carries the button state to the top handler (Playdate style).

        The walk is done in Lua (`__pd_dispatch_input`): reading the handler
        from Python would trigger its __index metatable in the middle of a lupa
        call and segfault. Here we only pass it three state queries.
        """
        if not self._input_stack:
            # Empty stack: do NOT return. The console then falls back to the
            # game globals (`playdate.AButtonDown`), which is how many games
            # read input without using inputHandlers.
            self._dispatch_to_globals()
            return
        b = self.playdate.button
        try:
            self.lua.eval("__pd_dispatch_input")(
                self._input_stack[-1],
                lambda name: b.isPressed(self._btn_name(name) or name),
                lambda name: b.wasPressed(self._btn_name(name) or name),
                lambda name: b.up(self._btn_name(name) or name),
            )
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Loading: accepts a source-code folder, a .pdx folder or a .pdz file
    def load(self, source):
        source = os.path.abspath(source)
        if os.path.isfile(source) and source.endswith(".pdz"):
            return self._load_pdx(source)
        if os.path.isdir(source) and (
            source.endswith(".pdx") or os.path.exists(os.path.join(source, "main.pdz"))
        ):
            return self._load_pdx(source)
        if os.path.isdir(source):
            return self._load_source(source)
        raise SystemExit(f"does not exist or is not a valid game: {source}")

    # --- source mode (main.lua) ----------------------------------------
    def _load_source(self, game_dir):
        from .filestore import FileSystem

        self.asset_dir = game_dir
        self.fs = FileSystem(game_dir)
        self._setup_globals(with_import=False)
        if self.on_ready:
            self.on_ready(self)
        self.emu.game_dir = game_dir
        self.lua.execute(
            f'package.path = {repr(os.path.join(game_dir, "?.lua"))} .. ";" .. package.path'
        )
        with open(os.path.join(game_dir, "main.lua"), "r", encoding="utf-8") as f:
            self.lua.execute(f.read())
        self._resolve_timers()

    # --- compiled mode (.pdx / .pdz) ----------------------------------
    def _load_pdx(self, path):
        if not HAS_PLAYDATE_LUA:
            raise SystemExit(
                "This game is COMPILED (.pdx/.pdz) and the system lupa "
                "(standard Lua) CANNOT load Playdate bytecode.\n"
                "`vendor/lupa` is missing: run tools/build_lua32.sh (see README)."
            )

        from . import pdx as pdx_mod

        info = pdx_mod.read_pdx(path)
        self.chunks = {}
        for _rel, parsed in info["pdz"].items():
            for e in parsed["entries"]:
                if e["type"] == 1:                      # 1 = compiled Lua bytecode
                    self.chunks[e["name"]] = e["data"]

        # root to resolve assets (.pdi/.pft/.pda) by name without extension
        self.asset_dir = path if os.path.isdir(path) else os.path.dirname(path)
        self.playdate.graphics.asset_dir = self.asset_dir
        self.meta = info.get("meta", {})
        from .filestore import FileSystem
        self.fs = FileSystem(self.asset_dir)      # playdate.file reads from the BUNDLE
        if info.get("meta", {}).get("name"):
            self.emu.game_name = info["meta"]["name"]

        self._setup_globals(with_import=True)
        if self.on_ready:
            self.on_ready(self)
        self.emu.game_dir = self.asset_dir

        if "main" not in self.chunks:
            raise SystemExit(f"the .pdx does not ship the 'main' chunk (chunks: {sorted(self.chunks)})")
        self._run_chunk("main")
        self._ensure_firmware_corelibs()
        self._resolve_timers()

    # CoreLibs that the FIRMWARE puts on the console: they always exist, even
    # if the .pdx does not import them. What to check is NOT whether they exist
    # (our stubs also exist) but whether they are REAL: Lua functions are
    # "function" and our Python ones come out as "userdata". If the module is a
    # stub, the SDK CoreLib is compiled. Smolitaire bundle does not ship
    # frameTimer, so the game frameTimers NEVER fired and its intro kept
    # waiting for a timer that never came.
    FIRMWARE_CORELIBS = (
        ("timer", "playdate.timer.updateTimers"),
        ("frameTimer", "playdate.frameTimer.updateTimers"),
        ("easing", "playdate.easingFunctions.linear"),
    )

    def _ensure_firmware_corelibs(self):
        """Replaces the stubs with the SDK system CoreLibs."""
        loaded = []
        for name, probe in self.FIRMWARE_CORELIBS:
            try:
                ok = self.lua.eval(f'type({probe}) == "function"')
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                continue                    # it is already the real CoreLib
            src = _sdk_corelib_source("CoreLibs/" + name)
            if not src:
                continue
            try:
                self.lua.execute(src)
                loaded.append(name)
            except Exception as exc:  # noqa: BLE001
                print(f"[corelibs] {name}: {exc}")
        if loaded:
            print(f"[corelibs] cargados del SDK: {', '.join(loaded)}")

    # --- helpers de chunks ---------------------------------------------
    def _run_chunk(self, name):
        f = self._pd_load(self.chunks[name], "@" + name)
        f()

    def _make_import(self):
        """Playdate: `import "CoreLibs/graphics"` loads the .pdz chunk ONCE."""
        loaded = set()

        def _do_import(name, *rest):
            n = str(name).lstrip("/")
            if n.endswith(".lua"):
                n = n[:-4]
            if n in loaded:
                return
            loaded.add(n)
            if n in self.chunks:
                self._run_chunk(n)
                return
            # CORELIBS LIVE IN THE CONSOLE FIRMWARE, not in the .pdx: a game
            # does `import "CoreLibs/nineslice"` and on the console it works
            # even if the bundle does not ship that file (Smolitaire only ships
            # animator/crank/easing/graphics/object/sprites/string/timer).
            # Without this fallback the import failed SILENTLY and the game was
            # left missing pieces: Smolitaire menu is a dialog2 window that
            # uses nineslice, so the menu simply did not exist.
            src = _sdk_corelib_source(n)
            if src is not None:
                self.lua.execute(src)      # the .lua source is compiled on the fly
                return
            print(f"[import] no encontrado en el .pdz: {name}")

        return _do_import

    # ------------------------------------------------------------------
    # Invokes the callbacks. Supports BOTH styles:
    #   playdate.setUpdateCallback(fn)   and   function playdate.update() end
    def _resolve_timers(self):
        """Caches the CoreLibs updaters (they exist after importing them)."""
        try:
            self._timer_update = self.lua.eval(
                "playdate.timer and playdate.timer.updateTimers or nil")
            self._frametimer_update = self.lua.eval(
                "playdate.frameTimer and playdate.frameTimer.updateTimers or nil")
            # gfx.sprite.update() moves AND DRAWS the sprites: without this the
            # game creates sprites that never appear on screen (the docs say:
            # "call gfx.sprite.update() to draw sprites").
            self._sprite_update = self.lua.eval(
                "playdate.graphics and playdate.graphics.sprite "
                "and playdate.graphics.sprite.update or nil")
        except Exception:  # noqa: BLE001
            self._timer_update = None
            self._frametimer_update = None
            self._sprite_update = None
        self._sprite_frame = -1

    def call_update(self):
        self._sprite_updated = False   # new frame: no one has drawn sprites yet
        self._restore_bg()
        self._dispatch_input()
        if self._timer_update:
            self._timer_update()
        if self._frametimer_update:
            self._frametimer_update()
        cb = self.playdate._update_cb
        if cb is None:
            cb = self.api["update"]
        if not cb:
            return
        # playdate.update() runs INSIDE A COROUTINE (official docs). It is
        # resumed with __pd_run_update, which keeps the coroutine on the Lua
        # side (lupa cannot pass it back and forth).
        self.lua.eval("__pd_run_update")(cb)
        # The sprites move and DRAW only when the GAME calls
        # playdate.graphics.sprite.update() during its update (that is how the
        # console does it: if the game does not call it, the sprite system
        # draws nor clears nothing and the canvas keeps what was drawn by
        # hand). Auto-calling it when the game did not use it erased the direct
        # drawing of games like Kickflip Coast splash every frame (blank
        # screen).

    def call_draw(self):
        cb = self.playdate._draw_cb
        if cb is None:
            cb = self.api["draw"]
        if cb:
            cb()

    def call_crank(self, delta, accelerated=0):
        """playdate.cranked(delta, accelerated): the console reports once per frame.

        Games register this as a GLOBAL (`function playdate.cranked(...)`), not
        with a setter, so the global must be called.
        """
        if self.playdate._crank_cb:
            self.playdate._crank_cb(delta, accelerated)
        try:
            self.lua.eval("__pd_call_crank")(delta, accelerated)
        except Exception:  # noqa: BLE001
            pass
