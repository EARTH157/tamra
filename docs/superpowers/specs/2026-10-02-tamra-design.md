# Tamra — Design Spec

**Date:** 2026-10-02
**Status:** Approved in brainstorming, pending written-spec review
**License:** Apache-2.0 (open source, `github.com/EARTH157/tamra`)

Tamra (ตำรา, "textbook / authoritative reference") is a Windows desktop app for asking
questions about your own documents. Every answer cites its sources, and any part of an
answer can be checked against the exact passage it came from.

## 1. Goals and non-goals

**Goals**
- Q&A over user documents in Thai, English, and Chinese, including mixed-language
  documents and cross-language queries.
- Works fully offline after models are present. Also offers a cloud API mode.
- Runs on any Windows machine, from low-spec laptops to GPU desktops, by letting the user
  pick an LLM size suited to the hardware.
- Every answer is verifiable: inline `[n]` citations, and selecting any text in an answer
  shows the source passage that supports it.

**Non-goals for v1**
- Platforms other than Windows 10/11 x64.
- File types beyond PDF, DOCX, TXT, MD (no XLSX, PPTX, HTML, images-as-files).
- Reranking model, LLM-based query rewriting, auto-update, code signing, multi-user or
  server deployment, remote model catalog.

## 2. Key decisions

| Decision | Choice | Reason |
|---|---|---|
| Stack | Python core + React/TypeScript UI in a pywebview (WebView2) window | Best local-AI ecosystem; web UI makes the popup and PDF highlighting easy; one installer |
| UI ↔ core | FastAPI on `127.0.0.1`, random port, per-launch token; SSE for streaming | Lets the UI be developed in a normal browser with hot reload |
| Local LLM | llama.cpp's official prebuilt `llama-server.exe` (Vulkan build), run as a managed child process on `127.0.0.1` and called through its OpenAI-compatible API; CPU fallback via `--device none` | One build covers NVIDIA, AMD, and Intel GPUs. Prebuilt binaries ship every release (the Python binding needs a local Vulkan SDK compile). Reuses the OpenAI-compatible client. A llama.cpp crash cannot take down the app. |
| API LLM | Anthropic, plus any OpenAI-compatible endpoint (Ollama, LM Studio, others) | User choice; the OpenAI-compatible option is nearly free once the interface exists |
| Embedding | `bge-m3` via ONNX Runtime, **always local**, single fixed model | Strong multilingual (TH/EN/ZH) and cross-lingual retrieval; offline search; index never depends on LLM mode |
| Storage | One SQLite database: metadata, chats, chunks, FTS5 (trigram) keyword index, and `sqlite-vec` for dense vectors | One file, one transaction across file/chunk updates; trigram FTS needs no Thai/Chinese word segmenter |
| PDF text | `pypdfium2` | Permissive license (PyMuPDF is AGPL, incompatible with Apache-2.0 distribution); gives per-character boxes for highlighting |
| Data location | `%LOCALAPPDATA%\Tamra\` (`tamra.db`, `models\`, `logs\`) | Standard per-user app data |

**Dependency rule:** every bundled dependency must be under a license compatible with
Apache-2.0 distribution. No AGPL/GPL libraries. LLM weights are downloaded by the user and
not bundled; their license is shown in the model picker.

**Scale assumption:** `sqlite-vec` does brute-force KNN. That is adequate for personal and
team corpora (up to roughly 100k chunks). Beyond that, revisit with an ANN index.

## 3. Architecture

```
pywebview window (WebView2)
  └─ React UI ──HTTP/SSE (127.0.0.1, token)──▶ FastAPI app (Python, same process)
                                                 ├─ ingest      watch folders, parse, chunk
                                                 ├─ embedder    bge-m3 ONNX
                                                 ├─ retriever   hybrid search
                                                 ├─ llm         OpenAICompatible (llama-server child | Ollama | LM Studio) | Anthropic
                                                 ├─ answer      prompt, stream, record sources
                                                 ├─ attribution selection → source passage
                                                 ├─ models      hardware detect, download, import, verify
                                                 └─ store       SQLite (+ FTS5, sqlite-vec)
