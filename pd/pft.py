"""pd/pft.py — decodificador de fuentes .pft (bitmap 1-bit de Playdate).

FORMATO — crackeado contra .pft reales compilados con `pdc`, cruzando con el
`.fnt` de origen como oraculo (16/16 avances correctos). La spec publica
(cranksters/playdate-reverse-engineering/formats/pft.md) esta INCOMPLETA; las
correcciones verificadas van marcadas con «OJO».

  cabecera:  char[12] "Playdate FNT" + uint32 flags
             flags & 0x80000000 -> comprimido con zlib
             flags & 0x00000001 -> tiene caracteres por encima de U+1FFFF
  si comprimido, cabecera de fuente (OJO: 16 bytes, la spec dice 12):
             uint32 tamano_descomprimido, uint32 max_glyph_w, uint32 max_glyph_h,
             uint32 reservado (visto 0)
  page list: uint8 glyph_w, uint8 glyph_h, uint16 tracking, 64 bytes page_usage,
             uint32 offset por cada pagina presente
             (OJO: las paginas van SEGUIDAS tras esta lista; esos offsets no son
              el inicio de cada pagina)
  pagina:    uint24 reservado, uint8 num_glyphs, 32 bytes glyph_usage,
             uint16[num_glyphs] tabla de offsets de glyph
             (OJO: la spec NO menciona esta tabla; los offsets son relativos a
              page_start+32)
             registros de glyph, consecutivos
  glyph:     uint8 advance, uint8 n_short, uint16 n_long,
             n_short x (uint8 codepoint_en_pagina, int8 kerning),
             padding a multiplo de 4 (medido desde el inicio del registro),
             n_long x (uint24 codepoint, int8 kerning),
             y los pixeles como "Image Cell" (el MISMO formato que .pdi)

El indice de pagina de un codepoint es `cp >> 8`; el glyph dentro de la pagina
es `cp & 0xFF`.

La posicion de cada glyph se localiza buscando hacia adelante un registro valido
(el padding variable entre registros impide encadenarlos a ciegas). El validador
exige que el box de la Cell sea exactamente glyph_w x glyph_h, que es lo que hace
que no haya falsos positivos.
"""

from __future__ import annotations

import struct
import zlib

import pygame

MAGIC = b"Playdate FNT"
CELL_HEADER = 16


class PGlyph:
    __slots__ = ("codepoint", "advance", "surface", "kerning")

    def __init__(self, codepoint, advance, surface, kerning):
        self.codepoint = codepoint
        self.advance = advance
        self.surface = surface            # pygame.Surface SRCALPHA (tinta opaca)
        self.kerning = kerning            # {codepoint: px}

    def __repr__(self):
        return f"<PGlyph U+{self.codepoint:04X} adv={self.advance}>"


