# Tamra M2 — Models, cloud API, and settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The user can choose where answers come from, switch between them, and make both paths work:
- a local model from a catalog, downloaded with resume or imported offline;
- a cloud API, either Anthropic or any OpenAI-compatible server.

They can also turn on "Think longer", get "Search only" results, and set up Tamra in a Settings page (General, AI model, Style) whose interface is in Thai or English. The design frames in `docs/design/` define the UI.

**Architecture:**
- **Settings.** Stored in a new `settings` table (schema v2). API keys live only in Windows Credential Manager, through `keyring`.
- **Models.** A new `tamra.models` package owns the model catalog (`catalog.json`), hardware detection, a resumable download manager, and import.
- **Providers.** `tamra.llm` gains an Anthropic provider and an API-mode OpenAI-compatible provider. Every provider is `generate(messages, max_tokens, *, think=False) -> Iterator[Chunk]`, where a chunk is either answer text or thinking text. `Core` builds the active provider from settings and rebuilds it when they change.
- **Answers.** The answer service gets a per-mode context budget, a `thinking` event, and a `search` response mode.
- **UI.** The composer gets the Answer/Search-only menu, the model menu, and the Think longer chip. A Settings page and an i18n string table are added.

**Tech Stack:** M1's stack, plus `anthropic` (MIT), `keyring` (MIT; uses `pywin32-ctypes`, BSD-3), and `truststore` (MIT) for corporate TLS in API mode and model downloads. There are no new npm packages.

**Spec:** `docs/superpowers/specs/2026-10-02-tamra-design.md` §6 (errors table, CPU fallback notice, context budget) and §9 (models and settings). Roadmap: `docs/superpowers/specs/2026-10-03-tamra-v1-roadmap-design.md` (M2 row). Design frames: `docs/design/` (1b, 1c, 1d, 2c, 2d, 4, 5, 6, 7). M2 items in "Deferred review findings by milestone" in `docs/spikes/2026-10-m0-results.md`.

**User decisions for M2 (2026-10-04):**
- **No model downloads during M2.** The user will add models later. The catalog and the download and import code are built and tested with fakes and small fixtures. The exit run uses the existing dev model, imported as an uncatalogued GGUF, plus a local OpenAI-compatible server.
- **The interface ships in Thai and English** (the "Interface language" setting in frame 4).

## Global Constraints

- Platform: Windows 10/11 x64. Python `>=3.12,<3.13` through uv. Never run `uv python install`, and never delete or recreate `.venv`.
- License: Apache-2.0. New runtime dependencies are `anthropic`, `keyring` (with `pywin32-ctypes`), and `truststore`; the implementer confirms the licence of each transitive dependency. No GPL, AGPL, or LGPL. No new npm packages.
- One SQLite file, and `tamra.store` is the only module that runs SQL. The schema moves from v1 to v2 through `migrate()` in one transaction. A v1 database from M1 must upgrade in place and keep its data.
- API keys are only ever in Windows Credential Manager (`keyring`, service name `Tamra`). They never go into SQLite, files, logs, API responses, or test fixtures. API responses show only `api_key_set: bool` and the last 4 characters.
- In API mode, the UI states that retrieved passages are sent to the provider (spec §9), and the model menu marks cloud entries the same way (frame 1c).
- Search and indexing always run locally. The embedding model never changes in M2.
- Every loopback HTTP call uses `trust_env=False`. Calls to cloud APIs and model downloads use the system trust store through `truststore`, and honour proxy settings.
- The default Anthropic model is `claude-sonnet-5-5` (spec §9). To get current SDK usage right (streaming, extended thinking), the implementer of the Anthropic task loads the `claude-api` skill.
- UI strings live in the i18n table (English and Thai). Code, comments, docs, and commit messages are in English. Answers follow the question's language.
- Before every commit, run `uv run ruff format`, `uv run ruff check --fix`, and `uv run ruff format --check`, then the touched tests, then `uv run pytest` once. UI tasks also run `npm --prefix ui test` and `npm --prefix ui run build`.
- Never kill processes by image name. Stop only processes you started, and only by PID. Never commit `AGENTS.md`. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- `fastapi.testclient.TestClient` uses `base_url="http://127.0.0.1"`.

