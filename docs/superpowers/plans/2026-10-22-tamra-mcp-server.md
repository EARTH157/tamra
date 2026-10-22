# Tamra MCP server — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude Desktop, Claude Code and other MCP clients search the user's Tamra projects through
`Tamra.exe mcp` and get cited passages back, only while Tamra is open and only after the user turns
it on.

**Architecture:**
- The running core gets two read-only JSON routes, `GET /api/mcp/projects` and
  `POST /api/mcp/search`, behind a second per-launch token and the `mcp_enabled` setting.
- `tamra.app` writes that token and the port to `mcp.json` in the data folder.
- `Tamra.exe mcp` (`tamra.mcp_server`) is an MCP server over stdio built on the `mcp` SDK. Each tool
  call reads `mcp.json` and asks the core over HTTP.
- The search reuses the answer service's retrieval: the stale filter, the hybrid search, the
  not-found gate and the top-k cut. Nothing is copied.

**Tech stack:**
- Python 3.12 (uv): FastAPI, httpx, and `mcp` 2.x (`MCPServer`, `ToolError`, the in-process
  `Client` for tests);
- React and TypeScript (Vitest);
- PyInstaller.

**Spec:** `docs/superpowers/specs/2026-10-22-tamra-mcp-design.md` (read it first). The behavior spec
`docs/superpowers/specs/2026-10-02-tamra-design.md` stays the source of truth for everything else.

## Global constraints

- Windows 10 1809+ / 11 x64. Python 3.12 via uv. Never run `uv python install`, never recreate
  `.venv`.
- **One new Python dependency:** `mcp>=2.3,<3`, added in Task 5 with `uv add`.
  - The new packages it resolves to (checked 2026-10-22) are: `mcp`, `mcp-types`, `attrs`,
    `jsonschema`, `jsonschema-specifications`, `referencing`, `rpds-py`, `pyjwt` (all MIT),
    `python-multipart` (Apache-2.0), `pywin32` (PSF) and `sse-starlette` (BSD-3-Clause).
  - If `uv add` brings in any other new package, check that its license is Apache-2.0-compatible
    (no GPL or AGPL) before committing.
  - No new npm dependency.
- **Read-only:** everything reachable with the MCP token is read-only. No folder path, file id,
  chat, message or setting is sent through `/api/mcp/*`.
- **Never log query or passage text**, in the core or in the bridge. Counts only.
- **The bridge** (`tamra.mcp_server`) must never import `tamra.core`, `onnxruntime` or `webview`. It
  writes nothing to stdout except the protocol.
- **Exact error texts**, copied from spec §5 and §6:
  - `AI access is off. Turn it on in Tamra: Settings > General > AI apps.` (403)
  - `Tamra has no projects yet.` (409)
  - `Unknown project: <name>. Call list_projects for the names.` (400)
  - `The index of <names> was built with a different embedding model. Rebuild it in Tamra.` (409)
  - `No documents are indexed yet. Wait for indexing to finish in Tamra.` (409)
  - `The search model is not installed. Download it in Tamra: Settings > AI model.` (503)
  - Notices: `Not searched (rebuild needed): <names>` and
    `No passage matches this query closely enough.`
  - Bridge: `Tamra is not running. Open Tamra and try again.` and
    `Tamra did not answer. Try again.`
