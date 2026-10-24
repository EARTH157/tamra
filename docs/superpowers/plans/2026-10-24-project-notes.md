# Project Notes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A project gets up to 1,000 characters of notes that every chat in it gives the model, to understand questions and shape answers. In Answer mode a fact taken from the notes cites them as their own source, "Your project notes", never a document.

**Architecture:** The notes are a `collections.notes` column (schema v6). They are cleaned and checked in `tamra.core`, and changed through the existing `PATCH /api/projects/{id}`. `tamra.answer` reads the chat's home project's notes on each question. In Answer mode they become the last numbered source, with a rule that names its number. In Chat mode they are a `<notes>` block after the rules. The `context` event reports them as their own `notes` part. The UI edits the notes in the project settings dialog, links to them from the sidebar card, shows the part in the context meter, and shows a notes source as "Your project notes".

**Revision (after Task 5):** In the first real-model check, qwen3.5:4b answered from notes that were "not a source", and in 4 of 8 runs it cited the lease document for the notes' fact. Tasks 1–5 stand. Task 6 replaces Task 3's Answer-mode prompt with the notes as a source, Task 7 shows that source in the UI, and Task 8 is the final check. The spec's "Why the notes are a source" has the numbers.

**Tech Stack:** Python 3.12 (FastAPI, SQLite through `tamra.store`), React + TypeScript (Vite, Vitest), uv, npm.

**Spec:** `docs/superpowers/specs/2026-10-24-project-notes-design.md` (the behavior spec `docs/superpowers/specs/2026-10-02-tamra-design.md` §4 and §6 already describe it).

## Global Constraints

- Notes: at most **1,000 characters** (code points) after cleaning; `\r\n` and `\r` become `\n`; leading and trailing whitespace is stripped; `''` means no notes.
- Allowed: new lines and tabs, and every format character except the bidi overrides and isolates. Refused: other Unicode `Cc` characters, and U+202A–U+202E and U+2066–U+2069.
- Fixed API detail for refused notes, verbatim: `Project notes must be at most 1,000 characters, without control characters.`
- In Answer mode the notes are the last numbered source, `rel_path` "Your project notes", `location` `{"kind": "notes"}`, and no chunk, file or hash. A fact from them cites that number, never a document's. In Chat mode they are a `<notes>` block that is not a source. They never enter retrieval's query text, the not-found gate, Search only results, or the answer's language.
- A chat uses only its **home** project's notes; a general chat has none; empty notes leave both prompts byte for byte as today.
- `tamra.store` is the only module that touches SQL. No new dependencies.
- Code, comments, docs and commit messages are in English. Every UI string is in both `ui/src/strings/en.ts` and `ui/src/strings/th.ts`.
- Commands: `uv run pytest`, `uv run ruff check src tests`, `uv run ruff format --check src tests`, `npm --prefix ui test`, `npx --prefix ui tsc -p ui --noEmit`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work on branch `project-notes` (it already holds the spec commit).

## File Structure

| File | Change |
|---|---|
| `src/tamra/store/schema.py` | v6 step: `collections.notes` |
| `src/tamra/store/models.py` | `Collection.notes` |
| `src/tamra/store/repo.py` | read `notes`; `set_collection_notes` |
| `src/tamra/core.py` | `BadNotes`, `MAX_NOTES`, `_clean_notes`, `Core.change_project` (replaces `rename_project`), `notes` in the project view |
| `src/tamra/server.py` | `ProjectChangeBody`, the PATCH route, the error mapping, `notes` in the payload |
| `src/tamra/answer.py` | `NOTES_TEXT`, `notes_text`, `{notes}` in both prompts, `notes` in `build_messages` / `chat_messages` / `context_event`, `AnswerService._notes` |
| `ui/src/types.ts` | `Project.notes`, optional `parts.notes` |
| `ui/src/projects.ts` | `ProjectChanges`, `NOTES_MAX`, `noteLength`, `change` (replaces `rename`) |
| `ui/src/ProjectDialog.tsx` | Notes field in `ProjectSettingsDialog` |
| `ui/src/IndexStatus.tsx` | Notes link on the card |
| `ui/src/App.tsx` | wiring: `change`, the dialog's notes focus, the card link |
| `ui/src/ContextMeter.tsx` | the `notes` row |
| `ui/src/apiErrors.ts`, `ui/src/strings/*.ts`, `ui/src/styles.css` | strings, the error text, styles |

---

### Task 1: Store notes (schema v6)

**Files:**
- Modify: `src/tamra/store/schema.py`, `src/tamra/store/models.py:8-13`, `src/tamra/store/repo.py:30,118-126`
- Test: `tests/test_store_repo.py`, `tests/test_store_settings.py:20-21,52`, `tests/test_store_ocr.py:118`

**Interfaces:**
- Produces: `Collection.notes: str` (default `""`, last field); `Store.set_collection_notes(collection_id: int, notes: str) -> bool` (False when the project is unknown; it stores the text as given, already cleaned by the core); `SCHEMA_VERSION == 6`.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_store_repo.py`:

```python
def test_a_project_has_no_notes_until_they_are_set(store, coll):
    assert coll.notes == ""
    assert store.get_collection(coll.id).notes == ""
    assert store.set_collection_notes(coll.id, "I am the tenant.\nAnswer as a list.")
    assert store.get_collection(coll.id).notes == "I am the tenant.\nAnswer as a list."
    assert [c.notes for c in store.list_collections()] == ["I am the tenant.\nAnswer as a list."]
    assert store.set_collection_notes(999, "x") is False


def test_a_v5_database_gets_empty_notes(tmp_path):
    from tamra.store.db import connect
    from tamra.store.schema import _V1, _V2, _V3, _V4, _V5

    path = tmp_path / "tamra.db"
    conn = connect(path)
    conn.executescript(
        "BEGIN;\n" + _V1 + _V2 + _V3 + _V4 + _V5 + "\nPRAGMA user_version = 5;\nCOMMIT;"
    )
    conn.execute(
        "INSERT INTO collections (name, folder_path, embedding_model_id, created_at)"
        " VALUES ('Old', 'C:/old', 'm', 't')"
    )
    conn.commit()
    conn.close()
    store = Store.open(path)
    try:
        assert [(c.name, c.notes) for c in store.list_collections()] == [("Old", "")]
    finally:
        store.close()
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 6
    conn.close()
```

In `tests/test_store_settings.py` change the three pins `5` to `6` (lines 20, 21 and 52: `assert SCHEMA_VERSION == 6`, `... fetchone()[0] == 6`). In `tests/test_store_ocr.py:118` change `== SCHEMA_VERSION == 5` to `== SCHEMA_VERSION == 6`.

- [x] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_store_repo.py tests/test_store_settings.py tests/test_store_ocr.py -q`
Expected: FAIL. `Collection` has no attribute `notes`, there is no `set_collection_notes`, and the version is still 5.

- [x] **Step 3: Implement**

`src/tamra/store/schema.py`: set `SCHEMA_VERSION = 6`, add after `_V5`:

```python
# v6: a project's notes, which every chat in it gives the model (project notes spec).
_V6 = """
ALTER TABLE collections ADD COLUMN notes TEXT NOT NULL DEFAULT '';
"""
```

and in `migrate`: `steps = [_V1, _V2, _V3, _V4, _V5, _V6]  # steps[n] upgrades schema n to n + 1`.