## Task 1: Settings storage and schema v2

**Files:** `src/tamra/store/schema.py`, `src/tamra/store/repo.py`, `src/tamra/store/models.py`, `src/tamra/settings.py` (new), tests in `tests/test_store_settings.py` and `tests/test_settings.py`.

**Produces:**
- Schema v2 adds `settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)`. `migrate()` upgrades v1 to v2 in one transaction, and a fresh database goes straight to v2.
- Store methods: `Store.get_setting(key) -> object | None`, `Store.set_settings(values: dict)` (one transaction), `Store.all_settings() -> dict`.
- `tamra.settings.Settings` is a frozen dataclass. Fields and defaults:
  - `mode: "local" | "api" = "local"`
  - `local_model_id: str | None = None` (catalog id, or `"import:<file name>"` for an uncatalogued file)
  - `api_provider: "anthropic" | "openai" = "anthropic"`
  - `api_model: str = "claude-sonnet-5-5"`
  - `api_base_url: str = ""`
  - `language: "en" | "th" = "en"`
  - `theme: "light" | "dark" | "system" = "light"`
  - `accent: "green" | "blue" | "orange" | "purple" | "slate" = "green"`
  - `text_size: "small" | "default" | "large" = "default"`
  - `spacing: "comfortable" | "compact" = "comfortable"`
  - `ask_before_delete: bool = True`
- `load_settings(store) -> Settings` ignores unknown keys and falls back to defaults for invalid values. `save_settings(store, changes: dict) -> Settings` validates every field, raises `ValueError` with the field name, and writes only valid changes.

**Tests:**
- A v1 database built with the M1 `_V1` DDL plus one chat upgrades to v2 and keeps the chat.
- Defaults come back when nothing is stored.
- Invalid values are rejected.
- Unknown stored keys are ignored.
- A round trip of every field works.

## Task 2: Model catalog and hardware detection

**Files:** `src/tamra/models/__init__.py`, `src/tamra/models/catalog.json`, `src/tamra/models/catalog.py`, `src/tamra/models/hardware.py`, tests.

**Catalog entries (pinned; values copied verbatim):**

| id | role | tier | file | size | sha256 | url |
|---|---|---|---|---|---|---|
| `bge-m3-int8` | embedding | — | `bge-m3/model.onnx` (+ `tokenizer.json`) | per `scripts/assets.json` | per `scripts/assets.json` | per `scripts/assets.json` |
| `qwen3-4b` | llm | small | `Qwen3-4B-Q4_K_M.gguf` | 2497280256 | `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5` | `https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/bc640142c66e1fdd12af0bd68f40445458f3869b/Qwen3-4B-Q4_K_M.gguf` |
| `qwen3-8b` | llm | medium | `Qwen3-8B-Q4_K_M.gguf` | 5027783488 | `d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785` | `https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/7c41481f57cb95916b40956ab2f0b139b296d974/Qwen3-8B-Q4_K_M.gguf` |
| `qwen3-14b` | llm | large | `Qwen3-14B-Q4_K_M.gguf` | 9001752960 | `500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0` | `https://huggingface.co/Qwen/Qwen3-14B-GGUF/resolve/530227a7d994db8eca5ab5ced2fb692b614357fd/Qwen3-14B-Q4_K_M.gguf` |

Each LLM entry also records:
- `name` (for example "Qwen3-8B");
- `license: "apache-2.0"`;
- `languages: ["th", "en", "zh"]`;
- `context_length: 32768`;
- `thinking: true`;
- `min_vram_gb`, set to 0 for small, 6 for medium, and 10 for large. Frame 5 says 14B needs 12 GB of VRAM; use 10, because a Q4 14B at 8k context fits in 10–11 GB.

Keep the M1 dev model (`qwen2.5-0.5b-instruct-q4_k_m.gguf`) out of the catalog. It is an uncatalogued import.