- **UI strings:** every new key gets an English and a Thai text.
- **User data:** `%LOCALAPPDATA%\Tamra\` holds `mcp.json` while Tamra runs. User document folders are
  never touched.
- **Before every commit:**
  - `uv run ruff format`, `uv run ruff check --fix`, `uv run ruff format --check`, `uv run pytest`;
  - if `ui/` is touched, also `npm --prefix ui test` and `npm --prefix ui run build`.
- Stage explicit paths only. Never commit `AGENTS.md` or `.claude/`. Never kill processes by image
  name.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work on a branch `mcp-server` made from the commit that holds this plan. Open a PR to `main` at
  the end. Never push tags or publish a release.

## Facts already checked (do not re-investigate)

- **Windowed exe and stdio:** a windowed (`console=False`) PyInstaller exe launched with stdin and
  stdout pipes, with or without `CREATE_NO_WINDOW`, gets a working `sys.stdin` and `sys.stdout`
  (probe, 2026-10-22). `Tamra.exe` stays the only exe, and `Tamra.exe mcp` is the command.
- **`mcp` 2.3.0 API:**
  - The server: `from mcp.server import MCPServer`, built as
    `MCPServer(name, instructions=..., version=..., log_level=...)`. `@server.tool(annotations=...)`
    registers a tool, and `server.run()` serves stdio.
  - Sync tool functions run in a worker thread (`anyio.to_thread`).
  - Errors: `from mcp.server.mcpserver.exceptions import ToolError`. Raising it returns
    `is_error=True` with the message in `content`. Any other exception becomes a generic
    "Error executing tool" result.
  - Annotations: `from mcp.types import ToolAnnotations`, with fields `read_only_hint`,
    `destructive_hint`, `idempotent_hint` and `open_world_hint`.
  - The client: `from mcp import Client, StdioServerParameters`. `Client(server_object)` connects
    in-process (for tests), and `Client(StdioServerParameters(command=..., args=[...], env={...}))`
    launches a subprocess.
  - Calls: `await client.list_tools()` returns `.tools`, each with `.name`, `.input_schema` and
    `.annotations`. `await client.call_tool(name, args)` returns a result with `.is_error`,
    `.content` (`[0].text`) and `.structured_content`. A tool annotated `-> dict[str, Any]` returns
    its dict unwrapped as `structured_content`.

## File structure

| File | Change | Responsibility |
|---|---|---|
| `src/tamra/answer.py` | modify | `StaleIndex`, `NothingIndexed`, `AnswerService.searchable()` and `.passages()`: retrieval shared by the chat and the MCP search |
| `src/tamra/settings.py` | modify | `mcp_enabled` setting |
| `src/tamra/server.py` | modify | `TokenGate` with the MCP token, `mcp_command` in the settings payload, `/api/mcp/*` routes |
| `src/tamra/core.py` | modify | `Core.search()`, `NoProjects`, `UnknownProject` |
| `src/tamra/discovery.py` | create | write, read and remove `mcp.json` (the format lives here only) |
| `src/tamra/app.py` | modify | make the MCP token, publish `mcp.json` while serving |
| `src/tamra/mcp_server.py` | create | `Tamra.exe mcp`: `MCPServer` with `list_projects` and `search` |
| `src/tamra/__main__.py` | modify | the `mcp` subcommand |
| `ui/src/types.ts`, `ui/src/settings.ts` | modify | `mcp_enabled`, `mcp_command` |
| `ui/src/SettingsGeneral.tsx` | modify | the "AI apps (MCP)" section and its Copy setup button |
| `ui/src/App.tsx`, `ui/src/styles.css` | modify | the sidebar badge |
| `ui/src/strings/en.ts`, `ui/src/strings/th.ts` | modify | new strings |
| `packaging/tamra.spec` | modify | hidden import `tamra.mcp_server` |
| `scripts/exe_smoke.py` | modify | MCP step against the packaged exe |
| `README.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-10-02-tamra-design.md` | modify | docs |

---

### Task 1: Retrieval that the chat and AI apps share

Split the retrieval steps out of `AnswerService._ask`, so the MCP search (Task 3) calls the same
code. Chat behavior must not change. The existing `tests/test_answer.py` must pass unchanged.

**Files:**
- Modify: `src/tamra/answer.py` (imports; new classes before `class AnswerService`; new methods after
  `run_if_idle`; `_ask` lines 285-336)
- Test: `tests/test_answer.py`

**Interfaces:**
- Produces:
  - `class StaleIndex(Exception)` with `.names: list[str]`;
  - `class NothingIndexed(Exception)` with `.skipped: list[str]`;
  - `AnswerService.searchable(collections: Sequence[Collection]) -> tuple[list[Collection], list[str]]`;
  - `AnswerService.passages(collection_ids: Sequence[int], vector: np.ndarray, query: str, k: int) -> list[SourceRecord]`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_answer.py`, add `NothingIndexed` and `StaleIndex` to the `from tamra.answer import (...)`
list. Then append after the `two` fixture's tests (end of the "Answers over a chat's scope" block):

```python
# Retrieval without a chat (shared with the MCP search)


def test_searchable_leaves_out_stale_projects_and_names_them(two):
    store, make, projects = two
    store.reset_index(projects["B"].id, "other-model")
    searched, skipped = make(FakeLLM()).searchable(store.list_collections())
    assert [c.name for c in searched] == ["A"]
    assert skipped == ["B"]


def test_searchable_raises_stale_index_when_every_project_is_stale(two):
    store, make, projects = two
    store.reset_index(projects["A"].id, "other-model")
    with pytest.raises(StaleIndex) as raised:
        make(FakeLLM()).searchable([store.get_collection(projects["A"].id)])
    assert raised.value.names == ["A"]


def test_searchable_raises_nothing_indexed_with_the_stale_names(two):
    store, make, projects = two
    store.reset_index(projects["A"].id, "other-model")
    store.reset_index(projects["B"].id, "m")  # pending again
    with pytest.raises(NothingIndexed) as raised:
        make(FakeLLM()).searchable(store.list_collections())
    assert raised.value.skipped == ["A"]
    with pytest.raises(NothingIndexed):
        make(FakeLLM()).searchable([])


def test_passages_are_numbered_best_first_and_cut_at_k(two):
    store, make, projects = two
    vector = FakeEmbedder().embed(["parking baht"])[0]
    found = make(FakeLLM()).passages(
        [projects["A"].id, projects["B"].id], vector, "parking baht", 1
    )
    assert [(s.n, s.rel_path, s.text) for s in found] == [(1, "b.md", "Parking costs fifty baht")]


def test_passages_are_empty_below_the_not_found_gate(two):
    store, make, projects = two
    query = "ใครเป็นผู้จัดการฝ่ายขาย"
    vector = FakeEmbedder().embed([query])[0]
    ids = [projects["A"].id, projects["B"].id]
    assert make(FakeLLM()).passages(ids, vector, query, 4) == []
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/test_answer.py -k "searchable or passages" -v`
Expected: collection error, `ImportError: cannot import name 'NothingIndexed'`.

- [ ] **Step 3: Implement**

In `src/tamra/answer.py`, change the store import to
`from tamra.store import Collection, SourceRecord, Store`.

Add before `class AnswerService:`:

```python
class StaleIndex(Exception):
    """Every project to search was indexed with another embedding model (spec §5)."""

    def __init__(self, names: list[str]):
        super().__init__(", ".join(names))
        self.names = names


class NothingIndexed(Exception):
    """None of the projects to search has an indexed file yet. `skipped` names the stale
    projects that were left out, so the caller can say why."""

    def __init__(self, skipped: list[str]):
        super().__init__("no indexed documents")
        self.skipped = skipped
```

Add these methods to `AnswerService`, after `run_if_idle`:

```python
    def searchable(self, collections: Sequence[Collection]) -> tuple[list[Collection], list[str]]:
        """The projects to search, and the names of the stale ones left out: a project indexed
        with another embedding model is never searched (spec §5, §6).

        Raises StaleIndex when every project is stale, and NothingIndexed when none of the others
        has an indexed file (also when there is no project at all).
        """
        searched = [c for c in collections if c.embedding_model_id == self._model_id]
        skipped = [c.name for c in collections if c not in searched]
        if collections and not searched:
            raise StaleIndex(skipped)
        if not any(self._store.status_counts(c.id)["indexed"] for c in searched):
            raise NothingIndexed(skipped)
        return searched, skipped

    def passages(
        self, collection_ids: Sequence[int], vector: np.ndarray, query: str, k: int
    ) -> list[SourceRecord]:
        """The best k passages of these projects for the query, numbered from 1, best first.
        None when even the best is not close enough: the "not found" gate of spec §6."""
        hits = hybrid_search(self._store, collection_ids, vector, fts_query(query))
        if best_similarity(hits) < self._settings.min_similarity:
            return []
        chunks = self._store.get_chunks([hit.chunk_id for hit in hits[:k]])
        return [
            SourceRecord(n, c.id, c.file_id, c.rel_path, c.text, c.location, c.file_hash)
            for n, c in enumerate(chunks, start=1)
        ]
```

In `_ask`, replace everything from `in_scope = [c for c in collections if c.id in wanted]`
through the `sources = [...]` list (lines 285-336). Keep the `wanted = ...` line and its comment
above it. The new block:

```python
        try:
            searched, skipped = self.searchable([c for c in collections if c.id in wanted])
        except StaleIndex as e:
            yield {
                "type": "error",
                "message": f"The index of {', '.join(e.names)} was built with a different "
                "embedding model. Rebuild the index.",
            }
            return
        except NothingIndexed as e:
            if e.skipped:  # say why those projects were left out, not only that nothing is ready
                yield {"type": "notice", "code": "stale_projects", "names": e.skipped}
            yield {
                "type": "error",
                "message": "No documents are indexed yet. Wait for indexing to finish, "
                "then ask again.",
            }
            return
        route = self._route()
        history = self._store.recent_exchanges(chat_id, HISTORY_TURNS)
        language = detect_language(question)
        query = query_text(question, history[-1][0] if history else None)
        try:
            vector = self._embed_query(query)
        except Exception as e:
            # Nothing is saved yet: a question that cannot be answered stays out of the chat.
            event = {"type": "error", "message": f"Embedding model unavailable: {e}"}
            if isinstance(e, EmbedderUnavailable):  # missing files: Settings can download them
                event["reason"] = "embedding_missing"
            yield event
            return
        self._store.add_user_message(chat_id, question)
        budget = self._settings.top_k if route.name == "local" else self._settings.api_top_k
        sources = self.passages([c.id for c in searched], vector, query, budget)
        if skipped:
            yield {"type": "notice", "code": "stale_projects", "names": skipped}
        if not sources:
            text = NOT_FOUND[language]
            message_id = self._store.add_assistant_message(
                chat_id, text, provider=None, model=None, sources=[]
            )
            yield {"type": "sources", "sources": []}
            yield {"type": "token", "text": text}
            yield {"type": "done", "message_id": message_id}
            return
```

The next line stays as it is:
`yield {"type": "sources", "sources": source_payloads(self._store, sources)}`.

- [ ] **Step 4: Run the tests to check they pass**

Run: `uv run pytest tests/test_answer.py tests/test_core.py tests/test_server_projects.py -v`
Expected: all PASS. The old tests are the proof that chat behavior did not change.

- [ ] **Step 5: Commit**

```bash
git add src/tamra/answer.py tests/test_answer.py
git commit -m "refactor: share the retrieval steps of answers (searchable, passages)"
```

---

### Task 2: The MCP token and the access switch

**Files:**
- Modify: `src/tamra/settings.py` (`Settings`, `_is_valid`)
- Modify: `src/tamra/server.py` (`TokenGate`, `_is_mcp`, `create_app`, `_settings_payload`,
  `_mcp_command`)
- Test: `tests/test_settings.py`, `tests/test_server.py`, `tests/test_server_settings.py`

**Interfaces:**
- Produces:
  - `Settings.mcp_enabled: bool = False`;
  - `create_app(..., mcp_token: str | None = None)`;
  - the settings payload key `mcp_command: list[str]` (read-only).

- [ ] **Step 1: Write the failing tests**

`tests/test_settings.py`:
- in `test_defaults_come_back_when_nothing_is_stored`, add `assert settings.mcp_enabled is False`;
- in `test_every_field_round_trips`, add `"mcp_enabled": True,` to `changes`;
- append:

```python
def test_mcp_enabled_must_be_a_bool(store):
    with pytest.raises(ValueError, match="mcp_enabled"):
        save_settings(store, {"mcp_enabled": "yes"})
    assert load_settings(store).mcp_enabled is False
```

`tests/test_server.py`, append at the end:

```python
def test_the_mcp_token_opens_only_the_mcp_routes():
    client = TestClient(
        create_app("secret", None, mcp_token="mcp-secret"), base_url="http://127.0.0.1"
    )
    mcp = {"X-Tamra-Token": "mcp-secret"}
    assert client.get("/api/health", headers=mcp).status_code == 401
    assert client.get("/api/mcp/projects", headers=AUTH).status_code == 401
    # Past the gate: without a core there is no MCP route, so 404 and not 401.
    assert client.get("/api/mcp/projects", headers=mcp).status_code == 404
    assert client.get("/api/mcp", headers=mcp).status_code == 404


def test_without_an_mcp_token_the_mcp_routes_stay_closed():
    client = make_client()
    assert client.get("/api/mcp/projects", headers=AUTH).status_code == 401
    assert client.get("/api/mcp/projects", headers={"X-Tamra-Token": ""}).status_code == 401
```

`tests/test_server_settings.py`:
- add `import sys` to the imports;
- in `test_get_settings_has_every_setting_and_the_key_state`, add these two entries to the expected
  dict (after `"ask_before_delete": True,` and after `"data_dir": ...` respectively):

```python
        "mcp_enabled": False,
```

```python
        "mcp_command": [sys.executable, "-m", "tamra", "mcp"],
```

- append:

```python
def test_mcp_command_is_this_exe_when_packaged(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    body = client.get("/api/settings", headers=AUTH).json()
    assert body["mcp_command"] == [sys.executable, "mcp"]


def test_put_settings_refuses_the_read_only_mcp_command(env):
    client, _, _ = env
    response = client.put("/api/settings", headers=AUTH, json={"mcp_command": ["evil.exe"]})
    assert response.status_code == 400


def test_mcp_enabled_is_saved_through_the_settings_route(env):
    client, core, _ = env
    response = client.put("/api/settings", headers=AUTH, json={"mcp_enabled": True})
    assert response.status_code == 200
    assert response.json()["mcp_enabled"] is True
    assert core.settings.mcp_enabled is True
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/test_settings.py tests/test_server.py tests/test_server_settings.py -v`
Expected: FAIL. `Settings` has no `mcp_enabled`; `create_app()` got an unexpected keyword argument
`mcp_token`; the payload lacks `mcp_command`.

- [ ] **Step 3: Implement**

`src/tamra/settings.py`: add the field after `ask_before_delete`:

```python
    ask_before_delete: bool = True
    # AI apps (MCP) may search every project while Tamra is open; checked on every MCP request.
    mcp_enabled: bool = False
```

In `_is_valid`, replace the `ask_before_delete` branch with:

```python
    if field in ("ask_before_delete", "mcp_enabled"):
        return isinstance(value, bool)
```

`src/tamra/server.py`:
- add `import sys` to the imports;
- replace `class TokenGate` and add `_is_mcp` after `_is_api`:

```python
class TokenGate:
    """Reject /api requests, HTTP and WebSocket, that lack the right per-launch token: the MCP
    token for /api/mcp/* (AI apps), the UI token for every other /api path. Without an MCP
    token the /api/mcp routes stay closed."""

    def __init__(self, app: ASGIApp, token: str, mcp_token: str | None = None):
        self.app = app
        self.token = token.encode()
        self.mcp_token = mcp_token.encode() if mcp_token else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and _is_api(scope["path"]):
            expected = self.mcp_token if _is_mcp(scope["path"]) else self.token
            supplied = dict(scope["headers"]).get(b"x-tamra-token", b"")
            if expected is None or not secrets.compare_digest(supplied, expected):
                if scope["type"] == "http":
                    response = JSONResponse({"detail": "invalid token"}, status_code=401)
                    await response(scope, receive, send)
                else:
                    await receive()  # websocket.connect
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)
```

```python
def _is_mcp(path: str) -> bool:
    path = "/" + path.lstrip("/")
    return path == "/api/mcp" or path.startswith("/api/mcp/")
```

- `create_app`: add the parameter `mcp_token: str | None = None` after `open_external`, and change
  the gate line to `app.add_middleware(TokenGate, token=token, mcp_token=mcp_token)`.
- add before `_settings_payload`:

```python
def _mcp_command() -> list[str]:
    """How an AI app starts Tamra's MCP server: this exe, or this Python when run from source."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "mcp"]
    return [sys.executable, "-m", "tamra", "mcp"]
```

- in `_settings_payload`, add after `"data_dir": ...`:

```python
        "mcp_command": _mcp_command(),  # read-only: for the "Copy setup" button
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `uv run pytest tests/test_settings.py tests/test_server.py tests/test_server_settings.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tamra/settings.py src/tamra/server.py tests/test_settings.py tests/test_server.py tests/test_server_settings.py
git commit -m "feat: MCP token for /api/mcp and the mcp_enabled setting"
```

---

### Task 3: Search routes for AI apps

**Files:**
- Modify: `src/tamra/core.py` (imports, new exceptions, `_nocase`, `Core.search`)
- Modify: `src/tamra/server.py` (imports, `McpSearchBody`, `MCP_OFF_DETAIL`, `_add_mcp_routes`,
  `create_app`)
- Create: `tests/test_server_mcp.py`

**Interfaces:**
- Consumes (Task 1): `AnswerService.searchable`, `AnswerService.passages`, `StaleIndex` and
  `NothingIndexed` from `tamra.answer`.
- Consumes (Task 2): `create_app(..., mcp_token=...)` and `Settings.mcp_enabled`.
- Produces:
  - `Core.search(query: str, names: list[str] | None, k: int) -> tuple[list[dict], list[str]]`;
  - `class NoProjects(LookupError)` and `class UnknownProject(LookupError)` (with `.name`) in
    `tamra.core`;
  - routes:
    - `GET /api/mcp/projects` → `{"projects": [{"name", "indexed_files", "stale"}]}`;
    - `POST /api/mcp/search` with `{query, projects?, k?}` →
      `{"passages": [{"project", "file", "location", "text"}], "notices": [str]}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_server_mcp.py`:

```python
"""The routes `Tamra.exe mcp` calls (docs/superpowers/specs/2026-10-22-tamra-mcp-design.md §5)."""

import logging
import time

import pytest
from fakes import FakeEmbedder, FakeLocalLLM, fake_spans
from fastapi.testclient import TestClient

from tamra.answer import AnswerSettings
from tamra.core import Core
from tamra.embedder import EmbedderUnavailable
from tamra.server import create_app

UI = {"X-Tamra-Token": "secret"}
MCP = {"X-Tamra-Token": "mcp-secret"}
OFF = "AI access is off. Turn it on in Tamra: Settings > General > AI apps."
LEASE = "The lease term is three years."
PARKING = "Parking costs fifty baht."


@pytest.fixture
def api(tmp_path):
    core = Core(
        tmp_path / "data",
        tmp_path / "models",
        tmp_path / "llama.exe",
        embedder_factory=FakeEmbedder,
        token_spans_factory=lambda: fake_spans,
        llm=FakeLocalLLM(),
        answer_settings=AnswerSettings(min_similarity=0.3),
        debounce=0.2,
    )
    core.start()
    client = TestClient(
        create_app("secret", None, core, mcp_token="mcp-secret"), base_url="http://127.0.0.1"
    )
    yield core, client, tmp_path
    core.shutdown()


def wait_for(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def indexed(client, project_id):
    projects = client.get("/api/projects", headers=UI).json()["projects"]
    return next(p["index"]["counts"]["indexed"] for p in projects if p["id"] == project_id)


def add_project(client, tmp_path, name, **files):
    """A project over a new folder of .md files, once every file is indexed."""
    folder = tmp_path / name
    folder.mkdir()
    for file_name, text in files.items():
        (folder / f"{file_name}.md").write_text(text, encoding="utf-8")
    response = client.post(
        "/api/projects", headers=UI, json={"name": name, "folder_path": str(folder)}
    )
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]
    wait_for(lambda: indexed(client, project_id) == len(files))
    return project_id


def turn_on(client):
    assert client.put("/api/settings", headers=UI, json={"mcp_enabled": True}).status_code == 200


def search(client, **body):
    return client.post("/api/mcp/search", headers=MCP, json=body)


def test_both_routes_are_refused_while_ai_access_is_off(api):
    _, client, _ = api
    for response in (client.get("/api/mcp/projects", headers=MCP), search(client, query="rent")):
        assert response.status_code == 403
        assert response.json() == {"detail": OFF}


def test_the_switch_applies_at_once(api):
    _, client, _ = api
    turn_on(client)
    assert client.get("/api/mcp/projects", headers=MCP).status_code == 200
    client.put("/api/settings", headers=UI, json={"mcp_enabled": False})
    assert client.get("/api/mcp/projects", headers=MCP).status_code == 403


def test_projects_have_names_counts_and_staleness_but_no_folders(api):
    core, client, tmp_path = api
    turn_on(client)
    leases = add_project(client, tmp_path, "Leases", lease=LEASE)
    add_project(client, tmp_path, "Parking", rules=PARKING)
    core.store.reset_index(leases, "old-model")
    assert client.get("/api/mcp/projects", headers=MCP).json() == {
        "projects": [
            {"name": "Leases", "indexed_files": 0, "stale": True},
            {"name": "Parking", "indexed_files": 1, "stale": False},
        ]
    }


def test_search_returns_project_file_location_and_text(api):
    _, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Leases", lease=LEASE)
    add_project(client, tmp_path, "Parking", rules=PARKING)
    response = search(client, query="lease term", k=1)
    assert response.status_code == 200
    assert response.json() == {
        "passages": [
            {"project": "Leases", "file": "lease.md", "location": "line 1", "text": LEASE}
        ],
        "notices": [],
    }


def test_search_can_be_limited_to_named_projects_ignoring_case(api):
    _, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Leases", lease=LEASE)
    add_project(client, tmp_path, "Parking", rules=PARKING)
    body = search(client, query="lease term parking baht", projects=["parking"]).json()
    assert [p["project"] for p in body["passages"]] == ["Parking"]


def test_an_unknown_project_name_is_named_back(api):
    _, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Leases", lease=LEASE)
    response = search(client, query="lease", projects=["Nope"])
    assert response.status_code == 400
    assert response.json() == {"detail": "Unknown project: Nope. Call list_projects for the names."}


def test_no_project_at_all(api):
    _, client, _ = api
    turn_on(client)
    response = search(client, query="lease")
    assert (response.status_code, response.json()) == (409, {"detail": "Tamra has no projects yet."})


def test_a_stale_project_is_left_out_and_named(api):
    core, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Leases", lease=LEASE)
    parking = add_project(client, tmp_path, "Parking", rules=PARKING)
    core.store.reset_index(parking, "old-model")
    body = search(client, query="lease term").json()
    assert body["notices"] == ["Not searched (rebuild needed): Parking"]
    assert [p["project"] for p in body["passages"]] == ["Leases"]


def test_only_stale_projects_ask_for_a_rebuild(api):
    core, client, tmp_path = api
    turn_on(client)
    leases = add_project(client, tmp_path, "Leases", lease=LEASE)
    core.store.reset_index(leases, "old-model")
    response = search(client, query="lease term")
    assert response.status_code == 409
    assert response.json() == {
        "detail": "The index of Leases was built with a different embedding model. "
        "Rebuild it in Tamra."
    }


def test_nothing_indexed_yet(api):
    _, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Empty")
    response = search(client, query="lease term")
    assert response.status_code == 409
    assert response.json() == {
        "detail": "No documents are indexed yet. Wait for indexing to finish in Tamra."
    }


def test_nothing_close_enough_sends_no_passage(api):
    _, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Leases", lease=LEASE)
    assert search(client, query="ใครเป็นผู้จัดการฝ่ายขาย").json() == {
        "passages": [],
        "notices": ["No passage matches this query closely enough."],
    }


def test_a_missing_search_model_is_a_503(api):
    core, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Leases", lease=LEASE)

    def missing(text):
        raise EmbedderUnavailable("bge-m3 is not installed")

    core._embed_query = missing
    response = search(client, query="lease term")
    assert response.status_code == 503
    assert response.json() == {
        "detail": "The search model is not installed. Download it in Tamra: Settings > AI model."
    }


@pytest.mark.parametrize(
    "body",
    [
        {"query": "   "},
        {"query": "x" * 4001},
        {"query": "rent", "k": 0},
        {"query": "rent", "k": 21},
        {"query": "rent", "projects": []},
        {"query": "rent", "projects": ["p"] * 51},
        {"query": "rent", "projects": ["x" * 81]},
    ],
)
def test_bad_requests_are_refused(api, body):
    _, client, _ = api
    turn_on(client)
    assert search(client, **body).status_code == 422


def test_the_log_has_counts_but_never_the_query(api, caplog):
    _, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Leases", lease=LEASE)
    with caplog.at_level(logging.INFO, logger="tamra.core"):
        search(client, query="lease term private-words")
    assert "mcp search" in caplog.text
    assert "private-words" not in caplog.text
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/test_server_mcp.py -v`
Expected: FAIL. The routes do not exist: 404 instead of 403 or 200.

- [ ] **Step 3: Implement `Core.search`**

`src/tamra/core.py`:
- change the answer import to
  `from tamra.answer import AnswerService, AnswerSettings, Route, source_payloads`;
- add after `class FolderNotAllowed`:

```python
class NoProjects(LookupError):
    """There is no project to search."""


class UnknownProject(LookupError):
    """A project name that matches no project."""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


def _nocase(name: str) -> str:
    """The name as SQLite's NOCASE compares it, folding only A-Z: the store keeps project names
    unique that way, so at most one project matches."""
    return "".join(c.lower() if c.isascii() else c for c in name)
```

- add to `class Core`, after `delete_project` (inside the `# --- projects ---` part):

```python
    def search(self, query: str, names: list[str] | None, k: int) -> tuple[list[dict], list[str]]:
        """The best k passages for an AI app (MCP spec §5), as `source_payloads` builds them,
        and the names of the stale projects left out. `names` limits the search to those
        projects (matched ignoring case); None searches every project. Nothing is saved.

        Raises NoProjects, UnknownProject, StaleIndex, NothingIndexed or EmbedderUnavailable.
        """
        collections = self.store.list_collections()
        if not collections:
            raise NoProjects("no projects")
        if names is not None:
            by_name = {_nocase(c.name): c for c in collections}
            chosen: list[Collection] = []
            for name in names:
                collection = by_name.get(_nocase(name))
                if collection is None:
                    raise UnknownProject(name)
                if collection not in chosen:
                    chosen.append(collection)
            collections = chosen
        searched, skipped = self.answers.searchable(collections)
        sources = self.answers.passages(
            [c.id for c in searched], self._embed_query(query), query, k
        )
        # Counts only: the query and the passages never go to the log.
        log.info("mcp search: %d project(s), k=%d, %d passage(s)", len(searched), k, len(sources))
        return source_payloads(self.store, sources), skipped
```

- [ ] **Step 4: Implement the routes**

`src/tamra/server.py`:
- add `Depends` to the `from fastapi import ...` line;
- change the answer import to
  `from tamra.answer import NothingIndexed, StaleIndex, location_label, source_payloads`;
- add `NoProjects` and `UnknownProject` to the `from tamra.core import (...)` list;
- add after `class ImportBody`:

```python
ProjectName = Annotated[str, StringConstraints(min_length=1, max_length=MAX_PROJECT_NAME)]


class McpSearchBody(BaseModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    # Left out: every project. Given: 1 to 50 names.
    projects: Annotated[list[ProjectName], Field(min_length=1, max_length=50)] | None = None
    k: int = Field(default=8, ge=1, le=20)


MCP_OFF_DETAIL = "AI access is off. Turn it on in Tamra: Settings > General > AI apps."
```

- in `create_app`, add `_add_mcp_routes(app, core)` after `_add_model_routes(app, core)`;
- add at the end of the file:

```python
def _add_mcp_routes(app: FastAPI, core: Core) -> None:
    """The read-only routes `Tamra.exe mcp` calls for AI apps (MCP spec §5). The TokenGate lets
    only the MCP token in; the switch is read on every request, so turning it off applies at
    once. Every detail is fixed text: no path or query goes back."""

    def require_access() -> None:
        if not core.settings.mcp_enabled:
            raise HTTPException(status_code=403, detail=MCP_OFF_DETAIL)

    @app.get("/api/mcp/projects", dependencies=[Depends(require_access)])
    def mcp_projects() -> dict:
        return {
            "projects": [
                {"name": v["name"], "indexed_files": v["counts"]["indexed"], "stale": v["stale"]}
                for v in core.projects()
            ]
        }

    @app.post("/api/mcp/search", dependencies=[Depends(require_access)])
    def mcp_search(body: McpSearchBody) -> dict:
        try:
            sources, skipped = core.search(body.query, body.projects, body.k)
        except NoProjects as e:
            raise HTTPException(status_code=409, detail="Tamra has no projects yet.") from e
        except UnknownProject as e:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown project: {e.name}. Call list_projects for the names.",
            ) from e
        except StaleIndex as e:
            raise HTTPException(
                status_code=409,
                detail=f"The index of {', '.join(e.names)} was built with a different embedding "
                "model. Rebuild it in Tamra.",
            ) from e
        except NothingIndexed as e:
            raise HTTPException(
                status_code=409,
                detail="No documents are indexed yet. Wait for indexing to finish in Tamra.",
            ) from e
        except EmbedderUnavailable as e:
            raise HTTPException(
                status_code=503,
                detail="The search model is not installed. Download it in Tamra: "
                "Settings > AI model.",
            ) from e
        notices = []
        if skipped:
            notices.append(f"Not searched (rebuild needed): {', '.join(skipped)}")
        if not sources:
            notices.append("No passage matches this query closely enough.")
        return {
            "passages": [
                {"project": s["project"], "file": s["file"], "location": s["label"], "text": s["text"]}
                for s in sources
            ],
            "notices": notices,
        }
```

- [ ] **Step 5: Run the tests to check they pass**

Run: `uv run pytest tests/test_server_mcp.py tests/test_server.py tests/test_answer.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tamra/core.py src/tamra/server.py tests/test_server_mcp.py
git commit -m "feat: read-only /api/mcp routes: list projects and search passages"
```

---

### Task 4: The discovery file

**Files:**
- Create: `src/tamra/discovery.py`
- Modify: `src/tamra/app.py` (imports, `_serve`, new `_publish_mcp`)
- Test: create `tests/test_discovery.py`; modify `tests/test_app.py`

**Interfaces:**
- Consumes (Task 2): `create_app(..., mcp_token=...)`.
- Produces:
  - `FILE_NAME = "mcp.json"`;
  - `write_discovery(folder: Path, port: int, token: str) -> None`;
  - `read_discovery(folder: Path) -> tuple[int, str] | None`;
  - `remove_discovery(folder: Path) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_discovery.py`:

```python
"""The MCP discovery file (docs/superpowers/specs/2026-10-22-tamra-mcp-design.md §4)."""

import pytest

from tamra.discovery import FILE_NAME, read_discovery, remove_discovery, write_discovery


def test_written_values_read_back_and_no_temporary_file_is_left(tmp_path):
    write_discovery(tmp_path, 5123, "tok")
    assert read_discovery(tmp_path) == (5123, "tok")
    assert [p.name for p in tmp_path.iterdir()] == [FILE_NAME]


def test_a_new_launch_replaces_the_old_values(tmp_path):
    write_discovery(tmp_path, 1111, "old")
    write_discovery(tmp_path, 2222, "new")
    assert read_discovery(tmp_path) == (2222, "new")


@pytest.mark.parametrize(
    "content",
    [
        "",
        "{",
        "[]",
        '{"port": 5123}',
        '{"port": "5123", "token": "t"}',
        '{"port": true, "token": "t"}',
        '{"port": 0, "token": "t"}',
        '{"port": 70000, "token": "t"}',
        '{"port": 5123, "token": ""}',
        '{"port": 5123, "token": 7}',
    ],
)
def test_a_missing_or_damaged_file_reads_as_none(tmp_path, content):
    assert read_discovery(tmp_path) is None
    (tmp_path / FILE_NAME).write_text(content, encoding="utf-8")
    assert read_discovery(tmp_path) is None


def test_remove_is_quiet_when_there_is_nothing_to_remove(tmp_path):
    remove_discovery(tmp_path)
    write_discovery(tmp_path, 5123, "tok")
    remove_discovery(tmp_path)
    assert read_discovery(tmp_path) is None
```

`tests/test_app.py`:
- add `from tamra.discovery import read_discovery` to the imports;
- append:

```python
class FakeCore:
    def start(self):
        pass

    def shutdown(self):
        pass


def test_serve_publishes_the_mcp_port_and_token_only_while_it_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path))
    port = free_port()
    monkeypatch.setattr(app, "DEV_PORT", port)
    monkeypatch.setattr(app, "build_core", FakeCore)
    real_create_app = app.create_app
    monkeypatch.setattr(
        app,
        "create_app",
        lambda token, ui_dir, core, **options: real_create_app(
            token, ui_dir, mcp_token=options["mcp_token"]
        ),
    )
    seen = {}

    def while_running(port_, token, thread):
        seen["published"] = read_discovery(tmp_path)
        seen["ui_token"] = token

    monkeypatch.setattr(app, "_wait_in_dev_mode", while_running)
    try:
        app._serve(True, "dev-token")
    finally:
        shutdown_logging()
    published_port, mcp_token = seen["published"]
    assert published_port == port
    assert len(mcp_token) >= 32
    assert mcp_token != seen["ui_token"]
    assert read_discovery(tmp_path) is None  # removed at shutdown
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/test_discovery.py tests/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tamra.discovery'`.

- [ ] **Step 3: Implement**

Create `src/tamra/discovery.py`:

```python
"""The MCP discovery file: how `Tamra.exe mcp` finds the running app's port and MCP token
(docs/superpowers/specs/2026-10-22-tamra-mcp-design.md §4).

It lives in the data folder, which only the user can read. The token in it opens only the
read-only /api/mcp routes, and only while the user has turned AI access on.
"""

import json
import os
from contextlib import suppress
from pathlib import Path

FILE_NAME = "mcp.json"


def write_discovery(folder: Path, port: int, token: str) -> None:
    """Write the file atomically: a reader sees the old content or the new, never half."""
    path = folder / FILE_NAME
    temporary = path.with_name(FILE_NAME + ".tmp")
    temporary.write_text(json.dumps({"port": port, "token": token}), encoding="utf-8")
    os.replace(temporary, path)


def read_discovery(folder: Path) -> tuple[int, str] | None:
    """The port and token, or None when the file is missing or damaged."""
    try:
        data = json.loads((folder / FILE_NAME).read_text(encoding="utf-8"))
        port, token = data["port"], data["token"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        return None
    if not isinstance(token, str) or not token:
        return None
    return port, token


def remove_discovery(folder: Path) -> None:
    """Best effort: a file left behind is replaced by the next launch."""
    with suppress(OSError):
        (folder / FILE_NAME).unlink(missing_ok=True)
```

`src/tamra/app.py`:
- add `from tamra.discovery import remove_discovery, write_discovery` to the imports;
- add after `open_external`:

```python
def _publish_mcp(port: int, token: str) -> None:
    """Tell `Tamra.exe mcp` where the API is. Without the file AI apps only see "not running",
    so a failure is logged and Tamra carries on."""
    try:
        write_discovery(data_dir(), port, token)
    except OSError as e:
        log.warning("cannot write the MCP discovery file: %s", e)
```

- in `_serve`:
  - after the `port, token = ...` line, add `mcp_token = secrets.token_urlsafe(32)`;
  - pass `mcp_token=mcp_token,` to `create_app(...)` after `open_external=...`;
  - in the inner `try`, call `_publish_mcp(port, mcp_token)` right after
    `_wait_until_up(port, token, thread)`;
  - in the inner `finally`, call `remove_discovery(data_dir())` before
    `server.should_exit = True`.

The inner block then reads:

```python
        try:
            _wait_until_up(port, token, thread)
            _publish_mcp(port, mcp_token)
            if picker is None:
                _wait_in_dev_mode(port, token, thread)
            else:
                _show_window(port, token, picker, file_picker)
        finally:
            remove_discovery(data_dir())
            server.should_exit = True
            thread.join(timeout=5)
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `uv run pytest tests/test_discovery.py tests/test_app.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tamra/discovery.py src/tamra/app.py tests/test_discovery.py tests/test_app.py
git commit -m "feat: publish the MCP port and token in mcp.json while Tamra runs"
```

---

### Task 5: `Tamra.exe mcp`, the stdio bridge

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (through `uv add`)
- Create: `src/tamra/mcp_server.py`
- Modify: `src/tamra/__main__.py`
- Create: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes (Task 4): `read_discovery(folder) -> tuple[int, str] | None`.
- Consumes (Task 3): the `/api/mcp/projects` and `/api/mcp/search` contracts.
- Produces:
  - `tamra.mcp_server.server: MCPServer` with the tools `list_projects()` and
    `search(query, projects=None, k=8)`;
  - `tamra.mcp_server.main() -> int`;
  - the constants `NOT_RUNNING` and `NO_ANSWER`;
  - the test seam `tamra.mcp_server._transport`.

- [ ] **Step 1: Record the bundle size before the new dependency**

Run (PowerShell; takes a few minutes):

```powershell
./scripts/build.ps1
"{0:N1} MB" -f ((Get-ChildItem dist\Tamra -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
```

Write the number down for Task 7. Nothing is committed in this step.

- [ ] **Step 2: Add the dependency**

Run: `uv add "mcp>=2.3,<3"`, then `git diff uv.lock | Select-String '^\+name = '`.
Expected new names: exactly the list in Global constraints. Stop and check the license of anything
else.

- [ ] **Step 3: Write the failing tests**

Create `tests/test_mcp_server.py`:

```python
"""`Tamra.exe mcp` (docs/superpowers/specs/2026-10-22-tamra-mcp-design.md §6), through the
SDK's in-process client."""

import json
import subprocess
import sys

import anyio
import httpx
import pytest
from mcp import Client

from tamra import mcp_server
from tamra.discovery import write_discovery

PROJECTS = {"projects": [{"name": "Contracts", "indexed_files": 3, "stale": False}]}
FOUND = {
    "passages": [
        {"project": "Contracts", "file": "lease.pdf", "location": "p. 2", "text": "Rent is 10,000."}
    ],
    "notices": [],
}


@pytest.fixture
def core(tmp_path, monkeypatch):
    """A fake core behind the discovery file. `replies` maps a path to a function of the
    request; `seen` records every request."""
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path))
    write_discovery(tmp_path, 5123, "mcp-token")
    seen: list[httpx.Request] = []
    replies = {
        "/api/mcp/projects": lambda request: httpx.Response(200, json=PROJECTS),
        "/api/mcp/search": lambda request: httpx.Response(200, json=FOUND),
    }

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return replies[request.url.path](request)

    monkeypatch.setattr(mcp_server, "_transport", httpx.MockTransport(handle))
    return seen, replies


def call(name, arguments=None):
    async def go():
        async with Client(mcp_server.server) as client:
            return await client.call_tool(name, arguments or {})

    return anyio.run(go)


def error_text(result) -> str:
    assert result.is_error
    return result.content[0].text


def test_the_server_offers_two_read_only_tools():
    async def go():
        async with Client(mcp_server.server) as client:
            return (await client.list_tools()).tools

    tools = {tool.name: tool for tool in anyio.run(go)}
    assert sorted(tools) == ["list_projects", "search"]
    assert all(tool.annotations.read_only_hint for tool in tools.values())
    schema = tools["search"].input_schema
    assert schema["required"] == ["query"]
    assert (schema["properties"]["k"]["minimum"], schema["properties"]["k"]["maximum"]) == (1, 20)


def test_list_projects_returns_the_core_s_list_and_sends_the_mcp_token(core):
    seen, _ = core
    result = call("list_projects")
    assert not result.is_error
    assert result.structured_content == PROJECTS
    assert seen[0].url == httpx.URL("http://127.0.0.1:5123/api/mcp/projects")
    assert seen[0].headers["X-Tamra-Token"] == "mcp-token"


def test_search_sends_the_query_projects_and_k(core):
    seen, _ = core
    result = call("search", {"query": "ค่าเช่าเท่าไร", "projects": ["Contracts"], "k": 3})
    assert result.structured_content == FOUND
    assert json.loads(seen[0].content) == {
        "query": "ค่าเช่าเท่าไร",
        "projects": ["Contracts"],
        "k": 3,
    }


def test_search_leaves_projects_out_when_none_are_named(core):
    seen, _ = core
    call("search", {"query": "rent"})
    assert json.loads(seen[0].content) == {"query": "rent", "k": 8}


def test_a_k_out_of_range_is_refused_before_the_core_is_asked(core):
    seen, _ = core
    assert call("search", {"query": "rent", "k": 50}).is_error
    assert seen == []


def test_without_the_discovery_file_tamra_is_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path))
    assert mcp_server.NOT_RUNNING in error_text(call("list_projects"))


def test_a_refused_connection_or_an_old_token_means_tamra_is_not_running(core):
    _, replies = core

    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    replies["/api/mcp/projects"] = refuse
    assert mcp_server.NOT_RUNNING in error_text(call("list_projects"))
    replies["/api/mcp/projects"] = lambda request: httpx.Response(401, json={"detail": "x"})
    assert mcp_server.NOT_RUNNING in error_text(call("list_projects"))


def test_the_core_s_reason_reaches_the_ai(core):
    _, replies = core
    off = "AI access is off. Turn it on in Tamra: Settings > General > AI apps."
    replies["/api/mcp/search"] = lambda request: httpx.Response(403, json={"detail": off})
    assert off in error_text(call("search", {"query": "rent"}))


def test_a_timeout_or_an_unreadable_reply_says_tamra_did_not_answer(core):
    _, replies = core

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    replies["/api/mcp/search"] = slow
    assert mcp_server.NO_ANSWER in error_text(call("search", {"query": "rent"}))
    replies["/api/mcp/search"] = lambda request: httpx.Response(500, text="oops")
    assert mcp_server.NO_ANSWER in error_text(call("search", {"query": "rent"}))
    replies["/api/mcp/search"] = lambda request: httpx.Response(200, text="not json")
    assert mcp_server.NO_ANSWER in error_text(call("search", {"query": "rent"}))


def test_the_bridge_loads_neither_the_core_nor_the_window():
    code = (
        "import sys, tamra.mcp_server; "
        "print([m for m in ('tamra.core', 'onnxruntime', 'webview') if m in sys.modules])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_the_mcp_command_runs_the_server_and_not_the_app(monkeypatch):
    from tamra import __main__ as entry

    ran = []
    monkeypatch.setattr(mcp_server, "main", lambda: ran.append("mcp") or 0)
    monkeypatch.setattr("tamra.app.run", lambda **kw: pytest.fail("the app must not start"))
    assert entry.main(["mcp"]) == 0
    assert ran == ["mcp"]
```

- [ ] **Step 4: Run the tests to check they fail**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: collection error, `ImportError: cannot import name 'mcp_server' from 'tamra'`.

- [ ] **Step 5: Implement the bridge**

Create `src/tamra/mcp_server.py`:

```python
"""`Tamra.exe mcp`: an MCP server over stdio for AI apps such as Claude Desktop and Claude Code
(docs/superpowers/specs/2026-10-22-tamra-mcp-design.md §6).

It holds no documents and no models. Each tool call finds the running Tamra through the
discovery file and asks its core, which checks the token and the user's switch. Nothing but the
protocol may be written to stdout, and the query never goes to a log.
"""

from typing import Annotated, Any

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

import tamra
from tamra.discovery import read_discovery
from tamra.paths import data_dir

NOT_RUNNING = "Tamra is not running. Open Tamra and try again."
NO_ANSWER = "Tamra did not answer. Try again."
TIMEOUT = 60.0  # the first search after launch can wait for the embedding model to load
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

_transport: httpx.BaseTransport | None = None  # tests put an httpx.MockTransport here

server = MCPServer(
    "Tamra",
    instructions=(
        "Searches the user's own documents indexed in Tamra (Thai, English and Chinese). "
        "Answer only from the passages it returns, and cite each one by its file and location."
    ),
    version=tamra.__version__,
    log_level="WARNING",
)


def _call(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask the running core. Every failure becomes a ToolError the AI can read."""
    found = read_discovery(data_dir())
    if found is None:
        raise ToolError(NOT_RUNNING)
    port, token = found
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"X-Tamra-Token": token},
            trust_env=False,  # a proxy must never see the token or the passages
            timeout=TIMEOUT,
            transport=_transport,
        ) as client:
            response = client.get(path) if body is None else client.post(path, json=body)
    except httpx.ConnectError as e:
        raise ToolError(NOT_RUNNING) from e
    except httpx.HTTPError as e:
        raise ToolError(NO_ANSWER) from e
    if response.status_code == 401:  # a file left by an earlier run: its token is gone
        raise ToolError(NOT_RUNNING)
    try:
        data = response.json()
    except ValueError as e:
        raise ToolError(NO_ANSWER) from e
    if response.status_code != 200:
        detail = data.get("detail") if isinstance(data, dict) else None
        raise ToolError(detail if isinstance(detail, str) else NO_ANSWER)
    if not isinstance(data, dict):
        raise ToolError(NO_ANSWER)
    return data


@server.tool(annotations=READ_ONLY)
def list_projects() -> dict[str, Any]:
    """List the user's Tamra projects (folders of documents): each project's name, how many of
    its files are indexed, and whether its index is stale (built with another embedding model,
    so it is not searched until the user rebuilds it in Tamra)."""
    return _call("/api/mcp/projects")


@server.tool(annotations=READ_ONLY)
def search(
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=4000,
            description="What to look for, in Thai, English or Chinese. A query in one "
            "language also finds passages written in another.",
        ),
    ],
    projects: Annotated[
        list[str] | None,
        Field(description="Project names from list_projects. Leave out to search every project."),
    ] = None,
    k: Annotated[int, Field(ge=1, le=20, description="How many passages to return.")] = 8,
) -> dict[str, Any]:
    """Search the user's own documents in Tamra. Returns the best-matching passages, best
    first, each with its project, file, location (page, heading or lines) and text. Answer only
    from these passages and cite each one by its file and location. No passages means nothing
    in the documents matches; `notices` names projects that were not searched."""
    body: dict[str, Any] = {"query": query, "k": k}
    if projects is not None:
        body["projects"] = projects
    return _call("/api/mcp/search", body)


def main() -> int:
    server.run()  # stdio, until the client closes it
    return 0
```

`src/tamra/__main__.py`:
- after the `sc.add_argument("--report", ...)` line, add:

```python
    sub.add_parser("mcp", help="MCP server over stdio for AI apps (Tamra must be running)")
```

- after the `selfcheck` block (`return 0 if report["ok"] else 1`), add:

```python
    if args.command == "mcp":
        from tamra.mcp_server import main as mcp_main  # light: no core, no window

        return mcp_main()
```

- [ ] **Step 6: Run the tests to check they pass**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: all PASS. If a `ToolError` text arrives with an SDK prefix, the `in` assertions still
hold. Do not weaken them further.

- [ ] **Step 7: Try it by hand against the dev core**

The dev core uses the repo's `.models` and your real `%LOCALAPPDATA%\Tamra`. Give it a scratch data
folder so your own projects and settings are not touched:

```powershell
$env:TAMRA_DATA_DIR = "$env:TEMP\tamra-mcp-try"; uv run tamra --dev
```

In a second terminal, write a throwaway client and run it isolated (`-I`), so nothing next to it
in `%TEMP%` is imported:

```powershell
$env:TAMRA_DATA_DIR = "$env:TEMP\tamra-mcp-try"
@'
import os
import sys

import anyio
from mcp import Client, StdioServerParameters


async def main():
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tamra", "mcp"],
        env={"TAMRA_DATA_DIR": os.environ["TAMRA_DATA_DIR"]},
    )
    async with Client(server) as client:
        print(await client.call_tool("list_projects", {}))


anyio.run(main)
'@ | Set-Content -Encoding utf8 "$env:TEMP\tamra_mcp_try.py"
uv run python -I "$env:TEMP\tamra_mcp_try.py"
```

Expected: a result with `is_error=True` and "AI access is off. ...". Stop the dev core with Ctrl+C,
then delete `$env:TEMP\tamra-mcp-try` and `$env:TEMP\tamra_mcp_try.py`. Task 7's smoke test covers
the full flow with real models.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/tamra/mcp_server.py src/tamra/__main__.py tests/test_mcp_server.py
git commit -m "feat: Tamra.exe mcp, a stdio MCP server with list_projects and search"
```

---

### Task 6: UI: the switch, Copy setup, and the sidebar badge

**Files:**
- Modify: `ui/src/types.ts` (`Settings`, `SettingsChanges`)
- Modify: `ui/src/settings.ts` (`DEFAULT_SETTINGS`)
- Modify: `ui/src/SettingsGeneral.tsx`
- Modify: `ui/src/App.tsx` (sidebar)
- Modify: `ui/src/styles.css`
- Modify: `ui/src/strings/en.ts`, `ui/src/strings/th.ts`
- Test: `ui/src/SettingsPage.test.tsx`, `ui/src/App.test.tsx`

**Interfaces:**
- Consumes (Task 2): `GET /api/settings` returns `mcp_enabled: boolean` and
  `mcp_command: string[]`; `PUT /api/settings` takes `{mcp_enabled}`.
- Produces: no new exports. The copied text is
  `JSON.stringify({mcpServers: {tamra: {command, args}}}, null, 2)`, and the tests check it
  verbatim.

- [ ] **Step 1: Write the failing tests**

`ui/src/SettingsPage.test.tsx`, inside `describe("General tab", ...)`:

```tsx
  function switchNamed(label: string): HTMLButtonElement {
    return find<HTMLButtonElement>(`button[role="switch"][aria-label="${label}"]`);
  }

  it("turns on AI access for MCP apps and warns that passages leave the computer", async () => {
    const { calls } = await mountPage();
    expect(text()).toContain("The passages these apps find are sent to their AI model");
    expect(switchNamed("Let AI apps search my documents").getAttribute("aria-checked")).toBe(
      "false",
    );
    await click(switchNamed("Let AI apps search my documents"));
    expect(calls).toContainEqual({ key: "PUT /api/settings", body: { mcp_enabled: true } });
    expect(switchNamed("Let AI apps search my documents").getAttribute("aria-checked")).toBe(
      "true",
    );
  });

  it("copies the MCP setup with this computer's command", async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    try {
      await mountPage({ settings: { mcp_command: ["C:\\Tamra\\Tamra.exe", "mcp"] } });
      await click(buttonByText(view!.container, "Copy setup"));
      expect(writeText).toHaveBeenCalledWith(
        JSON.stringify(
          { mcpServers: { tamra: { command: "C:\\Tamra\\Tamra.exe", args: ["mcp"] } } },
          null,
          2,
        ),
      );
      expect(buttonByText(view!.container, "Copied")).toBeDefined();
    } finally {
      delete (navigator as { clipboard?: unknown }).clipboard;
    }
  });

  it("says so when the setup cannot be copied", async () => {
    const writeText = vi.fn(async () => {
      throw new Error("denied");
    });
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    try {
      await mountPage({ settings: { mcp_command: ["C:\\Tamra\\Tamra.exe", "mcp"] } });
      await click(buttonByText(view!.container, "Copy setup"));
      expect(find('[role="alert"]').textContent).toBe("Could not copy to the clipboard.");
    } finally {
      delete (navigator as { clipboard?: unknown }).clipboard;
    }
  });

  it("cannot copy a setup before the core reports its command", async () => {
    await mountPage();
    expect(buttonByText(view!.container, "Copy setup")?.disabled).toBe(true);
  });
```

`ui/src/App.test.tsx`, inside the `describe` that defines `mountWithSettings`:

```tsx
  it("shows that AI apps can search while access is on, and opens General from it", async () => {
    await mountWithSettings({ mcp_enabled: true });
    await click(buttonByText(view!.container, "AI apps can search"));
    expect(view!.container.querySelector(".settings")).not.toBeNull();
    expect(
      view!.container.querySelector('[role="tab"][aria-selected="true"]')?.textContent,
    ).toBe("General");
  });

  it("shows no AI apps badge while access is off", async () => {
    await mountWithSettings();
    expect(buttonByText(view!.container, "AI apps can search")).toBeUndefined();
  });
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `npm --prefix ui test -- SettingsPage App.test`
Expected: FAIL. TypeScript reports that `mcp_command` and `mcp_enabled` are not in `Settings`, or
the elements are not found.

- [ ] **Step 3: Implement types, defaults and strings**

`ui/src/types.ts`, in `Settings` after `ask_before_delete: boolean;`:

```ts
  /** AI apps (MCP) may search every project while Tamra is open. */
  mcp_enabled: boolean;