`src/tamra/store/models.py`, `Collection`, after `created_at: str`:

```python
    notes: str = ""  # the project's notes for the model (project notes spec); "" when none
```

`src/tamra/store/repo.py`: `_COLLECTION_COLUMNS = "id, name, folder_path, embedding_model_id, created_at, notes"`, and after `rename_collection`:

```python
    def set_collection_notes(self, collection_id: int, notes: str) -> bool:
        """Save a project's notes, as the core cleaned them; False if the project is unknown."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE collections SET notes = ? WHERE id = ?", (notes, collection_id)
            )
        return cursor.rowcount > 0
```

- [x] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_store_repo.py tests/test_store_settings.py tests/test_store_ocr.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/tamra/store tests/test_store_repo.py tests/test_store_settings.py tests/test_store_ocr.py
git commit -m "feat: store a project's notes (schema v6)"
```

---

### Task 2: Clean, check and change notes through the API

**Files:**
- Modify: `src/tamra/core.py` (errors near `BadName`, helpers near `_clean_name`, `_project_view`, `rename_project` → `change_project`), `src/tamra/server.py` (`ProjectRenameBody`, `_project_payload`, `_PROJECT_ERRORS`, the PATCH route, the `tamra.core` import)
- Test: `tests/test_core.py:245-253`, `tests/test_server_projects.py`

**Interfaces:**
- Consumes: `Store.set_collection_notes`, `Collection.notes` (Task 1).
- Produces: `tamra.core.MAX_NOTES = 1000`; `tamra.core.BadNotes(ValueError)`; `Core.change_project(collection_id: int, *, name: str | None = None, notes: str | None = None) -> Collection | None` (None when unknown; raises `BadName`, `BadNotes` or `NameTaken` before saving anything); project payloads carry `"notes": str`; `PATCH /api/projects/{id}` takes `{"name"?: str, "notes"?: str}`.

- [x] **Step 1: Write the failing tests**

In `tests/test_core.py` replace `test_rename_project` with:

```python
def test_change_project_renames_and_sets_notes_all_or_nothing(core, tmp_path):
    first = core.create_project("Docs", str(make_docs(tmp_path, "one")))
    core.create_project("Other", str(make_docs(tmp_path, "two")))
    assert core.change_project(first.id, name="  Papers ").name == "Papers"
    assert core.change_project(999, name="Whatever") is None
    with pytest.raises(ValueError, match="name taken"):
        core.change_project(first.id, name="other")
    with pytest.raises(ValueError):
        core.change_project(first.id, name="   ")
    changed = core.change_project(first.id, notes="  I am the tenant.\r\nAnswer as a list.\r ")
    assert changed.notes == "I am the tenant.\nAnswer as a list."
    assert core.change_project(first.id, notes="   ").notes == ""  # empty means none
    # A refused part changes nothing: the good name is not saved with bad notes.
    with pytest.raises(BadNotes):
        core.change_project(first.id, name="Kept out", notes="x" * 1001)
    assert core.store.get_collection(first.id).name == "Papers"
    with pytest.raises(ValueError, match="name taken"):
        core.change_project(first.id, name="Other", notes="Not saved")
    assert core.store.get_collection(first.id).notes == ""


@pytest.mark.parametrize(
    ("notes", "ok"),
    [
        ("x" * 1000, True),
        ("x" * 1001, False),
        ("ฉันเป็นผู้เช่าห้อง 1204\tตอบเป็นข้อ ๆ", True),
        ("ค่าเช่า\u200bเดือนละ", True),  # a zero-width space pasted with Thai text
        ("ring \a", False),  # a control character
        ("a \u202e b", False),  # a bidi override
        ("a \u2066 b", False),  # a bidi isolate
    ],
)
def test_notes_are_checked(core, tmp_path, notes, ok):
    project = core.create_project("Docs", str(make_docs(tmp_path, "one")))
    if ok:
        assert core.change_project(project.id, notes=notes).notes == notes
    else:
        with pytest.raises(BadNotes):
            core.change_project(project.id, notes=notes)
```

and add `BadNotes` to the file's `from tamra.core import ...` line.

Append to `tests/test_server_projects.py`, before `# --- the token ---`:

```python
# --- project notes ---

BAD_NOTES = "Project notes must be at most 1,000 characters, without control characters."


def test_notes_are_saved_and_shown_with_the_project(api):
    client, tmp_path = api
    created = add_project(client, "Lease", folder(tmp_path, "docs"))
    assert created["notes"] == ""
    changed = client.patch(
        f"/api/projects/{created['id']}", headers=AUTH, json={"notes": " I am the tenant. "}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["notes"] == "I am the tenant."
    assert project(client, created["id"])["notes"] == "I am the tenant."


def test_bad_notes_are_a_400_and_change_nothing(api):
    client, tmp_path = api
    created = add_project(client, "Lease", folder(tmp_path, "docs"))
    refused = client.patch(
        f"/api/projects/{created['id']}",
        headers=AUTH,
        json={"name": "Renamed", "notes": "x" * 1001},
    )
    assert refused.status_code == 400
    assert refused.json()["detail"] == BAD_NOTES
    kept = project(client, created["id"])
    assert (kept["name"], kept["notes"]) == ("Lease", "")


def test_notes_for_an_unknown_project_are_a_404(api):
    client, _ = api
    response = client.patch("/api/projects/99", headers=AUTH, json={"notes": "x"})
    assert response.status_code == 404
```

Also add `"notes"` to the expected payload keys in `test_create_a_project_returns_it_with_its_index_status`: `assert set(body) == {"id", "name", "folder_path", "index", "notes"}`.

- [x] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_core.py tests/test_server_projects.py -q -k "change_project or notes or returns_it"`
Expected: FAIL (`BadNotes` and `change_project` do not exist; no `notes` in payloads).

- [x] **Step 3: Implement**

`src/tamra/core.py`, after `class BadName`:

```python
class BadNotes(ValueError):
    """Project notes over MAX_NOTES characters, or with a control character or a bidi override."""
```

after `_clean_name`:

```python
MAX_NOTES = 1000
_BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def _clean_notes(notes: str) -> str:
    """Notes as they are stored (project notes spec): one kind of new line, stripped, at most
    MAX_NOTES characters, and no control characters (new lines and tabs aside) or bidi
    overrides. Other format characters stay: a zero-width space comes with pasted Thai text."""
    cleaned = notes.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(cleaned) > MAX_NOTES:
        raise BadNotes("notes too long")
    if any(
        c in _BIDI_CONTROLS or (unicodedata.category(c) == "Cc" and c not in "\n\t")
        for c in cleaned
    ):
        raise BadNotes("bad notes characters")
    return cleaned
```

`_project_view`: add `"notes": collection.notes,` after `"folder_path"`.

Replace `rename_project` with:

```python
    def change_project(
        self, collection_id: int, *, name: str | None = None, notes: str | None = None
    ) -> Collection | None:
        """Rename a project and/or set its notes; None if it is unknown. Both are checked before
        either is saved (BadName, BadNotes, NameTaken), so a refused change changes nothing."""
        new_name = _clean_name(name) if name is not None else None
        new_notes = _clean_notes(notes) if notes is not None else None
        if self.store.get_collection(collection_id) is None:
            return None
        if new_name is not None and not self.store.rename_collection(collection_id, new_name):
            return None
        if new_notes is not None and not self.store.set_collection_notes(collection_id, new_notes):
            return None
        return self.store.get_collection(collection_id)
