#!/bin/sh
# install_pi.sh — one-shot setup for the Raspberry Pi Zero 2 W (Raspberry Pi OS).
#
# It installs the system packages, creates a Python virtualenv, installs the
# Python requirements and builds the 32-bit Lua bridge. After it finishes you
# can run a game with:
#
#   source .venv/bin/activate
#   python playdate_pi.py "games/<game>.pdx" --scale 1
#
# Usage:
#   sh install_pi.sh          # from the repo root
set -e

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

echo "[1/4] System packages (needs sudo) ..."
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y build-essential python3-dev python3-venv python3-pip curl
else
    echo "    apt-get not found — install manually: build-essential python3-dev python3-venv python3-pip curl"
fi

echo "[2/4] Python virtualenv (.venv) ..."
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
. ./.venv/bin/activate

echo "[3/4] Python requirements (pygame-ce, lupa) ..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "[4/4] Build the 32-bit Lua bridge (vendor/lupa) ..."
sh tools/build_lua32.sh

echo
echo "DONE. To run a game:"
echo "    source .venv/bin/activate"
echo "    python playdate_pi.py \"games/<game>.pdx\" --scale 1"
echo
echo "Tips:"
echo "  - No desktop (framebuffer): export SDL_VIDEODRIVER=kmsdrm"
echo "  - No audio device:          export SDL_AUDIODRIVER=dummy"