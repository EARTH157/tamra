# M0 spike results — 2026-10-03

Machine: AMD Ryzen 7 250 w/ Radeon 780M Graphics (8 cores / 16 threads), 16 GB RAM (15.3 GiB as
reported by Windows), Windows 11 Pro 10.0.26200. GPUs (`vendor/llama/llama-server.exe
--list-devices`): Vulkan0 AMD Radeon(TM) 780M Graphics (8094 MiB, shared memory), Vulkan1 NVIDIA
GeForce RTX 5060 Laptop GPU (7899 MiB).

Scope: the project owner first declined the ~1.1 GB asset downloads (2026-10-02), so the first pass
of this report measured only sqlite, the packaging, and the window. On 2026-10-03 the owner approved
the downloads. All pinned assets were fetched and verified against their sha256 pins, and the rows
that were pending are now measured.

**M0 exit criterion (spec §13): met.** The packaged `Tamra.exe`, rebuilt with the llama.cpp
binaries, passed the full selfcheck on this machine: it embeds text, stores and queries a vector,
and generates tokens with the small GGUF (`ok: true`, exit 0). The plan's done condition is also
met: the CI `package` job is green on PR #1 (the runner downloaded the llama zip, the bundle-layout
guard passed, and the packaged sqlite selfcheck exited 0).

Build: PyInstaller 6.22.3 (contrib hooks 2026.8), CPython 3.12.15, pywebview 6.2.1, pythonnet
3.2.0, onnxruntime 1.30.0, tokenizers 0.23.2, numpy 2.5.3, sqlite-vec 0.1.9, llama.cpp b11344
(Vulkan build). WebView2 runtime 154.0.4258.48.

| Check | Result |
|---|---|
| sqlite (packaged exe) | sqlite 3.53.1, sqlite-vec v0.1.9, trigram true |
| embedding (bge-m3 int8, CPU) | packaged exe: load 1.87 s, 5.95 passages/s (from source: 1.81 s, 6.23 passages/s). The selfcheck passage is 207 bge-m3 tokens, not ~450, so this is about 1,230-1,290 tokens/s |
| llm (qwen2.5-0.5b q4_k_m) | packaged exe: `gpu_used` true (Vulkan1, RTX 5060, 25/25 layers), start 2.45 s, first token 0.05 s, 255 chunks/s (selfcheck metric). llama-server's own timings: decode ~310 tok/s, prompt ~2,350 tok/s |
| llm, iGPU pinned (`device="Vulkan0"`, Radeon 780M) | decode 73.8 tok/s, prompt 14.0 tok/s, first token 1.01 s |
| llm, CPU only (`gpu=False`) | decode 41.7 tok/s, prompt 354 tok/s (selfcheck-style 40.5 chunks/s) |
| packaged exe size | 228.9 MB (228 files) in `dist/Tamra` with the llama.cpp binaries; 117.5 MB (162 files) without them |

The packaged-exe rows come from `Tamra.exe selfcheck --embed-model-dir ... --llm-model ... --report`.
The other llm rows come from a scratch script that drives `LlamaServer` and `OpenAICompatibleLLM`
and reads llama-server's `timings` for a 256-token completion.

## Findings

- **Vulkan devices:** with no `--device`, llama.cpp b11344 picks the NVIDIA dGPU (Vulkan1) alone and
  offloads all 25/25 layers. Pinning it gives the same speed (310.8 vs 315.0 tok/s decode), so no
  pinning is needed on this machine. The Radeon 780M iGPU is about 4x slower to decode and very slow
  at prompt processing (14 tok/s), so an iGPU should never be preferred over a dGPU. The very first
  launch on this machine was slower (prompt 18 tok/s, decode 126 tok/s, selfcheck 13.35 chunks/s),
  which fits one-time Vulkan pipeline compilation; every later launch matched the table.
- **llama-server log semantics:** at the default verbosity (3) llama-server logs no device lines.
  With `-lv 4` it logs `using device VulkanN (...)` and `VulkanN model buffer size`. In CPU mode
  (`--device none`) it still logs `offloaded 25/25 layers to GPU` while every buffer is
  `CPU_Mapped` / `CPU_REPACK`. Detect real GPU use from the `using device` and `VulkanN ... buffer`
  lines, never from "offloaded" (risk 3).
- **llama-server security:** it warns `no API key is set and CORS allows all origins`, so any web
  page in the user's browser can call it once it finds the port. A per-launch `--api-key` is in the
  M1 list below.
- **Unicode model paths:** llama-server loaded the GGUF through a path containing Thai characters
  (a hard link in a Thai-named folder under the temp directory) at full speed.
- **Embedding quality:** cross-lingual cosine for the selfcheck sentences: Thai-English 0.88,
  Thai-Chinese 0.84, Thai versus an unrelated English sentence 0.40.
