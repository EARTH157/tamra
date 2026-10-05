# M3 results

## Exit run

M3 adds source checking: select a sentence in an answer (or click a `[n]` chip), press **Check
source**, compare the answer with the best matching passage, and open the file at that passage
with a highlight. `dist/Tamra/Tamra.exe` was built on the dev machine and checked with
`scripts/exe_smoke.py` and the packaged selfcheck. Build output: `Tamra.exe` is 14.4 MB and the
`dist/Tamra` folder is 249 MB (the same as M2: no new dependency). Models: bge-m3 int8 and the
Qwen2.5-0.5B-instruct Q4_K_M dev model, nothing downloaded. The test suite had 693 passing tests
(12 `assets` tests deselected), the UI suite 334 tests, and the lint and format checks were clean.

**What the exit criterion covers**

- The packaged smoke passes with the new attribution and viewer checks (below).
- The controller checked the UI in the browser against the dev core: see "Visual check".
- The real-window check (pywebview) is the owner's.

The first build ran the steps of `./scripts/build.ps1` one by one, without its `npm ci` step:
`npm --prefix ui run build`, `fetch_assets.py llama` (the pinned zip was already there and
verified), then PyInstaller. After the final review fixes the controller rebuilt with
`./scripts/build.ps1` as is and ran the smoke again (same results as below).

## How the attribution was calibrated

`eval/attribution.jsonl` has 51 handwritten cases in Thai, English and Chinese: exact quotes,
paraphrases, cross-language translations, near-identical sources that differ only in a number,
`[n]` markers, chip (single-source) selections, and unrelated text. `scripts/eval_attribution.py`
runs them with the real bge-m3 and reports each group separately.

| group | cases | right source | label accuracy | unrelated text rejected | strong match for a wrong source |
|---|---|---|---|---|---|
| main (used to choose the constants) | 37 | 28 of 28 | 35 of 37 = 0.946 | 9 of 9 | none |
| heldout (never tuned on) | 10 | 8 of 8 | 8 of 10 = 0.800 | 2 of 2 | none |
| hard_negative | 4 | n/a | n/a | 0 of 4 | n/a |

Constants (from a grid over the main group): window 60 tokens with a stride of 30,
`TRIGRAM_WEIGHT` 0.2, `NUMBER_PENALTY` 0.15 (at most two penalties), `STRONG` 0.83, `PARTIAL`
0.57. The highest score among the unrelated cases is 0.544 and the weakest correct match is
0.602, so the gap is about 0.06.

- **Label misses.** Main: `en-para-rent` (a close paraphrase scores strong) and `chip-only` (a
  short exact quote inside a longer source scores 0.814, just under strong). Heldout:
  `h-en-para-lock` (0.876) and `h-th-para-hq` (0.875), close paraphrases that score strong.
- **Numbers.** A number in the selection that a candidate passage lacks lowers that passage's score
  (12 days against 6 days, rent 18,500 against 21,000). A number that no source has (a misquote
  or a computed total) caps the label at "partial".
- **Ranking.** Cited sources come first (spec section 7.2), then the better score; at most three
  matches.

**Known limitation.** The matcher measures similar wording, not truth. A "strong match" means the
selection reads like a passage in the sources; it does not mean the claim is supported. The four
hard negatives show it: a same-topic claim the documents never make (repaint the walls, 0.693,
partial) and contradictions that reuse the source's wording (MFA by SMS or email, 0.850, strong;
a ten-year sofa warranty against a five-year one, 0.905, strong) are not rejected. The UI says
"strong match" and "partial match", never "verified", and the README says the reader still judges
the claim by reading the highlighted passage. Re-run `uv run python scripts/eval_attribution.py`
if the embedding model ever changes.

## Packaged selfcheck

Exit code 0; all five checks `ok` (`sqlite`, `documents`, `providers`, `embedding`, `llm`), run as
`Tamra.exe selfcheck --embed-model-dir .models\bge-m3 --llm-model
.models\qwen2.5-0.5b-instruct-q4_k_m.gguf --report r.json` through `Start-Process -Wait`.

