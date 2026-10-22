# M5 OCR engine spike — 2026-10-21

Question (spec §10): which OCR engine should Tamra use for PDF pages that have no text layer, in
Thai, simplified Chinese and English? The engine must:

- work offline, on CPU, without admin rights;
- have Apache-2.0-compatible licenses for both code and weights;
- give boxes the viewer can highlight.

Machine: AMD Ryzen 7 250 (8 cores / 16 threads), Windows 11 Pro 10.0.26200. Software: CPython
3.12.15, onnxruntime 1.30.0, numpy 2.5.3 and pypdfium2 5.13.0 (the project's versions), in a
separate venv under the spike folder. The project `.venv` was not touched.

## Recommendation

**Use the PP-OCRv5 mobile ONNX models on the onnxruntime + numpy we already ship, with Tamra's
own small pre- and post-processing. Do not bundle the RapidOCR package or Tesseract.**

- One detection model (`ch_PP-OCRv5_det_mobile`).
- The Thai recogniser (`th_PP-OCRv5_rec_mobile`, Thai + Latin) on every line.
- The Chinese recogniser (`ch_PP-OCRv5_rec_mobile`, Chinese + Latin) only on lines where the Thai
  recogniser scored below 0.9. This is the `np_route` config below.
- Cost: three model files (29.4 MB), no new Python dependency, and every character gets a box.

How it compares (300 dpi; "bag" is the order-free error, defined under Method):

| | Thai | Chinese | English word hit |
|---|---|---|---|
| `np_route` | CER 0.039, bag 0.018 | CER 0.000 | 0.94 |
| Tesseract 5.5.3, tessdata_fast | CER 0.173, bag 0.068 | CER 0.080 | 0.94 |

`np_route` takes 2.6–6 s per page on 8 threads; Tesseract takes 0.8–1.3 s on 1 thread.

**Open issue before shipping:** on the photocopy variant (noise plus a 1° tilt), the numpy port
loses Chinese characters: CER 0.100 against RapidOCR's own pipeline at 0.021 with the same models.
Thai and English hold up. The M5 plan should port RapidOCR's crop geometry (Apache-2.0) more
faithfully and gate it on this test set: Chinese photocopy CER ≤ 0.03.

## Method

**Test set: 20 pages.** Built by `build_set.py`; the throwaway code is in
`.superpowers/sdd/2026-10-21-tamra-m5-ocr/spike/`.

- **English, 6 pages.** Text-layer PDFs from the owner's `CMU` folder, opened read-only:
  - QDrone 2 Data Sheet p1–p2;
  - OptiHub Quick Start p1;
  - workspace_setup p2;
  - AX-Digital-Info p1;
  - qbot user manual p9.

  Several are two-column datasheets with tables.
- **Thai, 8 pages.**
  - 6 pages written as HTML and printed with headless Edge, in Angsana New, Tahoma, Browallia
    New, Cordia New and Leelawadee UI at 10.5–18 pt. They include tables and English terms.
  - 2 owner pages: `2.1.7 UPS.pdf` p2 (a spec table, mostly English under Thai headings) and
    `2.4.1.1 Tang nano 4k-Thai.pdf` p1 (a Thai list in a book font, cropped to the region below
    the pinout figure).
- **Chinese, 6 pages.** HTML printed with Edge, in Microsoft YaHei and SimSun at 10.5–12 pt, with
  headings and tables. They are short, about 210–300 characters each.

**Ground truth.** The brief asked for the text layer (`get_text_range`), but the text layer was not
usable for most Thai pages:

- **Edge-printed Thai PDFs:** the text layer repeats cluster fragments (for example `สรุป` comes
  out as `สรุป\nรุ`) and splits sara am. The ground truth for every Edge page is therefore the HTML
  source text.
- **Owner Thai pages:** they have the same problem, or worse:
  - `2.1.6 Table.pdf` and `2.1.7 UPS.pdf` (web pages printed from a browser) have the same
    duplicated fragments, plus text that is never drawn. `2.1.7` p1 is mostly invisible text, and
    the brief already noted that p4 renders invisible.
  - `2.4.1.1` drops tone marks.

  For the two owner pages kept, I typed the ground truth from the render (`handgt.py`). I dropped
  `2.1.6` p1 (text inside photos that has no truth) and `2.1.7` p1.
- **English and Chinese:** the text layer is used as is; for Edge-printed Chinese, the HTML source
  text is used.

**Scans.** Each page was rendered with pypdfium2 at 300 dpi and at 150 dpi. A photocopy variant was
made from the 300 dpi render: grayscale, Gaussian noise σ = 18, and a 1° rotation. The engines
saw only the PNG images.

**Metrics.**

- **CER:** Levenshtein distance divided by truth length, after NFKC. For English, whitespace runs
  become one space; for Thai and Chinese, all whitespace is removed.
- **Bag:** an order-free lower bound of CER, max(missing, extra characters) / truth length. CER
  also penalises reading order: a two-column table read column by column costs about 0.7 CER with
  every character right (Tesseract on the UPS page), so read bag alongside CER.
- **English word hit:** the share of truth words, as a multiset, found in the OCR output.
- **Speed:** wall time per page, single process. onnxruntime uses its default intra-op thread
  count (the 8 physical cores); this Tesseract build has no OpenMP, so it runs on 1 thread.
- **Load:** first-call model load, measured separately.

**Configs.**

| Config | What it runs |
|---|---|
| `np_route` / `np_auto` | Numpy-only pipeline (`np_ocr.py`): DB detection, then connected components, rotated boxes from second moments, bilinear crops and CTC greedy decoding. `np_route` runs the Thai recogniser first and the Chinese one only on weak lines; `np_auto` runs both on every line and keeps the higher-scoring text. |
| `ro_*` | The RapidOCR 3.10.0 package (opencv-based). `ro_auto` = v5 det + ch rec + th rec, best score per line; `ro_auto_nocls` is the same with the 180° line classifier off; `ro_v5th` = Thai rec only; `ro_v6s` / `ro_v6t` = PP-OCRv6 small / tiny multilingual (no Thai). |
| `tess_*` | Tesseract 5.5.3 CLI with TSV output, `--psm 3`. `tess_fast` / `tess_best` use the per-language pack (`eng`, `tha+eng`, `chi_sim+eng`); `tess_fast_all` uses `tha+chi_sim+eng` on every page, because Tamra will not know a scanned page's language. |

## Results

Each cell: CER / bag, then seconds per page. English also shows the word hit. For Thai, `[e, o]`
is the mean CER on the 6 Edge pages and on the 2 owner pages.

**300 dpi**

| Engine | English | Thai | Chinese |
|---|---|---|---|
| **np_route** | 0.330 / 0.044, hit 0.94, 6.06 s | **0.039 / 0.018** [e 0.040, o 0.036], 2.60 s | **0.000 / 0.000**, 3.33 s |
| np_auto | 0.329 / 0.042, hit 0.95, 8.71 s | 0.038 / 0.017 [0.040, 0.035], 3.27 s | 0.000 / 0.000, 3.30 s |
| ro_auto_nocls | 0.315 / 0.047, hit 0.93, 7.36 s | 0.052 / 0.040 [0.061, 0.022], 2.52 s | 0.000 / 0.000, 2.96 s |
| ro_auto (cls on) | 0.350 / 0.065, hit 0.88, 8.26 s | 0.131 / 0.119 [0.167, 0.022], 2.98 s | 0.000 / 0.000, 3.52 s |
| ro_v5th | 0.350 / 0.065, hit 0.90, 3.98 s | 0.129 / 0.118, 1.61 s | — |
| ro_v6s | 0.333 / 0.052, hit 0.92, 3.23 s | 0.833 (no Thai in model), 1.08 s | 0.000 / 0.000, 1.18 s |
| ro_v6t | 0.339 / 0.059, hit 0.90, 1.45 s | — | 0.000 / 0.000, 0.86 s |
| tess_fast | 0.336 / 0.054, hit 0.94, 1.26 s | 0.173 / 0.068 [0.095, 0.409], 0.90 s | 0.080 / 0.078, 1.01 s |
| tess_best | 0.334 / 0.054, hit 0.94, 2.20 s | 0.173 / 0.067 [0.096, 0.405], 1.35 s | 0.089 / 0.084, 1.50 s |
| tess_fast_all | 0.346 / 0.061, hit 0.94, 2.07 s | 0.171 / 0.066 [0.095, 0.400], 1.16 s | 0.080 / 0.078, 1.29 s |

**Photocopy (300 dpi, grayscale, noise, 1° tilt)**

| Engine | English | Thai | Chinese |
|---|---|---|---|
| **np_route** | 0.331 / 0.037, hit 0.94, 5.41 s | 0.131 / 0.023 [e 0.098, o 0.228], 2.34 s | **0.100 / 0.045**, 3.21 s |
| np_auto | 0.330 / 0.036, hit 0.94, 7.52 s | 0.130 / 0.024, 2.96 s | 0.098 / 0.043, 3.27 s |
| ro_auto (cls on) | 0.345 / 0.066, hit 0.87, 7.41 s | 0.134 / 0.101 [0.144, 0.103], 2.82 s | 0.021 / 0.021, 3.38 s |
| ro_v6s | 0.348 / 0.071, hit 0.91, 3.27 s | — | 0.000 / 0.000, 1.61 s |
| tess_fast | 0.354 / 0.064, hit 0.92, 1.14 s | 0.173 / 0.066 [0.091, 0.419], 0.76 s | 0.089 / 0.087, 0.84 s |
| tess_best | 0.352 / 0.064, hit 0.92, 2.05 s | 0.172 / 0.063, 1.17 s | 0.095 / 0.091, 1.30 s |
| tess_fast_all | 0.367 / 0.069, hit 0.92, 1.89 s | 0.172 / 0.064, 0.98 s | 0.150 / 0.142, 1.09 s |

**150 dpi.**

- np_route: English hit 0.95 (3.44 s); Thai 0.068 / 0.011 (1.47 s); Chinese 0.011 / 0.002 (2.47 s).
- tess_fast: English hit 0.89 (0.78 s); Thai 0.153 / 0.053 (0.63 s); Chinese 0.063 / 0.027 (0.74 s).

**Threads, np_auto at 300 dpi** (Thai / Chinese / English s/page):

- 1 thread: 5.75 / 5.50 / 15.3 s.
- 4 threads: 4.24 / 4.16 / 11.0 s.
- Default (8): 3.27 / 3.30 / 8.71 s.

The speed-up flattens because a large share of the time is numpy pre- and post-processing in
Python; that is the first thing to optimise in M5.

**What the numbers say:**

- **English is a tie.** All engines reach a word hit of 0.92–0.95. English CER sits at about 0.33
  for every engine; that is reading order on the two-column datasheets (bag 0.04–0.07), not
  recognition.
- **Thai: PP-OCRv5 wins clearly.** Tesseract's Thai is about 2.5× worse on the clean Edge pages
  (0.095 vs 0.040). On the owner pages it loses mostly to reading order (UPS table, CER 0.73,
  bag 0.007). On the Tang nano page it reaches 0.08 against np_route's 0.05.
- **RapidOCR's default 180° line classifier hurts Thai.** It flips Thai lines that have marks above
  and below: CER goes from 0.052 to 0.131. Leave it off: scanned pages are upright.
- **Chinese:** PP-OCR is perfect at 300 and 150 dpi; Tesseract `chi_sim` has 8% CER. Running all
  three Tesseract languages costs Chinese accuracy on the photocopy (0.150).
- **Choosing a recogniser by score is fragile across model families.** The PP-OCRv6 multilingual
  recogniser outputs confident Latin garbage on Thai lines, so `ro_auto6` / `np_auto6` lose Thai
  (CER 0.12–0.18). Within the PP-OCRv5 family, best score per line works, and the Thai-first
  cascade (`np_route`) matches it while saving 1–3 s per page.
- **150 dpi is nearly as good as 300 dpi** for PP-OCR: detection runs at a long side of ≤ 2000 px
  either way, and recognition crops are resized to 48 px high. 300 dpi is still the safer default
  for small Thai marks (0.039 vs 0.068).

**Photocopy gap.** On the noisy tilted page, the numpy port drops Chinese characters with high
confidence: for example 飞行 → 飞 and 八十 vanish. Cropping from the page pre-scaled to 2000 px
(`NP_PRESCALE=1`) did not help: Chinese photocopy 0.103. RapidOCR, with the same models, uses
opencv contours, minimum-area rectangles, polygon scoring and a cubic perspective warp, and gets
0.021. The difference is therefore in the crop geometry, not the models.

## Boxes

| Engine | Boxes it gives |
|---|---|
| np_route (numpy) | Line boxes as rotated rectangles (centre, size, angle), plus a box for every recognised character from the CTC time steps, all in image pixels with a top-left origin. A visual check on the tilted Thai page showed the character boxes on the glyphs (`box_th07_copy.png` in the spike folder). |
| RapidOCR package | Line quadrilaterals in image pixels; `return_word_box=True` adds word or character boxes, which it computes the same way (CTC). |
| Tesseract CLI | TSV with word boxes (level 5) and line, paragraph and block boxes, in image pixels with a top-left origin. Character boxes need hOCR (`hocr_char_boxes`) or the C API. |

**Mapping to `pdf_rects`.** `viewer.pdf_rects` returns `(x0, y0, x1, y1)` as fractions of the page
as rendered: top-left origin, rotation and crop box applied. `PdfPage.render` draws that same
oriented page, so an OCR box maps with no transform: `x / bitmap_width`, `y / bitmap_height`. For
a rotated line box, take the axis-aligned bounding box of the character's slice (`_aabb` in
`np_ocr.py`).

