# Tamra MCP server — design

**Date:** 2026-10-22
**Status:** Approved by the project owner in brainstorming (2026-10-22), pending written-spec review
**Builds on:** `docs/superpowers/specs/2026-10-02-tamra-design.md` (the behavior spec).

## 1. Goal and non-goals

**Goal:** Claude Desktop, Claude Code and other MCP clients can search the user's Tamra projects
and get cited passages back: the passage text, its project, file, and location ("p. 4 · OCR",
"lines 7-9"). The connected AI writes its own answer from them.

**Non-goals:**
- An `ask` tool that runs Tamra's own answer pipeline. The caller is already an LLM, local
  models are slow, and it would hold the single answer slot that the window's chat uses.
- A `read_file` tool that returns a whole document.
- Choosing which projects to share. One switch shares every project.
- Searching while Tamra is closed, or starting Tamra from the MCP command.
- A log of AI searches in the UI.
- Write tools of any kind. Everything exposed is read-only.

## 2. Decisions

| Decision | Choice | Reason |
|---|---|---|
| When it works | Only while Tamra is open | The running core already has the embedder loaded and the watcher keeping the index current. A second process would load bge-m3 again (about 1 GB RAM), search a possibly stale index, and need its own copy of the opt-in check. |
| Transport | stdio: `Tamra.exe mcp`, a bridge process that calls the running core over HTTP | Every MCP client can launch a stdio command. Claude Desktop's config only launches stdio servers, and the core's port changes on every launch, so a URL in a client's config would break on restart. |
| Where MCP lives | Only in the bridge (`tamra.mcp_server`, the `mcp` SDK). The core gets two plain JSON routes under `/api/mcp/`. | The core stays free of the protocol. An MCP endpoint inside the core plus a stdio relay would have to proxy sessions and streams, and no client gains from it. |
| Finding the core | A discovery file `mcp.json` in the data folder with the port and an MCP token | The bridge cannot get the per-launch values any other way. The data folder is under the user's profile, which only that user (plus SYSTEM and administrators) can read. |
| Token | A second per-launch token that opens only `/api/mcp/*` | The discovery file sits on disk, so the token in it must not open the UI's API (settings, deletes, keys). The UI token does not open `/api/mcp/*` either. |
| Opt-in | Setting `mcp_enabled`, off by default, checked by the core on every MCP request | Passages go to whatever model the client uses, possibly in the cloud. The switch applies at once, with no restart, and holds even if a stale discovery file is left behind. |
| Scope | Every project | The owner's choice: one switch, no per-project sharing. |
| Tools | `list_projects`, `search` | Enough for an AI to find and cite passages. |
| SDK | `mcp` 2.x (MIT), `from mcp.server import MCPServer` | It handles protocol version negotiation and keeps up with the MCP spec. A hand-written JSON-RPC loop would have to follow each spec revision. |

## 3. Architecture

```
MCP client (Claude Desktop, Claude Code, ...)
  └─ launches ─▶ Tamra.exe mcp            (tamra.mcp_server: MCPServer over stdio)
                   │ reads %LOCALAPPDATA%\Tamra\mcp.json  {port, token}  on every tool call
                   └─ HTTP 127.0.0.1:{port}, X-Tamra-Token: {mcp token}
                        ▼
Tamra.exe (the running app)
  └─ FastAPI ─ TokenGate: /api/mcp/* needs the MCP token; every other /api route the UI token
       ├─ GET  /api/mcp/projects
       └─ POST /api/mcp/search ─▶ Core.search(...) ─▶ the same retrieval as "Search only" (spec §6)
```

The bridge imports only `mcp`, `httpx` and `tamra.paths`: never `tamra.core`, `onnxruntime` or
`webview`. It does not take the single-instance mutex (that guards the app, `tamra.app.run`). It
reads the discovery file on every tool call, so a client started before Tamra works once Tamra is
open, and a Tamra restart (new port and token) is picked up without restarting the client.

## 4. Discovery file

- Path: `data_dir() / "mcp.json"`, so `TAMRA_DATA_DIR` applies (dev and smoke runs use their own).
- Content: `{"port": <int>, "token": "<secrets.token_urlsafe(32)>"}`.
- `tamra.app` writes it once the API answers its health check, in both window and dev mode, whether
  or not `mcp_enabled` is on. It writes a temporary file in the same folder, then `os.replace`, so
  the bridge never reads half a file. Shutdown deletes it (`missing_ok`). A crash can leave it
  behind; the next launch overwrites it.
- Writing it whatever the setting is lets the bridge tell "Tamra is not running" (no file, or no
  answer on the port) from "AI access is off" (the core answers 403). While the switch is off the
  token opens nothing.