```

and after `data_dir: string;`:

```ts
  /** How an AI app starts Tamra's MCP server: the command, then its arguments (read-only). */
  mcp_command: string[];
```

Change `SettingsChanges` to:

```ts
export type SettingsChanges = Partial<
  Omit<Settings, "api_key_set" | "api_key_hint" | "data_dir" | "mcp_command">
>;
```

`ui/src/settings.ts`, `DEFAULT_SETTINGS`: add `mcp_enabled: false,` after `ask_before_delete: true,`
and `mcp_command: [], // unknown until the core answers` after `data_dir: "",`.

`ui/src/strings/en.ts`, after `"settings.footer": ...`:

```ts
  "settings.mcp": "AI apps (MCP)",
  "settings.mcpEnable": "Let AI apps search my documents",
  "settings.mcpHint":
    "Claude Desktop, Claude Code and other MCP apps can search all your projects while Tamra is open.",
  "settings.mcpWarning":
    "The passages these apps find are sent to their AI model, which may run in the cloud. Your files and the search index stay on this computer.",
  "settings.mcpSetup": "Connect an app",
  "settings.mcpSetupHint":
    "Paste it into Claude Desktop's claude_desktop_config.json, or into .mcp.json in a project folder for Claude Code.",
  "settings.mcpCopy": "Copy setup",
  "settings.mcpCopied": "Copied",
  "settings.mcpCopyFailed": "Could not copy to the clipboard.",
  "mcp.badge": "AI apps can search",
```

