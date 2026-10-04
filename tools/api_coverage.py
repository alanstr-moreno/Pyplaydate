#!/usr/bin/env python3
"""api_coverage.py — scans a game .lua files and reports which Playdate API it
uses that the emulator does NOT implement yet.

Usage:
    python tools/api_coverage.py games/hello
    python tools/api_coverage.py /path/to/game --all      # also shows what is implemented

Idea: instead of guessing which functions are missing, let the game tell you.
Implement first what YOUR target game actually calls.
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
    """Minimal emulator just to build the API."""

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


# Captures:  playdate.a.b   |   gfx.a.b  |  gfx:a  |  playdate.graphics:sprite.new
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
            # strip comments
            src = re.sub(r"--\[\[.*?\]\]", " ", src, flags=re.S)
            src = re.sub(r"--[^\n]*", " ", src)

            # names the game DEFINES (not missing APIs):
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
        # covered if its exact path is implemented, or if it is a prefix of an
        # implemented one (e.g. "playdate.graphics.sprite" covers its subtree)
        if path in impl:
            return True
        return any(i.startswith(path + ".") for i in impl)

    missing = sorted(p for p in used if not covered(p))
    # group by level-2 module
    groups = {}
    for p in missing:
        parts = p.split(".")
        key = ".".join(parts[:3]) if len(parts) > 3 else ".".join(parts[:2])
        groups.setdefault(key, []).append(p)

    print(f"Game: {folder}")
    print(f"Implemented APIs   : {len(impl)}")
    print(f"Detected calls     : {len(used)}")
    print(f"MISSING            : {len(missing)}\n")

    if not missing:
        print("  (nothing missing: the emulator covers everything this game uses)")
    for key in sorted(groups):
        print(f"  {key}  ({len(groups[key])})")
        for p in groups[key][:12]:
            files = ", ".join(sorted(used[p])[:3])
            print(f"      {p}    <- {files}")
        if len(groups[key]) > 12:
            print(f"      ... and {len(groups[key]) - 12} more")

    if show_all:
        print("\n--- implemented ---")
        for p in sorted(impl):
            print("   ", p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