```

(`rename_collection` raises `NameTaken` before it writes, and it runs before the notes are saved.)

`src/tamra/server.py`:
- Import `BadNotes` from `tamra.core` (keep the list sorted).
- Rename `ProjectRenameBody` to `ProjectChangeBody` and add `notes: str | None = None`.
- `_project_payload`: add `"notes": view["notes"],` after `"folder_path"`.
- After `BAD_NAME_DETAIL`:

```python
BAD_NOTES_DETAIL = "Project notes must be at most 1,000 characters, without control characters."
```

- `_PROJECT_ERRORS`: add `(BadNotes, 400, BAD_NOTES_DETAIL),` after the `BadName` entry.
- Replace the PATCH route:

```python
    @app.patch("/api/projects/{project_id}")
    def change_project(project_id: int, body: ProjectChangeBody) -> dict:
        """Rename a project and/or set its notes; a refused part changes nothing."""
        if body.name is not None or body.notes is not None:
            try:
                changed = core.change_project(project_id, name=body.name, notes=body.notes)
            except ValueError as e:
                raise _project_error(e) from e
            if changed is None:
                raise HTTPException(status_code=404, detail="Project not found.")
        return _project_response(core, project_id)
```

- [x] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_core.py tests/test_server_projects.py -q`
Expected: PASS. If another test still calls `rename_project`, run `grep -rn rename_project src tests` and switch it to `change_project(id, name=...)`.

- [x] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format --check src tests
git add src/tamra/core.py src/tamra/server.py tests/test_core.py tests/test_server_projects.py
git commit -m "feat: clean, check and change project notes through PATCH /api/projects"
```

---

### Task 3: Notes in the prompt and the context event

**Files:**
- Modify: `src/tamra/answer.py` (`SYSTEM_PROMPT`, `CHAT_PROMPT`, new `NOTES_TEXT` / `notes_text`, `build_messages`, `chat_messages`, `context_event`, `AnswerService._ask` / `_chat`)
- Test: `tests/test_answer.py`

**Interfaces:**
- Consumes: `Collection.notes`, `Store.set_collection_notes` (Task 1).
- Produces: `notes_text(notes: str) -> str`; `build_messages(question, history, sources, language, room=..., notes: str = "")`; `chat_messages(question, history, language, room=..., notes: str = "")`; `context_event(messages, history, sources, context_tokens, reply_tokens, notes: str = "")`, whose `parts` has the keys `instructions, notes, sources, history, question`.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_answer.py` (add `notes_text` and `SYSTEM_PROMPT` to the `from tamra.answer import (...)` list if not there):

```python
NOTES = "I am the tenant of unit 1204.\nAnswer in Thai, as a numbered list."


def test_empty_notes_leave_both_prompts_as_they_were():
    plain = build_messages("q", [], [], "en")
    assert build_messages("q", [], [], "en", notes="") == plain
    assert "<notes>" not in plain[0]["content"]
    assert chat_messages("q", [], "en", notes="") == chat_messages("q", [], "en")
    assert notes_text("") == ""


def test_notes_sit_between_the_rules_and_the_sources_and_are_not_a_source():
    location = {"kind": "text", "line_start": 1, "line_end": 1}
    source = SourceRecord(1, None, None, "lease.md", "The lease term is three years.", location, None)
    system = build_messages("q", [], [source], "en", notes=NOTES)[0]["content"]
    assert "They are not a source: never state a" in system
    block = f"The user's notes for this project:\n<notes>\n{NOTES}\n</notes>"
    assert block in system
    assert system.index("- Be concise.") < system.index(block) < system.index("Sources:")
    assert "[1] lease.md" in system  # the sources keep their numbers; the notes have none


def test_chat_mode_has_the_notes_after_its_rules():
    system = chat_messages("q", [], "th", notes=NOTES)[0]["content"]
    assert system.endswith(f"<notes>\n{NOTES}\n</notes>")
    assert "cannot see the user's\ndocuments" in system


def test_notes_braces_are_text_not_template_fields():
    system = build_messages("q", [], [], "en", notes="Use {curly} braces")[0]["content"]
    assert "<notes>\nUse {curly} braces\n</notes>" in system


def test_the_context_event_counts_the_notes_apart(env):
    store, make, chat = env
    store.set_collection_notes(chat.collection_id, NOTES)
    llm = FakeLLM(("Three years [1].",))
    events = list(make(llm).ask(chat.id, "How long is the lease term?"))
    context = next(e for e in events if e["type"] == "context")
    parts = context["parts"]
    assert parts["notes"] == estimate_tokens(notes_text(NOTES))
    assert sum(parts.values()) == context["prompt_tokens"]
    assert f"<notes>\n{NOTES}\n</notes>" in llm.calls[-1][0]["content"]


def test_a_chat_uses_only_its_home_projects_notes(env):
    store, make, chat = env
    other = store.create_collection("Other", "C:/other", "m")
    store.set_collection_notes(other.id, "Other project's notes")
    store.set_chat_scope(chat.id, [chat.collection_id, other.id])
    llm = FakeLLM(("Three years [1].",))
    list(make(llm).ask(chat.id, "How long is the lease term?"))
    assert "<notes>" not in llm.calls[-1][0]["content"]  # its home has none
    store.set_collection_notes(chat.collection_id, NOTES)
    list(make(llm).ask(chat.id, "And the rent?", mode="chat"))
    system = llm.calls[-1][0]["content"]
    assert NOTES in system and "Other project's notes" not in system


def test_a_general_chat_has_no_notes(env):
    store, make, chat = env
    store.set_collection_notes(chat.collection_id, NOTES)
    general = store.create_chat("", None)
    llm = FakeLLM(("Hi!",))
    events = list(make(llm).ask(general.id, "hello", mode="chat"))
    assert "<notes>" not in llm.calls[-1][0]["content"]
    context = next(e for e in events if e["type"] == "context")
    assert context["parts"]["notes"] == 0
```

The `env` fixture yields `store.create_chat(collection_id=collection.id)`, so its chat has a home project, and `Store.set_chat_scope(chat_id, collection_ids)` sets a chat's scope.

- [x] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_answer.py -q -k "notes"`
Expected: FAIL (`notes_text` does not exist; `build_messages` takes no `notes`).

- [x] **Step 3: Implement**

`src/tamra/answer.py`:

In `SYSTEM_PROMPT`, change the line `- Be concise.` to `- Be concise.{notes}` (it stays followed by the blank line and `Sources:`). In `CHAT_PROMPT`, change the last line `- Be concise."""` to `- Be concise.{notes}"""`.

After `CHAT_PROMPT`:

```python
# Project notes (spec §6): the user's situation and how they want answers. They follow the
# rules in both modes. They guide the answer and are never a source.
NOTES_TEXT = """
- The user's notes below describe their situation and how they want answers. Use them to
  understand the question and to shape the answer. They are not a source: never state a
  fact from them as the answer, and never cite them.

The user's notes for this project:
<notes>
{notes}
</notes>"""