`ui/src/strings/th.ts`, at the same place:

```ts
  "settings.mcp": "แอป AI (MCP)",
  "settings.mcpEnable": "ให้แอป AI ค้นหาเอกสารของฉัน",
  "settings.mcpHint":
    "Claude Desktop, Claude Code และแอป MCP อื่น ๆ ค้นหาได้ทุกโปรเจกต์ขณะที่ Tamra เปิดอยู่",
  "settings.mcpWarning":
    "ข้อความที่แอปเหล่านี้ค้นพบจะถูกส่งให้โมเดล AI ของแอปนั้น ซึ่งอาจทำงานบนคลาวด์ ไฟล์ของคุณและดัชนีการค้นหายังอยู่ในคอมพิวเตอร์เครื่องนี้",
  "settings.mcpSetup": "เชื่อมต่อแอป",
  "settings.mcpSetupHint":
    "วางใน claude_desktop_config.json ของ Claude Desktop หรือใน .mcp.json ในโฟลเดอร์งานสำหรับ Claude Code",
  "settings.mcpCopy": "คัดลอกการตั้งค่า",
  "settings.mcpCopied": "คัดลอกแล้ว",
  "settings.mcpCopyFailed": "คัดลอกไปยังคลิปบอร์ดไม่ได้",
  "mcp.badge": "แอป AI ค้นหาได้",
```

