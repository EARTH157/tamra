# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Tamra (ตำรา) is an open-source (Apache-2.0) Windows desktop app for asking questions about
your own documents (Thai, English, and Chinese). It works offline and gives answers with
verifiable citations.

- Design spec (source of truth for behavior): `docs/superpowers/specs/2026-10-02-tamra-design.md`
- Implementation plans, one per milestone: `docs/superpowers/plans/`
- Spike measurements: `docs/spikes/`

## Commands

```powershell
uv sync                                   # install/refresh Python env (Python 3.12, managed by uv)
uv run pytest                             # unit tests (skips tests marked `assets`)
uv run pytest tests/test_paths.py::test_data_dir_uses_override_and_creates_it -v   # single test
uv run pytest -m assets                   # tests needing downloaded models/binaries
uv run ruff check; uv run ruff format     # lint, format
uv run tamra --dev                        # core API only on :8765, token "dev"
npm --prefix ui run dev                   # UI with hot reload → http://localhost:5173/#token=dev
npm --prefix ui test                      # UI unit tests (Vitest)
npm --prefix ui run build                 # build UI into ui/dist (bundled by PyInstaller)
uv run python scripts/fetch_assets.py     # download pinned llama.cpp + dev models (~1.1 GB, gitignored)
./scripts/build.ps1                       # UI build + PyInstaller → dist/Tamra/Tamra.exe
# windowed exe: get output via --report and Start-Process -Wait
Start-Process dist\Tamra\Tamra.exe -ArgumentList "selfcheck","--report","r.json" -Wait
```

## Architecture

There is one Python package, `tamra` (`src/` layout, uv, Python 3.12). It serves FastAPI on
`127.0.0.1` behind a per-launch token (`X-Tamra-Token`). A pywebview (WebView2) window
loads the React/TypeScript UI built by Vite into `ui/dist`.

The modules follow the RAG pipeline. Each owns one stage:
- `ingest`: watch folders, parse files, and chunk them with locations.
- `embedder`: bge-m3 ONNX embeddings.
- `retriever`: hybrid search.
- `llm`: generation providers.
- `answer`: the prompt, `[n]` citations, and persisted sources.
- `attribution`: maps a selected answer span to its source passage.
- `models`: the model catalog, download/import, and hardware tiers.
- `store`: the only module that touches SQL.

Not all of these modules exist yet. Plans add them milestone by milestone.

## Rules that must hold

- **Storage is one SQLite file.** It holds metadata, chats, chunks, the FTS5 `trigram`
  keyword index, and `sqlite-vec` vectors. A file's chunk replacement happens in one
  transaction.
- **Embedding is always local and uses one fixed model.** Each collection records its
  `embedding_model_id`. On mismatch, require a rebuild. Never search a mismatched index.
- **Local LLM = llama.cpp's prebuilt `llama-server.exe`** (Vulkan build). It runs as a
  child process and is called through the OpenAI-compatible client. Do not add Python
  llama.cpp bindings. Every LLM provider exposes
  `generate(messages, max_tokens) -> Iterator[str]`.
- **Licenses:** every dependency must be Apache-2.0-compatible. No AGPL or GPL. For
  example, use `pypdfium2`, never PyMuPDF.
- **Assets:** model weights and llama.cpp binaries are pinned with sha256. They are never
  committed (`.models/`, `vendor/`).
- **Secrets:** API keys live only in Windows Credential Manager (`keyring`).
- **Data:** user data lives under `%LOCALAPPDATA%\Tamra\` (override: `TAMRA_DATA_DIR`).
  User document folders are never modified.
- **Language:** answer in the question's language. Code, comments, docs, and commit
  messages are in English.
