"""pdi.py — decodificadores de los formatos de imagen COMPILADOS de Playdate.

Un juego compilado no trae PNGs: `pdc` los convierte a:
  .pdi  -> imagen 1-bit (una sola "cell")
  .pdt  -> image table / spritesheet (varias cells)
  .pft  -> fuente 1-bit (no implementada aqui)

Formato "cell" (comun a .pdi/.pdt/.pft):
  header (16 bytes, uint16 LE):
    clipW, clipH, stride, clipL, clipR, clipT, clipB, flags
  luego el bitmap de color (stride*clipH bytes, 1 bit por pixel, 0=negro 1=blanco)
  y si flags&0x3, un segundo bitmap de alpha (1=opaco, 0=transparente)

Spec: github.com/cranksters/playdate-reverse-engineering (formats/pdi.md)
"""

import struct
import zlib

import pygame

CELL_HEADER = 16


def _u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def decode_cell(buf, off=0, background=(255, 255, 255)):
    """Decodifica una 'cell' -> (pygame.Surface SRCALPHA, offset_siguiente, (w,h)).

    Los bordes transparentes vienen recortados; se reconstruyen con clipL/R/T/B.
    """
    clip_w = _u16(buf, off)
    clip_h = _u16(buf, off + 2)
    stride = _u16(buf, off + 4)
    clip_l = _u16(buf, off + 6)
    clip_r = _u16(buf, off + 8)
    clip_t = _u16(buf, off + 10)
    clip_b = _u16(buf, off + 12)
    flags = _u16(buf, off + 14)

    p = off + CELL_HEADER
    n = stride * clip_h
    color_bm = buf[p:p + n]
    p += n
    alpha_bm = None
    if flags & 0x3:
        alpha_bm = buf[p:p + n]
        p += n

    w = clip_l + clip_w + clip_r
    h = clip_t + clip_h + clip_b
    surf = pygame.Surface((w, h), pygame.SRCALPHA)

    bg = tuple(background)
    for y in range(clip_h):
        row = y * stride
        for x in range(clip_w):
            byte = color_bm[row + (x >> 3)]
            bit = (byte >> (7 - (x & 7))) & 1         # MSB primero
            color = bg if bit else (0, 0, 0)
            a = 255
            if alpha_bm is not None:
                ab = alpha_bm[row + (x >> 3)]
                a = 255 if ((ab >> (7 - (x & 7))) & 1) else 0
            surf.set_at((clip_l + x, clip_t + y), (color[0], color[1], color[2], a))
    return surf, p, (w, h)


def decode_pdi(data, background=(255, 255, 255)):
    """Decodifica un .pdi -> pygame.Surface.

    Acepta el archivo con cabecera ('Playdate IMG' + flags) o sin ella (el caso
    de los assets que vienen DENTRO de un .pdz, que pierden los 16 primeros bytes).
    """
    if data[:12] == b"Playdate IMG":
        flags = _u32(data, 12)
        off = 16
        if flags & 0x80000000:                       # comprimido
            off += 16                                # image header (sin comprimir)
            buf = zlib.decompress(data[off:])
        else:
            buf = data[off:]
    else:
        buf = data
    surf, _, _ = decode_cell(buf, 0, background)
    return surf


def decode_pdt(data, background=(255, 255, 255)):
    """Decodifica un .pdt (image table) -> (lista de Surface, celdas_por_fila)."""
    if data[:12] == b"Playdate IMT":
        flags = _u32(data, 12)
        off = 16
        if flags & 0x80000000:
            off += 16                                # image header (sin comprimir)
            buf = zlib.decompress(data[off:])
        else:
            buf = data[off:]
    else:
        buf = data

    num_cells = _u16(buf, 0)
    cells_per_row = _u16(buf, 2)
    offs = [_u32(buf, 4 + 4 * i) for i in range(num_cells)]
    table_end = 4 + 4 * num_cells

    surfaces = []
    surfaces.append(decode_cell(buf, table_end, background)[0])       # cell 0
    for i in range(1, num_cells):
        surfaces.append(decode_cell(buf, table_end + offs[i - 1], background)[0])
    return surfaces, cells_per_row