- [ ] **Step 4: Implement the settings section**

`ui/src/SettingsGeneral.tsx`:
- add `import { AlertTriangle } from "lucide-react";` as the first import;
- add after `const DEFAULT_DATA_FOLDER = ...`:

```tsx
/** The JSON an MCP app's config takes to start Tamra's MCP server (an `mcpServers` entry). */
function mcpSetup(command: string[]): string {
  const [exe, ...args] = command;
  return JSON.stringify({ mcpServers: { tamra: { command: exe, args } } }, null, 2);
}
```

- change the component's doc comment to
  `/** The General tab: language, deleting chats, AI apps (MCP), and the data Tamra keeps. */`;
- add state after `confirmingDeleteAll`: `const [copied, setCopied] = useState(false);`
- add after `openDataFolder`:

```tsx
  async function copySetup() {
    setError(null);
    try {
      await navigator.clipboard.writeText(mcpSetup(settings.mcp_command));
      setCopied(true);
    } catch {
      setError(t("settings.mcpCopyFailed"));
    }
  }
```

- add this section between the General `</Section>` and `<Section title={t("settings.data")}>`:

```tsx
      <Section title={t("settings.mcp")}>
        <p className="warning-note">
          <AlertTriangle size={16} />
          {t("settings.mcpWarning")}
        </p>
        <div className="card">
          <Row title={t("settings.mcpEnable")} hint={t("settings.mcpHint")}>
            <Toggle
              label={t("settings.mcpEnable")}
              checked={settings.mcp_enabled}
              onChange={(mcp_enabled) => save({ mcp_enabled })}
            />
          </Row>
          <Row title={t("settings.mcpSetup")} hint={t("settings.mcpSetupHint")}>
            <button
              type="button"
              className="btn"
              disabled={settings.mcp_command.length === 0}
              onClick={() => void copySetup()}
            >
              {copied ? t("settings.mcpCopied") : t("settings.mcpCopy")}
            </button>
          </Row>
        </div>
      </Section>
```

