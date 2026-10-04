#!/bin/sh
# Lanzador del emulador Playdate Pi.
# Usa el Python de Hermes (tiene pygame-ce y lupa). En la Raspberry Pi,
# cambia PY por tu python3 con pygame-ce + lupa instalados.
set -e
cd "$(dirname "$0")"

PY=""
for p in ~/.hermes/tools/python-3.14*/bin/python3; do PY="$p"; done
[ -z "$PY" ] && PY=python3

exec "$PY" playdate_pi.py "${1:-hello}" --scale "${SCALE:-2}"
