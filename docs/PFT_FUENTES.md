# Fuentes `.pft` — formato CRACKEADO (y estado de la integracion)

## Estado en una linea

**El formato esta resuelto Y CABLEADO al runtime.** `pft.load_font()` carga los
4 `.pft` del juego y los del SDK, y renderiza texto legible (`render_text` produce
"Smolitaire 1.0 ABC xyz 012" perfectamente leible). `playdate.graphics.font.new()`
resuelve el asset y devuelve la fuente REAL (si no encuentra el `.pft`, cae al
stub).

Conteos medidos: `twenty-minute-roman-17` -> **103 glyphs**, `roman-bold` -> 92,
`roman` -> 92, `fine-print` -> 133, y `Asheville-Sans-14-Bold` (SDK) -> **294**,
que es exactamente el numero de glyphs que declara su `.fnt` de origen
(validacion cruzada independiente).

## Como se crackeo (esto es lo importante)

El SDK de Playdate trae **`.fnt` (fuente, TEXTO PLANO, documentada) y `.pft`
compilados con los mismos nombres**. Eso da un **oraculo exacto**: se sabe la
lista de glyphs con sus anchos, y se tiene el binario.

- SDK: `/Users/mac/Developer/PlaydateSDK`, compilador `bin/pdc`.
- Oracle: `mini.pft`, compilado por nosotros recortando
  `Asheville-Sans-14-Bold.fnt` a 16 glyphs conocidos
  (`space ! " # $ % & ' ( ) * + , - . /`, anchos 3,2,5,9,8,12,11,3,5,5,8,8,3,6,2,6).
  Montaje reproducible en `/tmp` -> `~/.hermes/cache/scratch/minifont`.
- Resultado: **16/16 avances correctos** con el formato de abajo.
- Validacion cruzada: **96/96** glyphs en `twenty-minute-roman-17.pft` con el
  script suelto `tools/pft_loose.py`.

## Formato real (corrige la spec publicada)

```
cabecera:  char[12] "Playdate FNT" + uint32 flags
           flags & 0x80000000 -> comprimido con zlib
           flags & 0x00000001 -> tiene caracteres por encima de U+1FFFF
si comprimido, cabecera de fuente:      <-- OJO: 16 bytes, la spec dice 12
           uint32 tamano_descomprimido, uint32 max_w, uint32 max_h,
           uint32 reservado (visto 0)
page list: uint8 glyph_w, uint8 glyph_h, uint16 tracking, 64 B page_usage,
           uint32 offset por pagina presente
           <-- OJO: las paginas van SEGUIDAS tras esta lista; esos offsets NO
               son el inicio de cada pagina (en mini.pft valian 860 y la pagina
               estaba en 72)
pagina:    uint24 reservado, uint8 num_glyphs, 32 B glyph_usage,
           uint16[num_glyphs] tabla de offsets de glyph
           <-- OJO: la spec NO menciona esta tabla
           registros de glyph, consecutivos
glyph:     uint8 advance, uint8 n_short, uint16 n_long,
           n_short x (uint8 codepoint_en_pagina, int8 kerning),
           padding a multiplo de 4 desde el INICIO del registro,
           n_long x (uint24 codepoint, int8 kerning),
           Image Cell (16 B cabecera + color [+ alpha si flags%4 in (1,3)])
```

Invariante que localiza cabeceras de pagina: **`popcount(glyph_usage) == num_glyphs`**.
El inicio de pagina **no siempre es `68 + 4*npaginas`**: en fuentes antiguas la
tabla de offsets trae mas entradas que paginas. Medido:
`mini.pft` -> 72 = 68+4; `Asheville-Sans-14-Bold.pft` -> 92 = 68+24;
`twenty-minute-roman-17.pft` -> **100** (usar 92 da 0 glyphs).

