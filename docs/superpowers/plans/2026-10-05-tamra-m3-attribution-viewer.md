# Tamra M3 — Source attribution and document viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The user selects text in an answer, or clicks an `[n]` chip, and sees where it came from. Three surfaces show it:
- The **source panel** (frame 2b) puts the answer's sentence next to the matching document passage.
- The **document viewer** (frame "Dialog · Open file") shows the real PDF page, or the extracted text of a DOCX/TXT/MD file, with the passage highlighted.
- **"Open with default app"** opens the file in the user's own application.

**Architecture:**
- **Attribution.** A new `tamra.attribution` module matches a selection against only that message's saved sources (spec §7). It splits each source snapshot into ~60-token windows with 50% overlap and embeds them with the existing bge-m3 embedder, caching per message. Each window is scored by cosine similarity plus a character-trigram overlap boost.
- **Viewer.** A new `tamra.viewer` module reads the current file from the collection folder. It finds the matched window's text in the parsed document, then returns, for a PDF, a page number plus highlight rectangles, or, for DOCX/TXT/MD, a paragraph or line range. PDF pages are rendered to PNG by `pypdfium2` (already a dependency).
- **API routes.** New routes expose attribution, locating, page images, text views and "open externally".
- **UI.** The UI gets a selection button, a redesigned source panel, and a viewer modal.

**Tech Stack:** M2's stack. There are no new Python or npm dependencies. PNG encoding uses `zlib` and `struct` from the standard library (a minimal RGB PNG writer), not Pillow.

**Spec:** `docs/superpowers/specs/2026-10-02-tamra-design.md` §7 (source attribution popup), §8 (document viewer), and §4 (`message_sources`). Design frames are in `docs/design/`: "2b · Chat — answer with source.png" and "Dialog · Open file.png". The roadmap row is M3: "Select text in an answer, see its source highlighted in the PDF".

**Controller decisions for M3 (2026-10-05), made under the user's "choose what you think is best":**
- **PDF pages are rendered server-side with `pypdfium2`, not with pdf.js** (a deviation from spec §8):
  - Highlight boxes come from the same pdfium text page that indexing used, so the coordinates match exactly.
  - No large JS dependency, worker, or CSP `worker-src` change is needed.
  - Thai glyph shaping is the PDF's own: we show an image of the real page, not re-flowed text.
- **No `pdf_char_boxes` table** (a deviation from spec §4). Boxes are computed on demand from the current file, but only when the file is unchanged since the answer (`files.content_hash == file_hash_at_answer`). When it has changed, the viewer still opens the current file and tries to find the passage by text. The UI always shows the "document changed since this answer" warning in that case.
- **The panel in frame 2b replaces the spec's "popup".** It is the existing right-side source panel, redesigned. The "Open file" button in it opens the viewer modal, and the modal holds "Open with default app".
- **An `[n]` chip click** attributes the sentence that ends at that chip, restricted to source `n`.

## Global Constraints

- Platform: Windows 10/11 x64. Python `>=3.12,<3.13` through uv. Never run `uv python install`, and never delete or recreate `.venv`.
- License: Apache-2.0. No new dependencies, Python or npm.
- `tamra.store` is the only module that runs SQL. No schema change is needed in M3; if a task finds it needs one, it stops and reports.
- User document folders are never modified. The viewer only reads files. Every file path is resolved from `collection.folder_path / files.rel_path` and must stay inside the collection folder, checked after `Path.resolve()`. The API never takes a filesystem path from the request.
- Attribution candidates are only the message's own `message_sources`; there is no corpus-wide search (spec §7). The UI shows "strong match" or "partial match", never a raw score.
- All routes are under `/api/` and therefore token-gated. Page images are fetched with the token header and shown through `blob:` URLs, so the CSP gains `img-src 'self' data: blob:`; nothing else in the CSP changes.
- UI strings go through `useT()`, in English and Thai. CSS uses theme tokens (`--primary-ink`, `--surface`, …), never hard-coded colours, except the highlight yellow, which becomes the new tokens `--highlight` and `--highlight-edge` with dark-mode values.
- Code, comments, docs, and commit messages are in English.
- Before every commit, run `uv run ruff format`, `uv run ruff check --fix`, and `uv run ruff format --check`, then the touched tests, then `uv run pytest` once. UI tasks also run `npm --prefix ui test` and `npm --prefix ui run build`.
- Never kill processes by image name. Never commit `AGENTS.md` or `.claude/`. Stage explicit paths only. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- `fastapi.testclient.TestClient` uses `base_url="http://127.0.0.1"`.
- Test documents are generated with `tests/docgen.py` (`make_pdf`, `make_docx`), as M1's parser tests do. Tests marked `assets` may load the real bge-m3 from `.models`; all other tests use fakes.