## Integration shape

**Call.**

- A new `tamra.ocr` module holds three `onnxruntime.InferenceSession`s (detection, Thai
  recognition, Chinese recognition). It is loaded lazily on the first `needs_ocr` page, takes
  0.3 s, and is kept for the indexing run.
- Per page:
  1. Render with `page.render(scale=300/72).to_numpy()` (0.10 s for a Letter page; 150 dpi takes
     0.03 s).
  2. Detect (numpy DB post-processing).
  3. Crop rotated lines.
  4. Run the Thai recogniser on every line, and the Chinese recogniser on lines with a score
     below 0.9.
  5. Drop lines with a score below 0.5.
  6. Return the lines (text, score, character boxes as fractions).
- No new Python dependency: no opencv, shapely, pyclipper, PIL or omegaconf. It runs on the
  indexer's background worker; cap the intra-op threads (for example 4) so the UI stays
  responsive.

**Storage.** PDF character boxes are not stored for text-layer pages (§4), because pdfium
recomputes them. OCR is too slow to recompute when the viewer opens, so for each OCR'd page store:

- the page text (lines joined with `\n`; this is what `get_text_range` would have returned);
- one box per character, as fractions, 1:1 with the text's non-newline characters.

Store them in SQLite, as a blob per page. `pdf_rects` for an OCR page groups the characters in
`[start, end)` by line and returns one rectangle per line run: x0 of the first character, x1 of
the last, and the line's y0/y1. The viewer's form is then unchanged (§10).