- No extra ACL code: the default ACL of `%LOCALAPPDATA%` already limits it to the user. A process
  running as the same user can read the file, but it can already read `tamra.db` directly.

## 5. Core routes

Both routes sit behind the MCP token. While `mcp_enabled` is off, both answer
`403 {"detail": "AI access is off. Turn it on in Tamra: Settings > General > AI apps."}`.

**`GET /api/mcp/projects`** →
`200 {"projects": [{"name": str, "indexed_files": int, "stale": bool}]}`, ordered by name.
`indexed_files` is the count of files with status `indexed`; `stale` is true when the index was
built with another embedding model (such a project is never searched). Folder paths are not sent.

**`POST /api/mcp/search`** with body:

| Field | Rule |
|---|---|
| `query` | string; surrounding whitespace removed; 1 to 4000 characters (as `QuestionBody`) |
| `projects` | optional list of 1 to 50 project names, each at most `MAX_PROJECT_NAME` characters, matched ignoring case; left out means every project |
| `k` | optional integer, 1 to 20, default 8 |

Steps, in this order (each failure has a fixed message; no path or query text is echoed):

1. No projects at all → `409 "Tamra has no projects yet."`
2. An unknown name in `projects` → `400 "Unknown project: <name>. Call list_projects for the names."`
   (the name the caller sent, so the AI can correct it).
3. Leave out stale projects. If every chosen project is stale → `409 "The index of <names> was built
   with a different embedding model. Rebuild it in Tamra."`
4. No indexed file in the remaining projects → `409 "No documents are indexed yet. Wait for
   indexing to finish in Tamra."`
5. Embed the query. Embedder missing → `503 "The search model is not installed. Download it in
   Tamra: Settings > AI model."`
6. `hybrid_search` over the remaining projects with `fts_query(query)`. If the best dense
   similarity is below `AnswerSettings.min_similarity` (0.43), there are no passages: unrelated
   text never leaves the machine.
7. Otherwise the top `k` hits, best first.

→ `200 {"passages": [{"project": str, "file": str, "location": str, "text": str}], "notices": [str]}`.
`file` is the path relative to the project folder; `location` is `location_label(...)`; `text` is
the whole chunk. `notices` names left-out stale projects ("Not searched (rebuild needed): A, B")
and, when there are no passages, says "No passage matches this query closely enough."

The query is the caller's text alone; there is no previous-question context (`query_text`) because
the route has no chat. Nothing is saved: no chat, message or `message_sources` row.

Steps 3 to 7 are the retrieval of `AnswerService._ask` in "search" mode. The implementation shares
that code (one place decides the stale filter, the threshold and the top-k cut) instead of copying
it.

The core logs each MCP search at INFO with counts only: projects searched, `k`, passages returned.
It never logs the query or passage text.

## 6. The bridge (`Tamra.exe mcp`)

