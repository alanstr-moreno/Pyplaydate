#!/usr/bin/env python3
"""headless_test.py — runs a game N frames without a window and reports the first
Lua/Python error with the exact frame. Ideal to validate each API you add.

Usage:
    python tools/headless_test.py games/hello
    python tools/headless_test.py games/hello -n 300
    python tools/headless_test.py games/hello -n 120 --keys "10:A:8,40:Left:20"
        # injects keys: frame:button:duration   (A, B, Up, Down, Left, Right)
    python tools/headless_test.py games/hello --save /tmp/frame.png
"""

import argparse
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
from pd.emulator import Emulator  # noqa: E402
from pd.runtime import Runtime  # noqa: E402


def parse_keys(spec):
    """'10:A:8,40:Left:20' -> [(10,'A',8), (40,'Left',20)]"""
    out = []
    if not spec:
        return out
    for part in spec.split(","):
        frame, btn, dur = part.split(":")
        out.append((int(frame), btn, int(dur)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("game")
    ap.add_argument("-n", "--frames", type=int, default=120)
    ap.add_argument("--keys", default="")
    ap.add_argument("--save", default=None)
    args = ap.parse_args(argv)

    emu = Emulator(args.game, headless=True)
    pygame.init()
    pygame.display.init()
    win = pygame.display.set_mode((640, 640))

    rt = Runtime(emu)
    emu._button = rt.playdate.button
    rt.load(emu._resolve_game_dir())

    presses = parse_keys(args.keys)

    for f in range(args.frames):
        pygame.event.pump()
        for start, btn, dur in presses:
            if f == start:
                emu._button._press(btn)
            if f == start + dur:
                emu._button._release(btn)
        try:
            rt.call_update()
            rt.call_draw()
        except Exception as e:  # noqa: BLE001
            print(f"ERROR en frame {f}: {type(e).__name__}: {e}")
            print("--- traceback ---")
            traceback.print_exc()
            return 1
        emu.screen.render(win, 2)
        pygame.display.flip()
        emu.frame += 1
        rt.playdate.button.end_frame()

    if args.save:
        pygame.image.save(emu.screen.canvas, args.save)
        print("frame saved to", args.save)
    print(f"OK: {args.frames} frames without errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