def notes_text(notes: str) -> str:
    """What the system prompt adds for a project's notes; '' when there are none, so the
    prompt is then exactly what it was without notes."""
    return NOTES_TEXT.format(notes=notes) if notes else ""
```

`build_messages`: add a last parameter `notes: str = ""`, and build the system prompt with
`SYSTEM_PROMPT.format(language=LANGUAGE_NAMES[language], sources=_blocks(sources), notes=notes_text(notes))`.
Add to its docstring: "notes are the chat's home project's notes (project notes spec)."

`chat_messages`: add `notes: str = ""` last, and `CHAT_PROMPT.format(language=LANGUAGE_NAMES[language], notes=notes_text(notes))`.

`context_event`: add `notes: str = ""` last, and:

```python
    source_tokens = estimate_tokens(_blocks(sources)) if sources else 0
    notes_tokens = estimate_tokens(notes_text(notes))
    parts = {
        "instructions": tokens(messages[0]) - source_tokens - notes_tokens,
        "notes": notes_tokens,
        "sources": source_tokens,
        "history": sum(tokens(m) for m in messages[1:-1]),
        "question": tokens(messages[-1]),
    }
```

`AnswerService`: add

```python
    def _notes(self, chat: Chat) -> str:
        """The notes of the chat's home project; '' for a general chat (project notes spec).
        Read once per question, so an edit applies from the next one."""
        if chat.collection_id is None:
            return ""
        home = self._store.get_collection(chat.collection_id)
        return home.notes if home is not None else ""
```

and import `Chat` in `from tamra.store import ...`. In `_ask`, after the `chat is None` check, add `notes = self._notes(chat)`. Then pass `notes=notes` to `build_messages(...)` and to `context_event(...)`. In `_chat`, keep the chat:

```python
        chat = self._store.get_chat(chat_id)
        if chat is None:
            yield {"type": "error", "message": f"Chat {chat_id} not found."}
            return
        notes = self._notes(chat)
```

and pass `notes=notes` to `chat_messages(...)` and `context_event(...)`.

- [x] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_answer.py -q`
Expected: PASS, including the existing context-event tests (they sum `parts.values()`, and `notes` is 0 there).

- [x] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format --check src tests
git add src/tamra/answer.py tests/test_answer.py
git commit -m "feat: give the model the home project's notes, apart from the sources"
```

---

### Task 4: Edit notes in the UI

**Files:**
- Modify: `ui/src/types.ts:30`, `ui/src/projects.ts`, `ui/src/ProjectDialog.tsx:81-193`, `ui/src/IndexStatus.tsx:146-183`, `ui/src/App.tsx` (dialog type, `renameProject`, the settings dialog, `IndexStatus`), `ui/src/apiErrors.ts`, `ui/src/strings/en.ts`, `ui/src/strings/th.ts`, `ui/src/styles.css` (`.collection-card > .link-button`, the dialog fields)
- Test: `ui/src/ProjectTree.test.tsx`, `ui/src/IndexStatus.test.tsx`, `ui/src/projects.test.tsx:149-167`, and the project fixtures

**Interfaces:**
- Consumes: the payload's `notes` and `PATCH {"name"?, "notes"?}` (Task 2).
- Produces: `Project.notes: string`; `ProjectChanges = { name?: string; notes?: string }`; `NOTES_MAX = 1000`; `noteLength(notes: string): number`; `useProjects().change(id, changes)` (replaces `rename`); `ProjectSettingsDialog` props `focusNotes?: boolean` and `onSave(changes)` (replaces `onRename`); `IndexStatus` prop `onNotes: () => void`.

- [x] **Step 1: Write the failing tests**

In `ui/src/ProjectTree.test.tsx`, after "shows a refused rename inside the project settings", add:

```tsx
  it("edits the notes in project settings, counting characters as the core does", async () => {
    let core: Core = { projects: [contracts], chats: [] };
    const calls = boot(core, {
      "GET /api/projects": () => json({ projects: core.projects }),
      "PATCH /api/projects/1": () => {
        core = { ...core, projects: [{ ...contracts, notes: "I am the tenant. 😀" }] };
        return json(core.projects[0]);
      },
    });
    view = await mount(<App />);
    await click(projectMenuButton("Contracts"));
    await click(buttonByText(menu()!, "Project settings"));
    const box = dialog()!;
    const notes = box.querySelector<HTMLTextAreaElement>("textarea.notes-input");
    expect(box.querySelector(".notes-count")?.textContent).toBe("0 / 1,000");
    await typeInto(notes, "x".repeat(1001));
    expect(box.querySelector(".notes-count.over")).not.toBeNull();
    expect(buttonByText(box, "Save")?.disabled).toBe(true);
    await typeInto(notes, "  I am the tenant. 😀 "); // stripped, and the emoji counts once
    expect(box.querySelector(".notes-count")?.textContent).toBe("18 / 1,000");
    await click(buttonByText(box, "Save"));
    expect(calls).toContainEqual({ key: "PATCH /api/projects/1", body: { notes: "I am the tenant. 😀" } });
    expect(dialog()).toBeNull();
  });

  it("opens the project settings on the Notes field from the card's Add notes link", async () => {
    boot({ projects: [contracts], chats: [] });
    view = await mount(<App />);
    await click(buttonByText(view.container.querySelector(".collection-card")!, "Add notes"));
    expect(dialog()?.querySelector("h2")?.textContent).toBe("Project settings");
    expect(document.activeElement?.classList.contains("notes-input")).toBe(true);
  });

  it("shows refused notes inside the project settings", async () => {
    boot(
      { projects: [contracts], chats: [] },
      {
        "PATCH /api/projects/1": () =>
          json(
            { detail: "Project notes must be at most 1,000 characters, without control characters." },
            400,
          ),
      },
    );
    view = await mount(<App />);
    await click(projectMenuButton("Contracts"));
    await click(buttonByText(menu()!, "Project settings"));
    await typeInto(dialog()?.querySelector("textarea.notes-input"), "Answer as a list.");
    await click(buttonByText(dialog()!, "Save"));
    expect(dialog()?.querySelector('[role="alert"]')?.textContent).toBe(
      "Notes can be at most 1,000 characters, without control characters.",
    );
  });
```

In `ui/src/IndexStatus.test.tsx`, add `notes: ""` to the `project()` fixture and add:

```tsx
  it("links to the project's notes: Add notes, or Notes once there are some", async () => {
    const onNotes = vi.fn();
    view = await mount(
      <IndexStatus project={project()} disabled={false} onChangeFolder={vi.fn()} onRebuild={vi.fn()} onNotes={onNotes} />,
    );
    await click(buttonByText(view.container, "Add notes"));
    expect(onNotes).toHaveBeenCalled();
    await view.unmount();
    view = await mount(
      <IndexStatus
        project={{ ...project(), notes: "I am the tenant." }}
        disabled={true}
        onChangeFolder={vi.fn()}
        onRebuild={vi.fn()}
        onNotes={onNotes}
      />,
    );
    expect(buttonByText(view.container, "Notes")?.disabled).toBe(false); // notes can change mid-answer
  });