class PDFont:
    """Fuente .pft cargada: metricas + glyphs por pagina."""

    def __init__(self, glyph_w, glyph_h, tracking, pages, max_w=None, max_h=None):
        self.glyph_w = glyph_w
        self.glyph_h = glyph_h
        self.tracking = tracking
        self.pages = pages                # {page_index: {codepoint: PGlyph}}
        self.max_w = max_w or glyph_w
        self.max_h = max_h or glyph_h

    # --- consultas que usa el juego / CoreLibs -------------------------
    def getGlyph(self, codepoint):
        return self.pages.get(int(codepoint) >> 8, {}).get(int(codepoint))

    def allGlyphs(self):
        for page in self.pages.values():
            for g in page.values():
                yield g

    def count(self):
        return sum(len(p) for p in self.pages.values())

    def getHeight(self, *a):
        """Altura REAL de la fuente: la tinta mas alta de sus glifos.

        NO es el alto de la Cell del .pft (glyph_h), que es la CAJA del bitmap y
        resulta mucho mayor que la tinta (medido en Smolitaire: Cell=21, tinta de
        'A'=8). CoreLibs decide si dibujar con

            if y + lineHeight + fontHeight <= bottom then <dibuja>

        asi que devolver el alto de la Cell hace que NO dibuje texto en un rect
        de la altura "natural" del texto -> los items del menu salian vacios.
        """
        if getattr(self, "_real_h", None) is None:
            # MODA de las alturas de tinta, no el maximo: unos pocos glifos
            # especiales (Ⓐ, U+FFFD) llenan la Cell entera y arrastrarian el
            # valor al alto de la Cell. La altura que representa a la fuente es
            # la de los glifos comunes (medido en twenty-minute-roman-17:
            # 53 glifos a 17 px, y solo 3 a 21 px).
            hist = {}
            for g in self.allGlyphs():
                sf = getattr(g, "surface", None)
                if sf is None:
                    continue
                try:
                    r = sf.get_bounding_rect(min_alpha=1)
                except TypeError:
                    r = sf.get_bounding_rect()
                except Exception:
                    continue
                if r.height > 0:
                    hist[r.height] = hist.get(r.height, 0) + 1
            if hist:
                # empate -> la mayor (queremos cubrir el glifo mas alto comun)
                self._real_h = max(hist.items(), key=lambda kv: (kv[1], kv[0]))[0]
            else:
                self._real_h = self.glyph_h
        return self._real_h

    def getLeading(self, *a):
        """Leading de la fuente: 0 por defecto.

        OJO: NO es el `tracking`. Devolverlo hacia que CoreLibs NO dibujara
        texto en rects ajustados: su condicion es

            if y + lineHeight + fontHeight <= bottom then <dibuja>

        con lineHeight = fontHeight + leading, o sea exige
        `2*fontHeight + leading <= altoDelRect`. Como el juego dimensiona el
        rect justo a la altura del texto, cualquier leading positivo lo saca de
        rango y la linea no se pinta (los dialogos con fondo negro salian sin
        letras). El tracking se consulta con getTracking().
        """
        return 0

    def getTracking(self, *a):
        return self.tracking

    def getTextWidth(self, text):
        """Ancho con kerning (usa el par anterior->actual)."""
        t = str(text)
        if not t:
            return 0
        total = 0
        prev = None
        for ch in t:
            cp = ord(ch)
            g = self.getGlyph(cp)
            total += (g.advance if g else self.glyph_w) + self.tracking
            if prev is not None and g is not None:
                total += g.kerning.get(prev, 0)
            prev = cp
        return max(0, total - self.tracking)

    def getTextSize(self, text):
        return (self.getTextWidth(text), self.glyph_h)

    def __repr__(self):
        return (f"<PDFont {self.glyph_w}x{self.glyph_h} track={self.tracking} "
                f"pages={len(self.pages)} glyphs={self.count()}>")


# ----------------------------------------------------------------------
# parseo

def _u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def _u24(b, o):
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _s8(b, o):
    return struct.unpack_from("<b", b, o)[0]


def _page_usage(data, off):
    """Los 64 bytes de flags -> lista de indices de pagina presentes."""
    return [i for i in range(256) if (data[off + (i >> 3)] >> (i & 7)) & 1]


def _glyph_surface(buf, off):
    """Pixeles del glyph (Image Cell) -> (Surface con la tinta opaca, offset).

    Convencion de tinta, verificada contra el glyph `/` del .pft minimo (color
    todo 0x00 + alpha en diagonal): **bit 0 = tinta**, igual que en .pdi (donde
    bit 1 = fondo). El plano de alpha, si existe, da la opacidad.
    """
    clip_w, clip_h, stride, clip_l, clip_r, clip_t, clip_b, flags = struct.unpack_from(
        "<8H", buf, off)
    p = off + CELL_HEADER
    n = stride * clip_h
    color_bm = buf[p:p + n]
    p += n
    alpha_bm = None
    if flags % 4 in (1, 3):               # bitmap de alpha presente
        alpha_bm = buf[p:p + n]
        p += n

    w = clip_l + clip_w + clip_r
    h = clip_t + clip_h + clip_b
    surf = pygame.Surface((max(w, 1), max(h, 1)), pygame.SRCALPHA)
    for y in range(clip_h):
        row = y * stride
        for x in range(clip_w):
            bit = (x & 7)
            cbit = (color_bm[row + (x >> 3)] >> (7 - bit)) & 1
            if cbit:                      # bit 1 = fondo, no es tinta
                continue
            if alpha_bm is not None:
                abit = (alpha_bm[row + (x >> 3)] >> (7 - bit)) & 1
                if not abit:
                    continue
            surf.set_at((clip_l + x, clip_t + y), (0, 0, 0, 255))
    return surf, p


