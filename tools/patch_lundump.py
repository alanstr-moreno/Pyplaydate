#!/usr/bin/env python3
"""patch_lundump.py — translates Playdate opcodes to the standard Lua 5.4.3 order
AT LOAD time (in lundump.c LoadCode).

Playdate uses the Lua 5.4 *beta* enum: LOADFALSE/LFALSESKIP/LOADTRUE are at the
END (81,82,83) and there is a hole at 5; the rest is shifted -2 relative to
final 5.4.3. Touching the enum breaks the VM dispatch table, so instead we
translate the bytes on the fly:

    standard_opcode = pd_map[playdate_opcode]

Usage: python3 patch_lundump.py <lua_src_dir>
"""

import os
import sys


def main(src):
    p = os.path.join(src, "lundump.c")
    C = open(p, encoding="utf-8").read()
    if "PLAYDATE_OPCODE_REMAP" in C:
        print("already patched")
        return

    m = [0] * 84
    for i in range(5):
        m[i] = i
    m[5] = 0                              # hole (should not appear)
    for i in range(6, 81):
        m[i] = i + 2                      # LOADNIL..EXTRAARG  -> std 8..82
    m[81], m[82], m[83] = 5, 6, 7         # LOADFALSE/LFALSESKIP/LOADTRUE -> std 5..7

    rows = []
    for i in range(0, 84, 14):
        rows.append("      " + ", ".join(f"{v:2d}" for v in m[i:i + 14]) + ",")
    table = "\n".join(rows)

    block = (
        "  {  /* --- PLAYDATE_OPCODE_REMAP: translate to the standard 5.4.3 enum --- */\n"
        "    static const unsigned char pd_map[84] = {\n"
        f"{table}\n"
        "    };\n"
        "    int i;\n"
        "    for (i = 0; i < n; i++) {\n"
        "      Instruction inst = f->code[i];\n"
        "      unsigned int op = (unsigned int)(inst & 0x7Fu);\n"
        "      if (op < 84u)\n"
        "        f->code[i] = (Instruction)((inst & ~(Instruction)0x7Fu) | pd_map[op]);\n"
        "    }\n"
        "  }\n"
    )

    old = "  loadVector(S, f->code, n);\n}"
    new = "  loadVector(S, f->code, n);\n" + block + "}"
    if old not in C:
        raise SystemExit("could not find the end of loadCode")
    C = C.replace(old, new, 1)
    open(p, "w", encoding="utf-8").write(C)
    print("lundump.c patched (opcode remap in loadCode)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/lua-5.4.3/src")