---

## Task 1: Viewer core — locate a passage and render pages

**Files:** `src/tamra/viewer.py` (new), `tests/test_viewer.py` (new).

**Interfaces:**
- Consumes: `tamra.ingest.parsers.parse_file(path) -> ParsedDoc` (units with `page`/`char` for PDF, `paragraph` for DOCX, `line` for text), and `Store` file and collection records.
- Produces:
  - `resolve_file(folder: str, rel_path: str) -> Path`. It joins and resolves the path and raises `ViewerError("outside the collection folder")` if the result is not under `Path(folder).resolve()`. It raises `ViewerError("file not found")` if the file is missing.
  - `@dataclass(frozen=True) class Found`, with fields:
    - `kind: "pdf" | "docx" | "text"`
    - `page: int | None`: 1-based, PDF only
    - `start: int, end: int`: PDF character offsets in that page's `get_text_range()` text; DOCX paragraph indices (inclusive start, exclusive end); text 1-based line numbers (inclusive start, exclusive end)
  - `locate(doc: ParsedDoc, passage: str, hint: dict | None = None) -> Found | None`:
    1. Normalise both sides the same way: `\r\n`/`\r` become `\n`, runs of whitespace collapse to one space, and the text is case-folded. Keep an index map from normalised positions back to the original unit offsets.
    2. Search the units for the passage. If the hint (a source `location` dict) names a page, paragraph or line range, search the units in that range first.
    3. A passage made of several `\n\n`-separated parts (a chunk that joined pieces) is matched part by part; the result spans from the first part's start to the last part's end in the same unit. If the parts span two PDF pages, return the page holding the longest part.
    4. Return `None` if the longest part (at least 12 normalised characters) is not found.
  - `pdf_page_count(path: Path) -> int`.
  - `render_pdf_page(path: Path, page: int, scale: float) -> bytes`: a PNG of the page at `scale` × 72 dpi. `scale` is clamped to 0.5..3.0.
    - Render with `page.render(scale=scale)` and `bitmap.to_numpy()`.
    - Encode RGB with a minimal PNG writer: an 8-byte signature; IHDR (bit depth 8, colour type 2); one IDAT holding `zlib.compress(raw, 6)` where every row is prefixed with filter byte 0; IEND. Each chunk carries its CRC from `zlib.crc32`.
    - Convert BGR or BGRA bitmaps to RGB.
  - `pdf_rects(path: Path, page: int, start: int, end: int) -> list[tuple[float, float, float, float]]`:
    - Get the boxes from `textpage.count_rects(start, end - start)` and `textpage.get_rect(i)`.
    - Return them as fractions of the page size, origin top-left: `(x0/w, 1 - y1/h, x1/w, 1 - y0/h)`.
  - `text_view(doc: ParsedDoc) -> dict`, which has one of two shapes:
    - text: `{"kind": "text", "lines": [str, ...]}`, holding every line of the file. Read the whole decoded text, not only the units, so blank lines keep their numbers.
    - docx: `{"kind": "docx", "paragraphs": [{"index": int, "text": str, "heading": bool}]}`, holding every non-empty paragraph.