`python -m tamra mcp` / `Tamra.exe mcp` runs `tamra.mcp_server.main()`: an `MCPServer("Tamra")`
with short server instructions ("Searches the user's own documents indexed in Tamra; cite passages
by file and location") and two tools, served over stdio.

- **`list_projects()`**: returns the core's project list as is.
- **`search(query: str, projects: list[str] | None = None, k: int = 8)`**: returns the core's
  passages and notices as is. The tool description says the documents may be in Thai, English or
  Chinese, that a query in one language finds passages in another, and that the answer should cite
  each passage by file and location.

Each call reads `mcp.json`, then sends the request with `httpx` (`trust_env=False`, timeout 60 s:
the first search after launch can wait for the embedder to load). Failures become MCP tool errors,
in English, which the calling AI relays in the user's language:

| Situation | Tool error |
|---|---|
| No `mcp.json`, connection refused, or 401 | "Tamra is not running. Open Tamra and try again." |
| 403, 400, 409, 503 | the core's `detail` |
| Timeout or other failure | "Tamra did not answer. Try again." |

The bridge writes nothing to stdout except the protocol. It logs warnings to stderr when the client
gave it one, and never the query or passage text.

## 7. Setting and UI

- `Settings.mcp_enabled: bool = False`, validated as a bool, saved through `PUT /api/settings` like
  the others.
- `GET /api/settings` also returns `mcp_command`, a read-only list: `[sys.executable, "mcp"]` in the
  packaged exe, `[sys.executable, "-m", "tamra", "mcp"]` from source.
- **Settings > General**, a new section "AI apps (MCP)":
  - a switch "Let AI apps search my documents";
  - its hint: Claude Desktop, Claude Code and other MCP apps can search all projects while Tamra is
    open, and the passages they find are sent to that app's AI model, which may run in the cloud;
  - a "Copy setup" button that copies
    `{"mcpServers": {"tamra": {"command": mcp_command[0], "args": mcp_command[1:]}}}`. The hint under it
    says where to paste it: Claude Desktop's `claude_desktop_config.json`, or `.mcp.json` in a
    project folder for Claude Code (both take this `mcpServers` form).
- **Sidebar:** while `mcp_enabled` is on, a badge above the Settings button reads "AI apps can
  search". It opens Settings > General.
- English and Thai strings for all of the above.

## 8. Security summary

- The routes are read-only and return only what `list_projects` and `search` describe: no folder
  paths, file ids, chat content or settings.
- Two tokens: the UI token opens everything except `/api/mcp/*`; the MCP token opens only
  `/api/mcp/*`. Both are compared with `secrets.compare_digest`. The Host allowlist and security
  headers apply unchanged.
- The switch is checked on every request, so turning it off stops access at once.
- Inputs are validated at the route (query length, `k` range, list size, name length).
- Tool errors never carry a path or the query.

## 9. Packaging and dependencies

- `pyproject.toml`: `mcp>=2.3,<3`. Resolved against the lock on 2026-10-22, it adds `mcp` and
  `mcp-types` (MIT), `jsonschema`, `jsonschema-specifications`, `referencing`, `rpds-py`, `attrs`
  (MIT), `pyjwt` (MIT), `python-multipart` (Apache-2.0), `pywin32` (PSF) and `sse-starlette`
  (BSD-3-Clause), all compatible with Apache-2.0. The rest of its tree (`cryptography`,
  `opentelemetry-api`, `httpx2`, `pydantic`, `starlette`, `uvicorn`, `anyio`) is already in the lock.
  `NOTICE` lists only adapted code, so it does not change.
- PyInstaller: `tamra.mcp_server` as a hidden import, like the other lazily imported entry points.
- The bundle grows (mainly `pywin32` and `rpds-py`). The plan records the size before and after.
- `Tamra.exe` is built with `console=False`. That is enough: on 2026-10-22 a throwaway windowed
  PyInstaller exe was launched with stdin and stdout pipes (with and without `CREATE_NO_WINDOW`), and
  `sys.stdin` and `sys.stdout` were set and carried the data both ways. No console exe is needed.
  `scripts/exe_smoke.py` checks the real `Tamra.exe mcp` (§10).

## 10. Testing

- **pytest, core:**
  - `TokenGate`: the MCP token opens `/api/mcp/*` and nothing else, and the UI token does not open
    `/api/mcp/*`.
  - 403 while the switch is off, and the switch takes effect without a restart.
  - Validation: empty or long query, `k` out of range, too many names.
  - Each 409, 400 and 503 case.
  - The not-found cutoff returns no passages.
  - Stale projects are left out and named.
  - The payload has no folder path.
  - The answer service's "search" mode still behaves as before (existing tests).
- **pytest, app:** the discovery file is written after startup with the right port, replaced
  atomically, and removed at shutdown.
- **pytest, bridge:** both tools against `httpx.MockTransport`, and each row of the error table,
  called through the SDK's in-process client (`mcp.Client(server)`), which also checks the tool
  names and input schemas and that a raised error arrives as an `isError` result.
- **Vitest:** the switch saves `mcp_enabled`, "Copy setup" copies the JSON built from
  `mcp_command`, and the badge appears only while the switch is on and opens Settings > General.
- **`scripts/exe_smoke.py`:** a new step with the packaged exe. Turn the switch on, launch
  `Tamra.exe mcp` against the smoke's data folder as a stdio subprocess, call `list_projects` and
  `search` (a Thai question must return a passage from the expected file), turn the switch off, and
  check the tool error.

## 11. Docs

- README: "Use Tamra from Claude (MCP)", with the setup steps and the privacy note.
- The behavior spec gets a short section pointing here, and `CLAUDE.md`'s module list gets
  `mcp_server`.

## 12. Risks

- **Windowed exe and stdio** (§9): a probe showed it works. The exe smoke test keeps checking it.
- **Bundle size** grows by the SDK's dependencies. It is measured, and accepted unless it is far
  larger than expected.
- **The `mcp` SDK's 2.x API** is newer than earlier milestones' experience. The plan pins the minor
  version and checks the tool-error behavior (an exception in a tool becomes an `isError` result)
  in a test.
- **A prompt-injected AI** can only search. It cannot change data or settings, and the user can
  turn the switch off.
