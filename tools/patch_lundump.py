#!/usr/bin/env python3
"""patch_lundump.py — traduce los opcodes de Playdate al orden estandar de Lua 5.4.3
AL CARGAR el bytecode (en lundump.c LoadCode).

Playdate usa el enum de Lua 5.4 *beta*: LOADFALSE/LFALSESKIP/LOADTRUE estan al
FINAL (81,82,83) y hay un hueco en 5; el resto va desplazado -2 respecto al
5.4.3 final. Tocar el enum rompe la tabla de dispatch del VM, asi que en vez de
eso traducimos los bytes al vuelo:

    opcode_estandar = pd_map[opcode_playdate]

Uso: python3 patch_lundump.py <dir_src_de_lua>
"""

import os
import sys


def main(src):
    p = os.path.join(src, "lundump.c")
    C = open(p, encoding="utf-8").read()
    if "PLAYDATE_OPCODE_REMAP" in C:
        print("ya estaba parcheado")
        return

    m = [0] * 84
    for i in range(5):
        m[i] = i
    m[5] = 0                              # hueco (no deberia aparecer)
    for i in range(6, 81):
        m[i] = i + 2                      # LOADNIL..EXTRAARG  -> std 8..82
    m[81], m[82], m[83] = 5, 6, 7         # LOADFALSE/LFALSESKIP/LOADTRUE -> std 5..7

    rows = []
    for i in range(0, 84, 14):
        rows.append("      " + ", ".join(f"{v:2d}" for v in m[i:i + 14]) + ",")
    table = "\n".join(rows)

    block = (
        "  {  /* --- PLAYDATE_OPCODE_REMAP: traduce al enum estandar de 5.4.3 --- */\n"
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
        raise SystemExit("no encontre el cierre de loadCode")
    C = C.replace(old, new, 1)
    open(p, "w", encoding="utf-8").write(C)
    print("lundump.c parcheado (remapeo de opcodes en loadCode)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/lua-5.4.3/src")
