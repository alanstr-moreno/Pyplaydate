"""pd/pft.py — .pft font decoder (Playdate 1-bit bitmap).

FORMAT — cracked against real .pft files compiled with `pdc`, cross-checked
with the source `.fnt` as an oracle (16/16 correct advances). The public spec
(cranksters/playdate-reverse-engineering/formats/pft.md) is INCOMPLETE; the
verified corrections are marked with «NOTE».

  header:    char[12] "Playdate FNT" + uint32 flags
             flags & 0x80000000 -> compressed with zlib
             flags & 0x00000001 -> has characters above U+1FFFF
  if compressed, font header (NOTE: 16 bytes, the spec says 12):
             uint32 decompressed_size, uint32 max_glyph_w, uint32 max_glyph_h,
             uint32 reserved (seen 0)
  page list: uint8 glyph_w, uint8 glyph_h, uint16 tracking, 64 bytes page_usage,
             uint32 offset per present page
             (NOTE: the pages come CONSECUTIVELY after this list; those offsets
              are not each page's start)
  page:      uint24 reserved, uint8 num_glyphs, 32 bytes glyph_usage,
             uint16[num_glyphs] glyph-offset table
             (NOTE: the spec does not mention this table; the offsets are
              relative to page_start+32)
             glyph records, consecutive
  glyph:     uint8 advance, uint8 n_short, uint16 n_long,
             n_short x (uint8 codepoint_in_page, int8 kerning),
             padding to a multiple of 4 (measured from the record start),
             n_long x (uint24 codepoint, int8 kerning),
             and the pixels as an "Image Cell" (the SAME format as .pdi)

A codepoint's page index is `cp >> 8`; the glyph within the page is `cp & 0xFF`.

Each glyph's position is found by scanning forward for a valid record (the
variable padding between records prevents chaining them blindly). The validator
requires the Cell box to be exactly glyph_w x glyph_h, which is what avoids
false positives.
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
        self.surface = surface            # pygame.Surface SRCALPHA (opaque ink)
        self.kerning = kerning            # {codepoint: px}

    def __repr__(self):
        return f"<PGlyph U+{self.codepoint:04X} adv={self.advance}>"


class PDFont:
    """Loaded .pft font: metrics + glyphs per page."""

    def __init__(self, glyph_w, glyph_h, tracking, pages, max_w=None, max_h=None):
        self.glyph_w = glyph_w
        self.glyph_h = glyph_h
        self.tracking = tracking
        self.pages = pages                # {page_index: {codepoint: PGlyph}}
        self.max_w = max_w or glyph_w
        self.max_h = max_h or glyph_h

    # --- queries used by the game / CoreLibs -------------------------
    def getGlyph(self, codepoint):
        return self.pages.get(int(codepoint) >> 8, {}).get(int(codepoint))

    def allGlyphs(self):
        for page in self.pages.values():
            for g in page.values():
                yield g

    def count(self):
        return sum(len(p) for p in self.pages.values())

    def getHeight(self, *a):
        """REAL font height: the tallest ink of its glyphs.

        It is NOT the .pft Cell height (glyph_h), which is the bitmap BOX and
        turns out much taller than the ink (measured in Smolitaire: Cell=21,
        'A' ink=8). CoreLibs decides whether to draw with

            if y + lineHeight + fontHeight <= bottom then <draw>

        so returning the Cell height makes it NOT draw text in a rect of the
        text's "natural" height -> the menu items came out empty.
        """
        if getattr(self, "_real_h", None) is None:
            # MODE of the ink heights, not the max: a few special glyphs
            # (Ⓐ, U+FFFD) fill the whole Cell and would drag the value up to
            # the Cell height. The height that represents the font is that of
            # the common glyphs (measured in twenty-minute-roman-17:
            # 53 glyphs at 17 px, and only 3 at 21 px).
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
                # tie -> the largest (we want to cover the tallest common glyph)
                self._real_h = max(hist.items(), key=lambda kv: (kv[1], kv[0]))[0]
            else:
                self._real_h = self.glyph_h
        return self._real_h

    def getLeading(self, *a):
        """Font leading: 0 by default.

        NOTE: it is NOT the `tracking`. Returning it made CoreLibs NOT draw
        text in tight rects: its condition is

            if y + lineHeight + fontHeight <= bottom then <draw>

        with lineHeight = fontHeight + leading, i.e. it requires
        `2*fontHeight + leading <= rectHeight`. Since the game sizes the rect
        exactly to the text height, any positive leading takes it out of range
        and the line is not drawn (the black-background dialogs came out
        without letters). Tracking is queried with getTracking().
        """
        return 0

    def getTracking(self, *a):
        return self.tracking

    def getTextWidth(self, text):
        """Width with kerning (uses the previous->current pair)."""
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
    """Glyph pixels (Image Cell) -> (Surface with opaque ink, offset).

    Ink convention, verified against the `/` glyph of the minimal .pft (all
    0x00 color + alpha on the diagonal): **bit 0 = ink**, same as in .pdi
    (where bit 1 = background). The alpha plane, if present, gives opacity.
    """
    clip_w, clip_h, stride, clip_l, clip_r, clip_t, clip_b, flags = struct.unpack_from(
        "<8H", buf, off)
    p = off + CELL_HEADER
    n = stride * clip_h
    color_bm = buf[p:p + n]
    p += n
    alpha_bm = None
    if flags % 4 in (1, 3):               # alpha bitmap present
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
            if cbit:                      # bit 1 = background, not ink
                continue
            if alpha_bm is not None:
                abit = (alpha_bm[row + (x >> 3)] >> (7 - bit)) & 1
                if not abit:
                    continue
            surf.set_at((clip_l + x, clip_t + y), (0, 0, 0, 255))
    return surf, p


def _try_record(buf, p, gw, gh, page_index):
    """Tries to read a glyph record at p. Returns dict or None."""
    n = len(buf)
    if p + 20 > n:
        return None
    advance = buf[p]
    n_short = buf[p + 1]
    n_long = _u16(buf, p + 2)
    if advance > gw + 8 or n_short > 64 or n_long > 64:
        return None
    q = p + 4 + 2 * n_short
    q += (-(q - p)) % 4                    # padding from the record start
    q += 4 * n_long
    if q + CELL_HEADER > n:
        return None
    cw, ch, stride, cl, cr, ct, cb, flags = struct.unpack_from("<8H", buf, q)
    if cw > gw or ch > gh:
        return None
    if cl + cw + cr != gw or ct + ch + cb != gh:      # the box is always complete
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
        # NOTE: the end is computed HERE, not with the offset returned by
        # _glyph_surface: that function already consumes both bitmaps (color
        # and alpha), so adding `size` again doubled the last bitmap's size and
        # desynced the walk (96 glyphs -> 12).
        "end": q + CELL_HEADER + size,
    }


def _walk_page(buf, pstart, page_index, gw, gh, trust_table):
    """Walks the glyphs of a page. Returns (glyphs, last_end)."""
    num_glyphs = buf[pstart + 3]
    usage = buf[pstart + 4:pstart + 36]
    slots = [i for i in range(256) if (usage[i >> 3] >> (i & 7)) & 1]
    table_off = pstart + 36
    offsets = [_u16(buf, table_off + 2 * i) for i in range(num_glyphs)]
    base = pstart + 32                    # the offsets are relative to here

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
    """Parses a page choosing the strategy that recovers ALL the glyphs.

    - `trust_table`: use the uint16 offset table. Valid when its first offset
      points exactly at the start of the glyph data (recent fonts, e.g. the
      current SDK ones).
    - advance-only: required in old fonts (pdc 2022, e.g. Smolitaire), where
      that table's base does NOT match and a spurious hit desyncs the walk
      (96 glyphs -> 10).
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
    """Finds a valid page header from from_off.

    Strong invariant: popcount(32 B usage) == num_glyphs. It is not always at
    `68 + 4*npages` (in old fonts the offset table carries more entries than
    pages), so a short window is scanned and validated by walking.
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
    """Parses .pft bytes -> PDFont."""
    if data[:12] != MAGIC:
        raise ValueError(f"not a .pft (magic={data[:12]!r})")
    flags = _u32(data, 12)
    off = 16
    max_w = max_h = None
    if flags & 0x80000000:
        max_w = _u32(data, off + 4)
        max_h = _u32(data, off + 8)
        off += 16                          # font header: 16 B, not 12
        buf = zlib.decompress(data[off:])
    else:
        buf = data[off:]

    glyph_w = buf[0]
    glyph_h = buf[1]
    tracking = _u16(buf, 2)
    present = _page_usage(buf, 4)
    pstart = 68 + 4 * len(present)         # the pages come consecutively

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
# debug helpers

def render_text(font: PDFont, text, color=(0, 0, 0), background=(255, 255, 255)):
    """Simple composition to VERIFY the font (not the real layout)."""
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
