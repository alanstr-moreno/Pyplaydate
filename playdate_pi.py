#!/usr/bin/env python3
"""playdate_pi — corre un juego de Playdate (Lua) en pygame.

Uso:
    python playdate_pi.py hello            # corre games/hello
    python playdate_pi.py hello --scale 3  # ventana 3x
    python playdate_pi.py hello --frames 60 # headless, 60 frames (para tests)

En la Raspberry Pi Zero 2 W usa el mismo comando (pygame ya funciona alli).
"""

import argparse
import sys

from pd.emulator import Emulator


def main(argv=None):
    ap = argparse.ArgumentParser(description="Emulador de Playdate sobre pygame")
    ap.add_argument("game", help="nombre del juego en games/ (o una ruta a un dir)")
    ap.add_argument("--scale", type=int, default=2, help="factor de escala de la ventana")
    ap.add_argument("--palette", default="device",
                    choices=["device", "bw", "yellow"],
                    help="colores de pantalla: device (Memory LCD), bw (negro/blanco "
                         "puro, como pide Panic para capturas) o yellow")
    ap.add_argument("--fps", type=int, default=30, help="frames por segundo de Playdate")
    ap.add_argument("--frames", type=int, default=None,
                    help="salir tras N frames (headless, para pruebas)")
    ap.add_argument("--verbose", action="store_true",
                    help="mostrar los `print` del juego y avisar de assets que faltan "
                         "(por defecto la consola se queda limpia)")
    args = ap.parse_args(argv)

    emu = Emulator(args.game, scale=args.scale, fps=args.fps,
                   headless=args.frames is not None, palette=args.palette,
                   verbose=args.verbose)
    emu.run(max_frames=args.frames)
    return 0


if __name__ == "__main__":
    sys.exit(main())
