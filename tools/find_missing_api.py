#!/usr/bin/env python3
"""find_missing_api.py — runs a game and reports which Playdate APIs the emulator
is missing, isolating errors to discover many in a single pass.

It is the tool to know what to implement next, instead of fixing one
"attempt to index a nil value" at a time.

Usage:
    python tools/find_missing_api.py smolitaire-1.0.1.pdx
    python tools/find_missing_api.py smolitaire-1.0.1.pdx -n 300
    python tools/find_missing_api.py games/hello          # also in source mode
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from pd import apidbg  # noqa: E402
from pd.emulator import Emulator  # noqa: E402
from pd.runtime import Runtime  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("game")
    ap.add_argument("-n", "--frames", type=int, default=120)
    args = ap.parse_args(argv)

    emu = Emulator(args.game, headless=True)
    pygame.init()
    pygame.display.init()
    pygame.display.set_mode((640, 640))

    rt = Runtime(emu)
    emu._button = rt.playdate.button
    rt.on_ready = apidbg.install          # track from the game boot

    load_error = None
    try:
        rt.load(emu._resolve_game_dir())
    except Exception as e:  # noqa: BLE001
        load_error = f"{type(e).__name__}: {e}"

    safe = apidbg.safe_caller(rt)
    clock = pygame.time.Clock()            # REAL pacing: timers measure real time
    for _ in range(args.frames):
        pygame.event.pump()
        safe(rt.playdate._update_cb)
        safe(apidbg.rawget(rt, rt.api, "update"))
        safe(rt.playdate._draw_cb)
        safe(apidbg.rawget(rt, rt.api, "draw"))
        emu.frame += 1
        rt.playdate.button.end_frame()
        clock.tick(30)

    missing, errors = apidbg.report(rt)

    print(f"Game: {args.game}   frames: {args.frames}")
    if load_error:
        print(f"\n[!] error loading/booting: {load_error[:160]}")

    print(f"\n=== MISSING APIs ({len(missing)}) — this is what to implement ===")
    if not missing:
        print("  (none: the emulator covers what the game touched)")
    groups = apidbg.api_suggestions(missing)
    for key in sorted(groups, key=lambda k: -sum(c for _, c in groups[k])):
        print(f"\n  {key}")
        for path, count in groups[key][:14]:
            print(f"      {path:52s} x{count}")
        if len(groups[key]) > 14:
            print(f"      ... and {len(groups[key]) - 14} more")

    print(f"\n=== ISOLATED ERRORS ({len(errors)}) ===")
    if not errors:
        print("  (none)")
    for msg, count in sorted(errors.items(), key=lambda x: -x[1])[:15]:
        print(f"  x{count:<3d} {msg[:150]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