**Models to pin** (ModelScope, RapidAI/RapidOCR tag v3.10.0; the sha256 values match RapidOCR's
`default_models.yaml`; the character dictionary is embedded in each recogniser's ONNX metadata
under `character`):

| File | URL | Bytes | sha256 |
|---|---|---|---|
| ch_PP-OCRv5_det_mobile.onnx | https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx | 4,819,576 | 4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae |
| th_PP-OCRv5_rec_mobile.onnx | https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/rec/th_PP-OCRv5_rec_mobile.onnx | 7,915,294 | de541dd83161c241ff426f7ecfd602a0ba77d686cf3ab9a6c255ea82fd08006e |
| ch_PP-OCRv5_rec_mobile.onnx | https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile.onnx | 16,631,306 | 5825fc7ebf84ae7a412be049820b4d86d77620f204a041697b0494669b1742c5 |

The total is 29.4 MB. The catalog (§9) uses Hugging Face URLs. PaddlePaddle publishes these
models on Hugging Face, under Apache-2.0, only in Paddle format: for example
`PaddlePaddle/th_PP-OCRv5_mobile_rec`, `inference.pdiparams`, sha256 `45ec91f2…`. The ONNX
conversions exist only on ModelScope. Either pin the ModelScope URLs as above, or mirror the three
files, which Apache-2.0 allows, to a Tamra-owned Hugging Face repo and keep the same sha256 values.