**Produces:**
- `Catalog` with `llms()`, `embedding()`, `get(id)`.
- `installed(models_dir) -> dict[id, Path]`, based on the file being present and its size matching. The sha256 is verified at download or import time and again when a file is first used; `catalog.verify(id, path)` does that check.
- `uncatalogued(models_dir) -> list[Path]` lists `*.gguf` files that match no catalog entry.
- `tamra.models.hardware`:
  - `detect(llama_exe) -> Hardware(ram_gb, gpus: list[Gpu(name, vram_mb)])`. RAM comes from `GlobalMemoryStatusEx` via ctypes. GPUs come from parsing `llama-server --list-devices` (format: `  Vulkan0: AMD Radeon(TM) 780M Graphics (8094 MiB, 7689 MiB free)`), run with a 15 s timeout and `CREATE_NO_WINDOW`. On error, return no GPUs.
  - `recommend(hardware, catalog) -> tier`. Pick the largest tier whose `min_vram_gb` fits the largest discrete GPU's VRAM. With no GPU, use small. Integrated GPUs whose name contains "Radeon(TM)" plus "Graphics", or "Intel", count as no discrete GPU.

**Tests:**
- Parse the `--list-devices` sample above, plus a sample with no devices and one with garbage output.
- `recommend` cases: 8 GB discrete gives medium, 12 GB gives large, iGPU only gives small, nothing gives small.
- `installed` and `uncatalogued` against fixture files of the right size, using small fake catalog entries.

## Task 3: Resumable downloads and import

**Files:** `src/tamra/download.py`, `src/tamra/models/manager.py` (new), tests.

**Produces:**
- `download_resumable(url, dest, sha256, *, size=None, on_progress=None, cancel: threading.Event | None, transport=None) -> Path`:
  - Continues a `dest.part` file with an HTTP `Range` header. If the server ignores Range (status 200), it restarts.
  - Verifies sha256 case-insensitively.
  - On a hash mismatch it deletes the `.part` and raises `ChecksumError`.
  - On cancel it keeps the `.part` and raises `DownloadCancelled`.
  - It never leaves an unverified file at `dest`.
  - `download_verified` stays unchanged for `fetch_assets.py`.
- `ModelManager(models_dir, catalog)` runs at most one download at a time, on a worker thread:
  - `start_download(id)`
  - `cancel_download(id)`
  - `status() -> {id: {"state": "idle"|"downloading"|"verifying"|"error", "done": int, "total": int, "error": str|None}}`
  - `import_file(path) -> ImportResult(id, path, catalogued: bool, warning: str|None)`
- `import_file` behaviour:
  - If the file's sha256 matches a catalog entry, copy it into `models_dir` under that entry's file name.
  - If it is an unknown `.gguf`, copy it as-is with the warning "This model is not in Tamra's catalog; answer quality is unknown."
  - Refuse an embedding-role file that does not match exactly, and refuse non-GGUF files.
  - Copy to `.part`, then rename, so a half-copied file never looks installed.
- Add `truststore` (`truststore.inject_into_ssl()` once, at app start in `app.run` and in `fetch_assets.py`).

**Tests (with `httpx.MockTransport`):**
- A Range resume after an interrupted first attempt yields the right bytes and hash.
- A server that ignores Range causes a restart.
- A hash mismatch deletes the `.part`.
- Cancel keeps the `.part`.
- An upper-case sha256 in the catalog is accepted.
- Progress callbacks arrive in order.
- Import: catalogued file, uncatalogued GGUF with the warning, non-GGUF refused, and a failed copy leaves no file behind.

## Task 4: LLM providers — thinking-aware local, OpenAI-compatible API, Anthropic

**Files:** `src/tamra/llm/base.py` (new), `src/tamra/llm/openai_compat.py`, `src/tamra/llm/anthropic_api.py` (new), `src/tamra/llm/runtime.py`, `src/tamra/secrets.py` (new), tests.

**Produces:**
- `tamra.llm.base`:
  - `Chunk(kind: "text"|"thinking", text: str)`.
  - Protocol `Provider` with `generate(messages, max_tokens, *, think: bool = False) -> Iterator[Chunk]`, `close()`, `label: str`, `kind: "local"|"api"`.
  - `ProviderError(LLMError)` with a `reason` field: `"offline" | "auth" | "quota" | "model_missing" | "other"`. The spec §6 error table maps these to UI messages.
