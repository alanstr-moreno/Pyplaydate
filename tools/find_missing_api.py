#!/usr/bin/env python3
"""find_missing_api.py — corre un juego y reporta QUE APIs de Playdate le faltan
al emulador, aislando errores para descubrir muchas de una sola pasada.

Es la herramienta para saber que implementar a continuacion, en vez de arreglar
un "attempt to index a nil value" por vez.

Uso:
    python tools/find_missing_api.py smolitaire-1.0.1.pdx
    python tools/find_missing_api.py smolitaire-1.0.1.pdx -n 300
    python tools/find_missing_api.py games/hello          # tambien en modo fuente
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
    rt.on_ready = apidbg.install          # rastrea desde el arranque del juego

    load_error = None
    try:
        rt.load(emu._resolve_game_dir())
    except Exception as e:  # noqa: BLE001
        load_error = f"{type(e).__name__}: {e}"

    safe = apidbg.safe_caller(rt)
    clock = pygame.time.Clock()            # pacing REAL: los timers miden tiempo real
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

    print(f"Juego: {args.game}   frames: {args.frames}")
    if load_error:
        print(f"\n[!] error al cargar/arrancar: {load_error[:160]}")

    print(f"\n=== APIs FALTANTES ({len(missing)}) — esto es lo que hay que implementar ===")
    if not missing:
        print("  (ninguna: el emulador cubre lo que el juego toco)")
    groups = apidbg.api_suggestions(missing)
    for key in sorted(groups, key=lambda k: -sum(c for _, c in groups[k])):
        print(f"\n  {key}")
        for path, count in groups[key][:14]:
            print(f"      {path:52s} x{count}")
        if len(groups[key]) > 14:
            print(f"      ... y {len(groups[key]) - 14} mas")

    print(f"\n=== ERRORES AISLADOS ({len(errors)}) ===")
    if not errors:
        print("  (ninguno)")
    for msg, count in sorted(errors.items(), key=lambda x: -x[1])[:15]:
        print(f"  x{count:<3d} {msg[:150]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