**Expected indexing cost per scanned page** (this machine, 8 threads, 300 dpi):

- Render: 0.1 s.
- OCR: 2.5–3.5 s for a Thai or Chinese text page, about 6 s for a dense English datasheet page.
- Embedding: about 0.5 s for 1–3 chunks.

That is about 3–7 s per page, so a 100-page scan takes about 5–12 minutes. With 4 threads, expect
about 30% more.

## Licenses and bundle

| | Code | Weights / data | Extra on disk | New Python deps |
|---|---|---|---|---|
| Recommended (numpy + PP-OCRv5 ONNX) | Tamra's own (Apache-2.0) | PaddleOCR PP-OCRv5, Apache-2.0 (PaddlePaddle model cards; RapidOCR's `MODEL_LICENSES.md` records the conversions as Apache-2.0) | 29.4 MB of models, downloaded; no code beyond about 300 lines | none |
| RapidOCR 3.10.0 package | Apache-2.0 | as above; it bundles PP-OCRv6 small (31 MB) | about 165 MB: `cv2` 113 MB (its wheel ships FFmpeg under LGPL-2.1), `rapidocr` 32 MB, PIL 15 MB, shapely 6 MB, omegaconf 2 MB | opencv-python-headless, pillow, shapely, pyclipper, omegaconf, pyyaml, colorlog, tqdm, requests, six |
| Tesseract 5.5.3 (conda-forge `tesseract-5.5.3-he87eeb8_0`) | Apache-2.0 | tessdata 4.1.0, Apache-2.0: `tha`, `chi_sim` and `eng` fast = 7.65 MB; best = 36.1 MB | 57.0 MB of runtime: 28 files, the import closure of `tesseract.exe`, including libcurl, OpenSSL, ICU (33 MB), libarchive and libiconv | none (CLI through subprocess) |

