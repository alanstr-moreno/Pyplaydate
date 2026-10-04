"""runtime.py — puente Lua <-> Python.

Dos notas importantes:

1) BUG DE LUPA EVITADO: lupa 2.8 + Lua 5.5 + Python 3.14 corrompe el acceso a
   ATRIBUTOS de objetos Python ANIDADOS desde Lua tras ~77 lecturas (vimos
   `playdate.button.isPressed` devolviendo `Graphics.drawText`). Por eso NO
   exponemos objetos Python anidados: la API se construye como TABLAS Lua
   (`rt.table_from`) con funciones Python en las hojas.

2) BYTECODE COMPILADO: un juego real es un `.pdx` con `main.pdz` (bytecode de
   Lua 5.4.3 con LUA_32BITS=1). Solo el lupa de `vendor/lupa` puede cargarlo
   (ver pd/luavm.py). En ese modo definimos `import(name)` que carga los chunks
   del propio `.pdz`, igual que hace Playdate.

PATRON PARA AGREGAR UNA API:
  1) implementa el metodo en la clase Python correspondiente (Graphics, Button...),
  2) agregalo al dict de `_build_api()` con su nombre Playdate.
"""

import os
import time

from . import graphics as gfx_mod
from . import input as input_mod
from .luavm import HAS_PLAYDATE_LUA, LUA_KIND, lupa  # noqa: F401  (lupa reexportado)


def _groups_to_mask(groups):
    """`setGroups({2,3})` -> mascara de bits (el grupo N ocupa el bit N-1).

    Definido aqui (nivel de modulo) porque las lambdas de `_build_api` lo
    resuelven como global: un import dentro de un metodo no les llega.
    El ejemplo oficial lo confirma: `setGroups({2})` == `setGroupMask(2)` y
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

    # OJO: desde Lua `setGroups({2, 3})` llega una TABLA LUA, no una list/tuple de
    # Python. Comprobar solo list/tuple dejaba la mascara en 0 en silencio, y un
    # juego de capas dejaba de colisionar con los muros (los sprites se salian
    # de la pantalla). Hay que aceptar cualquier iterable (incluida la tabla Lua).
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
    """playdate.display (implementacion Python)."""

    def __init__(self, screen):
        self._screen = screen
        self._refresh = 30

    def getWidth(self):
        return self._screen.width

    def getHeight(self):
        return self._screen.height

    def setInverted(self, flag):
        """playdate.display.setInverted(flag): dibuja el framebuffer invertido."""
        self._screen.setInverted(flag)

    def getInverted(self):
        return self._screen.getInverted()

    def getScale(self):
        return getattr(self._screen, "pixel_scale", 1)

    def setScale(self, s):
        """playdate.display.setScale(n): solo se admiten 1, 2, 4 y 8."""
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
    """Estado e implementacion del objeto `playdate` (lado Python)."""

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

    # --- tiempo / frame -------------------------------------------------
    def getTimer(self):
        return time.time() - self._t0

    def getElapsedTime(self):
        return time.time() - self._t0

    def resetElapsedTime(self):
        self._t0 = time.time()

    def getTime(self):
        # SEGUNDOS desde el arranque (asi lo define la API: "seconds since the app
        # started"). Antes devolvia milisegundos -- factor 1000 de error, un juego
        # que anime con getTime() iria mil veces rapido. Sigue siendo float: un
        # epoch (1.7e12) no cabe ni en lua_Integer (32 bits) ni en lua_Number.
        return time.time() - self._t0

    def getCurrentTimeMilliseconds(self):
        return int((time.time() - self._t0) * 1000.0)

    def getSecondsSinceEpoch(self):
        """Segundos desde el epoch Y milisegundos (DOS valores).

        La API devuelve `seconds, milliseconds` y los juegos usan AMBOS: un juego
        real hace `local s, ms = playdate.getSecondsSinceEpoch();
        math.randomseed(ms, s)`. Devolviendo solo los segundos, el segundo valor
        llega nil y `math.randomseed` revienta en la CARGA
        ("bad argument #1 to 'randomseed' (number expected, got nil)").
        """
        now = time.time()
        return (int(now), int((now - int(now)) * 1000))

    def getFPS(self):
        return self._emu.fps

    def drawFPS(self, x=0, y=0):
        """playdate.drawFPS(x, y): pinta el contador de FPS (lo usan los juegos)."""
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

    # --- callbacks del juego -------------------------------------------
    def setUpdateCallback(self, fn):
        self._update_cb = fn

    def setDrawCallback(self, fn):
        self._draw_cb = fn

    def getCrankPosition(self):
        """Angulo de la manivela (grados). El teclado la mueve con las teclas de
        la manivela; por defecto 0."""
        return getattr(self._emu, "crank_angle", 0.0)

    def getCrankChange(self):
        """Cambio de angulo desde la ultima llamada (es un "delta acumulado")."""
        cur = self.getCrankPosition()
        prev = getattr(self, "_crank_last", cur)
        self._crank_last = cur
        return cur - prev

    def getCrankTicks(self, ticks=1):
        """Clics de la manivela; aproximo con los saltos de 360/ticks grados."""
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
        """Bitmask de botones pulsados (API clasica, aun en uso).

        Bits: kButtonLeft=1, kButtonRight=2, kButtonUp=4, kButtonDown=8,
        kButtonA=16, kButtonB=32. Sin esto, un juego que la llame se queda con
        nil y su logica de input puede atascarse.
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
        """True si hay algun boton pulsado (usado por algun CoreLib/juego)."""
        try:
            return self.playdate.button.isPressedAny()
        except Exception:  # noqa: BLE001
            return False

    def setCrankCallback(self, fn):
        self._crank_cb = fn

    # --- control del emulador (util para dev) ---------------------------
    def exit(self):
        self._emu.running = False


