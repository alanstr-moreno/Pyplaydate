#!/usr/bin/env python3
"""playdate_pi — runs a Playdate game (Lua) on pygame.

Usage:
    python playdate_pi.py hello            # runs games/hello
    python playdate_pi.py hello --scale 3  # 3x window
    python playdate_pi.py hello --frames 60 # headless, 60 frames (for tests)

On the Raspberry Pi Zero 2 W the same command works (pygame already runs there).
"""

import argparse
import sys

from pd.emulator import Emulator


def main(argv=None):
    ap = argparse.ArgumentParser(description="Playdate emulator on pygame")
    ap.add_argument("game", help="game name in games/ (or a path to a dir)")
    ap.add_argument("--scale", type=int, default=2, help="window scale factor")
    ap.add_argument("--palette", default="device",
                    choices=["device", "bw", "yellow"],
                    help="screen colors: device (Memory LCD), bw (pure black/white, "
                         "as Panic asks for Catalog captures) or yellow")
    ap.add_argument("--fps", type=int, default=30, help="Playdate frames per second")
    ap.add_argument("--frames", type=int, default=None,
                    help="exit after N frames (headless, for tests)")
    ap.add_argument("--verbose", action="store_true",
                    help="show the game's `print`s and warn about missing assets "
                         "(by default the console stays clean)")
    args = ap.parse_args(argv)

    emu = Emulator(args.game, scale=args.scale, fps=args.fps,
                   headless=args.frames is not None, palette=args.palette,
                   verbose=args.verbose)
    emu.run(max_frames=args.frames)
    return 0


if __name__ == "__main__":
    sys.exit(main())