**More on Tesseract:**

- **Source:** taken only from the conda-forge archives (26 packages, 215 MB downloaded), each
  verified against its sha256 from the anaconda.org API and extracted with
  `fetch_tesseract.py`. No installer was run.
- **Licensing:** this build links **libiconv (LGPL-2.1)**; libarchive is the `lgpl_` variant. The
  library is dynamically linked, but it is LGPL; a custom build without libarchive and libcurl
  would avoid it and be much smaller, but that is extra build work.
- **Speed:** each page is a new process, so model load (0.2–1.1 s) is part of every page's time.
  pytesseract is not needed.

**Windows built-in OCR (Windows.Media.Ocr):** out. On this machine it has `en-US` only, and the
Thai and Chinese language features need admin rights to install (checked by the controller; not
measured further).

## Side finding (outside OCR)

Thai PDFs printed from Chromium-based browsers (Edge here; the owner's `2.1.6` and `2.1.7` are
browser prints too) have a **corrupt Thai text layer**, with duplicated cluster fragments and
split sara am. `2.4.1.1` drops Thai tone marks. These pages have a text layer, so they are not
`needs_ocr`, and today they are indexed with broken Thai. M5 could add a rule that OCRs a page
when its Thai text layer looks broken, for example many 1–2-character lines that duplicate the
previous line's tail. That needs its own check.

## Not checked

- **The photocopy fix:** porting RapidOCR's crop geometry into the numpy pipeline was not done.
  It is the open issue above.
- **Variant coverage:**
  - `np_auto6` was not re-run after the rotated-box change, so its photocopy numbers are stale
    and are left out.
  - `ro_auto_nocls` was run at 300 dpi only.
- **Real scans:** no real scanner or camera images were tested; the photocopy variant is
  synthetic, and noise and tilt were not tested separately. No vertical Chinese, rotated pages
  (90°/180°), handwriting or Traditional Chinese.
- **Thai sample:** small (8 pages, 2 from real owner files). The Chinese pages are short
  (about 210–300 characters each).
- **Speed and packaging:**
  - Speed was measured on this machine only, from source. The packaged (PyInstaller) build was
    not measured, and neither was the effect of OCR running beside the UI and embedder.
  - np_route was not timed with 1 or 4 threads; np_auto was.
- **Tesseract:**
  - A minimal custom build (size, licenses) was not attempted.
  - tessdata `script/Thai` was not tried, nor `--psm` settings other than 3, nor its hOCR
    character boxes.
- **PP-OCRv5 server and PP-OCRv6 medium models** were not tried (larger; v6 has no Thai).
- **The broken-Thai-text-layer detector** (side finding) was not built or measured.
