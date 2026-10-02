# M0 spike results — 2026-10-03

Machine: AMD Ryzen 7 250 w/ Radeon 780M Graphics (8 cores / 16 threads), 16 GB RAM (15.3 GiB as
reported by Windows), Windows 11 Pro 10.0.26200. GPUs (names from `Get-CimInstance
Win32_VideoController`; this substitutes for `vendor\llama\llama-server.exe --list-devices`, which
could not run because the llama.cpp binaries were not downloaded): AMD Radeon(TM) 780M Graphics,
NVIDIA GeForce RTX 5060 Laptop GPU.

Scope: the project owner declined the ~1.1 GB asset downloads (llama.cpp Vulkan zip, bge-m3, Qwen
GGUF) on 2026-10-02. Only the sqlite check, the packaging, and the windowed-exe check were measured.
Everything that needs the assets is marked pending.

Build: PyInstaller 6.22.3 (contrib hooks 2026.8), CPython 3.12.15, pywebview 6.2.1, pythonnet
3.2.0, onnxruntime 1.30.0, tokenizers 0.23.2, numpy 2.5.3, sqlite-vec 0.1.9. WebView2 runtime
154.0.4258.48.

| Check | Result |
|---|---|
| sqlite (packaged exe) | sqlite 3.53.1, sqlite-vec v0.1.9, trigram true (`Tamra.exe selfcheck --report`: `ok: true`, exit 0) |
| embedding (bge-m3 int8, CPU) | pending — assets not downloaded (owner's decision, 2026-10-02) |
| llm (qwen2.5-0.5b q4_k_m) | pending — assets not downloaded (owner's decision, 2026-10-02) |
| llm, CPU only (`gpu=False` test) | pending — assets not downloaded (owner's decision, 2026-10-02) |
| packaged exe size | 117.5 MB (112.0 MiB, 162 files) in `dist/Tamra`; excludes the llama.cpp binaries (the pinned Vulkan zip is 33,207,732 bytes compressed) |

## Findings

- **Vulkan devices:** pending — assets not downloaded (owner's decision, 2026-10-02). No
  `using device ...` log lines exist yet, so it is unknown which device llama-server picks by
  default and whether pinning a GPU with `LlamaServer(device=...)` is faster. This machine has an
  AMD iGPU and an NVIDIA dGPU, so it is a good case for that comparison.
- **Packaging fixes:** none were needed. `packaging/tamra.spec` as written built
  `dist/Tamra/Tamra.exe` on the first run (58 s) and the sqlite-only selfcheck passed unchanged.
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
  - PyInstaller accepts an empty `vendor/llama` for `datas` (it collects nothing). Inside the frozen
    exe the llama path resolves to `_internal/vendor/llama/llama-server.exe`, which is where a real
    build puts the binaries.
- **Embedding stack in the frozen exe (partial, no assets):** the full embedding check is pending,
  but two probes show how far the packaged stack gets. With an empty model directory the check
  fails inside the Rust `tokenizers` file loader (`The system cannot find the file specified. (os
  error 2)`), so numpy, onnxruntime, and tokenizers all imported. With a toy `tokenizer.json` and a
  16-byte junk `model.onnx` it fails in ONNX Runtime's native parser (`INVALID_PROTOBUF ... Protobuf
  parsing failed.`), so `onnxruntime.dll` loaded and ran. A real model run is still unproven.
- **Indexing estimate:** pending — it needs the measured passages/s from the embedding check.
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
    `%TEMP%\tmp*\EBWebView` user-data folder per launch (~3.3 MB), and a forced kill leaves it
    behind. A normal window close was not tested.
- **Decisions or risks to carry into the M1 plan:**
  1. Assets were never fetched: the fetch path (HF redirects, zip extraction, llama zip layout), the
     embedder's ONNX output shape, and the `assets`-marked tests are unverified. Before M1, run
     `uv run python scripts/fetch_assets.py` and `uv run pytest -m assets`, then rebuild with
     `./scripts/build.ps1` (the current local `dist/Tamra` has no llama binaries) and run the full
     selfcheck from the packaged exe (the second command of Task 10 Step 3 in
     `docs/superpowers/plans/2026-10-02-tamra-m0-foundation.md`, which writes `selfcheck-full.json`).
     Then fill in the pending rows and findings of this report.
  2. The pinned bge-m3 export is int8 (Xenova `model_int8.onnx`). If it uses dynamic activation
     quantisation, embeddings depend on batch composition and padding, so
     `test_padding_does_not_change_embedding` (atol 1e-3) may fail with a correct embedder. Consider
     a cosine-bound test or an fp16/fp32 export.
  3. `LlamaServer.gpu_used=True` only means the GPU-flag launch became healthy; llama.cpp may
     silently run on CPU when Vulkan is unusable. Parse the log's device/offload lines before
     showing the spec section 6 "fell back to CPU" notice.
  4. Every loopback HTTP call must bypass proxy settings (`trust_env=False`): httpx applies
     environment and Windows-registry proxies to 127.0.0.1, and new loopback code must follow this.
  5. The windowed exe has no stdout/stderr, and uvicorn runs with `log_config=None`, so its errors
     are dropped. Add a file log under `%LOCALAPPDATA%/Tamra/logs` in M1.
  6. `selfcheck.py` runs raw SQL for its probes, an M0 exception to "store is the only module that
     touches SQL". Move it onto store's vec API when M1 defines it.
  7. Selfcheck metric semantics: `tokens` counts SSE content chunks, `tokens_per_sec` includes
     prompt processing, and the real token count of the 648-character PASSAGE is unverified.
  8. The CI `package` job had not run when this was written (the repo was not pushed yet). Until it
     is green, the real-llama bundle (`fetch_assets.py llama` into `vendor/llama`, then PyInstaller)
     is unproven. CI's selfcheck is sqlite only, so it never launches llama-server, but the job now
     fails if `dist\Tamra\_internal\vendor\llama\llama-server.exe` is missing (a wrong zip layout).