- [ ] **Step 5: Implement the badge**

`ui/src/App.tsx`:
- add `Plug` to the `lucide-react` import (alphabetical:
  `AlertTriangle, BookOpen, Plug, Plus, RefreshCw, SlidersHorizontal, X`);
- add right before `<button type="button" className="sidebar-settings" ...>`:

```tsx
        {settings.mcp_enabled && (
          <button type="button" className="sidebar-mcp" onClick={() => openSettings("general")}>
            <Plug size={14} />
            {t("mcp.badge")}
          </button>
        )}
```

`ui/src/styles.css`, after the `.sidebar-settings:hover, ...` rule:

```css
/* Shown while AI apps (MCP) may search the documents; opens Settings > General. */
.sidebar-mcp {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  margin-top: 12px;
  padding: 6px 14px;
  border: 0;
  border-radius: var(--radius-md);
  background: var(--primary-soft);
  color: var(--primary-ink);
  font-size: 13px;
  font-weight: 500;
  text-align: left;
}

.sidebar-mcp:hover {
  background: var(--primary-soft-hover);
}
```

- [ ] **Step 6: Run the UI tests and the build**

Run: `npm --prefix ui test` then `npm --prefix ui run build`
Expected: every test PASS, including the old General tab tests (the language select and the
"Ask before deleting" switch are still the first ones on the page). The build succeeds.

