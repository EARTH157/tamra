# M5 results

## Exit run

M5 adds OCR for scanned PDF pages: pages with no text layer, or with a broken one, are read by
PP-OCRv5 mobile on onnxruntime and numpy, indexed as that page's text, and highlighted on the real
page image from the character boxes OCR found. `dist/Tamra/Tamra.exe` was built on the dev
machine with `./scripts/build.ps1` as is (including `npm ci`) at commit `26daf04` plus the Task 9
changes (`LICENSE` and `NOTICE` added to the bundle), in 106 s, and checked with
`scripts/exe_smoke.py` and the packaged selfcheck.

- **Build output:** `Tamra.exe` is 14.4 MB (14,445,406 bytes) and the `dist/Tamra` folder is
  248 MB (260,418,544 bytes). That is the same as M4 at this precision. The previous local build
  was 14,414,632 and 260,373,137 bytes, so the `tamra.ocr` code, `LICENSE` and `NOTICE` add about
  45 KB. There is no new dependency, and the OCR models are downloaded, not bundled.
- **Packaging:** the indexer imports `tamra.ocr` statically, and PyInstaller found it. No
  `hiddenimports` change was needed.
- **Models:** bge-m3 int8, the three PP-OCRv5 files (29.4 MB, sha256-pinned in
  `scripts/assets.json` and `catalog.json`), and the Qwen2.5-0.5B-instruct Q4_K_M dev model.
  `scripts/fetch_assets.py` fetches all of them; it already did so after Task 3.
- **Tests:** `uv run pytest` gives 896 passed (21 `assets` tests deselected).
  `uv run pytest -m assets -s` gives 21 passed. The UI suite gives 498 passed in 26 files. Ruff
  check and format are clean.

**What the exit criterion covers**

- "A scanned Thai PDF becomes searchable and highlightable" (spec §13): the packaged smoke's new
  `scanned` step (below).
- The controller checked the UI against the dev core in the browser: the Settings row, "Reading
  page x of y", the "p. 1 · OCR" label, and the per-line OCR boxes on the page in the source panel.
  This was done after the scan fix (see "What changed from the plan").
- The real window (pywebview, WebView2) is the owner's check.

## OCR quality gate

`tests/test_ocr_quality.py`, on the committed fixtures in `tests/fixtures/ocr/`: 6 Thai and
6 Chinese Edge-printed PDFs from the spike, plus 2 English pages. The numbers below are from the
final commit `26daf04`. The per-page figures come from the exit run's `-m assets -s` output.

| Gate | Limit | Result | Per page |
|---|---|---|---|
| Thai 300 dpi CER | ≤ 0.06 | 0.0069 | 0.011, 0.002, 0.0, 0.016, 0.005, 0.007 |
| Thai 200 dpi image-only scan CER | ≤ 0.02 | 0.0085 | 0.011, 0.002, 0.0, 0.023, 0.009, 0.005 |
| Thai scan line recall (paragraph heads, in order) | 1.0 on every page | 1.0 | 1.0 on all 6 |
| Thai, page turned 3° | ≤ 0.10 | 0.073 | 0.186, 0.002, 0.068, 0.146, 0.009, 0.026 |
| Chinese, page turned 3° | ≤ 0.01 | 0.0025 | 0.0, 0.0, 0.004, 0.0, 0.011, 0.0 |
| Chinese photocopy (noise and a 1° tilt) CER | ≤ 0.03 | 0.0273 | 0.0, 0.043, 0.0, 0.057, 0.0, 0.063 |
| English word hit | ≥ 0.9 | 1.0 | 1.0, 1.0 |

The scan, line-recall and tilt gates are new since the plan. They were added with the scan fix.