SDK_CORELIBS_DEFAULT = "/Users/mac/Developer/PlaydateSDK/CoreLibs"

# El SDK usa el DIALECTO Lua de Playdate, que anade operadores de asignacion
# compuesta (`a -= 1`, `t.x += y`, `t[i] *= 2`). El Lua de lupa es 5.4 estandar,
# asi que hay que expandirlos. frameTimer.lua:182 es `timer.remainingDelay -= 1`
# y sin esto no compila ("syntax error near '-'").
_COMPOUND_RE = None


def _expand_compound_assign(src):
    """Expande `lhs op= rhs` a `lhs = lhs op (rhs)` (dialecto Playdate)."""
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
            # descarta comparaciones (==, <=, >=, ~=) y lineas raras
            if lhs.strip() and "=" not in lhs and not lhs.endswith(
                    ("<", ">", "~", "+", "-", "*", "/", "%", "=")):
                out.append(f"{ind}{lhs} = {lhs} {op} ({rhs}){comment}")
                continue
        out.append(line)
    return "\n".join(out)


def _sdk_corelib_source(name):
    """Codigo fuente de un CoreLib del SDK instalado, o None si no existe.

    Los CoreLibs son parte de la consola, no del juego: se resuelven aqui para
    que `import "CoreLibs/nineslice"` funcione aunque el .pdx no lo traiga.
    """
    import os

    # `name` llega como "CoreLibs/nineslice"; el fichero es <base>/nineslice.lua.
    rel = name[len("CoreLibs/"):] if name.startswith("CoreLibs/") else name
    if ".." in rel or rel.startswith("/"):
        return None                       # nada de salirse del directorio
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
        self.verbose = False          # --verbose: prints del juego y avisos de assets
        self._asset_warned = set()   # assets ya avisados (no repetir)
        self.emu = emulator
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(_LOAD_HELPER)
        self._pd_load = self.lua.globals()["__pd_load"]
        self.playdate = Playdate(emulator)
        self.playdate.graphics.deref = self._deref   # handle Lua -> objeto Python
        from .sprite import SpriteSystem
        self.sprites = SpriteSystem(self.playdate.graphics, self.lua)
        self.api = None
        self.chunks = None          # modo .pdx: nombre -> bytes de bytecode
        self.asset_dir = None
        self.on_ready = None        # hook opcional: se llama antes de correr el juego
        self._registry = {}         # id -> objeto Python (imagenes, imagetables, fuentes)
        self._next_id = 1
        self.meta = {}              # pdxinfo del juego
        self.fs = None              # sandbox de archivos (playdate.file/datastore)
        self._input_stack = []      # playdate.inputHandlers
        self._timer_update = None   # playdate.timer.updateTimers (de CoreLibs)
        self._frametimer_update = None
        self._updater = None        # corrutina persistente de playdate.update

    # ------------------------------------------------------------------
    def _build_api(self):
        """Construye el global `playdate` como TABLAS Lua (ver nota 1 arriba)."""
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
            # setBackgroundColor: color con el que `clear()` rellena la pantalla.
            # La API real la expone en `playdate.graphics` (NO solo en
            # `graphics.sprite`), y los juegos la llaman al arrancar:
            # FlippyFish hace `gfx.setBackgroundColor(gfx.kColorWhite)` en su
            # primera linea. Si falta, la carga muere con
            # "field 'setBackgroundColor' is not callable".
            "setBackgroundColor": lambda c=None, *a: g.setBackgroundColor(c),
            "getBackgroundColor": lambda *a: g.getBackgroundColor(),
            "setPixel": g.setPixel,
            "getPixel": g.getPixel,
            "fillRect": g.fillRect,
            # OJO: getOrCreateImage/copyFrameBufferToImage NO se exponen a proposito.
            # El juego comprueba si existen: si recibe una imagen valida, se pasa a
            # su ruta de pantalla cacheada (que aun no funciona) y la intro
            # desaparece. Con nil usa la ruta directa, que si dibuja. Cuando la
            # ruta de cache este resuelta, se exponen.
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
            # sub-tablas de objetos (patron __id, ver pd/image.py)
            "image": self._image_table(),
            "imagetable": self._imagetable_table(),
            "font": self._font_table(),
            "sprite": self._sprite_table(),
            # constantes (para que el juego no reciba nil)
            # Valores REALES del SDK (pd_api_gfx.h: enum LCDSolidColor
            # { kColorBlack=0, kColorWhite=1 }). Estaban invertidos (1/0).
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
            # setOffset(x,y): desplaza TODA la pantalla (lo usa ScreenShake para
            # el temblor). Se aplica como offset de dibujo global en graphics.
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
            # constantes de boton (a NIVEL RAIZ)
            "kButtonLeft": "left", "kButtonRight": "right",
            "kButtonUp": "up", "kButtonDown": "down",
            "kButtonA": "a", "kButtonB": "b",
            # API de botones directa (los juegos la usan mucho en update()).
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
        """Ejecuta las librerias Lua puras de pd/lib/*.lua (definen globals __pd_*)."""
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
        # playdate.geometry se construye en PYTHON (objetos = userdata), no con
        # pd/lib/geometry.lua: CoreLibs comprueba `type(rect) == "userdata"` para
        # leer x/y/width/height, y con tablas Lua se saltaba esa rama.
        from .geometry import build_geometry, Polygon, AffineTransform

        api["geometry"] = build_geometry(self.lua.table_from)
        g["playdate"] = api
        # Los metametodos de geometry se instalan DESPUES de publicar `playdate`:
        # el helper los aplica con Lua (`debug.setmetatable` sobre el userdata),
        # y ese Lua necesita resolver `playdate.geometry.polygon`.
        self._install_geometry_metamethods(api["geometry"], Polygon, AffineTransform)

        def _print(*args):
            # Los `print` del juego son suyos (a menudo debug del autor, uno por
            # frame). La consola real los muestra; aqui estan detras de --verbose
            # para que no inunden la salida.
            if self.verbose:
                print("[lua]", *args)

        g["print"] = _print
        if with_import:
            g["import"] = self._make_import()

    # ------------------------------------------------------------------
    # Handles: los "objetos" de Playdate (image, imagetable, font...) son
    # tablas Lua con __id; el estado real vive aqui (por el bug de lupa).
    def _register(self, obj):
        hid = self._next_id
        self._next_id += 1
        self._registry[hid] = obj
        return hid

    def _install_geometry_metamethods(self, geom, Polygon, AffineTransform):
        """Da `__mul` a polygon/affineTransform manteniendo type()=="userdata".

        OJO: lupa NO traduce los `__mul__` de Python a metametodos Lua — un
        `poly * t` revienta con "attempt to perform arithmetic on a POBJECT
        value", que es justo lo que hace el ejemplo Asheteroids. La via que si
        funciona es `debug.setmetatable` sobre el userdata con una tabla Lua:
        el objeto SIGUE siendo userdata (type() == "userdata", que es lo que
        CoreLibs comprueba) y el metametodo lo ejecuta Lua, llamando de vuelta
        a una funcion Python por el puente.
        """
        lua = self.lua
        try:
            lua.globals()["__pd_poly_mul"] = lambda a, b: (
                a * b if isinstance(a, (Polygon, AffineTransform)) else b * a)
            lua.execute(LUA_GEOM_MT)
        except Exception as e:  # noqa: BLE001
            # NO silenciar: si esto falla, `poly * t` revienta lejos de aqui con
            # "attempt to perform arithmetic on a POBJECT value" y se pierde la causa.
            print(f"[geometry metamethods] {type(e).__name__}: {str(e)[:200]}")

    def _deref(self, tbl):
        """handle Lua -> objeto Python, robusto dentro de CORRUTINAS.

        OJO (causa raiz de "los sprites no se dibujan"): leer `tbl["__id"]` desde
        Python cuando el thread Lua activo es una CORRUTINA devuelve el par
        `(<Lua thread>, id)` en vez del id. `int(tupla)` lanza y esta funcion
        devolvia None en silencio -> `image_draw` salia sin pintar y la pantalla
        quedaba en blanco, SIN un solo error. Solo pasaba en juegos que llaman
        `playdate.graphics.sprite.update()` DESDE su propio `playdate.update()`
        (que corre en corrutina); los que lo hacen desde draw no lo veian.
        Extraemos el entero de la tupla si lupa la envuelve asi.
        """
        try:
            v = tbl["__id"]
        except Exception:  # noqa: BLE001
            return None
        if isinstance(v, tuple):
            # lupa devuelve (thread, valor) al leer dentro de una corrutina
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
        """playdate.graphics.getOrCreateImage(name, w, h): imagen del cache.

        El juego la usa para no recrear imagenes cada frame (y como lienzo de
        pantalla). Faltaba por completo.
        """
        key = str(name) if name is not None else f"{w}x{h}"
        cache = getattr(self, "_image_cache", None)
        if cache is None:
            cache = self._image_cache = {}
        if key in cache:
            return cache[key]
        import pygame as _pg

        from .image import PDImage

        if w is None or h is None:                 # por nombre: carga del asset
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
        """Objeto de audio de mentira que acepta CUALQUIER metodo.

        Un juego real (Kickflip Coast) usa el secuenciador entero
        (`sequence.new`, `track.new`, `synth.new`, `seq:addTrack/play/stop/
        setTempo`, `track:addNote/setInstrument`, `synth:setADSR/playNote`...).
        Implementarlo de verdad es un motor de audio; lo que el juego NO puede
        tolerar es que el constructor devuelva nil, porque entonces indexa nil y
        revienta (`attempt to index a nil value (upvalue 'seq')`) y se queda sin
        dibujar. Devolvemos un handle que responde a todo con un no-op.

        Se hace el metatable EN LUA (desde Python solo se ESCRIBEN campos; leer
        un campo con __index dispara codigo Lua dentro de una llamada de lupa y
        segfaultea -- ver la regla de oro en la skill).
        """
        hid = self._register(None)
        handle = self.lua.table_from({"__id": hid})
        try:
            return self.lua.eval("__pd_audio_stub")(handle)
        except Exception:
            return handle

    def _image_clear(self, handle, color=None):
        """image:clear([color]): vacia la imagen.

        kColorClear (0) y sin argumento = TRANSPARENTE (vacio de verdad). Un
        color explicito = relleno opaco. Sin este metodo, el `img:clear(...)`
        del sistema UI de un juego revienta y el dibujado no llega a la imagen.
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
            # drawFaded(x, y, alpha, ditherType, flip): en una pantalla de 1 bit
            # "translucido" solo puede ser un TRAMA. Sin motor de dithering
            # completo se aproxima por tramos (solido / 50% en damero / nada).
            "drawFaded": lambda _h, x, y, alpha=1.0, dt=None, flip=None:
                g.image_draw_faded(_h, x, y, alpha, flip),
            "drawAnchored": lambda _h, x, y, ax=0, ay=0, *a: g.image_draw_anchored(
                _h, x, y, ax, ay),
            # clear([color]): el UI de los juegos lo usa para vaciar su imagen
            # cacheada antes de redibujarla. Faltaba por completo.
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
        # Alias de indexado numerico: `t[7]` == `t:getImage(7)`. Los juegos lo
        # usan muchisimo (FlippyFish: self.swimImages[fishAngle + 7*frame] para
        # cambiar de cuadro del pez). OJO: el __index tiene que ser una FUNCION
        # LUA, no una funcion Python: lupa no enruta las llamadas de metametodo
        # sobre userdata Python (da "attribute name must be string").
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
            # La API es image.new(width, height, [bgcolor]): el tercer argumento
            # (color de fondo) es opcional y un juego real lo pasa -> sin aceptarlo
            # revienta con "takes from 0 to 2 positional arguments but 3 were given".
            try:
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    surf = _pg.Surface((int(a), int(b)), _pg.SRCALPHA)
                    # TRANSPARENTE: en la consola image.new() crea una imagen vacia
                    # (transparente) hasta que dibujas en ella. Rellenarla de blanco
                    # opaco hacia que un blit de una imagen "vacia" TAPARA todo lo ya
                    # dibujado (por eso la intro, que el juego pinta al framebuffer,
                    # desaparecia al blitear su buffer vacio).
                    surf.fill((0, 0, 0, 0))
                    return self._image_handle(PDImage(surf))
                return self._image_handle(load_image(self.playdate.graphics.asset_dir, a))
            except Exception as e:  # noqa: BLE001
                # `image.new` devuelve nil cuando el asset no existe y el juego
                # suele manejarlo (pide frames de mas, p.ej. explosion/9-11 sobre
                # 8 reales). Avisar CADA VEZ inunda la consola de un juego que
                # llama esto cada frame; se avisa una vez por asset y solo en
                # modo verbose.
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
            # aceptamos argumentos de mas para no reventar (algunos juegos pasan
            # celdas ademas de la ruta).
            try:
                return self._imagetable_handle(
                    load_imagetable(self.playdate.graphics.asset_dir, path))
            except Exception as e:  # noqa: BLE001
                print(f"[imagetable.new] {path!r}: {e}")
                return None

        return self.lua.table_from({"new": _new})

    def _btn_name(self, b):
        """Normaliza un boton: acepta 'a'/'A', o el bitmask antiguo (kButtonA=16...).

        En la API moderna `playdate.kButtonA` es el string "a"; los juegos mas
        viejos pasan el numero del bitmask, asi que se aceptan las dos formas.
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
        if b is None:                       # sin argumento = "cualquiera"
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
        """Handle de fuente.

        Con un `PDFont` (.pft ya decodificado) expone metricas y glyphs REALES;
        sin el, un stub con numeros plausibles (el juego no se rompe, pero las
        metricas son falsas). El PDFont se guarda en el registro bajo `__id`,
        igual que imagenes y sprites, para que Graphics pueda recuperarlo.
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
                # Los juegos dibujan texto con `font:drawText(...)` y
                # `font:drawTextAligned(...)` (Fishing Simulator usa el segundo en
                # un callback de sprite). Sin estos metodos, el callback muere con
                # "method 'drawTextAligned' is not callable".
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
            # `font:drawText(...)` / `font:drawTextAligned(...)`: los juegos dibujan
            # texto con el handle de fuente (Fishing Simulator usa drawTextAligned
            # en un callback de sprite). Sin esto, el callback muere con
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
            return self._make_font()          # stub si no hay .pft

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
            # El modulo se llama SIN argumentos (bucle de sprites del juego).
            # Algunos juegos, ademas, invocan `spr:update()` en sprites que NO
            # definen update propio: la cadena __index los trae hasta aqui; con
            # un sprite como primer argumento se ignora (en la consola ese
            # sprite simplemente no tiene callback y no hace nada).
            if a:
                return sysr.default_update(a[0])
            return sysr.update()

        # Valores REALES del SDK (C_API/pd_api_sprite.h): Slide=0, Freeze=1,
        # Overlap=2, Bounce=3. Los juegos los leen (SCM: collisionResponse =
        # gfx.sprite.kCollisionTypeBounce); sin esto era nil y no rebotaban.
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
            # setBackgroundColor: color con el que clear() rellena la pantalla. Es
            # una API REAL y el juego la llama al arrancar; si no existe, la llamada
            # revienta y se lleva por delante la inicializacion (y el sintoma es
            # "el juego corre pero no pinta nada").
            "setBackgroundColor": lambda c=None, *a: g.setBackgroundColor(c),
            "getBackgroundColor": lambda *a: g.getBackgroundColor(),
            # setAlwaysRedraw: sprites que se repintan siempre, no solo al marcarse
            # sucios. Nuestro sistema ya los repinta todos; se recuerda la bandera.
            "setAlwaysRedraw": lambda flag=True, *a: setattr(sysr, "always_redraw", bool(flag)),
            "getAlwaysRedraw": lambda *a: bool(getattr(sysr, "always_redraw", False)),
            "redrawBackground": lambda *a: None,
            "addEmptyCollisionSprite": _add_empty,
            "addWallSprites": lambda *a: None,
            "update": self._api_sprite_update,
            "newSpriteWithText": _new_with_text,
        })

    def _spr_move_collisions(self, spr, x, y=None):
        """sprite:moveWithCollisions(x,y) -> actualX, actualY, colisiones, numColisiones.

        La API devuelve CUATRO valores y los juegos usan el CUARTO como limite de
        un bucle: `local ax,ay,cols,n = spr:moveWithCollisions(x,y); for i=1,n do`.
        Devolviendo tres, n llega nil y el update muere con
        "bad 'for' limit (number expected, got nil)". El tercero es una tabla de
        colisiones, cada una con `other` (el sprite chocado) y `type`.
        """
        ax, ay, cols = spr.moveWithCollisions(x, y)
        lua = self.lua
        t = lua.table() if lua is not None else None
        n = 0
        for item in (cols or []):
            # cada colision es (otro_sprite, normal_x, normal_y). La tabla Lua
            # lleva `other` (el handle del sprite), `normal` (vector con x/y, que
            # los juegos leen para rebotar) y `type`.
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
            """CoreLibs/sprites llama `o:init(imagen)` al crear el sprite.

            OJO: el sprites.luac del BUNDLE hace `local o = newfunc()` (sin imagen)
            y despues `o:init(imagen)`. Si init ignora el argumento, el sprite se
            queda SIN imagen y no dibuja nada -- asi desaparecia el "LOADING" de
            Fishing Simulator (un sprite con imagen).
            """
            if image is not None:
                obj = self._deref(image)
                spr.setImage(obj, image, 0, 1)
                self._refresh_size(spr)

        def _overlap(_h=None):
            return self.lua.table_from([s.handle for s in sysr.overlapping(spr)])

        t = self.lua.table_from({
                    "__id": hid,
                    # `x`/`y`: muchos juegos leen `sprite.x`/`sprite.y` directamente (p.ej.
                    # Fishing Simulator hace `sprites.hook.x - 3`). Sin estos campos, el
                    # valor es nil y el juego revienta con "attempt to perform arithmetic
                    # on a nil value (field 'x')". Se sincronizan con la posicion.
                    "x": 0, "y": 0,
                    "init": _init,                        # CoreLibs/sprites lo invoca al crear
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
            # API LEGACY: los juegos de los ejemplos del SDK (FlippyFish) llaman
            # `sprite:addSprite()` / `sprite:removeSprite()`. En el SDK real viven
            # en la tabla del modulo (`playdate.graphics.sprite.addSprite(s)`), que
            # es la MISMA tabla que la clase, asi que `s:addSprite()` las resuelve
            # como metodo. Sin estos alias el juego muere en la carga con
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
            # Mascaras de colision. `setGroups({n})` marca el bit n-1 (igual que
            # setGroupMask(1<<(n-1))); `setCollidesWithGroups({n})` lo mismo para
            # "choca con". Sin `setGroupMask` un juego de capas muere en la carga
            # con "method 'setGroupMask' is not callable".
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

        # `spr:update()` debe existir en TODO sprite (CoreLibs lo llama cada
        # frame si existe; Smolitaire lo invoca explicitamente en sprites
        # planos). ANTES se ponfa como clave raw del handle y TAPABA el
        # `Fish:update` del juego (FlippyFish: el pez no caia nunca). Ahora va
        # en el __index del metatable: los sprites de CLASE tienen su propio
        # metatable (class() lo pone sobre el handle y pisa este), asi que su
        # update manda; los planos caen aqui y reciben el default no-op.
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
        # Sincroniza `x`/`y` del handle con la posicion del sprite (los juegos
        # leen `sprite.x`/`sprite.y` directamente).
        def _sync_pos():
            try:
                px, py = spr.getPosition()
                t["x"] = px
                t["y"] = py
            except Exception:  # noqa: BLE001
                pass
        _sync_pos()
        # `width`/`height` como CAMPOS REALES (no por metatable). Antes los
        # resolvia un metatable __index, y eso es un cebo: CUALQUIER lectura de un
        # campo ausente desde Python disparaba codigo Lua -> reentrada en lupa ->
        # SIGSEGV. Se refrescan en _refresh_size cuando cambia imagen/tamano.
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
        """playdate.getSystemMenu() — menu del sistema (stub funcional).

        Los items que registra el juego se guardan en `self.menu_items` para poder
        inspeccionarlos (y para que el menu se pueda mostrar de verdad).
        """
        T = self.lua.table_from
        # Acumulativo: el juego puede llamar a getSystemMenu() varias veces; los
        # items deben acumularse para poder inspeccionarlos.
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
        """playdate.sound — objetos USERDATA (ver pd/sound.py), no tablas."""
        from .sound import Channel, Instrument, SoundPlayer, Synth
        from .sound import get_current_time as _sound_get_current_time

        T = self.lua.table_from
        adir = lambda: getattr(self.playdate.graphics, "asset_dir", None)

        def _sp(*a):
            # sampleplayer/fileplayer/sample: el constructor recibe la ruta y el
            # asset_dir para resolver el .pda real.
            return SoundPlayer(*(a + (adir(),)))

        return T({
            "sampleplayer": T({"new": _sp}),
            "fileplayer": T({"new": _sp}),
            "sample": T({"new": _sp}),
            "synth": T({"new": Synth}),
            "instrument": T({"new": Instrument}),
            "channel": T({"new": Channel}),
            "effect": T({"new": lambda *a: None}),
            "source": T({"new": lambda *a: None}),
            "sequence": T({"new": self._audio_stub}),
            "track": T({"new": self._audio_stub}),
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
        """dict/list de Python -> tabla Lua, recursivo.

        Necesario porque el datastore se guarda como JSON y `json.loads` devuelve
        dict/list de Python, que Lua NO ve como tablas: el juego intenta indexarlos
        y falla con "attempt to index a string value" (o los ve como userdata).
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

        # el datastore SI es un sandbox escribible aparte
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
        pass # Obsoleto: el fondo ahora se limpia en _sprite_update_once

    def _api_sprite_update(self, *a):
        """`playdate.graphics.sprite.update()` llamado POR EL JUEGO.

        Muchos juegos lo invocan ellos mismos al final de su update. Si ademas
        lo llamamos nosotros en `call_update`, los sprites se dibujan DOS veces
        por frame: la primera en su posicion vieja y la segunda en la nueva, lo
        que produce letras "gruesas" y una estela por donde pasan. Marcamos la
        bandera para no repetirlo.
        """
        self._sprite_updated = True
        return self._sprite_update_once()

    def _sprite_update_once(self):
        """playdate.graphics.sprite.update(): actualiza y dibuja los sprites.

        Idempotente por frame: la doc recomienda al juego llamarlo desde su
        update(), y el sistema tambien lo llama; sin esta guardia los sprites se
        actualizarian y dibujarian dos veces por frame.
        """
        f = self.emu.frame
        if self._sprite_frame == f:
            return
        self._sprite_frame = f
        # Segun el SDK oficial de Playdate, al llamar `playdate.graphics.sprite.update()`:
        # "First, the background is drawn. If a background drawing callback is provided,
        # the sprite system calls it to draw the background. Otherwise, the sprite system
        # clears the background to the color specified by setBackgroundColor()."
        # Dado que nuestro emulador no usa "dirty rects" (repinta toda la pantalla cada
        # frame), simplemente rellenamos el canvas entero con el color de fondo o
        # llamamos al callback, ANTES de que los sprites se dibujen.
        # Esto elimina la necesidad del hack del "snapshot", que dejaba pegados
        # dibujos hechos directamente al canvas en estados anteriores (ej: el boton
        # Play en la intro de FlippyFish).
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
        """playdate.inputHandlers: pila de handlers de input.

        CoreLibs registra aqui un handler con metodos AButtonDown/upButtonHeld/
        cranked... y el runtime se los despacha cada frame.
        """

        def _push(h, *a):
            self._input_stack.append(h)

        def _pop(*a):
            if self._input_stack:
                self._input_stack.pop()

        return self.lua.table_from({"push": _push, "pop": _pop})

    def _refresh_size(self, spr):
        """Actualiza `width`/`height` del handle tras setImage/setSize."""
        h = getattr(spr, "handle", None)
        if h is None:
            return
        try:
            w, hh = spr.getSize()
            h["width"] = w          # escribir SI es seguro (no dispara __index)
            h["height"] = hh
        except Exception:  # noqa: BLE001
            pass

    def _dispatch_to_globals(self):
        """Despacho a los globales `playdate.AButtonDown` etc. (sin handler)."""
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
        """Lleva el estado de botones al handler de arriba (estilo Playdate).

        El recorrido lo hace Lua (`__pd_dispatch_input`): leer el handler desde
        Python dispararia su metatable __index en mitad de una llamada de lupa y
        segfaultea. Aqui solo se le pasan tres consultas de estado.
        """
        if not self._input_stack:
            # Pila vacia: NO se sale. La consola cae entonces a los globales del
            # juego (`playdate.AButtonDown`), que es como muchos juegos leen el
            # input sin usar inputHandlers.
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
    # Carga: acepta carpeta de codigo fuente, carpeta .pdx o archivo .pdz
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
        raise SystemExit(f"no existe o no es un juego valido: {source}")

    # --- modo fuente (main.lua) ----------------------------------------
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

    # --- modo compilado (.pdx / .pdz) ----------------------------------
    def _load_pdx(self, path):
        if not HAS_PLAYDATE_LUA:
            raise SystemExit(
                "Este juego esta COMPILADO (.pdx/.pdz) y el lupa del sistema "
                "(Lua estandar) NO puede cargar bytecode de Playdate.\n"
                "Falta `vendor/lupa`: corre tools/build_lua32.sh (ver README)."
            )

        from . import pdx as pdx_mod

        info = pdx_mod.read_pdx(path)
        self.chunks = {}
        for _rel, parsed in info["pdz"].items():
            for e in parsed["entries"]:
                if e["type"] == 1:                      # 1 = compiled Lua bytecode
                    self.chunks[e["name"]] = e["data"]

        # raiz para resolver assets (.pdi/.pft/.pda) por nombre sin extension
        self.asset_dir = path if os.path.isdir(path) else os.path.dirname(path)
        self.playdate.graphics.asset_dir = self.asset_dir
        self.meta = info.get("meta", {})
        from .filestore import FileSystem
        self.fs = FileSystem(self.asset_dir)      # playdate.file lee del BUNDLE
        if info.get("meta", {}).get("name"):
            self.emu.game_name = info["meta"]["name"]

        self._setup_globals(with_import=True)
        if self.on_ready:
            self.on_ready(self)
        self.emu.game_dir = self.asset_dir

        if "main" not in self.chunks:
            raise SystemExit(f"el .pdx no trae el chunk 'main' (chunks: {sorted(self.chunks)})")
        self._run_chunk("main")
        self._ensure_firmware_corelibs()
        self._resolve_timers()

    # CoreLibs que en la consola pone el FIRMWARE: existen siempre, aunque el
    # .pdx no los importe. Lo que hay que mirar NO es si existen (nuestros stubs
    # tambien existen) sino si son de VERDAD: las funciones de Lua son "function"
    # y las nuestras de Python salen como "userdata". Si el modulo es un stub, se
    # compila el CoreLib del SDK. El bundle de Smolitaire no trae frameTimer, asi
    # que los frameTimer del juego no disparaban NUNCA y su intro se quedaba
    # esperando un timer que no llegaba.
    FIRMWARE_CORELIBS = (
        ("timer", "playdate.timer.updateTimers"),
        ("frameTimer", "playdate.frameTimer.updateTimers"),
        ("easing", "playdate.easingFunctions.linear"),
    )

    def _ensure_firmware_corelibs(self):
        """Sustituye los stubs por los CoreLibs de sistema del SDK."""
        loaded = []
        for name, probe in self.FIRMWARE_CORELIBS:
            try:
                ok = self.lua.eval(f'type({probe}) == "function"')
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                continue                    # ya es el CoreLib de verdad
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
        """Playdate: `import "CoreLibs/graphics"` carga el chunk del .pdz UNA sola vez."""
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
            # LOS CORELIBS VAN EN EL FIRMWARE DE LA CONSOLA, no en el .pdx: un
            # juego hace `import "CoreLibs/nineslice"` y en la consola funciona
            # aunque el bundle no traiga ese fichero (el de Smolitaire solo trae
            # animator/crank/easing/graphics/object/sprites/string/timer). Sin
            # este respaldo el import fallaba en SILENCIO y el juego se quedaba
            # sin piezas: el menu de Smolitaire es una ventana dialog2 que usa
            # nineslice, asi que el menu simplemente no existia.
            src = _sdk_corelib_source(n)
            if src is not None:
                self.lua.execute(src)      # se compila la fuente .lua al vuelo
                return
            print(f"[import] no encontrado en el .pdz: {name}")

        return _do_import

    # ------------------------------------------------------------------
    # Invoca los callbacks. Soporta los DOS estilos:
    #   playdate.setUpdateCallback(fn)   y   function playdate.update() end
    def _resolve_timers(self):
        """Cachea los actualizadores de CoreLibs (existen tras importarlos)."""
        try:
            self._timer_update = self.lua.eval(
                "playdate.timer and playdate.timer.updateTimers or nil")
            self._frametimer_update = self.lua.eval(
                "playdate.frameTimer and playdate.frameTimer.updateTimers or nil")
            # gfx.sprite.update() mueve Y DIBUJA los sprites: sin esto el juego
            # crea sprites que nunca aparecen en pantalla (lo dice la doc:
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
        self._sprite_updated = False   # nuevo frame: nadie ha dibujado sprites aun
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
        # playdate.update() corre DENTRO DE UNA CORRUTINA (doc oficial). Se
        # reanuda con __pd_run_update, que mantiene la corrutina del lado Lua
        # (lupa no puede pasarla de ida y vuelta).
        self.lua.eval("__pd_run_update")(cb)
        # Los sprites se mueven y DIBUJAN solo cuando el JUEGO llama a
        # playdate.graphics.sprite.update() durante su update (asf lo hace la
        # consola: si el juego no la llama, el sistema de sprites no pinta ni
        # borra nada y el canvas conserva lo dibujado a mano). Autollamarla
        # cuando el juego no la usaba borraba cada frame el dibujo directo de
        # juegos como el splash de Kickflip Coast (pantalla blanca).

    def call_draw(self):
        cb = self.playdate._draw_cb
        if cb is None:
            cb = self.api["draw"]
        if cb:
            cb()

    def call_crank(self, delta, accelerated=0):
        """playdate.cranked(delta, accelerated): la consola avisa una vez por frame.

        Los juegos registran esto como GLOBAL (`function playdate.cranked(...)`),
        no con un setter, asi que hay que llamar al global.
        """
        if self.playdate._crank_cb:
            self.playdate._crank_cb(delta, accelerated)
        try:
            self.lua.eval("__pd_call_crank")(delta, accelerated)
        except Exception:  # noqa: BLE001
            pass
