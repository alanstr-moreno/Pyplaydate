"""pdx.py — reader (and test writer) of the COMPILED Playdate format.

A real game is NOT distributed as `main.lua`: it is distributed as a `.pdx`
bundle (folder) that contains:

    MyGame.pdx/
        pdxinfo          -- metadata (key=value)
        main.pdz         -- container with the compiled Lua bytecode (+ assets)
        pdex.bin | pdex.elf   -- native code (C games)
        *.pdi  *.pft  *.pda   -- already-compiled assets (images/fonts/audio)

The `.pdz` is a container: `Playdate PDZ` header + flags, then entries
(Lua bytecode, .pdi images, .pft font, .pda audio, ...).

Spec: github.com/cranksters/playdate-reverse-engineering (formats/pdz.md)
"""

import os
import struct
import zlib

PDZ_MAGIC = b"Playdate PDZ"

FLAG_COMPRESSED = 0x80          # the entry data is zlib-compressed
FLAG_ENCRYPTED = 0x40000000     # DRM (only Catalog/store games)

TYPE_NAMES = {
    0: "unknown", 1: "luac", 2: "pdi", 3: "pdt",
    4: "pdv", 5: "pda", 6: "pds", 7: "pft",
}
TYPE_EXT = {
    1: ".luac", 2: ".pdi", 3: ".pdt", 4: ".pdv", 5: ".pda", 6: ".pds", 7: ".pft",
}
# asset extensions that pdc leaves loose in the .pdx (outside the .pdz)
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
    """Reads a .pdz container and returns {encrypted, flags, entries}.

    Each entry: {name, type, type_name, size, data, compressed, ...}
    Raises PDXError if it is not a .pdz or if it is encrypted.
    """
    if data[:12] != PDZ_MAGIC:
        raise PDXError("not a .pdz (missing the 'Playdate PDZ' header)")

    flags = _u32(data, 12)
    encrypted = bool(flags & FLAG_ENCRYPTED)
    if encrypted:
        raise PDXError(
            "this .pdz is ENCRYPTED (Catalog DRM). It cannot be decompressed or "
            "run: the encryption method is proprietary and unknown."
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
            raise PDXError("entry name without null terminator")
        name = data[pos:end].decode("utf-8", "replace")
        pos = end + 1

        # padding to align to a multiple of 4
        while pos % 4:
            pos += 1

        etype = eflags & 0x7F
        extra = {}
        if etype == 5:  # .pda -> sample rate + audio format
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
                    f"decompressed size mismatch in '{name}' "
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
    """WRITER (tests only): entries = [(name, type, data, compress)]."""
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
    """Reads a .pdx bundle (folder) or a loose .pdz.

    Returns:
      {"path", "name", "meta", "pdz": {name: parsed}, "assets": [(rel, size)]}
    """
    if os.path.isfile(path) and path.endswith(".pdz"):
        with open(path, "rb") as f:
            parsed = parse_pdz(f.read())
        return {"path": path, "name": os.path.basename(path), "meta": {},
                "pdz": {os.path.basename(path): parsed}, "assets": []}

    if not os.path.isdir(path):
        raise PDXError(f"does not exist or is not a .pdx/.pdz: {path}")

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
        raise PDXError(f"'{path}' does not look like a .pdx (no .pdz or assets)")

    return {"path": path, "name": name, "meta": meta, "pdz": pdz, "assets": assets}


def extract_pdx(path, outdir):
    """Extracts the contents of a .pdx to `outdir`. Returns the list of paths."""
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