```

Add `onNotes={vi.fn()}` to every other `<IndexStatus ... />` in that file. In `ui/src/projects.test.tsx` change `latest!.rename(5, "Renamed")` to `latest!.change(5, { name: "Renamed" })`. Add `notes: ""` to every typed `Project` fixture that `npx --prefix ui tsc -p ui --noEmit` reports. The fixtures in `ProjectTree.test.tsx:35-40,725`, `IndexStatus.test.tsx:15`, `ChatView.scope.test.tsx:26`, `projects.test.tsx:12` and `App.chats.test.tsx:23-24` build projects. Add it to the untyped ones in `App.test.tsx:35` as well.

- [x] **Step 2: Run the tests to see them fail**

Run: `npm --prefix ui test -- ProjectTree IndexStatus projects`
Expected: FAIL (no Notes field, no card link, no `change`).

- [x] **Step 3: Implement**

`ui/src/types.ts`: `export type Project = { id: number; name: string; folder_path: string; notes: string; index: IndexStatus };`

`ui/src/projects.ts`, after `NAME_MAX`:

```ts
/** What project settings can change (PATCH /api/projects/{id}); fields left out stay as they are. */
export type ProjectChanges = { name?: string; notes?: string };

/** The longest project notes the core accepts, in characters (src/tamra/core.py MAX_NOTES). */
export const NOTES_MAX = 1000;

/** Notes' length as the core counts it: code points of the stripped text, so an emoji is one. */
export function noteLength(notes: string): number {
  return [...notes.trim()].length;
}
```

and replace `rename` with:

```ts
  async function change(id: number, changes: ProjectChanges): Promise<void> {
    await api<Project>("PATCH", `/api/projects/${id}`, changes);
    await refresh();
  }
```

returning `change` in place of `rename`.

`ui/src/ProjectDialog.tsx`, `ProjectSettingsDialog`: replace the props and the state/save logic and add the field:

```tsx
type SettingsProps = {
  /** The project as the core last reported it: the index status follows the polling. */
  project: Project;
  /** An answer is being written: the folder and the index cannot change now. */
  busy: boolean;
  /** Open with the Notes field focused (the card's Notes link). */
  focusNotes?: boolean;
  /** Save what changed: resolve to close the dialog; reject with an ApiError to show why it was refused. */
  onSave: (changes: ProjectChanges) => Promise<void>;
  onChangeFolder: () => void;
  onRebuild: () => Promise<void>;
  onClose: () => void;
};

/** Project settings: its name, its notes, its folder, and its search index (status and Rebuild index). */
export function ProjectSettingsDialog({
  project,
  busy,
  focusNotes = false,
  onSave,
  onChangeFolder,
  onRebuild,
  onClose,
}: SettingsProps) {
  const t = useT();
  const [name, setName] = useState(project.name);
  const [notes, setNotes] = useState(project.notes);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const next = name.trim();
  const nextNotes = notes.trim();
  const used = noteLength(notes);
  const tooLong = used > NOTES_MAX;
  const changes: ProjectChanges = {
    ...(next !== project.name ? { name: next } : {}),
    ...(nextNotes !== project.notes ? { notes: nextNotes } : {}),
  };
  const changed = Object.keys(changes).length > 0;

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!next || !changed || tooLong) return;
    setWorking(true);
    setError(null);
    try {
      await onSave(changes);
    } catch (e) {
      setError(projectErrorText(e as Error, t));
      setWorking(false);
    }
  }
```

Keep `rebuild` and the JSX as they are, with these changes:
- The name input's `data-autofocus` becomes `data-autofocus={focusNotes ? undefined : true}`.
- After the name `<label>`, add:

```tsx
        <label className="field">
          <span className="field-label">{t("project.notesLabel")}</span>
          <textarea
            className="field-input notes-input"
            rows={6}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            aria-describedby="project-notes-help"
            data-autofocus={focusNotes ? true : undefined}
          />
          <span className="notes-foot">
            <span id="project-notes-help" className="field-help">
              {t("project.notesHelp")}
            </span>
            <span className={tooLong ? "notes-count over" : "notes-count"}>
              {t("project.notesCount", { used: used.toLocaleString("en"), max: NOTES_MAX.toLocaleString("en") })}
            </span>
          </span>
        </label>
```

- The Save button's `disabled` becomes `disabled={working || !next || !changed || tooLong}`.

Import `NOTES_MAX`, `noteLength` and `type ProjectChanges` from `./projects`.

`ui/src/IndexStatus.tsx`: add `onNotes: () => void;` to `Props` and the destructuring, and replace the Change folder button with:

```tsx
      <div className="collection-links">
        <button type="button" className="link-button" onClick={onChangeFolder} disabled={disabled}>
          {t("index.changeFolder")}
        </button>
        {/* Notes can change while an answer is written: it keeps the notes it started with. */}
        <button type="button" className="link-button" onClick={onNotes}>
          {project.notes ? t("project.notes") : t("project.addNotes")}
        </button>
      </div>
```

`ui/src/App.tsx`:
- The dialog type's settings case becomes `| { kind: "settings"; id: number; focus?: "notes" }`.
- `renameProject` calls `projectsApi.change(id, { name })`.
- `<IndexStatus ...>` gets `onNotes={() => setDialog({ kind: "settings", id: activeProject.id, focus: "notes" })}`.
- `<ProjectSettingsDialog ...>` gets `focusNotes={dialog.focus === "notes"}`, and `onRename` becomes:

```tsx
          onSave={async (changes) => {
            await projectsApi.change(dialogProject.id, changes);
            setDialog(null);
          }}
```

`ui/src/apiErrors.ts`, in `PROJECT_ERRORS`:

```ts
  "Project notes must be at most 1,000 characters, without control characters.": "project.err.notes",
```

`ui/src/strings/en.ts` (next to the `project.*` keys):

```ts
  "project.notesLabel": "Notes",
  "project.notesHelp":
    "Tamra gives these notes to the model in every chat of this project, to understand your questions and shape its answers. They are not a source: answers still cite your documents.",
  "project.notesCount": "{used} / {max}",
  "project.notes": "Notes",
  "project.addNotes": "Add notes",
  "project.err.notes": "Notes can be at most 1,000 characters, without control characters.",
```

and change `"project.settings.text"` to `"Name the project, write its notes, choose its folder, and check its search index."`.

`ui/src/strings/th.ts`:

```ts
  "project.notesLabel": "โน้ต",
  "project.notesHelp":
    "Tamra ให้โมเดลอ่านโน้ตนี้ในทุกแชทของโปรเจกต์ เพื่อเข้าใจคำถามและปรับวิธีตอบ โน้ตไม่ใช่แหล่งข้อมูล คำตอบยังอ้างอิงจากเอกสารของคุณเสมอ",
  "project.notesCount": "{used} / {max}",
  "project.notes": "โน้ต",
  "project.addNotes": "เพิ่มโน้ต",
  "project.err.notes": "โน้ตยาวได้ไม่เกิน 1,000 ตัวอักษร และต้องไม่มีอักขระควบคุม",
```

and `"project.settings.text": "ตั้งชื่อโปรเจกต์ เขียนโน้ต เลือกโฟลเดอร์ และดูสถานะดัชนีการค้นหา"`.

`ui/src/styles.css`: replace the `.collection-card > .link-button { margin-top: 10px; }` rule with:

```css
.collection-card > .collection-links {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 10px;
}
```

and after the `.field-input:focus` rule add:

```css
/* Project notes: a few lines that grow on demand, with the help text and the counter under it. */
.field-input.notes-input {
  height: auto;
  padding: 8px 12px;
  font: inherit;
  font-size: 14px;
  line-height: 20px;
  resize: vertical;
}