**Seconds per page** (dev machine, 4 onnxruntime threads, render plus read): Thai 300 dpi 2.06 s,
Thai scan 1.98 s, Chinese photocopy 2.99 s. These vary by about ±0.1 s between runs. In the
end-to-end check of Task 6 (real `Core`, a 3-page Thai scan), warm pages took 3.03, 2.65 and
2.89 s, including the 300 dpi render. So expect about 2–3 s per page.

**Against the spike.** The spike's own numpy pipeline (`np_route`) had a Chinese photocopy CER of
0.100, where RapidOCR got 0.021. Two changes closed the gap, both in the numpy port of RapidOCR's
geometry:

- the unclip, made faithful to pyclipper, including Clipper's half-away-from-zero rounding;
- page scaling to multiples of 32.

With these, the pipeline matches RapidOCR on the spike's set (0.021), and the committed fixtures
measure 0.027. The scan fix then cut Thai 300 dpi from 0.023 to 0.007. Its slow box score and
merged marks are described under "What changed from the plan".

## Broken text layers (decision 6)

These were measured on the owner's folder, read only: 17 PDFs, 94 pages. The script and its
output stay in the gitignored SDD folder, and nothing from the folder was committed. The
measurement was re-run after each change to the checks (the echo rule fix, the image skip and its
narrowing), and the flags never changed: **6 of 94 pages flagged**.

| File | Page | Chars | Thai | Echoes | Ink per char | OCR |
|---|---|---|---|---|---|---|
| `2.1.6 Table.pdf` | 1 | 1100 | 767 | 16 | 6.35 | yes (echoes) |
| `2.1.7 UPS.pdf` | 1 | 741 | 419 | 5 | 2.55 | yes (echoes) |
| `2.1.7 UPS.pdf` | 2 | 1522 | 489 | 5 | 0.48 | yes (echoes) |
| `2.1.7 UPS.pdf` | 3 | 1766 | 597 | 5 | 0.35 | yes (echoes) |
| `2.1.7 UPS.pdf` | 4 | 1214 | 833 | 5 | 0.00 | yes (echoes, invisible) |
| `2.1.7 UPS.pdf` | 5 | 660 | 429 | 5 | 0.00 | yes (echoes, invisible) |
| `2.4.1.1 Tang nano 4k-Thai.pdf` | 1 | 709 | 354 | 0 | 5.93 | no |
| QDrone 2 Data Sheet | 1–2 | 1821, 2561 | 0 | 0 | 5.65, 3.35 | no |
| AX-Digital-Info | 1–7 | 645–1401 | 0 | 0 | 3.63–7.85 | no |
| AX-Digital-Info | 8 | 59 | 0 | 0 | 0.00 | no (under 100 chars) |

**Thresholds** (`tamra.ingest.parsers`):

- **Garbled Thai.** At least `ECHO_MIN = 3` echoes, where an echo is a Thai letter with marks
  repeated after one letter (the `บอร์ดร์` pattern), or a short line repeating the previous
  line's tail (`สรุป\nรุ`).
  - The browser prints have 5 to 44 echoes a page; clean Thai has 0.
  - A line equal to the line above, such as a repeated table cell, does not count.
- **Invisible text.** A page with at least `INVISIBLE_MIN_CHARS = 100` characters whose 18 dpi
  render has under `INVISIBLE_INK_PER_CHAR = 0.1` dark pixels per character.
  - The check is skipped when one image covers at least `SCAN_IMAGE_AREA = 0.5` of the page. Such
    a page is a searchable scan, and the image hides its text layer on purpose.
- **Margins.** The closest good page to the ink threshold is an English print at 0.44 ink per
  character, about 4× the limit. The other 12 PDFs (77 pages of English manuals, datasheets and
  web prints) have 0 echoes. The only Thai on a good English page is 24 letters on one
  datasheet, with 0 echoes.

## Packaged selfcheck

Exit code 0. All five checks are `ok` (`sqlite`, `documents`, `providers`, `embedding`, `llm`).
It was run as `Tamra.exe selfcheck --embed-model-dir .models\bge-m3 --llm-model
.models\qwen2.5-0.5b-instruct-q4_k_m.gguf --report r.json` through `Start-Process -Wait`, in
13.3 s.