- `OpenAICompatibleLLM` changes:
  - It yields `Chunk`s.
  - Reasoning deltas (`delta.reasoning_content`) become `thinking` chunks.
  - `think` is sent as `chat_template_kwargs: {"enable_thinking": bool}`, which llama-server and Qwen3 honour.
  - Base URLs that end in `/v1` or `/v1/` are normalised, which was deferred from M0.
  - It sends `max_tokens`. If the server answers 400 mentioning `max_tokens`, it retries once with `max_completion_tokens`.
  - Timeouts: connect 10 s, read 300 s for local and 120 s for API.
  - HTTP 401/403 map to `auth`, 429 to `quota`, and connection errors to `offline`.
  - The M1 behaviour and tests stay, adapted to chunks.
- `tamra.llm.anthropic_api.AnthropicLLM(model, api_key)`:
  - Streams with the official SDK.
  - The system prompt goes in `system`.
  - `think=True` turns on extended thinking with a budget of 2048 tokens and raises `max_tokens` to allow for it. Thinking deltas become `thinking` chunks.
  - SDK errors map to `ProviderError` reasons.
- `tamra.secrets`:
  - `get_api_key(provider) -> str | None`, `set_api_key(provider, key)`, `delete_api_key(provider)`.
  - It uses `keyring` with service `Tamra` and username `<provider>`, and never logs the key.
- `LocalLLM`:
  - Takes the model path at `client()` time through a `model_path()` callable, so a change of model restarts llama-server on the next question.
  - Exposes `gpu_offload: bool | None`, parsed from the llama-server log (the line that reports layers offloaded to the GPU, `offloaded N/M layers to GPU`). This feeds the spec §6 "fell back to CPU" notice, which was deferred from M0.
- All existing M1 LLM tests are kept and adapted.

**Tests:**
- Reasoning deltas become thinking chunks.
- `enable_thinking` is sent.
- The `/v1` base URL is normalised.
- The `max_completion_tokens` retry works.
- Each error reason is mapped.
- Anthropic streaming is tested with a stubbed SDK client: thinking and text deltas, and auth and rate-limit errors.
- `keyring` is tested with an in-memory backend (`keyring.set_keyring(...)` in a fixture). No real Credential Manager writes happen in tests.
- The `LocalLLM` model change causes a restart.
- Offload parsing is tested on sample log lines.

## Task 5: Core and answer service wiring

**Files:** `src/tamra/core.py`, `src/tamra/answer.py`, tests.

**Produces:**
- `Core` loads `Settings` and builds the active provider:
  - In local mode, it uses `LocalLLM` with the selected installed model. If none is selected, it uses the first installed catalog LLM, then any uncatalogued GGUF.
  - In API mode, it uses `AnthropicLLM` or `OpenAICompatibleLLM`, with the key from `tamra.secrets`.
  - `Core.apply_settings(changes) -> Settings` saves the changes and rebuilds the provider, closing the old one.
  - `Core.models` is the `ModelManager`. `Core.hardware()` is cached after the first call.
- `AnswerService.ask(chat_id, question, *, mode="answer"|"search", think=False)`:
  - **Context budget:** the top 4 chunks for local and the top 8 for API (spec §6).
  - **`search` mode:** yields `sources`, then saves an assistant message with empty content and the sources (the UI renders it as passages only), then `done`. It never calls the LLM.
  - **Thinking:** `thinking` chunks are yielded as `{"type": "thinking", "text": ...}` events. They are not saved into the answer content.
  - **Saved provider and model:** the saved message records `provider="local"|"anthropic"|"openai"` and the provider label.
  - **Missing local model:** no installed local model gives an error event with `reason: "model_missing"`. The spec §6 behaviour is to send the user to model download/import.
  - **Provider errors:** these become error events carrying `reason`. Partial text is still kept.
- The auto-citation fallback and the not-found gate are unchanged.

**Tests:**
- The budget is 4 for local and 8 for API.
- Search mode never calls the LLM and saves the sources.
- Thinking events are not saved.
- A missing model gives `model_missing`.
- A provider switch closes the old provider.
- An API-mode answer is saved with `provider="anthropic"`.

## Task 6: API routes for settings, models, and keys