| Measure | Value |
|---|---|
| Embedding (packaged) | 7.0 passages/s, model load 1.45 s |
| Local LLM start | 2.92 s, first token 0.04 s |
| Local LLM speed | 275.54 tokens/s (64 tokens) |
| GPU used (Vulkan) | yes |
| `providers` probe | WinVaultKeyring, anthropic 1.11.0, 4 catalog models |

## Smoke test

The exe in `--dev` mode with a scratch data folder and a scratch models folder holding only
bge-m3. Exit code 0, no problems, 3 of 3 answerable questions cited, and no `llama-server.exe`
left over. The M1 and M2 steps are unchanged and still pass: startup 3.5 s, import of the dev GGUF
(uncatalogued, with the warning), indexing 10 files in 3.4 s, Thai, English and Chinese answers
cited with provider `local`, the not-found reply for the off-topic question, a `think: true`
request accepted, the folder watcher in 2.5 s, and API mode with the throwaway key (provider
`openai`, then back to `local`, key deleted and checked).

**New in M3** (after the lease answer, `en-lease-rent`, which cites `apartment-lease.pdf`):

| Step | Result |
|---|---|
| `POST /api/attribution` with a sentence of the answer ("The rent is 18,500 baht") | 1 match: source 1, `apartment-lease.pdf`, strong, `changed` false |
| `GET /api/sources/{message}/1/locate` on that match | `found` true, `kind` pdf, page 1 of 2, 4 highlight boxes |
| `GET /api/files/{file}/pages/1?scale=1.5` | 200, PNG signature checked, 50,145 bytes |
| `GET /api/files/{file}/text` for the TXT cited by the Chinese answer (`furniture-warranty.txt`) | 200, `kind` text, 8 lines |
| A repeated attribution call | the same matches |

**Timings** (packaged exe, 0.5B model):

| Measure | Value |
|---|---|
| Attribution, first call after the answer | 0.375 s |
| Attribution, repeated call | 0.500 s |
| Page render at scale 1.5, A4 PDF page, after one warm-up request | 0.031 s |

After each answer the core fills the attribution window cache in the background, so in the smoke
the first call already finds the windows cached and only embeds the selection; that is why the two
calls cost about the same (the difference is noise). The cold numbers were measured in Task 2,
with the real bge-m3 and four sources of about 450 tokens: first call (windows and selection)
1.62 s, restricted to one source with `n` 0.41 s, windows cached 20 ms. The page time is for one
request to the running exe on a small page; a first request for a large PDF also parses it once.

## Visual check

The controller ran the dev core on a scratch data folder with the corpus from
`scripts/build_eval_corpus.py` and looked at the UI in the browser:

- Clicking the `[1]` chip opens the compare panel: "Answer" and "Document" columns, a "Strong
  match" label, the page ("page 1 of 2"), and the snapshot passage marked.
- Selecting a sentence shows the **Check source** button next to the selection.
- **Open file** opens the PDF viewer with the real page image and yellow boxes exactly on the cited
  text. Another source card switches the viewer to a TXT view with numbered Thai lines highlighted.
- Zoom resizes the page and the boxes follow it: 150% was checked visually; 50% and 200% are
  covered by unit tests. This was a bug that review found (the page kept its width while the label
  changed) and that was fixed and re-checked.
- Another defect found during these checks was fixed: a window that started in the middle of a
  Latin word (windows now snap to word boundaries).
- **The exit flow in one run** (after the final review fixes): drag-select "The monthly rent is
  18,500 baht" in the lease answer → **Check source** appears → the panel shows "Strong match",
  `apartment-lease.pdf`, page 1 of 2 → **Open file** → the viewer shows page 1 of the real PDF
  with yellow boxes on "Kanya Srisuk / 1. Term… / 2. Rent. The monthly rent is 18,500 baht, due on
  the 5th day of each month. / A late payment fee…", the matched window.

## Deviations from the spec

The spec (`docs/superpowers/specs/2026-10-02-tamra-design.md`) now describes what was built; these
are the places where the first version of it differed, and why.

