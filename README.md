# Tamra

**Ask questions about your own documents and check every answer against its source.**

Tamra (ตำรา, Thai for "textbook" or "authoritative reference") is an open-source desktop
app for Windows. Point it at a folder of documents and ask questions in Thai, English, or
Chinese. Answers cite the passages they came from, and you can select any part of an
answer to see the exact source text that supports it.

> **Status: pre-alpha.** Milestone M1 works: index a folder, ask in Thai, English, or Chinese, and get answers with [n] citations from a small local model. There is no installer yet (M6). See the [roadmap](#roadmap).

## Planned features

- **Offline-first.** Documents, the search index, and the AI models stay on your machine.
  Internet access is needed only to download models, and even that can be replaced by
  importing model files by hand.
- **Your choice of AI.** Use a local model that runs on your own hardware (sized to the
  machine, from laptops to GPU desktops), or connect to a cloud API (Anthropic, or any
  OpenAI-compatible server such as Ollama or LM Studio).
- **Thai, English, and Chinese**, including mixed-language documents and cross-language
  questions.
- **Verifiable answers.** Inline `[n]` citations. Select any text in an answer to see the
  supporting passage, highlighted in the original PDF.
- **Folder-based collections.** Tamra watches your folders and keeps the index up to date.
  It never modifies your files.
- PDF, Word (.docx), TXT, and Markdown, with OCR for scanned PDFs planned.

## Try it from source

You need Windows 10 or 11, [uv](https://docs.astral.sh/uv/), and Node.js 24.

    uv sync
    uv run python scripts/fetch_assets.py   # llama.cpp, bge-m3, and a small test model (~1.1 GB)
    npm --prefix ui ci
    npm --prefix ui run build
    uv run tamra

Choose a folder of PDF, Word, text, or Markdown files, wait for indexing, and ask a question.
`./scripts/build.ps1` packages the same app as `dist\Tamra\Tamra.exe`. The packaged
`Tamra.exe` looks for models in `%LOCALAPPDATA%\Tamra\models` (copy the contents of `.models`
there) or in the folder named by `TAMRA_MODELS_DIR`. M1 uses Qwen2.5-0.5B, a very small model,
so expect rough answers, especially in Thai and Chinese; M2 adds larger models and cloud APIs.

## Roadmap

| Milestone | Scope |
|---|---|
| M0 | Foundation: project scaffold, CI, packaging spike |
| M1 | Core loop: index a folder, hybrid search, cited answers with a local model |
| M2 | Cloud API mode, settings, model download/import, hardware-based model tiers |
| M3 | Source popup and document viewer with highlights |
| M4 | Multiple collections and chat history |
| M5 | OCR for scanned documents |
| M6 | Windows installer and release workflow |

The full design is in [docs/superpowers/specs/2026-10-02-tamra-design.md](docs/superpowers/specs/2026-10-02-tamra-design.md).

## Built with

Python (FastAPI, ONNX Runtime, SQLite + sqlite-vec) · React + TypeScript (Vite) ·
pywebview · [llama.cpp](https://github.com/ggml-org/llama.cpp) ·
[bge-m3](https://huggingface.co/BAAI/bge-m3) embeddings

## ภาษาไทย

Tamra คือแอปบน Windows สำหรับถามคำถามจากเอกสารของคุณเอง รองรับภาษาไทย อังกฤษ และจีน
ใช้งานแบบ offline ได้ และทุกคำตอบอ้างอิงแหล่งที่มา คลุมข้อความในคำตอบเพื่อดูต้นฉบับที่ใช้ตอบได้ทันที
ตอนนี้ (M1) ใช้งานจากซอร์สโค้ดได้แล้ว: เลือกโฟลเดอร์เอกสาร ถามคำถาม แล้วได้คำตอบพร้อมอ้างอิง [n] ยังไม่มีตัวติดตั้ง

## License

[Apache-2.0](LICENSE)