**Files:** `src/tamra/server.py`, `src/tamra/app.py` (file picker for import), tests.

**Routes** (all token-gated; reuse the existing gate):
- `GET /api/settings` returns the settings plus `api_key_set` and `api_key_hint` (the last 4 characters) for the selected provider.
- `PUT /api/settings` takes a partial body and returns the new settings. A bad value returns 400 naming the field.
- `PUT /api/settings/api-key` takes `{provider, key}` and stores the key in keyring. It returns 204, and an empty key deletes it.
- `POST /api/settings/test-connection` sends a 1-token request with the current API settings and returns `{ok, reason, message}`.
- `GET /api/models` returns:
  - `{hardware, recommended_tier, active: {mode, label}, gpu_offload, local: [...catalog llms with installed/downloading state/progress/size/tier...], uncatalogued: [...], embedding: {...}}`
- `POST /api/models/{id}/download` returns 202. `DELETE /api/models/{id}/download` cancels. `POST /api/models/import` takes `{path}` and returns the `ImportResult`.
- `POST /api/pick-file` opens a GGUF file dialog through pywebview, mirroring `/api/pick-folder`.
- `POST /api/chats/{id}/messages` accepts `{content, mode?, think?}`.
- `DELETE /api/chats` deletes all chats (frame 4, "Delete all chats").
- `POST /api/open-data-folder` runs `os.startfile(data_dir())`, only in window mode, and returns 501 in dev mode.

**Tests:**
- Every route, including validation.
- The key never appears in any response or log line; assert on captured logs.
- The token gate covers the new routes.

## Task 7: UI foundations — i18n, settings client, theme

**Files:** `ui/src/i18n.ts` (new), `ui/src/strings/en.ts`, `ui/src/strings/th.ts`, `ui/src/settings.ts` (new), `ui/src/styles.css`, `ui/src/types.ts`, tests.

**Produces:**
- `t(key, params?)` with English and Thai tables, using the language from `/api/settings`.
- Every existing UI string moves into the tables, and Thai is written as natural Thai, not word-for-word. Brand names (Tamra, ตำรา, model names) stay as they are.
- `useSettings()` is a React context that loads `/api/settings` once, gives an `update(changes)` that PUTs, and updates optimistically with rollback on error.
- Style settings map to CSS:
  - **Theme:** `data-theme="light|dark"` on `<html>`. `system` follows `prefers-color-scheme`.
  - **Dark palette:** derived so it matches frame 7's dark preview: background `#1F1D18`, sidebar `#28251F`, surface `#2E2B24`, text `#F2EFE7`, and the accent kept.
  - **Accent:** green `#0F5C56`, blue `#2B3FD1`, orange `#B5532A`, purple `#7A3E98`, slate `#3D4554`. These were sampled from frame 7; the implementer re-samples them with Pillow and corrects if needed.
  - **Text size:** small, default, or large scales the font size of the chat and source text.
  - **Spacing:** compact reduces vertical spacing.

**Tests:**
- `t()` fallback and params.
- Every key exists in both languages, checked by a test that compares the key sets.
- Theme and accent attributes are applied.
- `update` rolls back on error.

## Task 8: Composer chips, model menu, thinking block, search-only results

**Files:** `ui/src/ChatView.tsx`, `ui/src/Composer.tsx` (new), `ui/src/ModelMenu.tsx` (new), `ui/src/ModeMenu.tsx` (new), `ui/src/Thinking.tsx` (new), `ui/src/api.ts` (send `mode` and `think`), tests.

Match frames 1b, 1c, 1d, 2c, and 2d.
- **Mode menu:** only "Answer" and "Search only". Summarise and Check my draft are later ideas and are not shown.
- **Model menu:**
  - "On this computer" lists installed catalog models plus uncatalogued imports. A model that is downloading shows "downloading N%" and is disabled.
  - "Cloud API" shows the configured API model, marked "passages are sent to the provider".
  - "Manage models…" opens Settings > AI model.
  - Choosing an entry calls `PUT /api/settings` (mode and `local_model_id`).
