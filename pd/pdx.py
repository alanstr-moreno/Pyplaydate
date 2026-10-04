"""pdx.py — lector (y escritor de test) del formato COMPILADO de Playdate.

Un juego de verdad NO se distribuye como `main.lua`: se distribuye como un
bundle `.pdx` (carpeta) que contiene:

    MiJuego.pdx/
        pdxinfo          -- metadatos (key=value)
        main.pdz         -- contenedor con el bytecode Lua compilado (+ assets)
        pdex.bin | pdex.elf   -- codigo nativo (juegos en C)
        *.pdi  *.pft  *.pda   -- assets ya compilados (imagenes/fuentes/audio)

El `.pdz` es un contenedor: cabecera `Playdate PDZ` + flags, y luego entradas
(bytecode Lua, imagenes .pdi, font .pft, audio .pda, ...).

Spec: github.com/cranksters/playdate-reverse-engineering (formats/pdz.md)
"""

import os
import struct
import zlib

PDZ_MAGIC = b"Playdate PDZ"

FLAG_COMPRESSED = 0x80          # el dato de la entrada esta zlib-comprimido
FLAG_ENCRYPTED = 0x40000000     # DRM (solo juegos de la Catalog/store)

TYPE_NAMES = {
    0: "unknown", 1: "luac", 2: "pdi", 3: "pdt",
    4: "pdv", 5: "pda", 6: "pds", 7: "pft",
}
TYPE_EXT = {
    1: ".luac", 2: ".pdi", 3: ".pdt", 4: ".pdv", 5: ".pda", 6: ".pds", 7: ".pft",
}
# extensiones de asset que pdc deja sueltas en el .pdx (fuera del .pdz)
ASSET_EXTS = (".pdi", ".pdt", ".pft", ".pda", ".pds", ".pdv")


class PDXError(Exception):
    pass


def _u24(b, o):
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


# ----------------------------------------------------------------------
# .pdz
# ----------------------------------------------------------------------
def parse_pdz(data):
    """Lee un contenedor .pdz y devuelve {encrypted, flags, entries}.

    Cada entrada: {name, type, type_name, size, data, compressed, ...}
    Lanza PDXError si no es un .pdz o si esta cifrado.
    """
    if data[:12] != PDZ_MAGIC:
        raise PDXError("no es un .pdz (falta la cabecera 'Playdate PDZ')")

    flags = _u32(data, 12)
    encrypted = bool(flags & FLAG_ENCRYPTED)
    if encrypted:
        raise PDXError(
            "este .pdz esta CIFRADO (DRM de la Catalog). No se puede descomprimir "
            "ni ejecutar: el metodo de cifrado es propietario y no se conoce."
        )

    pos = 16
    entries = []
    while pos < len(data):
        eflags = data[pos]
        pos += 1
        length = _u24(data, pos)
        pos += 3

        end = data.find(b"\x00", pos)
        if end < 0:
            raise PDXError("nombre de entrada sin terminador nulo")
        name = data[pos:end].decode("utf-8", "replace")
        pos = end + 1

        # padding para alinear a multiplo de 4
        while pos % 4:
            pos += 1

        etype = eflags & 0x7F
        extra = {}
        if etype == 5:  # .pda -> sample rate + formato de audio
            extra["sample_rate"] = _u24(data, pos)
            extra["audio_format"] = data[pos + 3]
            pos += 4

        raw = data[pos:pos + length]
        pos += length

        if eflags & FLAG_COMPRESSED:
            usize = _u32(raw, 0)
            raw = zlib.decompress(raw[4:])
            if usize != len(raw):
                raise PDXError(
                    f"tamano descomprimido no coincide en '{name}' "
                    f"({usize} != {len(raw)})"
                )

        entries.append({
            "name": name,
            "type": etype,
            "type_name": TYPE_NAMES.get(etype, f"tipo{etype}"),
            "compressed": bool(eflags & FLAG_COMPRESSED),
            "size": len(raw),
            "data": raw,
            **extra,
        })

    return {"encrypted": False, "flags": flags, "entries": entries}


def build_pdz(entries, encrypted=False):
    """ESCRITOR (solo para tests): entradas = [(name, type, data, compress)]."""
    out = bytearray(PDZ_MAGIC)
    out += struct.pack("<I", FLAG_ENCRYPTED if encrypted else 0)
    for name, etype, data, compress in entries:
        body = data
        eflags = etype & 0x7F
        if compress:
            eflags |= FLAG_COMPRESSED
            body = struct.pack("<I", len(data)) + zlib.compress(data)
        hdr = bytearray()
        hdr += bytes([eflags])
        hdr += bytes([len(body) & 0xFF, (len(body) >> 8) & 0xFF, (len(body) >> 16) & 0xFF])
        hdr += name.encode("utf-8") + b"\x00"
        while (len(out) + len(hdr)) % 4:
            hdr += b"\x00"
        out += hdr
        out += body
    return bytes(out)


# ----------------------------------------------------------------------
# .pdx (bundle)
# ----------------------------------------------------------------------
def parse_pdxinfo(text):
    meta = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        meta[k.strip()] = v.strip()
    return meta


def read_pdx(path):
    """Lee un bundle .pdx (carpeta) o un .pdz suelto.

    Devuelve:
      {"path", "name", "meta", "pdz": {nombre: parsed}, "assets": [(rel, size)]}
    """
    if os.path.isfile(path) and path.endswith(".pdz"):
        with open(path, "rb") as f:
            parsed = parse_pdz(f.read())
        return {"path": path, "name": os.path.basename(path), "meta": {},
                "pdz": {os.path.basename(path): parsed}, "assets": []}

    if not os.path.isdir(path):
        raise PDXError(f"no existe o no es un .pdx/.pdz: {path}")

    name = os.path.basename(path.rstrip("/"))
    if name.endswith(".pdx"):
        name = name[:-4]

    meta = {}
    info = os.path.join(path, "pdxinfo")
    if os.path.isfile(info):
        with open(info, encoding="utf-8", errors="replace") as f:
            meta = parse_pdxinfo(f.read())

    pdz = {}
    assets = []
    for root, _, files in os.walk(path):
        for fn in sorted(files):
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, path)
            if fn.endswith(".pdz"):
                with open(full, "rb") as f:
                    pdz[rel] = parse_pdz(f.read())
            elif fn.endswith(ASSET_EXTS):
                assets.append((rel, os.path.getsize(full)))

    if not pdz and not assets:
        raise PDXError(f"'{path}' no parece un .pdx (sin .pdz ni assets)")

    return {"path": path, "name": name, "meta": meta, "pdz": pdz, "assets": assets}


def extract_pdx(path, outdir):
    """Extrae el contenido de un .pdx a `outdir`. Devuelve la lista de rutas."""
    info = read_pdx(path)
    written = []
    for rel_pdz, parsed in info["pdz"].items():
        base = os.path.dirname(rel_pdz)
        for e in parsed["entries"]:
            out = os.path.join(outdir, base, e["name"] + TYPE_EXT.get(e["type"], ""))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as f:
                f.write(e["data"])
            written.append(out)
    for rel, _ in info["assets"]:
        src = os.path.join(info["path"], rel)
        out = os.path.join(outdir, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(src, "rb") as fi, open(out, "wb") as fo:
            fo.write(fi.read())
        written.append(out)
    return written