| Measure | Value |
|---|---|
| Embedding (packaged) | 5.8 passages/s, model load 1.7 s |
| Local LLM start | 1.65 s, first token 0.03 s |
| Local LLM speed | 194 tokens/s (50 tokens) |
| GPU used (Vulkan) | yes |
| Catalog models | 5 (the three LLM tiers, bge-m3, and PP-OCRv5) |

## Smoke test

The exe ran in `--dev` mode with a scratch data folder, and a scratch models folder holding
hard links to bge-m3 and `ppocr-v5`. The new `link_models` replaces `link_embedding_model` and
stops with a clear message when either folder is missing. The run took 42 s. Exit code 0, no
problems, 3 of 3 answerable questions cited, and no `llama-server.exe` left over.

The M1 to M4 steps are unchanged and still pass:

- startup 3.1 s, and the dev GGUF imported as uncatalogued;
- the two projects (6 files) indexed in 1.5 s;
- Thai, English and Chinese answers cited with provider `local`, and the not-found reply for the
  off-topic question;
- a `think: true` request accepted, and the folder watcher in 2.5 s;
- attribution (first call 1.11 s, repeat 0.77 s, "strong"), locate with 4 highlight boxes, the PDF
  page PNG (0.078 s at scale 1.5), and the text route;
- API mode with the throwaway key: provider `openai`, then back to `local`; the key was deleted
  and the deletion checked;
- the M4 project steps: scope, move, rename with a 409 on a clash, rebuild, and delete.

**New in M5: the scanned page** (`check_scanned`, step 8):

| Step | Result |
|---|---|
| `GET /api/models` → `ocr.installed` | `true` (the linked `ppocr-v5` folder) |
| Make the scan | `tests/fixtures/ocr/th_0.pdf` (committed, Thai lab rules) rendered by `docgen.image_pdf` at 200 dpi into an image-only `lab-rules-scan.pdf` in a "Scans" folder |
| Create project "Scans" and wait | 1 file indexed, no problems, in 4.0 s. That includes loading the OCR models, the 300 dpi render, OCR and embedding |
| Ask "ผู้ใช้ห้องปฏิบัติการต้องผ่านการอบรมอะไรก่อนเริ่มใช้งานเครื่องมือ" | the answer cites `[n]`; its one source is labelled "p. 1 · OCR", and its location has `"ocr": true` |
| `GET /api/sources/{id}/{n}/locate` | `found: true, kind: "pdf", page: 1, ocr: true`, with 20 highlight rectangles from the stored OCR boxes |

## What changed from the plan