- **Asset tests:** `uv run pytest -m assets` gave 7 passed and 1 failed:
  `test_padding_does_not_change_embedding` (max abs diff 0.0148 > atol 1e-3), as risk 2 predicted.
- **Fetch path:** `scripts/fetch_assets.py` fetched and extracted the llama zip (flat layout:
  `llama-server.exe` at the zip root, 52 files). The bge-m3 model download then failed mid-stream
  (`RemoteProtocolError: peer closed connection without sending complete message body (received
  55805053 bytes, expected 568456694)`) at about 0.5-1 MB/s, and the fetcher has no resume or retry.
  The three Hugging Face files were then fetched with `curl` (resume and retries), and
  `fetch_assets.py` verified every file against its sha256 pin. Resumable, retrying downloads are
  needed before end users download models (risk 1).
- **Indexing estimate:** about 1,230-1,290 bge-m3 tokens/s on this CPU (int8, batch 16). 1,000 pages
  at ~500 tokens per page plus ~15% chunk overlap take about 7-8 minutes; at ~1,000 tokens per page
  (dense Thai) about 15 minutes. That is far better than the spec §14 worry, but it was measured on
  an 8-core Ryzen; low-end laptops will be several times slower.
- **Build script:** `scripts/build.ps1` now stops when a step fails. `npm ci` fails with EPERM while
  a Vite dev server (`npm --prefix ui run dev`) is running, because Vite holds rolldown's native
  binding. Stop the dev server first, or, when `ui/dist` is current, run only the PyInstaller step.
- **Packaging fixes:** none were needed. `packaging/tamra.spec` as written built
  `dist/Tamra/Tamra.exe` on the first run, both without and with the llama.cpp binaries.
  - `collect_dynamic_libs("sqlite_vec")` places `vec0.dll` in `_internal/sqlite_vec/`. A scratch copy
    of the bundle without that file fails with `OperationalError: The specified module could not be
    found.` (exit 1), so the passing check does use the bundled DLL.
  - The hooks that ship with pywebview and pyinstaller-hooks-contrib (webview, clr_loader,
    onnxruntime, uvicorn) bundled pywebview's `webview/lib` (WebView2 loader and .NET assemblies),
    pythonnet/clr_loader, and the onnxruntime DLLs. No `hiddenimports`, `binaries`, or `datas`
    additions beyond the brief's spec were needed.
  - Build warnings, none of which affected anything exercised: hidden imports
    `pycparser.lextab` and `pycparser.yacctab` (legacy tables that current pycparser no longer
    ships) and `tzdata` (requested by the stdlib `zoneinfo` hook but not installed, so the exe has
    no IANA time-zone database; add the `tzdata` package if M1 needs named time zones).
  - Inside the frozen exe the llama path resolves to `_internal/vendor/llama/llama-server.exe`.
- **Windowed-exe verification (no screen access):** launched `dist/Tamra/Tamra.exe` with no
  arguments and `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<free port>` to read
  the page through the Chrome DevTools Protocol.
  - The process main window title was `Tamra` on the first poll, 13 s after launch.
  - The WebView2 page list (`/json`) held one page, titled `Tamra`, at
    `http://127.0.0.1:<app port>/#token=<redacted>`; the exe's only listening socket was that
    loopback port.
  - The rendered `document.body.innerText` was `Tamra` and `Tamra core: ok (v0.1.0)`, so the bundled
    UI loaded from `_internal/ui/dist`, its script ran, and the token round trip to `/api/health`
    worked.
  - `GET /` returned 200 `text/html` and was byte-identical to `ui/dist/index.html`; `GET
    /api/health` without a token (and with a wrong one) returned 401.
  - The exe was stopped by PID with `taskkill /PID <pid> /T /F` (the exe plus 6 WebView2 children);
    the process, its children, and both ports were gone afterwards. pywebview uses a fresh
    `%TEMP%/tmp*/EBWebView` user-data folder per launch (~3.3 MB), and a forced kill leaves it
    behind. A normal window close was not tested.
