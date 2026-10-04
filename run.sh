#!/bin/sh
# Playdate-Pi emulator launcher.
# Uses Hermes's Python (has pygame-ce + lupa). On the Raspberry Pi,
# change PY to your python3 with pygame-ce + lupa installed.
set -e
cd "$(dirname "$0")"

PY=""
for p in ~/.hermes/tools/python-3.14*/bin/python3; do PY="$p"; done
[ -z "$PY" ] && PY=python3

exec "$PY" playdate_pi.py "${1:-games/hello}" --scale "${SCALE:-2}"
