"""api_stub_audit.py — auditoria de cobertura contra la API REAL de Playdate.

El SDK trae `CoreLibs/__stub.lua`, que declara las ~914 funciones de la API
completa (`function playdate.graphics.image:drawAnchored(...) end`). Eso es un
mapa definitivo: se extraen todas las rutas y se comprueba en Lua cuales existen
en nuestro runtime.

La comprobacion se hace EN LUA a proposito: leer tablas Lua desde Python dispara
metatables y puede segfaultear lupa (ver la skill).

Uso:  python3 tools/api_stub_audit.py [--missing] [--group]
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STUB = "/Users/mac/Developer/PlaydateSDK/CoreLibs/__stub.lua"


def stub_paths():
    """Extrae las rutas de la API declaradas en __stub.lua."""
    txt = open(STUB, encoding="utf-8", errors="ignore").read()
    out = []
    for m in re.finditer(r"^function\s+(playdate[\w.:]*)\s*\(", txt, re.M):
        path = m.group(1).replace(":", ".").rstrip(".")
        out.append(path)
    return sorted(set(out))


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("game", help="juego a cargar (asi se cargan tambien sus CoreLibs)")
    ap.add_argument("--missing", action="store_true", help="solo listar lo que falta")
    args = ap.parse_args()

    import pygame  # noqa: E402

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    pygame.init()
    pygame.display.init()
    pygame.display.set_mode((900, 600))

    from pd.emulator import Emulator  # noqa: E402
    from pd.runtime import Runtime  # noqa: E402

    emu = Emulator(args.game, headless=True)
    rt = Runtime(emu)
    rt.load(emu._resolve_game_dir())

    paths = stub_paths()
    print(f"API declarada en __stub.lua: {len(paths)} funciones")

    # Comprobacion en Lua, en un solo viaje. Cada ruta va en un pcall: si un
    # modulo intermedio no existe (p.ej. playdate.geometry.affineTransform),
    # indexarlo directamente seria "attempt to index a nil value".
    #
    # OJO: las funciones de API implementadas en PYTHON las ve Lua como
    # `userdata` (lupa las envuelve asi), no como `function` -- pero SI son
    # invocables. Comprobar `type(v) ~= "function"` da ~870 falsos negativos.
    # Hay que mirar tambien el metametodo __call.
    lua_lines = [
        "local miss = {}",
        "local function callable(v)",
        "  if type(v) == 'function' then return true end",
        "  if type(v) == 'userdata' or type(v) == 'table' then",
        "    local ok, mt = pcall(getmetatable, v)",
        "    if ok and mt and mt.__call ~= nil then return true end",
        "  end",
        "  return false",
        "end",
    ]
    for p in paths:
        lua_lines.append(
            f'do local ok, v = pcall(function() return {p} end); '
            f'if not ok or not callable(v) then miss[#miss+1] = "{p}" end end')
    lua_lines.append('return table.concat(miss, "\\n")')
    missing = rt.lua.execute("\n".join(lua_lines))
    missing = [m for m in (missing or "").split("\n") if m]

    have = len(paths) - len(missing)
    print(f"implementadas: {have}/{len(paths)}  ({100.0 * have / len(paths):.1f}%)")
    print(f"faltan:        {len(missing)}")
    print()

    groups = {}
    for m in missing:
        parts = m.split(".")
        key = ".".join(parts[:2]) if len(parts) > 2 else m
        groups.setdefault(key, []).append(m)
    for key in sorted(groups, key=lambda k: -len(groups[k])):
        items = groups[key]
        print(f"  {key:28s} {len(items):4d}  {', '.join(x.split('.')[-1] for x in items[:8])}"
              + (" ..." if len(items) > 8 else ""))


if __name__ == "__main__":
    main()
