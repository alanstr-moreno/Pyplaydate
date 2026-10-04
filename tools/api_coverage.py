#!/usr/bin/env python3
"""api_coverage.py — escanea los .lua de un juego y reporta qué API de Playdate
usa que el emulador TODAVÍA NO implementa.

Uso:
    python tools/api_coverage.py games/hello
    python tools/api_coverage.py /ruta/a/juego --all      # muestra tambien lo implementado

Idea: en vez de adivinar qué funciones faltan, dejas que el juego te lo diga.
Implementa primero lo que TU juego objetivo realmente llama.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import lupa  # noqa: E402
import pygame  # noqa: E402

pygame.init()
from pd import screen as S  # noqa: E402
from pd.runtime import Runtime  # noqa: E402


class _Dummy:
    """Emulador minimo solo para construir la API."""

    def __init__(self):
        self.game_name = "scan"
        self.frame = 0
        self.running = True
        self.game_dir = "."
        self.screen = S.Screen()


def implemented_paths():
    rt = Runtime(_Dummy())
    api = rt._build_api()
    out = set()

    def walk(tbl, prefix):
        for k in tbl.keys():
            key = str(k)
            path = f"{prefix}.{key}" if prefix else key
            v = tbl[k]
            if lupa.lua_type(v) == "table":
                walk(v, path)
            else:
                out.add(path)

    walk(api, "playdate")
    return out


# Captura:  playdate.a.b   |   gfx.a.b  |  gfx:a  |  playdate.graphics:sprite.new
RE_CALL = re.compile(
    r"\b(playdate|gfx|graphics)"
    r"((?:[.:][A-Za-z_]\w*)+)"
)


def used_paths(folder):
    used = {}
    for root, _, files in os.walk(folder):
        for fn in files:
            if not fn.endswith((".lua", ".lu")):
                continue
            p = os.path.join(root, fn)
            try:
                src = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            # quitar comentarios
            src = re.sub(r"--\[\[.*?\]\]", " ", src, flags=re.S)
            src = re.sub(r"--[^\n]*", " ", src)

            # nombres que el juego DEFINE (no son APIs que falten):
            #   function playdate.update() ... end   |   playdate.draw = function() end
            defs = set()
            for d in re.findall(r"\bfunction\s+(playdate(?:[.:][A-Za-z_]\w*)+)\s*\(", src):
                defs.add(d.replace(":", "."))
            for d in re.findall(r"\b(playdate(?:[.:][A-Za-z_]\w*)+)\s*=\s*function", src):
                defs.add(d.replace(":", "."))

            for m in RE_CALL.finditer(src):
                base, rest = m.group(1), m.group(2)
                path = rest.replace(":", ".")
                if base in ("gfx", "graphics"):
                    path = "playdate.graphics" + path
                else:
                    path = "playdate" + path
                if path in defs:
                    continue
                used.setdefault(path, set()).add(fn)
    return used


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    folder = argv[1]
    show_all = "--all" in argv

    impl = implemented_paths()
    used = used_paths(folder)

    def covered(path):
        # cubierto si su ruta exacta esta implementada, o si es prefijo de una
        # implementada (p.ej. "playdate.graphics.sprite" cubre su subarbol)
        if path in impl:
            return True
        return any(i.startswith(path + ".") for i in impl)

    missing = sorted(p for p in used if not covered(p))
    # agrupar por modulo de nivel 2
    groups = {}
    for p in missing:
        parts = p.split(".")
        key = ".".join(parts[:3]) if len(parts) > 3 else ".".join(parts[:2])
        groups.setdefault(key, []).append(p)

    print(f"Juego: {folder}")
    print(f"APIs implementadas : {len(impl)}")
    print(f"Llamadas detectadas: {len(used)}")
    print(f"FALTAN             : {len(missing)}\n")

    if not missing:
        print("  (nada faltante: el emulador cubre todo lo que usa este juego)")
    for key in sorted(groups):
        print(f"  {key}  ({len(groups[key])})")
        for p in groups[key][:12]:
            files = ", ".join(sorted(used[p])[:3])
            print(f"      {p}    <- {files}")
        if len(groups[key]) > 12:
            print(f"      ... y {len(groups[key]) - 12} mas")

    if show_all:
        print("\n--- implementadas ---")
        for p in sorted(impl):
            print("   ", p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
