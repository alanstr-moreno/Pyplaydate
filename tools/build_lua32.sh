#!/bin/sh
# build_lua32.sh — construye el Lua que puede CARGAR Y EJECUTAR bytecode de
# Playdate, mas el lupa que lo embebe. Reproducible en macOS y Linux
# (incluida la Raspberry Pi Zero 2 W).
#
# Resultado: vendor/lupa/lua.<abi>.so   ->  se usa con `import lupa.lua`
#
# Que hace, y por que:
#   1. Lua 5.4.3 con LUA_32BITS=1  (Playdate usa enteros y floats de 4 bytes)
#   2. remapeo de opcodes en lundump.c: Playdate usa el enum de Lua 5.4-beta
#      (LOADFALSE/LFALSESKIP/LOADTRUE al final, hueco en 5); traducimos al
#      enum estandar AL CARGAR, sin tocar el VM
#   3. lupa compilado contra esa liblua.a
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
WORK=${WORK:-/tmp/pdbuild}
LUA_VER=5.4.3
LUPA_VER=2.8

mkdir -p "$WORK" "$HERE/vendor/lupa"
cd "$WORK"

echo "[1/5] Lua $LUA_VER"
[ -f lua-$LUA_VER.tar.gz ] || curl -sL "http://www.lua.org/ftp/lua-$LUA_VER.tar.gz" -o lua-$LUA_VER.tar.gz
[ -d lua-$LUA_VER ] || tar xzf lua-$LUA_VER.tar.gz

echo "[2/5] activar LUA_32BITS"
python3 - "$WORK/lua-$LUA_VER/src/luaconf.h" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
s2 = re.sub(r"(?m)^#define\s+LUA_32BITS\s+0\b", "#define LUA_32BITS\t1", s, count=1)
assert s2 != s or "#define LUA_32BITS\t1" in s2, "no pude activar LUA_32BITS"
open(p, "w", encoding="utf-8").write(s2)
print("   LUA_32BITS -> 1")
PY

echo "[3/5] remapeo de opcodes Playdate -> 5.4.3"
python3 "$HERE/tools/patch_lundump.py" "$WORK/lua-$LUA_VER/src"

echo "[4/5] compilando Lua (liblua.a + luac)"
cd "$WORK/lua-$LUA_VER/src"
make clean >/dev/null 2>&1 || true
make luac >/dev/null 2>&1 || make posix >/dev/null 2>&1
ls -la liblua.a luac >/dev/null
cd "$WORK"

echo "[5/5] lupa $LUPA_VER contra esa Lua"
[ -f lupa-$LUPA_VER.tar.gz ] || python3 -m pip download "lupa==$LUPA_VER" --no-binary :all: --no-deps -d "$WORK" >/dev/null
[ -d lupa-$LUPA_VER ] || tar xzf lupa-$LUPA_VER.tar.gz
cd "$WORK/lupa-$LUPA_VER"
rm -rf build
python3 setup.py build_ext --inplace \
    --lua-lib "$WORK/lua-$LUA_VER/src/liblua.a" \
    --lua-includes "$WORK/lua-$LUA_VER/src"
cp lupa/lua.*.so "$HERE/vendor/lupa/" || cp lupa/lua.*.dylib "$HERE/vendor/lupa/"
echo "LISTO -> $HERE/vendor/lupa/"
ls "$HERE/vendor/lupa/"*.so 2>/dev/null || ls "$HERE/vendor/lupa/"*.dylib 2>/dev/null
