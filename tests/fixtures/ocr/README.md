# OCR quality-gate fixtures

`th_0.pdf` … `th_5.pdf` and `zh_0.pdf` … `zh_5.pdf` are the M5 OCR spike's own HTML test pages
printed to PDF by headless Microsoft Edge. Each `.txt` is the page's source text, the ground truth
for `tests/test_ocr_quality.py`. No user document is among them.

These files are test documents with embedded font subsets, distributed under each font's
embedding permission.

Embedded fonts and their OS/2 `fsType` (read from each embedded font program with pypdfium2's
`FPDFFont_GetFontData`). `0x0008` is "Editable embedding"; no font is "Restricted License
embedding" (`0x0002`).

| File | Fonts (subset) | fsType |
|---|---|---|
| `th_0.pdf`, `th_5.pdf` | Angsana New, Angsana New Bold | `0x0008` |
| `th_1.pdf` | Tahoma, Tahoma Bold | `0x0008` |
| `th_2.pdf` | Browallia New, Browallia New Bold | `0x0008` |
| `th_3.pdf` | Cordia New, Cordia New Bold | `0x0008` |
| `th_4.pdf` | Leelawadee UI, Leelawadee UI Bold | `0x0008` |
| `zh_0.pdf`, `zh_2.pdf`, `zh_4.pdf` | Microsoft YaHei, Microsoft YaHei Bold | `0x0008` |
| `zh_1.pdf`, `zh_3.pdf`, `zh_5.pdf` | SimSun | `0x0008` |

`zh_1.pdf`, `zh_3.pdf` and `zh_5.pdf` also contain an unnamed Type3 font, most likely Edge's
synthetic bold of SimSun, drawn as glyph outlines in the page content. It has no embedded font program and so
no `fsType` of its own.
