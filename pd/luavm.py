"""luavm.py — selecciona el interprete Lua para el emulador.

El `lupa` del sistema usa Lua 5.5 estandar, que NO puede cargar el bytecode de
Playdate (es Lua 5.4.3 con LUA_32BITS=1 -> rechaza con "lua_Integer size
mismatch"). En `vendor/lupa` vive un lupa compilado contra una Lua 5.4.3 con
LUA_32BITS=1, que SI carga ese bytecode.

Si `vendor/lupa` existe -> se usa (modo compilado habilitado).
Si no -> se cae al lupa del sistema (solo sirve para juegos en codigo fuente).

Receta del build: tools/build_lua32.sh
"""

import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENDOR = os.path.join(_HERE, "vendor")

if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

try:
    import lupa.lua as lupa          # build propio: Lua 5.4 + LUA_32BITS
    HAS_PLAYDATE_LUA = True
    LUA_KIND = "vendor (Lua %d.%d, LUA_32BITS)" % tuple(lupa.LUA_VERSION)
except Exception:                     # noqa: BLE001
    import lupa                      # type: ignore
    HAS_PLAYDATE_LUA = False
    LUA_KIND = "sistema (Lua %s, estandar)" % (getattr(lupa, "LUA_VERSION", "?"),)