El validador de un registro exige que el box de la Cell sea **exactamente**
`glyph_w x glyph_h` (`clip_l+clip_w+clip_r == glyph_w`, `clip_t+clip_h+clip_b == glyph_h`).
Eso es lo que evita los falsos positivos que hicieron perder tanto tiempo: sin esa
condicion, un filtro laxo sobre todo el buffer devuelve ~100 "cells" que al
renderizarlas salen **bloques negros solidos**.

## El fallo residual (donde retomarlo)

`tools/pft_loose.py` (suelto, con `pstart=100` fijo) recupera **96/96** glyphs en
roman-17. El mismo recorrido dentro de `pd/pft.py` (`_walk_page`) recupera 12.
Los dos fallos que quedaban, ya corregidos (los dos eran de bulto):

1. `_try_record` calculaba el fin del registro como
   `_glyph_surface(...) + size`, pero `_glyph_surface` **ya consume los dos
   bitmaps** -> sumaba el ultimo bitmap dos veces y desincronizaba el recorrido
   (96 glyphs -> 12). Ahora el fin es `q + CELL_HEADER + size`.
2. La tinta estaba invertida: `_glyph_surface` pintaba donde el bit era 1, pero
   el glyph `/` del `.pft` minimo demuestra que **bit 0 = tinta** (color todo
   `0x00` + alpha en diagonal). El plano de alpha da la opacidad.

Aparte, `_find_page` localiza la cabecera por el invariante
`popcount(usage) == num_glyphs` (el inicio no es siempre `68 + 4*npaginas`), y
`_parse_page` prueba las dos estrategias (fiarse de la tabla uint16 o solo
avanzar) y se queda con la que recupere mas glyphs.

```
script suelto:  avances [10, 3,  8, 15, 11, 14, 17,  3,  6,  6, ...]
_walk_page:     avances [10, 3, 15, 14,  3,  6, 10, 10,  3,  9, ...]
```

`_try_record` (modulo) RECHAZA un registro valido que `try_rec` (script) acepta,
en torno al offset 452-460, y a partir de ahi el recorrido se desincroniza.
Las dos funciones se escribieron para ser equivalentes, asi que el siguiente paso
es **diffearlas linea a linea** (el script tiene la version que funciona) o
directamente reemplazar `_walk_page` por el bucle del script.

Dato util: `_walk_page(100, trust_table=True)` da 20 glyphs y `False` da 12,
mientras el script da 96 -> el problema esta en el **validador**, no en la
estrategia de recorrido.

## Fuentes del SDK para seguir validando (todas con su .fnt al lado)

- `Resources/Fonts/Asheville/Asheville Sans 14 Bold/Asheville-Sans-14-Bold.fnt`
  + `Disk/System/Fonts/Asheville-Sans-14-Bold.pft` (293 lineas de .fnt)
- `Disk/System/Fonts/Roobert-11-Medium.pft`, `Roobert-24-Medium.pft`, etc.

## Herramientas de esta investigacion

| archivo | que hace |
|---|---|
| `tools/pft_loose.py` | **el que funciona**: 96/96 glyphs en roman-17 |
| `tools/pft_decode.py`, `pft_final.py` | validacion 16/16 contra el .fnt minimo |
| `tools/pft_pages.py` | localiza paginas por el invariante popcount |
| `tools/pft_oracle.py` | parseo del .pft minimo contra la tabla de offsets |
| `tools/pft_walk.py`, `pft_dump*.py`, `pft_findhdr.py`, `pft_scan.py`, `pft_brute.py` | pasos intermedios del analisis |

## Utilidad de depuracion incluida

`pd/pft.py` trae `render_text(font, texto)` que compone un texto con los glyphs de
la fuente sobre una superficie. Sirve para validar visualmente sin arrancar el juego.

```python
from pd import pft
f = pft.load_font("common/images/twenty-minute-roman-17.pft")
img = pft.render_text(f, "Smolitaire 1.0")
pygame.image.save(img, "font_test.png")
```