```

Each module has one job and a narrow interface. `llm` exposes one streaming
`generate(messages, max_tokens) -> Iterator[str]` interface (synchronous; FastAPI runs
it in its threadpool for SSE). Nothing outside `llm` knows which provider is active.
`store` is the only module that touches SQL. The only child process is `llama-server`,
which is started on demand in local mode and stopped when the app exits.

## 4. Data model (SQLite)

- `collections(id, name, folder_path, embedding_model_id, created_at)`
- `files(id, collection_id, rel_path, size, mtime, content_hash, status, error, indexed_at)`
  — `status ∈ {pending, indexing, indexed, failed, skipped}`
- `chunks(id, file_id, ord, text, location_json)` — `location_json` holds page range and
  character offsets (PDF), heading path and paragraph index (DOCX), or line range (TXT/MD).
- `chunks_fts` — FTS5 table over `chunks.text`, `tokenize='trigram'`
- `chunk_vectors` — `sqlite-vec` virtual table, 1024-dim float32, keyed by chunk id
- `pdf_char_boxes(file_id, page, data)` — compact per-page character boxes for highlighting
- `chats(id, title, created_at, updated_at)`, `chat_collections(chat_id, collection_id)`
- `messages(id, chat_id, role, content, provider, model, created_at)`
- `message_sources(message_id, n, chunk_id, file_id, text_snapshot, location_json, file_hash_at_answer)`
- `settings(key, value)` — API keys are **not** stored here (see §9)

Deleting a file's chunks and inserting the new ones happens in one transaction, so a crash
never leaves a file half-indexed.

## 5. Ingestion and indexing

- A collection is a name plus a folder. Files stay where they are; Tamra never modifies
  them. Upload or drag-and-drop copies the file into the collection folder, and the watcher
  picks it up. The folder is the source of truth, and the index can always be rebuilt
  ("Rebuild index").
- **While running:** `watchdog` observes each collection folder. Events are debounced
  (about 2 s after the last write). Office temp files (`~$*`) and hidden or system files
  are ignored.
- **On startup:** reconcile each folder against `files`. Compare size and mtime, hash the
  content only when they differ, and handle added, changed, and removed files. Any file
  left in `indexing` (crash) is re-queued.
- **Pipeline per file:** parse → chunk → embed → write. It runs on a single background
  worker queue. The UI shows per-file status and overall progress.
- **Parsers:**
  - PDF: `pypdfium2`. Text per page plus character boxes. A page with no text layer is
    flagged `needs_ocr` (skipped until M5).
  - DOCX: `python-docx`. Paragraphs with their heading path.
  - TXT/MD: encoding detected with `charset-normalizer` (Thai TIS-620/CP874 files are
    common), then line-tracked text.
- **Chunking:** sized by `bge-m3` tokenizer tokens, about 450 tokens per chunk with about
  15% overlap. Split points prefer paragraph boundaries, then sentence punctuation, then
  whitespace, then a hard cut. Every chunk records its location (§4).
- **Embedding model guard:** each collection stores the `embedding_model_id` it was built
  with. On mismatch, the collection is marked stale and the UI asks the user to rebuild. It
  never silently searches a mismatched index.

## 6. Retrieval and answering

- Each chat selects one or more collections. The selection can change mid-chat.
- **Query text:** the latest user question plus the previous user question, so follow-ups
  like "what about item 2?" stay on topic without an extra LLM call.
- **Hybrid search:** dense KNN (`sqlite-vec`) and keyword (FTS5 trigram, BM25), each top
  ~30, fused with Reciprocal Rank Fusion.
- **Context budget:** take the top chunks that fit the active model's context, about 4 for
  small local models and 8–10 for large local or API models. If over budget, drop the
  lowest-ranked chunks first.
- **No relevant results:** if the best fused result is below threshold, reply "not found in
  the documents" without calling the LLM. The threshold is tuned on the eval set (§12).
- **Prompt:** numbered sources `[1]..[k]` with file name and location. Instructions: answer
  only from the sources, put `[n]` after each supported claim, answer in the language of
  the question, and say so plainly when the sources are insufficient.
- **Streaming:** tokens go to the UI over SSE, and generation can be cancelled. `[n]`
  markers render as clickable chips that open the source popup (§7).
- **Persistence:** after the answer completes, save the message and one `message_sources`
  row per `[n]`, including the text snapshot and the file hash at answer time. Old chats
  keep showing what the model actually saw, even if the document changes later.

**Errors surfaced to the user:**

| Situation | Behavior |
|---|---|
| API mode: offline, invalid key, quota exceeded | Clear message, with an offer to switch to local |
| Local mode: model file missing | Route to model download/import |
| Model load failure (e.g. Vulkan unavailable) | Fall back to CPU, show a notice |

## 7. Source attribution popup

- **Trigger:** selecting text inside an assistant message shows a small "Check source"
  button near the selection. Clicking it opens the popup. It does not open automatically,
  because selecting text to copy is common. Clicking an `[n]` chip opens the same popup.
- **Matching (`POST /attribution {message_id, selected_text}`):**
  1. Candidates are only that message's `message_sources`. No corpus-wide search, so the
     match reflects what the model was given.
  2. If the selection, or the sentence containing it, has `[n]` markers, those sources
     rank first.
  3. Split candidate snapshots into windows of about 60 tokens with 50% overlap. Window
     embeddings are computed once per message and cached.
  4. Score = cosine similarity (selection vs window), boosted by character-trigram overlap
     so numbers, names, and terms that must match exactly are weighted.
  5. Return the top 1–3 windows above threshold, or "no clear source found".
- **Popup shows:** file name, page or heading, the chunk text with the best window
  highlighted, and a match label ("strong match" / "partial match", never a raw score).
  It also has next/previous controls for multiple sources and an "Open in document"
  button. If `file_hash_at_answer` differs from the current file, show the snapshot with a
  "document changed since this answer" warning.

## 8. Document viewer

A side panel in the app.

| Type | Display |
|---|---|
| PDF | pdf.js renders the real page. The highlight overlay is drawn from `pdf_char_boxes`, mapped from the matched window's character offsets. |
| DOCX / TXT / MD | Extracted text, scrolled to the location with the passage highlighted (not the original formatting). |

Every type also has "Open with default app" (`os.startfile`).

## 9. Models and settings

- **Catalog:** `models.json` shipped with the app. Each entry has `id`, `role`
  (embedding|llm), `tier` (small|medium|large), `file_size`, `url` (Hugging Face),
  `sha256`, `license`, `languages`, `context_length`. It updates with app releases.
- **Hardware detection:** RAM, and GPU devices/VRAM via `llama-server --list-devices`.
  Tamra recommends a tier; the user can override.
  - Small: about 1–4B parameters, for low-spec or CPU-only machines.
  - Medium: about 7–9B.
  - Large: about 14B and up, for GPUs with enough VRAM.
- **Initial LLM candidates:** the Qwen family (strong Chinese, reasonable Thai) and
  Typhoon (Thai-tuned). Exact picks per tier are chosen in M2 against the eval set.
- **Download:** resumable (HTTP Range), with progress, verified against `sha256` before
  use, stored in `models\`.
- **Import:** for offline machines. Pick a file, verify `sha256` against the catalog, and
  copy it into `models\`. Uncatalogued GGUF files are allowed for the LLM role, with a
  warning. The embedding model must match the catalog exactly.
- **Settings:**
  - Mode: `local` or `api`.
  - API provider: Anthropic or OpenAI-compatible (base URL), plus model name. The default
    Anthropic model is `claude-sonnet-5-5`.
  - API keys are stored in Windows Credential Manager via `keyring`, never in SQLite or
    files.
  - The UI always shows which mode is active. In API mode it states that retrieved
    passages are sent to the provider.

## 10. OCR (M5)

- PDF pages flagged `needs_ocr` are run through OCR. Word boxes are stored in the same
  shape as `pdf_char_boxes`, so highlighting works unchanged.
- **Engine:** decided by a spike at the start of M5. Compare Tesseract (tha/chi_sim/eng),
  RapidOCR, and Windows built-in OCR on real Thai, Chinese, and English scans, scoring
  accuracy, speed, bundle size, and license.

## 11. Packaging and release

- PyInstaller (one-folder) bundles the Python core and the built UI. Inno Setup produces
  `Tamra-Setup-<version>.exe`.
- GitHub Actions builds the installer on a version tag and attaches it to the GitHub
  release.
- v1 has no code signing (SmartScreen warns on first run) and no auto-update.

## 12. Testing

- **pytest (core):**
  - Chunker with Thai, Chinese, English, and mixed fixtures.
  - Each parser, including a TIS-620 TXT and a PDF with known character boxes.
  - Reconcile and watcher logic.
  - Hybrid retrieval on a small fixture corpus.
  - Attribution matching.
  - `answer` with a fake `llm` provider.
- **Retrieval eval:** `eval/questions.jsonl` holds TH/EN/ZH questions, each with the
  expected file and page. A script reports hit@k. Run it whenever chunking, retrieval, or
  embedding changes. It also tunes the §6 and §7 thresholds.
- **Vitest (UI):** selection → "Check source" button, `[n]` chip rendering, and popup
  states.
- **Manual smoke test** of the packaged installer on a low-spec machine before each
  release.

## 13. Milestones

| # | Scope | Exit criterion |
|---|---|---|
| M0 | Scaffold (uv + Vite), CI (lint + tests), CLAUDE.md commands. **Spike:** llama-server (Vulkan) + bge-m3 ONNX + sqlite-vec packaged with PyInstaller, running on Windows | A packaged exe embeds text, stores and queries a vector, and generates tokens with a small GGUF |
| M1 | Core loop: one collection, 4 file types, hybrid search, local LLM answers with `[n]`, chats persisted | Ask a question about a folder of mixed TH/EN/ZH docs and get a cited answer |
| M2 | API providers, settings UI, model catalog/download/import, hardware tiers, LLM picks per tier | Switch local↔API and see both work; install a model offline via import |
| M3 | Attribution popup + document viewer with highlights | Select text in an answer, see its source highlighted in the PDF |
| M4 | Multiple collections per chat + chat history UI | Create two collections and query either or both |
| M5 | OCR (engine spike, then integration) | A scanned Thai PDF becomes searchable and highlightable |
| M6 | Installer + release workflow | Tagging a version publishes a working installer |

## 14. Risks

- **llama-server Vulkan on varied drivers** may fail to start or crash. This is why it is
  the M0 spike. The CPU retry (`--device none`) covers most cases. Fallback: also ship
  the CPU-only build.
- **Small local models answer Thai and Chinese poorly.** Mitigations: tier
  recommendations, the API mode, and eval-driven model picks.
- **bge-m3 ONNX on CPU** is slow for large first-time indexing. Mitigations: the background
  queue with progress, and batching. Measure in M0.
- **Trigram FTS** cannot match queries shorter than 3 characters. Dense search still covers
  these.