**Tests:**
- `resolve_file` refuses `..\..\x`, an absolute path, and a symlink-free sibling-folder escape, and accepts a nested file.
- `locate` cases:
  - exact match in a TXT, giving the right 1-based lines;
  - match across `\r\n` and collapsed spaces;
  - a two-part passage (joined by `\n\n`) inside one DOCX paragraph range;
  - a PDF passage found on page 2, with `start` and `end` checked against that page's `get_text_range()`;
  - not found, returning `None`;
  - a hint narrows the search when the same text appears twice (the second occurrence's page is chosen by the hint).
- `render_pdf_page` returns bytes starting with `b"\x89PNG\r\n\x1a\n"`. Decoding IHDR gives the expected width for `scale=1.0` (the page width in points, rounded).
- `pdf_rects` for a known line in a `make_pdf` document returns at least one rect, with every coordinate in 0..1 and `y0 < y1`.
- `text_view` keeps blank lines (line numbers match the file) and marks DOCX headings.

## Task 2: Attribution engine

**Files:** `src/tamra/attribution.py` (new), `src/tamra/store/repo.py` (a `get_message(message_id) -> MessageRecord | None` method, if one does not exist), `eval/attribution.jsonl` (new), `scripts/eval_attribution.py` (new), and the tests `tests/test_attribution.py` and `tests/test_store_chats.py`.

**Interfaces:**
- Consumes: `MessageRecord.sources: list[SourceRecord]`, where each source has `n`, `text` (the snapshot), `location`, `file_id`, `rel_path` and `file_hash_at_answer`. Also the embedder callable that `Core` already has (`Core._embed_query`-style `Callable[[list[str]], np.ndarray]` returning L2-normalised rows), the bge token spans (`tamra.ingest.chunker.TokenSpans`), and `tamra.retriever.trigrams`.
- Produces:
  - `WINDOW_TOKENS = 60` and `WINDOW_STRIDE = 30`.
  - `windows(text: str, spans: TokenSpans) -> list[tuple[int, int]]`: character ranges of ~60-token windows with 50% overlap, covering the whole snapshot. A snapshot of 60 tokens or fewer gives one window.
  - `cited_in(content: str, selection: str) -> set[int]`: the `[n]` numbers inside the selection, or inside the sentence(s) of `content` that contain the selection. Sentence ends are `.!?。！？` or a newline.
  - `@dataclass(frozen=True) class Match`, with fields `n: int`, `start: int`, `end: int` (window char range in the snapshot), `label: "strong" | "partial"`, and `score: float` (internal; never sent to the UI).
  - `class Attributor`:
    - `Attributor(embed: Callable[[list[str]], np.ndarray], spans: TokenSpans, cache_size: int = 16)`.
    - `attribute(message: MessageRecord, selection: str, only: int | None = None) -> list[Match]`:
      - Candidates are the message's sources, or source `only` alone when given.
      - Window embeddings are cached per `(message.id, n)`, using an LRU over messages.
      - Score = `cosine(selection, window) + TRIGRAM_WEIGHT * overlap`, where `overlap = |T(sel) ∩ T(win)| / max(1, |T(sel)|)` over `trigrams()` sets.
      - Sources in `cited_in(...)` get `+CITED_BONUS`.
      - Return at most 3 matches with score ≥ `PARTIAL`, best first, at most one window per source unless only one source exists.
      - `label = "strong"` if score ≥ `STRONG`.
      - An empty list means "no clear source found".
    - It is thread-safe: guard the cache with a lock.
  - `TRIGRAM_WEIGHT`, `CITED_BONUS`, `STRONG` and `PARTIAL` are module constants, calibrated in this task (see below).
- **Calibration:**
  - `eval/attribution.jsonl` holds at least 24 handwritten cases built from `eval/corpus` texts. Each case is `{"id", "sources": [{"n", "text"}...], "selection", "expect_n": int | null, "expect": "strong" | "partial" | "none"}`, covering:
    - Thai, English and Chinese;
    - exact quotes (strong);
    - paraphrases and translations, i.e. an English selection against a Thai source (partial);
    - numbers that differ, e.g. 6 days vs 10 days (must not be strong for the wrong source);
    - unrelated text (none).
  - `scripts/eval_attribution.py` loads the real bge-m3 from `.models`, prints the per-case score and label, and prints the accuracy of `expect_n` and `expect`.
  - Choose the constants so that `expect_n` is right in at least 90% of non-none cases and no "none" case returns a match. Record the numbers in the task report and in `docs/spikes/2026-10-m3-results.md` (created in Task 6; Task 2 writes them to its report).

**Tests** (fake embedder: deterministic vectors from character trigrams, normalised, so cosine is meaningful without the model):
- `windows` covers the text, overlaps by about half, and gives one window for short text.
- `cited_in` finds `[2]` in the selection's sentence and returns an empty set for none.
- `attribute` cases:
  - an exact quote of source 2 ranks source 2 first with label strong;
  - `only=1` restricts the candidates;
  - unrelated text returns `[]`;
  - the cache embeds each source's windows once across two calls (count the embed calls);
  - at most 3 matches.
- An `assets`-marked test runs the calibration set with the real model and asserts the accuracy bar.

## Task 3: API routes for attribution, viewing, and opening files

**Files:** `src/tamra/server.py`, `src/tamra/core.py` (wire an `Attributor` lazily, sharing the embedder; add `Core.open_file_external` injection), `src/tamra/app.py` (inject `open_external = os.startfile` in window mode), `tests/test_server_attribution.py` (new).

**Interfaces:**
- Consumes: Task 1 `resolve_file`, `locate`, `Found`, `pdf_page_count`, `render_pdf_page`, `pdf_rects` and `text_view`. Task 2 `Attributor.attribute` and `Match`.
- Produces (all token-gated):
  - `POST /api/attribution` with body `{message_id: int, selection: str (1..2000 chars), n?: int}`:
    - 404 if the message is missing, 400 if it is not an assistant message.
    - Response: `{"matches": [{"n", "start", "end", "text": snapshot[start:end], "label", "file_id", "file", "location_label", "changed": bool}]}`.
    - `changed` is true when the file is gone or `files.content_hash != file_hash_at_answer`.
    - Runs on the threadpool, as a sync handler.
  - `GET /api/sources/{message_id}/{n}/locate?start=&end=` locates `snapshot[start:end]`, or the whole snapshot if `start` and `end` are omitted, in the current file. The response is `{"file_id", "kind", "changed", "found": bool, "page"?: int, "page_count"?: int, "rects"?: [[x0,y0,x1,y1]], "start"?: int, "end"?: int}`.
    - PDF: `rects` come from `pdf_rects` for the found range, and `page_count` is included.
    - text: `start` and `end` are 1-based lines, end exclusive.
    - docx: `start` and `end` are paragraph indices.
    - `found: false` with no position when `locate` returns `None`.
    - 404 if the source or the file is missing; the JSON says which.
  - `GET /api/files/{file_id}/pages/{page}?scale=1.5` returns `image/png` with `Cache-Control: no-store`. It is 404 for a non-PDF or an out-of-range page, and refuses a scale outside 0.5..3.0 with 400.
  - `GET /api/files/{file_id}/text` returns `text_view(...)` for DOCX/TXT/MD and 400 for a PDF.
  - `POST /api/files/{file_id}/open` gives 204 and calls the injected opener with the resolved path. It is 501 in dev mode, where no opener is injected. The path always comes from the store, never from the request.
  - The CSP `img-src` gains `blob:`.
- File reads (render, rects, text) parse the file at request time. Use a small per-process LRU (8 entries) of `ParsedDoc` keyed by `(file_id, size, mtime)` so paging through a PDF does not re-parse it.

**Tests:**
- Every new route returns 401 without the token.
- Attribution:
  - 404 and 400 cases;
  - a strong match on a fake-embedder core returns `text` equal to the snapshot slice and never includes `score`;
  - `changed` is true after the file's hash is altered in the store.
- Locate on generated PDF, DOCX and TXT files in a temp collection:
  - each returns the right page, lines or paragraphs;
  - the PDF has rects;
  - `found: false` when the file was rewritten without the passage.
- The page route returns PNG bytes and 404 for page 0 or page count + 1.
- The text route returns lines for TXT and 400 for PDF.
- Open: 501 without an opener; with a fake opener, it is called once with a path inside the folder.
- A file record whose `rel_path` was tampered with to `..\outside.txt` gives a 4xx, and the opener is never called.
- The CSP header contains `img-src 'self' data: blob:`.

## Task 4: UI — Check source and the compare panel

**Files:** `ui/src/ChatView.tsx`, `ui/src/AnswerText.tsx`, `ui/src/SourcePanel.tsx` (new; move the panel out of ChatView), `ui/src/selection.ts` (new), `ui/src/api.ts`, `ui/src/types.ts`, `ui/src/strings/en.ts`, `ui/src/strings/th.ts`, `ui/src/styles.css`, and the tests `ui/src/SourcePanel.test.tsx` and `ui/src/selection.test.ts`.

**Interfaces:**
- Consumes: `POST /api/attribution` and `GET /api/sources/{message_id}/{n}/locate` from Task 3. Also `useT()` and `useSettings()`, and the existing `Message`/`Source` types; add `file_id` to `Source` if the payload needs it (Task 3 may add it to `source_payload`).
- Produces:
  - **Selection.**
    - When the user selects text inside one saved assistant message's answer text (not across messages, not in the pending answer, length 2..2000 after trim), a small floating "Check source" button appears near the selection end.
    - It disappears on a collapsed selection, scroll, Esc, or a click elsewhere.
    - Clicking it calls attribution with `{message_id, selection}`.
    - `selection.ts` exports pure helpers:
      - `selectionInside(root: HTMLElement): {text: string, start: number, end: number} | null`: offsets in the message's content, mapping through the rendered citation chips;
      - `sentenceBefore(content: string, chipIndex: number): {start, end}`.
  - **Chip click.** Clicking an `[n]` chip attributes `sentenceBefore(...)` with `n` set, replacing the M1 behaviour of opening the raw source.
  - **SourcePanel**, matching frame 2b:
    - Header: file name, then a second line built from `location_label` (for a PDF, "page X of Y · lines a–b" when `locate` gives the page; otherwise the label), an "Open file" button (it opens the Task 5 viewer), and close.
    - "Compare" section: an "Answer" pill with the selected text, and a "Document" pill with the matched window text. Both use the `--highlight` background.
    - The match label reads "Strong match" or "Partial match".
    - Previous and next controls appear when there are several matches ("1 of 3").
    - Below that comes the snapshot text, with the matched window highlighted and scrolled into view.
    - When the match is `changed`: a warning banner "This document changed after the answer. Showing the text Tamra used then."
    - When there are no matches: "No clear source found for this selection.", plus the list of the answer's sources to open manually.
    - A loading state while attribution runs, and an error notice on failure.
  - The attributed span stays highlighted with `--highlight` in the answer text while the panel is open (frame 2b).
  - Tokens `--highlight` and `--highlight-edge` are added for light and dark, with the light values sampled from frame 2b. Text on `--highlight` must reach 4.5:1; add the check to `theme.test.ts`.

**Tests:**
- `selectionInside` maps a selection that spans a chip to the right content offsets, and returns `null` across two messages.
- `sentenceBefore` finds the sentence before chip 1 and before chip 2 (Thai and English).
- The Check source button appears on a selection and calls the API with the message id. A chip click sends `n`.
- The panel renders compare, label, prev/next, the changed warning, the no-match state and the error state.
- The highlight in the answer text clears when the panel closes.
- en/th key parity holds; the existing i18n test covers it.

## Task 5: UI — Document viewer modal

**Files:** `ui/src/DocumentViewer.tsx` (new), `ui/src/PdfPage.tsx` (new), `ui/src/ChatView.tsx` / `ui/src/SourcePanel.tsx` (open the viewer), `ui/src/api.ts` (`fetchBlob(path)` with the token header), strings, styles, and the test `ui/src/DocumentViewer.test.tsx`.

**Interfaces:**
- Consumes: Task 3 `locate`, `GET /api/files/{id}/pages/{page}`, `GET /api/files/{id}/text` and `POST /api/files/{id}/open`. Task 4's current match: message id, `n`, the window range and the selected text.
- Produces, matching "Dialog · Open file":
  - A modal at about 90% of the window. It reuses `Modal.tsx` focus handling, closes on Esc, and returns focus on close.
  - **Header:** file name and folder path, page navigation ("14 / 32", PDF only), zoom (−, "100%", +; steps 50/75/100/125/150/200%, PDF only), "Open with default app", and close.
  - **Left pane:**
    - "Selected in the answer": the selected text, or the chip's sentence.
    - "Found in this file": the window text, with its location label.
    - "Other sources in this answer": cards that switch the viewer to that source (locate with no range).
    - Footer note: "Yellow marks the same passage on both sides, so you can read them against each other."
  - **Right pane, PDF:**
    - One page at a time. The image comes from `fetchBlob` (scale = zoom × devicePixelRatio, capped at 3).
    - The highlight is drawn as absolutely positioned boxes from `rects` (fractions, so they follow zoom), using `--highlight` with `mix-blend-mode: multiply` in light mode.
    - The viewer opens on the located page and scrolls the first rect into view.
    - Page navigation loads other pages; the highlight shows only on the located page.
    - Object URLs are revoked on page change and on unmount.
  - **Right pane, DOCX/TXT/MD:** a white "paper" with line or paragraph numbers in a gutter. Headings are bold for DOCX. The located range is highlighted with a left edge (`--highlight-edge`) and scrolled into view (frame).
  - **Not found:** when `found` is false, show the document without a highlight and the notice "Tamra could not find this passage in the current file."
  - **Changed:** the warning from Task 4 appears here too.
  - **"Open with default app":** calls the open route. On 501 (dev mode), the button is replaced by "Only available in the Tamra window", as Settings does.
- Accessibility:
  - The page image has `alt` "Page N of FILE".
  - Highlight boxes are `aria-hidden`, and the left pane's "Found in this file" text carries the meaning.
  - Zoom and page buttons have labels.

**Tests:**
- PDF: the image `src` is a blob URL from `fetchBlob` (mocked). The highlight boxes are positioned from the rects. Next and previous page change the request. Zoom changes the scale. Object URLs are revoked.
- Text: the line numbers render, the highlighted range has the class, and `scrollIntoView` is called (stubbed).
- Other-source cards re-locate.
- The not-found notice, and the 501 on open.

## Task 6: Packaging, exit check, and docs

**Files:** `scripts/exe_smoke.py`, `packaging/tamra.spec` (only if needed), `README.md`, `docs/spikes/2026-10-m3-results.md` (new), and `CLAUDE.md` (the module list gains `viewer`; `attribution` is already listed).

**Work:**
- **exe_smoke additions:** after the first cited answer, call:
  - `POST /api/attribution` with a sentence from the answer, expecting at least one match;
  - `locate` on the first match, expecting `found: true`; for a PDF source, expecting a non-empty `rects`;
  - the page route, expecting PNG bytes, for a PDF source;
  - the text route, for a text source.
  - Pick a question whose answer cites a PDF (the lease or shipping policy questions in `eval/questions.jsonl`). Record everything in the smoke JSON.
- **Build and run:** run `./scripts/build.ps1`, then `uv run python scripts/exe_smoke.py`, then the exe selfcheck. Paste the real outputs into the results doc.
- **Results doc:** `docs/spikes/2026-10-m3-results.md` holds:
  - the attribution calibration numbers from Task 2;
  - the smoke results;
  - timings: attribution latency first call vs cached, and page render time at scale 1.5;
  - "Not done in M3".
- **README:** a "Checking a source" section covering select → Check source, the compare panel, Open file, and Open with default app.

**Exit criterion:**
- The packaged smoke passes with the new checks.
- The controller verifies the UI in the browser (dev core, scratch data dir, the eval corpus built by `scripts/build_eval_corpus.py`): select a sentence, Check source, open the PDF, see the highlight.
- The real-window check is the user's.

## After M3

M4 is multiple collections per chat and the chat history UI ("Projects" in the frames): the `chat_collections` table, the sidebar project tree, and the "New project" and "Change folder" dialogs.