- **pdf.js became `pypdfium2`.** Spec section 8 had pdf.js render the page in the browser. The core
  renders the page to PNG with `pypdfium2` instead, and the highlight rectangles come from the same
  pdfium text page that indexing used, so the coordinates match exactly. It also needs no large JS
  dependency, no worker and no CSP `worker-src` change, and the page is the PDF's own rendering, so
  Thai shaping is right.
- **No `pdf_char_boxes` table.** Spec section 4 stored per-page character boxes. Boxes are computed
  on demand from the current file, so there is no schema change and nothing to keep in step with
  the file.
- **A panel instead of a popup.** The attribution result is the right-side source panel (compare the
  answer with the document, a strong or partial label, previous and next, the changed warning), not
  a popup near the selection.
- **A modal instead of a side panel for the viewer.** The document viewer is a modal over the chat,
  opened by "Open file" in the panel. "Open with default app" is in the viewer.
- **Rectangles are also computed for a changed file.** The spec only showed the stored snapshot when
  `file_hash_at_answer` differed from the file. The viewer opens the current file and searches it
  for the passage as well as it can; the "document changed since this answer" warning is always
  shown in that case, because the passage it finds may not be the one the answer used.
- **Chip semantics.** Clicking an `[n]` chip attributes the sentence that ends at that chip,
  restricted to source `n` (the spec only said it opens the same popup). The chip marks the
  source being shown as active, and a sentence with no text before the chip opens the source as it
  is, without a comparison.

## Not done in M3

- **`scan_folder` follows junctions.** The M1 indexer can index files behind a junction; the viewer
  correctly refuses to open or show them (400), so such a source cannot be viewed.
- **`location_label` is not hardened against malformed locations.** Locations come from the
  chunker, so this cannot happen with real data. The viewer's own location hint is hardened.
- **The Check source button has no `aria-live` and no keyboard shortcut,** so a keyboard or
  screen-reader user does not discover it from the selection alone.
- **WebView2 manual checks.** Triple-click selections and selections that start at an element
  boundary were reasoned about and unit-tested, but not tried in the real window.
- **`parse_pdf` holds the pdfium lock for the whole file.** It should release the lock per page so
  that a page render is not blocked behind the indexing of a long PDF.
- **The footer note next to the no-rects notice.** When the passage is on a page but Tamra cannot
  draw boxes, the footer note is still shown beside the notice.
- **Panes are not keyboard-scrollable.** The viewer's two panes are not focusable scroll areas.
- **"Show earlier lines" has no scroll anchoring.** Widening a long text view upward leaves the
  scroll position as the browser keeps it.
- **Code-point offsets are not documented on `ViewerRequest`.** Its `start` and `end` are code
  points (Python's character offsets), converted to UTF-16 units by `cpToUtf16` wherever the UI
  slices a snapshot; the type does not say so.
- **A matcher that checks support.** "Strong match" is similar wording only (see the known
  limitation above). Deciding whether a passage supports a claim would need an entailment model.
- **The cross-page PDF locate test is weak.** `test_locate_pdf_part_across_pages_returns_the_page_of_the_longest_part`
  uses a hand-written passage joined with a blank line, not a chunk the real chunker cut across a
  page break, and it only checks that the longer side wins. Only the part on that page is
  highlighted, and the part on the other page is not.
- **The "document changed" banner is hidden when the answer's sources are missing.** It is drawn
  with the snapshot, and a match whose source is not among the message's sources has no snapshot,
  so neither is shown. Every match comes from the message's own sources, so this cannot happen with
  real data.
- **`Core.file_path` resolves against the single collection.** It reads `get_collection()` and does
  not check that the file belongs to it. M4 adds more collections, so it must add `collection_id`
  to `FileRecord` and check it there.
- **Very large pages are scaled down silently (M6).** A page above the pixel cap is rendered
  smaller than asked, so the displayed size differs from the zoom the user chose.
- **The document cache cap is keyed on file size (M7).** The parsed-document cache skips files above
  a byte limit and keeps the eight most recent others; a parsed document's memory use is not
  proportional to its file size, so the cap is only a rough guard.
- **Anthropic and the real window** are still the owner's checks, as in M2.