.notes-foot {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
}

.notes-foot .field-help {
  color: var(--muted);
  font-size: 12px;
  line-height: 17px;
}

.notes-count {
  flex: none;
  color: var(--muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.notes-count.over {
  color: var(--error-text);
}
```
- [x] **Step 4: Run the tests to see them pass**

Run: `npx --prefix ui tsc -p ui --noEmit` and `npm --prefix ui test`
Expected: no type errors; all tests pass.

- [x] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat: edit project notes in project settings, with a link on the project card"
```

---

### Task 5: The notes part in the context meter

**Files:**
- Modify: `ui/src/types.ts` (`ContextEvent.parts`), `ui/src/ContextMeter.tsx` (`ContextUse.parts`, `usedTokens`, `segments`), `ui/src/strings/en.ts`, `ui/src/strings/th.ts`
- Test: `ui/src/ChatView.test.tsx`

**Interfaces:**
- Consumes: the `context` event's `parts.notes` (Task 3).
- Produces: `parts.notes?: number` (optional: a context saved before project notes has none); the Details row with key `notes`.

- [x] **Step 1: Write the failing test**

In `ui/src/ChatView.test.tsx`, after "shows the context meter saved with a chat when it is opened again", add:

```tsx
  it("lists the project notes in the context details, and not when a context has none", async () => {
    const saved = (parts: Record<string, number>) => ({
      prompt_tokens: 0,
      context_tokens: 8192,
      reply_tokens: 1024,
      parts,
      source_count: 0,
      history_kept: 0,
      history_total: 0,
      measured: null,
    });
    const rows = () => [...view!.container.querySelectorAll(".context-parts li")].map((li) => li.textContent);
    mockFetch({
      "GET /api/chats/5": () =>
        json({ ...chat, messages: [], context: saved({ instructions: 100, notes: 50, sources: 0, history: 0, question: 20 }) }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    expect(view.container.querySelector(".context-summary")?.textContent).toContain("170");
    await click(buttonByText(view.container, "Details"));
    expect(rows().find((row) => row?.startsWith("Project notes"))).toBe("Project notes≈50");
    await view.unmount();
    mockFetch({
      "GET /api/chats/5": () =>
        json({ ...chat, messages: [], context: saved({ instructions: 100, sources: 0, history: 0, question: 20 }) }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    await click(buttonByText(view.container, "Details"));
    expect(rows().some((row) => row?.startsWith("Project notes"))).toBe(false);
  });
```

(If the meter shows the summary differently, match the existing "shows the context meter saved with a chat" test: there 900 estimated with 620 measured shows "Context 662 / 8.2k tokens". With `measured: null` the summary shows the estimate, 170.)

- [x] **Step 2: Run the test to see it fail**

Run: `npm --prefix ui test -- ChatView`
Expected: FAIL (no "Project notes" row; the summary is 120, without the notes).

- [x] **Step 3: Implement**

`ui/src/types.ts`, in `ContextEvent`:

```ts
  /** `notes` is missing from a context saved before project notes; read it as 0. */
  parts: { instructions: number; notes?: number; sources: number; history: number; question: number };
```

`ui/src/ContextMeter.tsx`: give `ContextUse.parts` the same type (`ContextEvent["parts"]`). Then add

```ts
/** The prompt's estimated size: the sum of its parts. */
function promptEstimate(parts: ContextUse["parts"]): number {
  return parts.instructions + (parts.notes ?? 0) + parts.sources + parts.history + parts.question;
}
```

and use it in `usedTokens` (`return promptEstimate(use.parts);` when not measured) and in `segments`:

```ts
  const estimate = promptEstimate(use.parts);
  const scale = use.measured && estimate > 0 ? use.measured.prompt / estimate : 1;
  const [instructions, notes, sources, history, question] = [
    use.parts.instructions,
    use.parts.notes ?? 0,
    use.parts.sources,
    use.parts.history,
    use.parts.question,
  ].map((n) => Math.round(n * scale));
```

with `{ key: "notes", label: t("context.notes"), tokens: notes, estimated: true },` right after the `instructions` segment, and `notes` added to `taken`. Rows with 0 tokens are already filtered out, so the row only shows when there are notes.

Strings: `"context.notes": "Project notes",` (en) and `"context.notes": "โน้ตของโปรเจกต์",` (th), next to `context.instructions`.

- [x] **Step 4: Run the tests to see them pass**

Run: `npx --prefix ui tsc -p ui --noEmit` and `npm --prefix ui test`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat: show project notes as their own part of the context meter"
```

---

### Task 6: The notes as a numbered source in Answer mode

**Files:**
- Modify: `src/tamra/answer.py` (new `NOTES_SOURCE_NAME`, `NOTES_SOURCE_RULE`, `is_notes`, `notes_source`; `_blocks`; `build_messages`; `context_event`; `AnswerService._ask`), `src/tamra/core.py` (`source_changed`)
- Test: `tests/test_answer.py` (replace Task 3's Answer-mode tests), `tests/test_core.py`

**Interfaces:**
- Consumes: `AnswerService._notes(chat)`, `notes_text(notes)` and `NOTES_TEXT` (Task 3, kept for Chat mode).
- Produces: `NOTES_SOURCE_NAME = "Your project notes"`; `is_notes(source: SourceRecord) -> bool` (its `location["kind"] == "notes"`); `notes_source(notes: str, n: int) -> SourceRecord`; `build_messages(question, history, sources, language, room=...)` (no `notes` parameter: a notes source in `sources` brings its rule); `context_event(..., notes: str = "")`, where `notes` is only for Chat mode; `Core.source_changed(notes source) is False`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_answer.py`, add `NOTES_SOURCE_NAME`, `notes_source` and `is_notes` to the `from tamra.answer import (...)` list. Delete Task 3's `test_empty_notes_leave_both_prompts_as_they_were`, `test_notes_sit_between_the_rules_and_the_sources_and_are_not_a_source`, `test_notes_braces_are_text_not_template_fields` and `test_the_context_event_counts_the_notes_apart`. Add these in their place:

```python
LEASE_LOCATION = {"kind": "text", "line_start": 1, "line_end": 1}


def lease_source(n: int = 1) -> SourceRecord:
    text = "The lease term is three years, starting on 1 March 2026."
    return SourceRecord(n, None, None, "lease.md", text, LEASE_LOCATION, None)


def test_without_notes_both_prompts_are_as_they_were():
    plain = build_messages("q", [lease_source()], [], "en")
    assert NOTES_SOURCE_NAME not in plain[0]["content"] and "<notes>" not in plain[0]["content"]
    assert chat_messages("q", [], "en", notes="") == chat_messages("q", [], "en")
    assert notes_text("") == ""


def test_answer_mode_lists_the_notes_as_the_last_source_with_its_rule():
    notes = notes_source(NOTES, 2)
    assert is_notes(notes) and not is_notes(lease_source())
    assert (notes.n, notes.chunk_id, notes.file_id, notes.rel_path) == (2, None, None, NOTES_SOURCE_NAME)
    system = build_messages("q", [], [lease_source(), notes], "en")[0]["content"]
    assert "Source [2] is the user's own notes for this project" in system
    assert "A fact taken from the notes must cite [2], never a\n  document's number." in system
    assert f"[2] Your project notes\n{NOTES}" in system  # no location for the notes
    assert system.index("[1] lease.md") < system.index("[2] Your project notes")
    assert system.index("Source [2] is") < system.index("Sources:")
    assert "<notes>" not in system  # Answer mode: a source, not a block


def test_braces_in_notes_are_text_in_both_modes():
    answer_system = build_messages("q", [], [notes_source("Use {curly} braces", 1)], "en")
    assert "[1] Your project notes\nUse {curly} braces" in answer_system[0]["content"]
    chat_system = chat_messages("q", [], "en", notes="Use {curly} braces")[0]["content"]
    assert "<notes>\nUse {curly} braces\n</notes>" in chat_system


def test_an_answer_cites_from_the_notes_as_their_own_source(env):
    store, make, chat = env
    store.set_collection_notes(chat.collection_id, NOTES)
    llm = FakeLLM(("You moved in on 3 March [3].",))
    events = list(make(llm).ask(chat.id, "How long is the lease term?"))
    shown = next(e for e in events if e["type"] == "sources")["sources"]
    documents, notes = shown[:-1], shown[-1]
    assert (notes["n"], notes["file"], notes["file_id"]) == (len(documents) + 1, NOTES_SOURCE_NAME, None)
    assert notes["text"] == NOTES and notes["location"] == {"kind": "notes"}
    context = next(e for e in events if e["type"] == "context")
    assert context["source_count"] == len(documents)
    assert context["parts"]["notes"] > 0
    assert sum(context["parts"].values()) == context["prompt_tokens"]
    saved = store.get_message(next(e for e in events if e["type"] == "done")["message_id"])
    assert [s.text for s in saved.sources if is_notes(s)] == [NOTES]  # what the model saw


def test_search_only_and_not_found_never_use_the_notes(env):
    store, make, chat = env
    store.set_collection_notes(chat.collection_id, NOTES)
    llm = FakeLLM()
    events = list(make(llm).ask(chat.id, "How long is the lease term?", mode="search"))
    shown = next(e for e in events if e["type"] == "sources")["sources"]
    assert shown and all(s["file"] != NOTES_SOURCE_NAME for s in shown)
    events = list(make(llm).ask(chat.id, "ใครเป็นผู้จัดการฝ่ายขาย"))  # nothing in the documents
    assert next(e for e in events if e["type"] == "sources")["sources"] == []
    assert llm.calls == []  # not found: the model is not asked, whatever the notes say
```

Keep `test_chat_mode_has_the_notes_after_its_rules`, `test_a_chat_uses_only_its_home_projects_notes` and `test_a_general_chat_has_no_notes` from Task 3. In `test_a_chat_uses_only_its_home_projects_notes`, also assert `NOTES_SOURCE_NAME not in llm.calls[0][0]["content"]` after the first, Answer-mode question (its home has no notes yet).

(The not-found question is the one `test_an_unrelated_question_gets_not_found_without_the_llm` already uses.)

In `tests/test_core.py` add (import `notes_source` from `tamra.answer`):

```python
def test_a_notes_source_never_counts_as_changed(core):
    assert core.source_changed(notes_source("I am the tenant.", 3)) is False
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_answer.py tests/test_core.py -q -k "notes or cites or search_only"`
Expected: FAIL (`notes_source` does not exist).

- [ ] **Step 3: Implement**

`src/tamra/answer.py`, after `notes_text`:

```python
# Answer mode (project notes spec): the notes are the last numbered source, so a fact the model
# takes from them cites them. A rule that is "not a source" did not stop a small model from
# stating the notes' facts, and it then cited a document for them.
NOTES_SOURCE_NAME = "Your project notes"
NOTES_SOURCE_RULE = """
- Source [{n}] is the user's own notes for this project: their situation and how they want
  answers. Follow how they want answers. A fact taken from the notes must cite [{n}], never a
  document's number."""


def is_notes(source: SourceRecord) -> bool:
    """Whether a source is the project's notes rather than a passage of a document."""
    return source.location.get("kind") == "notes"


def notes_source(notes: str, n: int) -> SourceRecord:
    """The project's notes as source [n] of an Answer-mode prompt: no chunk, file or hash."""
    return SourceRecord(n, None, None, NOTES_SOURCE_NAME, notes, {"kind": "notes"}, None)
```

`_blocks`:

```python
def _blocks(sources: list[SourceRecord]) -> str:
    """The numbered sources as the system prompt lists them; the notes have no location."""
    return "\n\n".join(
        f"[{s.n}] {s.rel_path}\n{s.text}"
        if is_notes(s)
        else f"[{s.n}] {s.rel_path} ({location_label(s.location)})\n{s.text}"
        for s in sources
    )
```

`build_messages`: remove the `notes` parameter and its docstring sentence. Build the rule from the sources:

```python
    notes = next((s for s in sources if is_notes(s)), None)
    rule = NOTES_SOURCE_RULE.format(n=notes.n) if notes else ""
    system = SYSTEM_PROMPT.format(
        language=LANGUAGE_NAMES[language], sources=_blocks(sources), notes=rule
    )
```

and add to its docstring: "A notes source (`notes_source`) brings its rule."

`context_event`: keep `notes: str = ""` (Chat mode only), and count the parts as:

```python
    documents = [s for s in sources if not is_notes(s)]
    from_notes = [s for s in sources if is_notes(s)]
    source_tokens = estimate_tokens(_blocks(documents)) if documents else 0
    if from_notes:  # Answer mode: the notes source and its rule
        rule = NOTES_SOURCE_RULE.format(n=from_notes[0].n)
        notes_tokens = estimate_tokens(_blocks(from_notes)) + estimate_tokens(rule)
    else:  # Chat mode: the notes block ("" without notes)
        notes_tokens = estimate_tokens(notes_text(notes))
```

and `"source_count": len(documents),`.

`AnswerService._ask`: move the `sources` event so that Search only never shows the notes:

```python
        if mode == "search":  # passages only: no model is involved, so no notes either
            yield {"type": "sources", "sources": source_payloads(self._store, sources)}
            message_id = self._store.add_assistant_message(
                chat_id, "", provider=None, model=None, sources=sources
            )
            yield {"type": "done", "message_id": message_id}
            return
        if notes:  # the last source (project notes spec): what the model takes from them cites them
            sources = [*sources, notes_source(notes, len(sources) + 1)]
        yield {"type": "sources", "sources": source_payloads(self._store, sources)}
```

(The `skipped` notice stays before both.) Then call `build_messages(question, history, sources, language, room)` and `context_event(messages, history, sources, route.context_tokens, reply)` without `notes`. `_write` saves `sources`, the notes source included. `auto_citation` sees it too.

`src/tamra/core.py`, at the top of `source_changed`:

```python
        if is_notes(source):
            return False  # the saved notes are what the answer saw; there is no file to change
```

with `is_notes` added to the `from tamra.answer import ...` line.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_answer.py tests/test_core.py tests/test_server_attribution.py -q`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format --check src tests
git add src/tamra/answer.py src/tamra/core.py tests/test_answer.py tests/test_core.py
git commit -m "feat: in Answer mode the project notes are their own numbered source"
```

---

### Task 7: Show a notes source in the UI

**Files:**
- Modify: `ui/src/SourcePanel.tsx` (`isProjectNotes`, `shownFile`, the locate target), `ui/src/ChatView.tsx` (source cards, passages, `copyableAnswer`), `ui/src/strings/en.ts`, `ui/src/strings/th.ts`
- Test: `ui/src/ChatView.test.tsx`

**Interfaces:**
- Consumes: a source payload with `location: {"kind": "notes"}`, `file_id: null`, `file: "Your project notes"` (Task 6).
- Produces: `isProjectNotes(source: Pick<Source, "location">): boolean`; `shownFile(source: Source, withProject: boolean, notesName: string): string` (every caller passes `t("source.projectNotes")`).

- [ ] **Step 1: Write the failing test**

In `ui/src/ChatView.test.tsx`, after "copies an answer with the sources it cites, and says it did", add:

```tsx
  it("shows the project notes as their own source, with no file to open", async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    const notes = {
      n: 2,
      file: "Your project notes",
      file_id: null,
      label: "",
      text: "I am the tenant of unit 1204. I moved in on 3 March.",
      location: { kind: "notes" },
      project: null,
    };
    const calls = mockFetch({
      "GET /api/chats/5": () =>
        json({
          ...chat,
          messages: [
            {
              id: 2,
              role: "assistant",
              content: "The term is three years [1]. You moved in on 3 March [2].",
              provider: "local",
              model: "qwen",
              created_at: "t",
              sources: [source, notes],
            },
          ],
          context: null,
        }),
    });
    view = await mount(<ChatView {...base} chatId={5} />);
    const cards = [...view.container.querySelectorAll(".source-card")];
    expect(cards[1].textContent).toBe("2Your project notes"); // no location line
    await click(cards[1]);
    const panel = view.container.querySelector(".source-panel")!;
    expect(panel.querySelector(".source-title strong")?.textContent).toBe("Your project notes");
    expect(buttonByText(panel, "Open file")).toBeUndefined();
    expect(panel.textContent).toContain("I moved in on 3 March.");
    expect(calls.some((call) => call.key.includes("/locate"))).toBe(false); // no file to locate
    await click(buttonByText(view.container, "Copy"));
    expect(writeText).toHaveBeenCalledWith(
      "The term is three years [1]. You moved in on 3 March [2].\n\nSources:\n" +
        "[1] lease.pdf, p. 2\n[2] Your project notes",
    );
  });
```

(`source` is the file's fixture: `lease.pdf`, label "p. 2". If the "Open file" text differs, take it from `t("source.open")` in `strings/en.ts`.)

- [ ] **Step 2: Run it to see it fail**

Run: `npm --prefix ui test -- ChatView.test`
Expected: FAIL. The card shows "Your project notes" from `file` already, but the panel asks to locate the notes and the copy says nothing different. Check which assertion fails first.

- [ ] **Step 3: Implement**

`ui/src/SourcePanel.tsx`:

```ts
/** The project's notes as a source of an answer (project notes spec): no file, no location. */
export function isProjectNotes(source: Pick<Source, "location">): boolean {
  return source.location?.kind === "notes";
}
```

`shownFile` gets a third parameter and names the notes in the user's language:

```ts
/** A source's file name, with its project when the answer spans projects; the notes by name. */
export function shownFile(source: Source, withProject: boolean, notesName: string): string {
  if (isProjectNotes(source)) return notesName;
  const name = fileName(source.file);
  return withProject && source.project ? `${source.project} · ${name}` : name;
}
```

Pass `t("source.projectNotes")` at its three calls in `SourcePanel.tsx` (the title and the list of sources) and its two in `ChatView.tsx` (source cards, passages). In `SourcePanel`, never locate a notes source:

```ts
  const targetSource = match ? sources.find((s) => s.n === match.n) : source;
  const notesTarget = !!targetSource && isProjectNotes(targetSource);
  const target = notesTarget
    ? null
    : match
      ? { n: match.n, start: match.start, end: match.end }
      : !check && source
        ? { n: source.n, start: null, end: null }
        : null;
```

`ChatView.tsx`, `copyableAnswer`: add a `notesName: string` parameter after `heading`, and build each line's file name as

```ts
      const file = isProjectNotes(source)
        ? notesName
        : multi && source.project
          ? `${source.project} · ${source.file}`
          : source.file;
```

The call becomes `copyableAnswer(text, sources, multi, t("chat.copySources"), t("source.projectNotes"))`. Import `isProjectNotes` from `./SourcePanel`.

Strings: `"source.projectNotes": "Your project notes",` (en) and `"source.projectNotes": "โน้ตของโปรเจกต์",` (th). Change `project.notesHelp` to the spec's new wording:

- en: `"Tamra gives these notes to the model in every chat of this project, to understand your questions and shape its answers. An answer that uses them cites them as \"Your project notes\", apart from your documents."`
- th: `"Tamra ให้โมเดลอ่านโน้ตนี้ในทุกแชทของโปรเจกต์ เพื่อเข้าใจคำถามและปรับวิธีตอบ ถ้าคำตอบใช้ข้อมูลจากโน้ต จะอ้างอิงเป็น \"โน้ตของโปรเจกต์\" แยกจากเอกสารของคุณ"`

- [ ] **Step 4: Run the tests to see them pass**

Run: `npx --prefix ui tsc -p ui --noEmit` and `npm --prefix ui test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat: show the project notes as their own source, with no file to open"
```

---

### Task 8: Check the whole feature, with a real model

**Files:**
- Modify: `docs/superpowers/specs/2026-10-24-project-notes-design.md` (status line)

- [ ] **Step 1: Run everything**

Run: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run pytest -q`, `npx --prefix ui tsc -p ui --noEmit`, `npm --prefix ui test`
Expected: all pass.

- [ ] **Step 2: Try it in the app**

Start the dev servers (`tamra-core-dev` and `tamra-ui-dev` in `.claude/launch.json`) and open `http://localhost:5174/#token=dev`. On a project with the lease documents:
1. Open the card's "Add notes" link. The dialog opens with the Notes field focused. Write `Answer as a numbered list.` and `I am the tenant of unit 1204. I moved in on 3 March.` (two lines), and save. The card now says "Notes".
2. Use a 4B+ local model, `qwen3.5:4b` (the dev data's API mode would spend the owner's API credit). Ask in Answer mode: "How long is the lease term?" The reply cites the lease, `[1]`. Context → Details shows a "Project notes" row.
3. Ask: "When did I move in?" several times (8 in the experiment). 3 March must never cite a document: when the reply gives 3 March, it cites the notes source, and its card says "Your project notes". Click that card: the panel shows the notes text and no "Open file". Select the sentence and use "Check source": it matches the notes, with no "document changed" warning.
4. Clear the notes and save. The card says "Add notes" again, and the next question has no notes source and no notes row.

Write down what the model replied in steps 2 and 3, and how often it followed the list style, for the PR description. Then set the dev data's model back to what it was.

- [ ] **Step 3: Mark the spec implemented and commit**

Change the spec's status line to `Status: implemented (plan docs/superpowers/plans/2026-10-24-project-notes.md). Date: 2026-10-24.`

```bash
git add docs/superpowers/specs/2026-10-24-project-notes-design.md
git commit -m "docs: mark project notes implemented"
```
