#!/bin/sh
# Playdate-Pi emulator launcher.
# Uses the python3 that has pygame-ce and lupa installed. Override PY if
# needed (e.g. a virtualenv) by setting it before running, or export SCALE.
set -e
cd "$(dirname "$0")"

PY="${PY:-python3}"

exec "$PY" playdate_pi.py "${1:-games/hello}" --scale "${SCALE:-2}"