- **Decisions or risks to carry into the M1 plan:**
  1. Model downloads are not robust: the Hugging Face CDN dropped a long download and the fetcher
     cannot resume or retry. Add resumable (HTTP Range), retrying downloads with progress before the
     M2 model download UI.
  2. The pinned bge-m3 export is int8 with dynamic activation quantisation (96
     `DynamicQuantizeLinear` and 144 `MatMulInteger` ops). A passage's vector depends on what else is
     in its batch: cosine 0.989 against the same text embedded alone, with or without padding
     (identical batches give 1.0). The embedder code is correct; the padding test's premise does not
     hold for this model. Decide in M1: accept about 1% batch noise and change the test to a cosine
     bound, embed one passage per call for deterministic vectors, or switch to an fp16/fp32 export.
     This affects index reproducibility and the spec §7 attribution scores.
  3. `LlamaServer.gpu_used=True` only means the GPU-flag launch became healthy. Detect real GPU use
     from the log as described above (start llama-server with `-lv 4`, or query devices) before
     showing the spec §6 "fell back to CPU" notice. On machines with both, the default chose the dGPU
     here; confirm the default on iGPU-only machines for the M2 hardware tiers.
  4. Every loopback HTTP call must bypass proxy settings (`trust_env=False`): httpx applies
     environment and Windows-registry proxies to 127.0.0.1, and new loopback code must follow this.
  5. The windowed exe has no stdout/stderr, and uvicorn runs with `log_config=None`, so its errors
     are dropped. Add a file log under `%LOCALAPPDATA%/Tamra/logs` in M1.
  6. `selfcheck.py` runs raw SQL for its probes, an M0 exception to "store is the only module that
     touches SQL". Move it onto store's vec API when M1 defines it.
  7. Selfcheck metric semantics: `tokens` counts SSE content chunks, and `tokens_per_sec` includes
     prompt processing (the first launch showed 13 chunks/s against ~255 later). The PASSAGE is 207
     bge-m3 tokens. Quote llama-server's own `timings` where speed matters.

## Deferred review findings by milestone

Findings deferred during the M0 task reviews and the final review, triaged by target milestone.

**M1 (before the app wires these modules together)**
- Security: add `TrustedHostMiddleware` (127.0.0.1/localhost) against DNS rebinding — dev mode uses a fixed port and token; replace the `@app.middleware("http")` token gate with a pure ASGI gate that also covers WebSocket scopes and works with SSE cancellation; reject empty tokens; gate bare `/api`.
- UI: read the token once at startup and drop it from the URL (`history.replaceState`); handle a malformed `#token=` escape; render LLM/document text as plain text and add a Content-Security-Policy.
- llama-server ownership: run it in a Job Object (KILL_ON_JOB_CLOSE) so a killed Tamra.exe cannot orphan it; pass a per-launch `--api-key`; make `stop()` safe against an in-flight `start()` and double start; wrap log-open/mkdir failures as `LlamaServerError`; share one timeout budget across the GPU and CPU attempts.
- App: detect server-thread death / port in use in `_wait_until_up`; graceful Ctrl+C and try/finally around the window; friendly error when `ui/dist` is missing; file log under `%LOCALAPPDATA%/Tamra/logs`.
- Store: file-DB test pinning WAL, foreign keys and `check_same_thread`; close the connection if `connect()` fails; make `capabilities()` probe safe on a shared connection; move selfcheck's raw SQL onto store's vec API.
- Embedder and selfcheck: make the embedder testable without assets (injectable session/tokenizer or a tiny generated model); multi-batch test; guard `tokenizer.json` in the fixture; validate `batch_size`; name the missing file in errors; stub-server test for `_check_llm`; define selfcheck metrics (`tokens` counts SSE chunks) before quoting asset numbers; avoid creating `data_dir()` eagerly in the CLI.
- Paths: handle a missing `LOCALAPPDATA`, an empty override, and a relative `TAMRA_DATA_DIR`; test the frozen-exe branch of `resource_dir()`.
- LLM client: tests for incremental streaming, cancel-by-close and Thai/Chinese content; remove the no-op `except LLMError: raise` and dead store.
- Tests: clear `NO_PROXY` in the proxy-bypass tests; tighten the cp1252 selfcheck test to assert the printed JSON.
- Packaging: add `multiprocessing.freeze_support()` to `packaging/entry.py` together with any process pool.

**M2**
- `gpu_used` only means the GPU-flag launch became healthy; parse llama-server's offload/device log lines before showing the spec §6 CPU-fallback notice.
- OpenAI-compatible endpoints: normalise base URLs that end in `/v1`; support `max_completion_tokens`; revisit the fixed 120 s read timeout.
- Downloads: case-insensitive sha256; resumable HTTP Range downloads with `.part` cleanup; tests for chunk boundaries and error paths; clear stale llama DLLs on re-extraction before the first pin bump.
- Consider `truststore` for API mode and model downloads (corporate TLS-intercepting proxies).

**M6 (release)**
- Ship a consolidated third-party license notice in the bundle (release blocker).
- Bundle only `llama-server.exe` and its DLLs (+ license), not every llama.cpp tool.
- CI: restrict the `package` job to main, tags and `workflow_dispatch`, set artifact `retention-days`, add a concurrency group, avoid push+PR double runs.
- `ui/package.json` metadata (version 1.0.0 vs core 0.1.0); decide the UI language (`lang="th"` on an English UI).