- [ ] **Step 7: Look at it**

Run `uv run tamra --dev` and `npm --prefix ui run dev`, then open
`http://localhost:5173/#token=dev`. In Settings > General:
- turn the switch on: the badge appears above Settings in the sidebar;
- click the badge: it opens General;
- check the warning note, the light and dark themes, and Thai.

Turn the switch back off afterwards (it is saved in the real data folder).

- [ ] **Step 8: Commit**

```bash
git add ui/src/types.ts ui/src/settings.ts ui/src/SettingsGeneral.tsx ui/src/App.tsx ui/src/styles.css ui/src/strings/en.ts ui/src/strings/th.ts ui/src/SettingsPage.test.tsx ui/src/App.test.tsx
git commit -m "feat(ui): AI apps (MCP) switch, Copy setup, and the sidebar badge"
```

---

### Task 7: Packaging, the exe smoke test, and docs

**Files:**
- Modify: `packaging/tamra.spec` (hidden imports)
- Modify: `scripts/exe_smoke.py` (docstring, imports, `check_mcp`, `main`)
- Modify: `README.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-10-02-tamra-design.md`

**Interfaces:**
- Consumes: everything above. The smoke drives the packaged `Tamra.exe mcp` through
  `mcp.Client(StdioServerParameters(...))`.