- **Think longer:** a toggle chip that is on for the next questions. It is disabled when the active local model's catalog entry has `thinking: false`, and enabled for uncatalogued models.
- **Thinking block:** "Thinking… N s" streams the thinking text, collapsible (frame 2c). After the answer it collapses to "Thought for N s" (frame 2d). Thinking text is not saved, so a reloaded chat shows no thinking block.
- **Search-only results:** a "Passages found" list of source cards with their text, and no answer text.
- **Errors:** an error event with `reason` shows a matching message from the i18n table. `model_missing` gets a button that opens Settings > AI model.
- **Answer meta line:** shows the provider, for example "claude-sonnet-5-5 · Anthropic" or "Qwen3-8B · local".

**Tests:** the menus, the chip state, the thinking stream and collapse, search-only rendering, the `model_missing` button, and that `mode` and `think` are sent.

## Task 9: Settings page (General, AI model, Style)

**Files:** `ui/src/Settings.tsx` (new) plus tab components, `ui/src/App.tsx` (sidebar Settings link, routing between chat and settings), tests.

Match frames 4, 5, 6, and 7.

**General:**
- Interface language select (English or ไทย).
- "Ask before deleting a chat" toggle. When it is off, delete skips the dialog.
- Documents folder with Change folder, which reuses the dialog.
- Search index with "N files indexed · built with bge-m3" and Rebuild index.
- Data folder with Open folder; this is hidden in dev mode, where the route returns 501.
- Chat history with Delete all chats, which asks for confirmation.
- Footer "Tamra · pre-alpha · Apache-2.0 license".

**AI model:**
- The AI mode cards (Local model / Cloud API).
- The "Detected … RAM · GPU, N GB VRAM" line.
- The local model list with tier, size, "can think longer", Installed / Downloading N% with a bar / Download / Cancel, and the "Recommended" badge on the recommended tier. A download needs confirmation first: "Download Qwen3-8B (5.0 GB)?".
- Uncatalogued imports with the warning.
- "Import model file…", which uses the file picker or a typed path, plus its result message.
- In API mode:
  - the yellow passages warning;
  - the Anthropic / OpenAI-compatible segmented control;
  - Model and Base URL fields (Base URL disabled for Anthropic);
  - the API key field, shown masked with its last 4 characters once a key is set; typing replaces it;
  - Save and Test connection with the Connected or error result.

**Style:** the theme cards, accent swatches, text size and spacing segmented controls, and the live Preview.

**Tests:** each tab's controls call the right routes, the download confirmation, the masked key behaviour, the Delete all confirmation, and the language switch re-rendering strings.

## Task 10: Packaging, exit check, and docs

**Packaging:** add `tamra/models/catalog.json` as package data in `packaging/tamra.spec`. Add hidden imports for `keyring.backends.Windows`, the `anthropic` SDK, and `truststore`. Add a selfcheck `providers` probe: keyring backend available, anthropic importable, catalog loads.

**`scripts/exe_smoke.py` additions:**
- **Import:** the 0.5B dev model, imported through `POST /api/models/import` as an uncatalogued file, then selected as the local model and asked one question.
- **API mode:** an OpenAI-compatible provider pointed at a second llama-server, which the script starts with the 0.5B model on a free port and a known API key. The key is set through the API, which uses keyring for real here. The script deletes it at the end with `DELETE` (empty key) and verifies it is gone. One question is asked, and the answer must record `provider="openai"`.
- **Switch back to local:** one more question.
- **Thinking:** this cannot be exercised with the 0.5B model, so the script only checks that the request is accepted.

**Docs:**
- README: models and API setup, the Thai UI, and where keys are stored.
- `docs/spikes/2026-10-m2-results.md`: the exit run.
- CLAUDE.md commands, if new ones appear.

**Exit criterion (roadmap):**
- Switching between local and API works, here with an OpenAI-compatible server. The user tests Anthropic with their own key.
- A model can be installed offline via import.
- The real-window check is the user's.

## After M2

- The user adds real models (download or import) and an Anthropic key. Then run `scripts/eval_retrieval.py`, plus an answer-quality pass per tier, to confirm the per-tier picks. That is the spec's "LLM picks per tier from the eval set", deferred because no downloads happen in M2.
- Next is M3: the source popup, attribution, and document viewer with highlights (the frame 2b compare view, and Open file).
