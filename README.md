# Tamra

**Ask questions about your own documents and check every answer against its source.**

Tamra (ตำรา, Thai for "textbook" or "authoritative reference") is an open-source desktop
app for Windows. Point it at a folder of documents and ask questions in Thai, English, or
Chinese. Answers cite the passages they came from, and you can select any part of an
answer to see the exact source text that supports it.

> **Status: pre-alpha.** Milestone M3 works: index a folder, ask in Thai, English, or Chinese, and get answers with [n] citations from a local model or a cloud API, check any sentence against its source and open the PDF at the highlighted passage, with a Settings page and an English or Thai interface. There is no installer yet (M6). See the [roadmap](#roadmap).

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
`Tamra.exe` looks for models in `%LOCALAPPDATA%\Tamra\models` or in the folder named by
`TAMRA_MODELS_DIR`; see [Models](#models). The Qwen2.5-0.5B test model that `fetch_assets.py`
downloads is very small, so expect rough answers from it, especially in Thai and Chinese.

## Checking a source

Every answer cites its sources with numbers like [1]. To check a claim, select a sentence in
the answer and press **Check source**, or click a number to check the sentence that ends at it.
A panel opens beside the answer. It shows your selection next to the passage Tamra found in the
sources of that answer, with a label: **Strong match** or **Partial match**. Tamra only looks in
the sources the answer used; it does not search your other documents.

Press **Open file** to see the passage in the document itself. A PDF opens as the real page,
with a yellow mark on the passage, and you can zoom and turn pages. A Word, text, or Markdown
file opens as text with the matching lines marked. If you have changed the file since the answer
was written, Tamra says so and still tries to find the passage. **Open with default app** in
that window opens the file in your usual program for it. Tamra only reads your files; it never
changes them.

The yellow mark shows where the passage is. A "strong match" means the wording matches, not
that the claim is true or fully supported: the passage may be on the same topic and say
something different. You still judge the claim by reading it.

## Models

Open **Settings > AI model**. Tamra detects your RAM and GPU and recommends a model size:

| Tier | Model | Download | For |
|---|---|---|---|
| Small | Qwen3-4B (Q4_K_M) | 2.5 GB | CPU-only machines |
| Medium | Qwen3-8B (Q4_K_M) | 5.0 GB | a GPU with 6 GB VRAM or more |
| Large | Qwen3-14B (Q4_K_M) | 9.0 GB | a GPU with 10 GB VRAM or more |

- **Download.** Press **Download** next to a model. Tamra downloads the file once, resumes an
  interrupted download, and checks its sha256 when it is downloaded or imported.
- **Import for offline use.** On a computer without internet, press **Import model file...** and
  choose a `.gguf` file (when you run Tamra from source in the browser, there is no file dialog:
  enter the full path instead). Tamra copies it into its models folder. A file that
  matches a catalog model is recognised as that model. Any other GGUF is imported as an
  *uncatalogued* model: it works, but Tamra warns that its answer quality is unknown. Files that
  are not GGUF, or that share a catalog file's name but not its content, are refused.
- **Think longer.** Models that can reason (the Qwen3 family) offer a "think longer" switch in
  the message box. It makes answers slower and usually better.

Models are stored in `%LOCALAPPDATA%\Tamra\models` (or `TAMRA_MODELS_DIR`). When you run
Tamra from source, they live in the repo's `.models` folder instead. Search and
indexing always run locally with the bge-m3 embedding model, whichever AI mode you choose.

## Cloud API

For better answers on a computer that cannot run a large model, switch to **Cloud API** in
**Settings > AI model**. Pick a provider:

- **Anthropic** (default model `claude-sonnet-5-5`), or
- **OpenAI-compatible**: OpenAI, or any server that speaks the same API, such as Ollama or LM
  Studio. Enter its Base URL, for example `http://localhost:11434`.

Paste your API key and press **Test connection**.

- **What leaves your computer.** In API mode, the passages found for each question, and the
  question itself, are sent to the provider. So are the previous question of the chat and up
  to 600 characters of its answer, which help the model follow a follow-up. Your files and the
  search index stay on your computer. Use a local model if your documents must not leave it.
- **Where the key is stored.** In Windows Credential Manager, under the name `Tamra` (the user
  name is the provider: `anthropic` or `openai`). It is never written to Tamra's files, database,
  or logs. To remove it, press **Remove key** next to the key field in **Settings > AI model**
  and confirm, or delete the `Tamra` entry in Credential Manager.

## Thai interface

Choose **Settings > General > Interface language > ไทย** to switch every screen between
English and Thai. Answers always follow the language of your question, whatever the interface
language is.

## Roadmap

| Milestone | Scope |
|---|---|
| M0 | Foundation: project scaffold, CI, packaging spike |
| M1 | Core loop: index a folder, hybrid search, cited answers with a local model |
| M2 | Cloud API mode, settings, model download/import, hardware-based model tiers |
| M3 | Source checking and document viewer with highlights |
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
ตอนนี้ (M2) ใช้งานจากซอร์สโค้ดได้แล้ว: เลือกโฟลเดอร์เอกสาร ถามคำถาม แล้วได้คำตอบพร้อมอ้างอิง [n]
จากโมเดลในเครื่องหรือ API บนคลาวด์ มีหน้าตั้งค่าและเปลี่ยนภาษาของแอปเป็นไทยได้ ยังไม่มีตัวติดตั้ง

- **โมเดล:** ที่ ตั้งค่า > โมเดล AI ดาวน์โหลดโมเดลที่แนะนำตามเครื่องของคุณ หรือกด "นำเข้าไฟล์โมเดล"
  เพื่อใช้ไฟล์ .gguf ที่มีอยู่แล้วโดยไม่ต้องต่ออินเทอร์เน็ต
- **API บนคลาวด์:** รองรับ Anthropic และเซิร์ฟเวอร์ที่เข้ากันได้กับ OpenAI เมื่อใช้โหมดนี้
  ข้อความที่ค้นพบสำหรับแต่ละคำถามจะถูกส่งไปยังผู้ให้บริการ ส่วนไฟล์และดัชนีค้นหายังอยู่ในเครื่อง
- **ที่เก็บ API key:** Windows Credential Manager ภายใต้ชื่อ Tamra ไม่เขียนลงไฟล์ของ Tamra

## License

[Apache-2.0](LICENSE)