- [ ] **Step 1: Hidden import**

`packaging/tamra.spec`: change the last hidden-import line to

```python
    + ["tamra.app", "tamra.selfcheck", "tamra.embedder", "tamra.mcp_server"],
```

- [ ] **Step 2: Add the smoke step**

`scripts/exe_smoke.py`:
- in the module docstring, insert before the last step (renumber "Ends the exe by PID..." to 10):

```
9. AI apps (MCP): turns AI access on, launches `Tamra.exe mcp` with the scratch data folder as a
   stdio MCP server, checks that it offers list_projects and search, that list_projects names the
   same projects as GET /api/projects, and that the Thai leave question finds hr-leave-policy.docx;
   then turns access off and checks that the next search is refused with "AI access is off".
```

- add `import anyio` to the imports, and `from mcp import Client, StdioServerParameters` after
  `import httpx`;
- add before `def main()`:

```python
async def _mcp_session(exe: Path, data_dir: Path, query: str, turn_off) -> dict:
    """One stdio session with `Tamra.exe mcp`: the tools, both calls, then a search after
    turn_off() switched AI access off."""
    server = StdioServerParameters(
        command=str(exe), args=["mcp"], env={"TAMRA_DATA_DIR": str(data_dir)}
    )
    async with Client(server) as mcp:
        tools = sorted(tool.name for tool in (await mcp.list_tools()).tools)
        projects = await mcp.call_tool("list_projects", {})
        found = await mcp.call_tool("search", {"query": query, "k": 3})
        turn_off()
        refused = await mcp.call_tool("search", {"query": query})
    return {"tools": tools, "projects": projects, "found": found, "refused": refused}


def check_mcp(
    client: httpx.Client, exe: Path, data_dir: Path, question: dict, report: dict, problems: list[str]
) -> None:
    """MCP (docs/superpowers/specs/2026-10-22-tamra-mcp-design.md): the packaged exe as a stdio
    MCP server, with AI access on and then off."""
    put_settings(client, mcp_enabled=True)
    started = time.monotonic()
    calls = anyio.run(
        _mcp_session,
        exe,
        data_dir,
        question["question"],
        lambda: put_settings(client, mcp_enabled=False),
    )
    report["mcp_s"] = round(time.monotonic() - started, 1)
    if calls["tools"] != ["list_projects", "search"]:
        problems.append(f"mcp: unexpected tools {calls['tools']}")
    listed = sorted(
        p["name"] for p in (calls["projects"].structured_content or {}).get("projects", [])
    )
    expected = sorted(p["name"] for p in client.get("/api/projects").json()["projects"])
    if listed != expected:
        problems.append(f"mcp: list_projects gave {listed}, the app has {expected}")
    passages = (calls["found"].structured_content or {}).get("passages", [])
    report["mcp_files"] = [p["file"] for p in passages]
    if calls["found"].is_error or not any(p["file"] == question["file"] for p in passages):
        problems.append(f"mcp: search did not find {question['file']}")
    refused = calls["refused"]
    text = refused.content[0].text if refused.content else ""
    if not refused.is_error or "AI access is off" not in text:
        problems.append(f"mcp: a search with access off was not refused: {text!r}")
```

- in `main`, after `check_scanned(client, Path(tmp) / "Scans", report, problems)`, add:

```python
                check_mcp(
                    client,
                    args.exe,
                    Path(tmp) / "data",
                    questions[SMOKE_IDS[0]],  # th-leave-annual: hr-leave-policy.docx, Company folder
                    report,
                    problems,
                )
```

- [ ] **Step 3: Docs**

`README.md`, add after the "Cloud API" section:

```markdown
## AI apps (MCP)

Claude Desktop, Claude Code and other [MCP](https://modelcontextprotocol.io) apps can search your
projects while Tamra is open. It is off until you turn it on.

1. In Tamra, open **Settings > General > AI apps (MCP)** and turn on **Let AI apps search my
   documents**.
2. Click **Copy setup**, then paste it into Claude Desktop's `claude_desktop_config.json`, or into
   `.mcp.json` in a project folder for Claude Code.
3. Restart the app you connected. It gets two read-only tools: `list_projects` and `search`.

The passages these apps find are sent to their AI model, which may run in the cloud. Your files and
the search index stay on this computer. Turning the switch off stops access at once. While it is on,
the sidebar shows **AI apps can search**.
```

`README.md` "Built with": add ` · the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)`
after the PaddleOCR or RapidOCR entry, keeping the line wrapping.

`CLAUDE.md`:
- under Commands, add after the `uv run tamra --dev` line:

```
uv run tamra mcp                          # MCP server over stdio for AI apps (needs the app or --dev core running)
```

- in the module list, add after `core`:

```
- `mcp_server`: `Tamra.exe mcp`, the stdio MCP server for AI apps. It finds the running core
  through `discovery` (`mcp.json`) and calls its read-only `/api/mcp` routes.
```

`docs/superpowers/specs/2026-10-02-tamra-design.md`: add a section after §14:

```markdown
## 15. AI apps (MCP)

`Tamra.exe mcp` is a stdio MCP server that lets Claude Desktop, Claude Code and other MCP apps
search the user's projects while Tamra is open. It is off by default (Settings > General > AI apps)
and offers two read-only tools, `list_projects` and `search`. Design:
`docs/superpowers/specs/2026-10-22-tamra-mcp-design.md`.
```

- [ ] **Step 4: Run the whole suite**

Run:
- `uv run ruff format`, `uv run ruff check --fix`, `uv run ruff format --check`;
- `uv run pytest`;
- `npm --prefix ui test`.

Expected: all PASS.

- [ ] **Step 5: Build, measure, and run the exe smoke test**

Run (PowerShell). The smoke needs `.models` from `scripts/fetch_assets.py` and port 8765 free, and
takes several minutes, so run it in the background:

```powershell
./scripts/build.ps1
"{0:N1} MB" -f ((Get-ChildItem dist\Tamra -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
uv run python scripts/exe_smoke.py --out "$env:TEMP\tamra-smoke.json"
```

Expected:
- the smoke exits 0, with `"problems": []`;
- `mcp_files` in the report includes `hr-leave-policy.docx`.

If `Tamra.exe mcp` fails with a `ModuleNotFoundError`, add that module to the hidden imports in
`packaging/tamra.spec`, rebuild, and run the smoke again. Record the size before (Task 5 Step 1)
and after.

- [ ] **Step 6: Commit and open the PR**

```bash
git add packaging/tamra.spec scripts/exe_smoke.py README.md CLAUDE.md docs/superpowers/specs/2026-10-02-tamra-design.md
git commit -m "build: package Tamra.exe mcp; smoke-test it; document AI apps (MCP)"
git push -u origin mcp-server
```

Then open a PR to `main`. In its body:
- the bundle size before and after;
- the smoke result (`mcp_s`, `mcp_files`);
- that the release workflow runs on this PR because `packaging/` changed.