def _try_record(buf, p, gw, gh, page_index):
    """Intenta leer un registro de glyph en p. Devuelve dict o None."""
    n = len(buf)
    if p + 20 > n:
        return None
    advance = buf[p]
    n_short = buf[p + 1]
    n_long = _u16(buf, p + 2)
    if advance > gw + 8 or n_short > 64 or n_long > 64:
        return None
    q = p + 4 + 2 * n_short
    q += (-(q - p)) % 4                    # padding desde el inicio del registro
    q += 4 * n_long
    if q + CELL_HEADER > n:
        return None
    cw, ch, stride, cl, cr, ct, cb, flags = struct.unpack_from("<8H", buf, q)
    if cw > gw or ch > gh:
        return None
    if cl + cw + cr != gw or ct + ch + cb != gh:      # el box siempre es completo
        return None
    if not (cw == 0 and ch == 0) and stride != max(1, (cw + 7) // 8):
        return None

    kerning = {}
    kp = p + 4
    for _ in range(n_short):
        kerning[(page_index << 8) | buf[kp]] = _s8(buf, kp + 1)
        kp += 2
    kp = p + 4 + 2 * n_short
    kp += (-(kp - p)) % 4
    for _ in range(n_long):
        kerning[_u24(buf, kp)] = _s8(buf, kp + 3)
        kp += 4

    surf, _consumed = _glyph_surface(buf, q)
    size = stride * ch * (2 if flags % 4 in (1, 3) else 1)
    return {
        "advance": advance,
        "surface": surf,
        "kerning": kerning,
        "cell": (cw, ch, stride, cl, cr, ct, cb, flags),
        # OJO: el fin se calcula AQUI, no con el offset que devuelve
        # _glyph_surface: esa funcion ya consume los dos bitmaps (color y alpha),
        # asi que sumarle `size` otra vez doblaba el tamaño del ultimo bitmap y
        # desincronizaba el recorrido (96 glyphs -> 12).
        "end": q + CELL_HEADER + size,
    }


def _walk_page(buf, pstart, page_index, gw, gh, trust_table):
    """Recorre los glyphs de una pagina. Devuelve (glyphs, ultimo_fin)."""
    num_glyphs = buf[pstart + 3]
    usage = buf[pstart + 4:pstart + 36]
    slots = [i for i in range(256) if (usage[i >> 3] >> (i & 7)) & 1]
    table_off = pstart + 36
    offsets = [_u16(buf, table_off + 2 * i) for i in range(num_glyphs)]
    base = pstart + 32                    # los offsets son relativos a aqui

    p = table_off + 2 * num_glyphs
    glyphs = {}
    last_end = p
    for k, slot in enumerate(slots):
        rec = None
        cand = []
        if trust_table and k < len(offsets):
            cand.append(base + offsets[k])
        cand += range(p, min(p + 96, len(buf) - 20))
        for probe in cand:
            r = _try_record(buf, probe, gw, gh, page_index)
            if r is not None:
                rec = r
                break
        if rec is None:
            continue
        cp = (page_index << 8) | slot
        glyphs[cp] = PGlyph(cp, rec["advance"], rec["surface"], rec["kerning"])
        p = max(p, rec["end"])
        last_end = max(last_end, rec["end"])
    return glyphs, last_end


def _parse_page(buf, pstart, page_index, gw, gh):
    """Parsea una pagina eligiendo la estrategia que recupere TODOS los glyphs.

    - `trust_table`: usar la tabla uint16 de offsets. Valida cuando su primer
      offset apunta exactamente al inicio de los datos de glyph (fuentes
      recientes, p.ej. las del SDK actual).
    - solo avance: obligatorio en fuentes antiguas (pdc 2022, p.ej. Smolitaire),
      donde la base de esa tabla NO coincide y un acierto espurio desincroniza
      el recorrido (96 glyphs -> 10).
    """
    num_glyphs = buf[pstart + 3]
    table_off = pstart + 36
    table_end = table_off + 2 * num_glyphs
    off0 = _u16(buf, table_off) if num_glyphs else None
    trust = off0 is not None and (pstart + 32 + off0) == table_end

    best = None
    for trust_try in ((True, False) if trust else (False, True)):
        glyphs, end = _walk_page(buf, pstart, page_index, gw, gh, trust_try)
        if best is None or len(glyphs) > len(best[0]):
            best = (glyphs, end)
        if len(glyphs) == num_glyphs:
            break
    return best


def _find_page(buf, from_off, page_index, gw, gh):
    """Localiza una cabecera de pagina valida desde from_off.

    Invariante fuerte: popcount(usage de 32 B) == num_glyphs. No siempre esta en
    `68 + 4*npaginas` (en fuentes antiguas la tabla de offsets trae mas entradas
    que paginas), asi que se busca una ventana corta y se valida recorriendo.
    """
    for q in range(from_off, min(from_off + 48, len(buf) - 36)):
        num = buf[q + 3]
        if num == 0:
            continue
        used = buf[q + 4:q + 36]
        if sum(bin(x).count("1") for x in used) != num:
            continue
        glyphs, end = _parse_page(buf, q, page_index, gw, gh)
        if len(glyphs) >= max(1, num // 2):
            return q, glyphs, end
    return None, {}, from_off


def parse_font(data) -> PDFont:
    """Parsea bytes de un .pft -> PDFont."""
    if data[:12] != MAGIC:
        raise ValueError(f"no es un .pft (magic={data[:12]!r})")
    flags = _u32(data, 12)
    off = 16
    max_w = max_h = None
    if flags & 0x80000000:
        max_w = _u32(data, off + 4)
        max_h = _u32(data, off + 8)
        off += 16                          # cabecera de fuente: 16 B, no 12
        buf = zlib.decompress(data[off:])
    else:
        buf = data[off:]

    glyph_w = buf[0]
    glyph_h = buf[1]
    tracking = _u16(buf, 2)
    present = _page_usage(buf, 4)
    pstart = 68 + 4 * len(present)         # las paginas van seguidas

    pages = {}
    for pi in present:
        q, glyphs, nxt = _find_page(buf, pstart, pi, glyph_w, glyph_h)
        if q is not None and glyphs:
            pages[pi] = glyphs
            pstart = max(nxt, q + 36)
    return PDFont(glyph_w, glyph_h, tracking, pages, max_w, max_h)


def load_font(path) -> PDFont:
    with open(path, "rb") as f:
        return parse_font(f.read())


# ----------------------------------------------------------------------
# helpers de depuracion

def render_text(font: PDFont, text, color=(0, 0, 0), background=(255, 255, 255)):
    """Composicion simple para VERIFICAR la fuente (no es el layout real)."""
    text = str(text)
    w = font.getTextWidth(text) + 4
    h = font.glyph_h + 4
    surf = pygame.Surface((max(w, 8), max(h, 8)))
    surf.fill(background)
    x = 2
    for ch in text:
        g = font.getGlyph(ord(ch))
        if g is not None:
            if color != (0, 0, 0) and g.surface is not None:
                tinted = g.surface.copy()
                tinted.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
                surf.blit(tinted, (x, 2))
            else:
                surf.blit(g.surface, (x, 2))
            x += g.advance + font.tracking
        else:
            x += font.glyph_w + font.tracking
    return surf
