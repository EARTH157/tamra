# M2 results

## Exit run

`dist/Tamra/Tamra.exe` was built with `./scripts/build.ps1` (Windows PowerShell 5.1) and checked on
the dev machine with `scripts/exe_smoke.py` and the packaged selfcheck. Build output: `Tamra.exe`
is 14.4 MB and the whole `dist/Tamra` folder is 249 MB. Models: bge-m3 int8 and the
Qwen2.5-0.5B-instruct Q4_K_M dev model, nothing downloaded. The test suite had 538 passing tests
and the lint and format checks were clean.

**What the exit criterion covers**

- Switching between local and API works, with an OpenAI-compatible server (a second
  llama-server). Anthropic is not exercised here: the owner tests it with their own key.
- A model can be installed offline by import.
- The real-window check (the UI in the pywebview window) is the owner's.

### Packaged selfcheck

Exit code 0; `sqlite`, `documents`, `providers`, `embedding`, and `llm` all `ok`. The new
`providers` probe imports the Anthropic SDK, builds `anthropic.Anthropic(api_key="x")` (no request),
reads the keyring backend, imports truststore, and loads `catalog.json` from inside the exe:

```json
"providers": {
  "ok": true,
  "keyring_backend": "WinVaultKeyring",
  "anthropic": "1.11.0",
  "catalog_models": 4
}
```

| Measure | Value |
|---|---|
| Embedding (packaged) | 6.75 passages/s, model load 1.8 s |
| Local LLM start | 4.93 s, first token 0.04 s |
| Local LLM speed | 267.86 tokens/s (64 tokens) |
| GPU used (Vulkan) | yes |

### Smoke test

The exe in `--dev` mode with a scratch data folder and a scratch models folder holding only
bge-m3 (hard-linked from `.models`). Exit code 0, no problems, 3 of 3 answerable questions cited.

| Step | Result |
|---|---|
| Startup to healthy API | 4.0 s |
| Import of the 0.5B dev GGUF (`POST /api/models/import`) | id `import:qwen2.5-0.5b-instruct-q4_k_m.gguf`, uncatalogued, with the warning; listed under `uncatalogued` and active as the local model |
| Indexing 10 files | 3.6 s (10 indexed, 0 failed, 0 skipped) |
| Thai, English, Chinese questions | 3.6 s (includes the first model use), 0.2 s, 0.1 s; all cited, provider `local` |
| Off-topic question | 0.0 s, the not-found reply with no sources |
| `think: true` request | accepted, stream ended with `done`, no error (the 0.5B model cannot think) |
| Folder watcher indexes a new file | 2.5 s |

**API mode** (second llama-server on a free port with a per-run throwaway key and the
`--api-key` flag; provider "openai" pointed at it):

| Step | Result |
|---|---|
| Throwaway key saved through `PUT /api/settings/api-key` | reported as set (`api_key_set` true) |
| `POST /api/settings/test-connection` | ok |
| Question in API mode (`en-lease-rent`) | 3.5 s, cited, saved with provider `openai` |
| Switch back to local, same question | 3.3 s, cited, saved with provider `local` |
| Key deleted with an empty `PUT`, then checked | gone from the app (`api_key_set` false) and from Credential Manager |

The step reads the real Credential Manager entry (service `Tamra`, user `openai`) first and
skips itself if a key is already there; it never overwrites or deletes an existing key. Here no key
was stored, so the step ran. A check after the run found no `openai` key stored. The script
restores the mode and provider settings it changed, and it never prints the key.

**Process cleanup.** The exe was ended with a hard kill by PID. No `llama-server.exe` started by
the run (the exe's own and the API-mode one) was left afterwards.

## Not done in M2

- **LLM picks per tier.** The spec picks each tier's model from the eval set. No model downloads
  happened in M2, so the Qwen3 picks (4B, 8B, 14B) are unverified. After the owner adds real
  models, run `uv run python scripts/eval_retrieval.py` and an answer-quality pass per tier.
- **Retrieval numbers.** The embedding model and the index did not change in M2, so the M1
  retrieval results in [2026-10-m1-results.md](2026-10-m1-results.md) still apply and were not
  re-measured.
- **Anthropic.** Streaming and extended thinking are covered by unit tests with a fake client; a
  live call with a real key is the owner's check.
- **Verify a model on first use.** Downloads and imports check the sha256 when the file arrives,
  and later launches check only the file size. Hashing a 5-9 GB model again on every launch
  would add 10 seconds or more to the first answer, so this is deferred. A model file that is
  damaged on disk after it was installed is not noticed until it fails to load.
- **Clear stale llama DLLs on re-extraction** (carried over from M0). `fetch_assets.py` extracts a
  new llama.cpp build over the old one without removing files the new build no longer ships.
  Do this before the first pin bump.
- **Smaller gaps a user may notice:**
  - A catalog model whose file has the right size but a damaged body cannot be repaired from
    the app: Download answers "Already installed." until the file is deleted by hand.
  - Closing Tamra during a model download does not cancel it first; the `.part` file stays
    and the next download resumes from it. An import that was interrupted can leave a hidden
    `.*.import` temp file in the models folder, which is not cleaned up at startup.
  - Import hashes a file before it checks that it is a GGUF, so a very large wrong file is
    refused slowly.
  - The radio groups in Settings take one Tab stop per option instead of arrow-key navigation.
  - A "thinking" block is dropped when an answer fails, so a long thought that ended in an
    error is not kept on screen.