- **Scan fix** (`7417cf2`, then `26daf04` after review).
  - **Why.** The controller's browser check on a 200 dpi image-only scan of `th_0` found line 2
    missing. The detector had joined the line with its upper marks, and the fast box score
    (0.49) fell under the 0.5 threshold. The gate had no image-scan case, so it missed this.
  - **Slow score.** The box is scored over its own pixels (PaddleOCR's `score_mode="slow"`),
    not over its mostly empty rectangle.
  - **Nested-mark merge** (`NESTED = 0.4`). A box that lies mostly inside a larger one, usually
    Thai upper marks found apart from their line, is merged into that line, so the marks are
    read with it.
    - The first version measured the overlap on axis-aligned boxes. At a 3° tilt that merged
      whole lines (Thai CER 0.294, Chinese 0.393).
    - `26daf04` confirms each candidate in the host line's own frame and keeps the host's tilt.
      Chains of nested boxes go to the outermost host.
  - **Mean-y reading order.** Lines are ordered on the box's mean y instead of its top-left y,
    so a merged table cell no longer splits its row.
  - **New gates:** the Thai scan CER, in-order line recall, and the 3° tilt pages.
- **Image skip, narrowed** (`bd7f177`, then `5d59b10`).
  - The invisible-text check first skipped every page with an image object. That avoided a
    13 s render on 300-page searchable scans, but it also turned the check off for
    `2.1.7 UPS.pdf`, whose pages all carry 4 to 34 images.
  - It now skips only a page where one image covers at least half of it. `2.1.7` p4 and p5 are
    flagged as invisible again, and a 300-page searchable scan parses in 0.20 s.
- **Broken-model flag** (`f3a66fb`). When the OCR model fails to load, the core remembers this
  and does not try again for every file. Installing the model again clears the flag, and so does
  restarting the app.
- **Smaller changes:**
  - Start-up re-queues the files that waited for OCR when the model is already installed. This
    covers a crash between the download and the re-queue.
  - The echo rule is stricter: a letter is required, and repeated table cells are ignored.
  - When OCR finds no text, the page keeps its text-layer unit.
  - The Task 3 fixtures passed a font-embedding check: every embedded font is fsType `0x0008`,
    recorded in `tests/fixtures/ocr/README.md`.
  - `LICENSE` and `NOTICE` now ship in the exe folder (`packaging/tamra.spec`).
  - Spec §10 "Not in M5" now says "rotated pages beyond a small tilt", to match the 3° gate.

## Not done in M5

- **Small margin on the Chinese photocopy gate:** 0.027 against a limit of 0.03. onnxruntime's
  float kernels differ between CPUs, so the result may differ on other machines.
- **Lost lines at a 2–3° tilt.** At 2–3° the Thai pages `th_0` and `th_3` still lose a line
  (line recall 0.88 and 0.86 at 3°). This also happens with no merge at all, so the merge is not
  the cause. It was not investigated further.
- **The `th_3` scan CER rose** from 0.013 to 0.023 after the host-frame nesting (`26daf04`). It is
  still within the gate, and the cause was not found.
- **A damaged model file of the right size cannot be repaired from Settings.** `installed()`
  checks sizes only, so Settings shows the model as installed. Delete the files by hand to
  download them again.
- **Rebuild and reconcile wait for the current file.** OCR checks for a stop between pages, but a
  rebuild or reconcile request waits for the file in progress. A 300-page scan takes about
  15 minutes.
- **Tiled or nested scans pay the 18 dpi ink render.** This affects speed only. A scan made of
  several images, none of which covers half the page, is rendered once to check its ink.
- **The wrong note for a broken model.** When the OCR model is installed but cannot load, the
  needs-OCR note still says to download it in Settings.
- **Junk single-character lines.** Some pages get a few stray one- or two-character lines, such
  as Thai marks read alone: 2 to 4 per fixture page set (3 on Thai 300 dpi, 4 on the Thai scan).
  The data did not support a score filter: the junk scored up to 0.995, and the real one-digit
  table cells scored 0.997 and 0.999.
- **Recognition still drops some Thai marks**, for example "ผู้เริมต้น" on the `th_3` scan. This
  is the recogniser, not detection.
- **No real scanner or camera images were tested.** The photocopy and tilt pages are synthetic,
  made from Edge-printed PDFs.
- **Chinese line recall is not gated.** With the current metric it would be 0.75 on `zh_5`.
- **Out of scope (decision 7):**
  - OCR of image files (PNG, JPG) and of images inside DOCX;
  - vertical or Traditional Chinese;
  - rotated pages beyond a small tilt;
  - a GPU path, an OCR language setting, and re-OCR on demand.
- **Speed.** About 2–3 s per page on the dev machine with 4 threads. There was no further
  optimisation.
- **Model source.** The OCR models come from ModelScope (RapidAI's v3.10.0 export), not from a
  Tamra-owned mirror.
- **The owner's real window check** (pywebview, WebView2) of the OCR row, the reading progress
  and an OCR highlight. The smoke uses the exe's dev mode and opens no window.
