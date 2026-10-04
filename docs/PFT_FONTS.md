# `.pft` fonts — CRACKED format (and integration status)

## One-line status

**The format is solved AND wired into the runtime.** `pft.load_font()` loads the
game's 4 `.pft` files and the SDK's, and renders readable text
(`render_text` produces "Smolitaire 1.0 ABC xyz 012" perfectly legible).
`playdate.graphics.font.new()` resolves the asset and returns the REAL font (if
the `.pft` is not found, it falls back to the stub).

Measured counts: `twenty-minute-roman-17` → **103 glyphs**, `roman-bold` → 92,
`roman` → 92, `fine-print` → 133, and `Asheville-Sans-14-Bold` (SDK) → **294**,
which is exactly the number of glyphs its source `.fnt` declares (independent
cross-validation).

## How it was cracked (this is the important part)

The Playdate SDK ships **`.fnt` (font, PLAIN TEXT, documented) and `.pft`
compiled with the same names**. That gives an **exact oracle**: the glyph list
with its widths is known, and the binary is available.

- SDK: `the local Playdate SDK install`, compiler `bin/pdc`.
- Oracle: `mini.pft`, compiled by us trimming `Asheville-Sans-14-Bold.fnt` to
  16 known glyphs (`space ! " # $ % & ' ( ) * + , - . /`, widths 3,2,5,9,8,12,11,3,5,5,8,8,3,6,2,6).
  Reproducible build in `/tmp` → `minifont/`.
- Result: **16/16 correct advances** with the format below.
- Cross-validation: **96/96** glyphs in `twenty-minute-roman-17.pft` with the
  loose script `scratch/pft_loose.py`.

## The real format (corrects the published spec)

```
header:      char[12] "Playdate FNT" + uint32 flags
             flags & 0x80000000 -> compressed with zlib
             flags & 0x00000001 -> has characters above U+1FFFF
if compressed, font header:                <-- NOTE: 16 bytes, the spec says 12
             uint32 decompressed_size, uint32 max_w, uint32 max_h,
             uint32 reserved (seen 0)
page list:   uint8 glyph_w, uint8 glyph_h, uint16 tracking, 64 B page_usage,
             uint32 offset per present page
             <-- NOTE: the pages come CONSECUTIVELY after this list; those offsets
                 are NOT each page's start (in mini.pft they were 860 and the page
                 was at 72)
page:        uint24 reserved, uint8 num_glyphs, 32 B glyph_usage,
             uint16[num_glyphs] glyph-offset table
             <-- NOTE: the spec does not mention this table
             glyph records, consecutive
glyph:       uint8 advance, uint8 n_short, uint16 n_long,
             n_short x (uint8 codepoint_in_page, int8 kerning),
             padding to a multiple of 4 from the START of the record,
             n_long x (uint24 codepoint, int8 kerning),
             Image Cell (16 B header + color [+ alpha if flags%4 in (1,3)])
```

Invariant that locates page headers: **`popcount(glyph_usage) == num_glyphs`**.
The page start is **not always `68 + 4*npages`**: in older fonts the offset table
carries more entries than pages. Measured: `mini.pft` → 72 = 68+4;
`Asheville-Sans-14-Bold.pft` → 92 = 68+24; `twenty-minute-roman-17.pft` → **100**
(using 92 gives 0 glyphs).

A record's validator requires the Cell box to be **exactly**
`glyph_w x glyph_h` (`clip_l+clip_w+clip_r == glyph_w`, `clip_t+clip_h+clip_b == glyph_h`).
That is what avoids the false positives that cost so much time: without that
condition, a lax filter over the whole buffer returns ~100 "cells" that render
as **solid black blocks**.

## The residual bug (where to resume)

`scratch/pft_loose.py` (loose, with fixed `pstart=100`) recovers **96/96** glyphs
in roman-17. The same walk inside `pd/pft.py` (`_walk_page`) recovers 12. The
two bugs that remained were already fixed (both were gross):

1. `_try_record` computed the record end as
   `_glyph_surface(...) + size`, but `_glyph_surface` **already consumes both
   bitmaps** → it added the last bitmap twice and desynced the walk
   (96 glyphs → 12). Now the end is `q + CELL_HEADER + size`.
2. The ink was inverted: `_glyph_surface` painted where the bit was 1, but the
   `/` glyph of the minimal `.pft` proves that **bit 0 = ink** (all-`0x00`
   color + alpha on the diagonal). The alpha plane gives the opacity.

Separately, `_find_page` locates the header with the invariant
`popcount(usage) == num_glyphs` (the start is not always `68 + 4*npages`), and
`_parse_page` tries both strategies (trusting the uint16 table or just
advancing) and keeps whichever recovers more glyphs.

```
loose script:  advances [10, 3,  8, 15, 11, 14, 17,  3,  6,  6, ...]
_walk_page:    advances [10, 3, 15, 14,  3,  6, 10, 10,  3,  9, ...]
```

`_try_record` (module) REJECTS a valid record that `try_rec` (script) accepts,
around offset 452–460, and after that the walk desyncs. The two functions were
written to be equivalent, so the next step is to **diff them line by line** (the
script has the version that works) or directly replace `_walk_page` with the
script's loop.

Useful fact: `_walk_page(100, trust_table=True)` gives 20 glyphs and `False`
gives 12, while the script gives 96 → the problem is in the **validator**, not
the walk strategy.

## SDK fonts for more validation (all with their `.fnt` beside them)

- `Resources/Fonts/Asheville/Asheville Sans 14 Bold/Asheville-Sans-14-Bold.fnt`
  + `Disk/System/Fonts/Asheville-Sans-14-Bold.pft` (293 `.fnt` lines)
- `Disk/System/Fonts/Roobert-11-Medium.pft`, `Roobert-24-Medium.pft`, etc.

## Tools of this investigation

| file | what it does |
|---|---|
| `scratch/pft_loose.py` | **the one that works**: 96/96 glyphs in roman-17 |
| `scratch/pft_decode.py`, `pft_final.py` | 16/16 validation against the minimal `.fnt` |
| `scratch/pft_pages.py` | locates pages by the popcount invariant |
| `scratch/pft_oracle.py` | parses the minimal `.pft` against the offset table |
| `scratch/pft_walk.py`, `pft_dump*.py`, `pft_findhdr.py`, `pft_scan.py`, `pft_brute.py` | intermediate analysis steps |

## Included debug utility

`pd/pft.py` ships `render_text(font, text)` which composes text with the font's
glyphs onto a surface. Useful to validate visually without booting the game.

```python
from pd import pft
f = pft.load_font("common/images/twenty-minute-roman-17.pft")
img = pft.render_text(f, "Smolitaire 1.0")
pygame.image.save(img, "font_test.png")
```
