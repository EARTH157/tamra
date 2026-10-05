# Tamra M4 — Projects (multiple collections) and chat history Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The user keeps several **projects**. A project is a name plus one folder of documents, with its own search index. Chats are grouped under their project in the sidebar. A chat searches its own project by default, and can also search other projects. In the roadmap's words: "Create two collections and query either or both". Projects can be created, renamed, re-pointed to a new folder, rebuilt and deleted. Chats can be moved between projects.

**Architecture:**
- The data stays one SQLite file, with schema v3.
- `collections` already exists, and the `chunk_vectors` vec0 table already partitions by `collection_id`. M4 lifts the "exactly one collection" assumption everywhere:
  - store methods for many collections;
  - a home project per chat (`chats.collection_id`);
  - the chat's search scope in `chat_collections`;
  - one indexer worker that reconciles every project in turn;
  - one folder watcher per project;
  - retrieval that fuses hits across the scope;
  - viewer and attribution paths resolved through each file's own collection.
- The UI swaps the single folder card for a project tree (frames "0 · First run — no projects", "1c", "Menu · Project", "Menu · Chat", "Dialog · New project", "Dialog · Change folder"). It adds a "Search in" chip to the composer.

**Tech Stack:** M3's stack. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-02-tamra-design.md`:
- §4: `collections`, `chats`, `chat_collections`.
- §5: "A collection is a name plus a folder", the watcher, reconcile, the embedding guard.
- §6: "Each chat selects one or more collections. The selection can change mid-chat."
- The roadmap row M4.

The design frames are in `docs/design/`. In the UI, "Project" is the user-facing name for a collection; the code keeps `collection`.

**Controller decisions for M4 (2026-10-06), under the user's "choose what you think is best":**
- **Home project and search scope.** A chat has one *home* project, which sets where it is listed in the sidebar (the frames). It also has a *search scope*: one or more projects, defaulting to the home project. The scope is chosen with a "Search in" chip in the composer, which the spec requires and the frames omit. "Move to project" changes the home project. If the old home was the only project in scope, the scope follows the move.
- **Deleting a project** deletes its index and its chats, after a confirmation that names the chat count. The user's folder is untouched. Other chats that also searched the deleted project lose it from their scope; a chat left with an empty scope falls back to its home project.
- **One indexer worker for all projects** (spec §5, "single background worker queue"). Reconcile runs project by project, then pending files are indexed oldest first across projects. Status is reported per project.
- **"Export with proof"** (Menu · Chat) and the suggestion cards are later ideas. They are not shown in M4.
- **The General tab's "Documents folder" and "Search index" rows move to a per-project "Project settings" dialog.** Its fields are name, folder (Change folder), index status, and Rebuild index. General keeps language, ask-before-delete, data folder and chat history.

## Global Constraints

- **Platform and tooling:** Windows 10/11 x64. Python `>=3.12,<3.13` through uv. Never run `uv python install`, and never delete or recreate `.venv`.
- **Licence:** Apache-2.0. No new dependencies, Python or npm.
- **Storage:**
  - One SQLite file, and `tamra.store` is the only module that runs SQL.
  - The schema moves from v2 to v3 through `migrate()` in one transaction. A v2 database from M3 upgrades in place and keeps its data: its one collection becomes the first project, and every existing chat gets that project as home and scope.
  - A file's chunk replacement stays in one transaction.
- **Embedding:** it is always local, with one fixed model. Each collection records its `embedding_model_id`. A stale project is never searched: it is left out of the scope with a notice, never silently.
- **User folders:** never modified. Every file path is resolved from **its own collection's** `folder_path` plus `files.rel_path`, through `tamra.viewer.resolve_file`.
- **API:**
  - All routes are under `/api/` and token-gated.
  - Fixed error details, with no raw paths or exception text.
  - Pydantic bodies.
  - The 422 handler stays as it is.
- **UI:**
  - Strings go through `useT()` in en and th, with natural Thai.
  - Theme tokens only.
  - Reuse `Modal`, `ConfirmDialog`, `PopMenu`, `Setup` (the folder dialog) and the existing chat-row menu.
  - Accessible menus: roles, keyboard, Esc.
- **Language:** code, comments, docs and commit messages are in English.
- **Before every commit:**
  - Run `uv run ruff format`, `uv run ruff check --fix` and `uv run ruff format --check`, then the touched tests, then `uv run pytest` once.
  - UI tasks also run `npm --prefix ui test` and `npm --prefix ui run build`.
- **Processes and staging:**
  - Never kill processes by image name.
  - Never commit `AGENTS.md` or `.claude/`. Stage explicit paths only.
  - Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Tests:** `fastapi.testclient.TestClient` uses `base_url="http://127.0.0.1"`.

---

## Task 1: Store — schema v3 and many collections

**Files:** `src/tamra/store/schema.py`, `src/tamra/store/repo.py`, `src/tamra/store/models.py`, and the tests `tests/test_store*.py` (add `tests/test_store_projects.py`).

**Produces:**
- **Schema v3:**
  - `chats` gains `collection_id INTEGER REFERENCES collections(id) ON DELETE CASCADE`. It is the home project; it is NULL only for chats with no project yet.
  - `chat_collections(chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE, collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE, PRIMARY KEY (chat_id, collection_id))`.
  - `collections.name` is UNIQUE, compared case-insensitively (`COLLATE NOCASE`). If adding the constraint needs a table rebuild in SQLite, do it inside the migration transaction and keep the ids.
  - `migrate()` from v2: give the single existing collection to every chat as its home and scope.
- **`FileRecord`** gains `collection_id`.
- **`Chat`** gains `collection_id: int | None` and `scope: list[int]`. List and get both fill them.
- **Store methods:**
  - `list_collections() -> list[Collection]`, ordered by name with NOCASE.
  - `get_collection(collection_id: int) -> Collection | None`. This replaces the no-argument form; update every caller.
  - `create_collection(name, folder_path, model_id) -> Collection`. Raises `ValueError("name taken")` on a duplicate name.
  - `rename_collection(collection_id, name) -> bool`. Duplicate names raise `ValueError`.
  - `set_collection_folder(collection_id, folder_path, model_id)`: one transaction that drops the project's whole index (its files, chunks and vectors) and points it at the new folder.
  - `delete_collection(collection_id) -> int`: deletes the project, its files, chunks and `chunk_vectors` rows (vec0 has no FK cascade, so delete by `collection_id` explicitly), and its home chats; returns the number of chats deleted. Other chats' `chat_collections` rows go by cascade. A chat left with an empty scope gets its home re-added.
  - `next_pending_file_any() -> FileRecord | None`: the oldest pending file across all non-stale collections.
  - `create_chat(title="", collection_id: int | None = None)`: the scope is `[collection_id]` when it is given.
  - `move_chat(chat_id, collection_id) -> bool`: if the old home was the only project in scope, the scope becomes `[new]`; otherwise the new home is added to the scope.
  - `set_chat_scope(chat_id, collection_ids: list[int]) -> bool`: the list must be non-empty and every id must exist, else `ValueError`.
  - `search_dense(collection_ids: Sequence[int], vector, k)` and `search_keyword(collection_ids: Sequence[int], fts_query, k)` take a list.
    - Dense: run one vec0 KNN per collection (the partition key needs an equality), then merge by distance and keep the best `k`.
    - Keyword: use `IN (...)`.
- **Removed:** `replace_collection`. Update `reset_index`, `status_counts` and `problem_files` callers to pass the id.

**Tests:**
- A v2 database built with the M3 DDL, with one collection and two chats, upgrades to v3: the chats have that home and scope, and their messages are kept.
- create, list, rename (with the duplicate-name case), set folder (the old files, chunks and vectors are gone), and delete (count, cascade, scope fallback).
- `move_chat` both ways, and `set_chat_scope` validation.
- Dense and keyword search across two collections returns hits from both, and from one only when scoped to one.
- `next_pending_file_any` skips a collection whose `embedding_model_id` differs from the given model.

## Task 2: Core — indexing and watching every project

**Files:**
- `src/tamra/ingest/indexer.py`, `src/tamra/ingest/watcher.py`, `src/tamra/core.py`, `src/tamra/viewer.py` (only if needed), and `src/tamra/selfcheck.py` (only if it uses `get_collection()`).
- Tests: `tests/test_indexer.py`, `tests/test_watcher.py`, `tests/test_core.py`.

**Consumes:** Task 1's store methods.

**Produces:**
- `Indexer` works on all projects:
  - `request_reconcile(collection_id | None)`: None means all projects.
  - `request_rebuild(collection_id)`.
  - One worker thread. Each pass reconciles the requested projects, then indexes pending files with `next_pending_file_any` until none remain or a stop is requested.
  - `scan_folder` skips directory junctions and symlinked directories and files (the deferred M3 item), with a test using `_winapi.CreateJunction`.
  - `state(collection_id) -> IndexerState`, giving the per-project progress (current file and error).
- `FolderWatcher` watches several folders:
  - `watch(collection_id, folder)` and `unwatch(collection_id)`;
  - events debounce per project into `request_reconcile(collection_id)`.
- `Core` project operations:
  - `projects() -> list[dict]`, where each item is `{id, name, folder_path, counts, stale, current, error, problems}`;
  - `create_project(name, folder) -> Collection`: validates that the folder exists, starts the watcher and requests a reconcile;
  - `rename_project(id, name)`;
  - `change_project_folder(id, folder)`: drops the index, re-watches and reconciles;
  - `rebuild_project(id)`;
  - `delete_project(id) -> int`: unwatches, deletes through the store, and drops any `Core.document` cache entries for its files; returns the chats deleted;
  - `Core.start` watches every project.
- `Core.file_path(file_id)` resolves against the file's **own** collection, through `FileRecord.collection_id` (the M3 "Not done" note).
- The single-collection API (`Core.set_collection`, `Core.index_status`) is removed.

**Tests:**
- Two projects in two temp folders, both indexed by one worker.
- A file added to one folder reconciles only that project.
- Rebuilding one project leaves the other indexed.
- Deleting a project stops its watcher, and its files leave the store.
- Changing a folder re-indexes from the new folder.
- `file_path` for a file in project B resolves under B's folder.
- A junction inside a folder is skipped.

## Task 3: Answers over a chat's scope

**Files:** `src/tamra/answer.py`, `src/tamra/retriever.py`, `src/tamra/core.py` (wiring), and the tests `tests/test_answer.py` and `tests/test_retriever.py`.

**Consumes:** Task 1's `Chat.scope` and list-based search. Task 2's per-project stale state.

**Produces:**
- `hybrid_search(store, collection_ids: Sequence[int], ...)`: the same RRF over the merged dense and keyword lists.
- `AnswerService.ask` reads the chat's scope:
  - Projects whose `embedding_model_id` is stale are left out. If any were left out, a `notice` event `{type: "notice", code: "stale_projects", names: [...]}` is sent before the sources.
  - If no project in scope has indexed files, it gives the existing "No documents are indexed yet" error.
  - The not-found gate, budget, citations and saving are unchanged.
- Source payloads gain `project` (the collection name), so the UI can label cards when more than one project is in scope.

**Tests:**
- A chat scoped to A finds only A's passage.
- A chat scoped to A and B can cite both in one answer (with a fake LLM that cites [1] and [2]).
- A stale B is skipped with the notice.
- An empty scope falls back to the home project.

## Task 4: API routes for projects, chat homes and scope

**Files:** `src/tamra/server.py`, `src/tamra/app.py` (only if the pickers change), and the tests `tests/test_server_projects.py` (new) plus the existing server tests.

**Consumes:** Tasks 1–3.

**Produces:**
- **Projects:**
  - `GET /api/projects` returns `{projects: [{id, name, folder_path, index: {counts, stale, current, error, problems}}]}`.
  - `POST /api/projects` with `{name, folder_path}` returns 201 with the project. Errors:
    - 400 for a missing folder;
    - 409 for a duplicate name;
    - 400 for a blank name over 80 chars, after a strip.
  - `PATCH /api/projects/{id}` with `{name?}` returns the project; 404 if missing, 409 on a duplicate.
  - `PUT /api/projects/{id}/folder` with `{folder_path}` returns the project.
  - `POST /api/projects/{id}/rebuild` gives 202.
  - `DELETE /api/projects/{id}` gives 200 `{chats_deleted}`, or 409 while an answer is streaming (use `run_if_idle`).
- **Chats:**
  - `GET /api/chats` items gain `project_id` and `scope`.
  - `POST /api/chats` takes `{project_id?}` and returns 201.
  - `PATCH /api/chats/{id}` accepts `{title?, project_id?}`; the project move uses `move_chat`.
  - `PUT /api/chats/{id}/scope` with `{project_ids: [..]}` returns the chat; 400 for an empty list or unknown ids.
  - `GET /api/chats/{id}` includes `project_id` and `scope`.
- **Removed:** `/api/collection` and `/api/collection/rebuild`. The UI moves to the project routes. Note the removal in the task report.
- Every new route is tested, including the 401 without a token.

## Task 5: UI — project tree, menus and dialogs

**Files:**
- New: `ui/src/ProjectTree.tsx`, `ui/src/ProjectDialog.tsx` (New project and Project settings), `ui/src/projects.ts` (a `useProjects` hook with polling while any project is indexing).
- Changed: `ui/src/App.tsx`, `ui/src/ChatList.tsx` (merge into, or reuse in, the tree), `ui/src/Welcome.tsx` (frame 0), `ui/src/IndexStatus.tsx` (now per project, shown as the bottom card for the active project), `ui/src/SettingsGeneral.tsx` (remove the Documents rows), `ui/src/api.ts`, `ui/src/types.ts`, the strings, `ui/src/styles.css`.
- Tests.

**Consumes:** Task 4's routes.

**Produces:**
- **Sidebar** (frames 0 and 1c), under the heading "PROJECTS" with a `+` button:
  - Each project row has a chevron to expand or collapse it, a folder icon, the name, an indexed file count and a `⋯` menu.
  - The project's chats sit under it.
  - The active chat's project is expanded. Expansion state is kept in `localStorage`, guarded with try/catch.
  - There is no flat "CHATS" list any more.
- **Empty state:** with no projects, the sidebar shows "No projects yet. Add a folder of documents to start." and the main area shows the first-run card from frame 0. "Choose a folder" opens New project.
- **Project menu** (Menu · Project): Project settings, Rename (inline, like chat rename), Change folder (reuses the `Setup` dialog), Rebuild index, and Delete project. Delete needs a `ConfirmDialog` that names the chat count and says the folder is not touched.
- **Chat menu** (Menu · Chat):
  - Rename.
  - "Move to project ›", a submenu of the other projects that is keyboard reachable.
  - Delete chat.
  - "Export with proof" is not shown.
- **New project dialog** (frame): name, a folder with Browse… (the existing pick-folder route) or a typed path, a hint, Cancel and Create project. Server errors show inline: 409 gives "A project with this name already exists.", and a bad folder gives "That folder does not exist."
- **Project settings dialog:** name, folder with Change folder, index status (the existing IndexStatus content) and Rebuild index.
- **New chat:** "New chat" creates a chat in the active project, which is the active chat's home, else the first project. It is disabled with no projects.
- **Bottom card** (frame 1c): the active project's name, folder, "N files indexed", and a Change folder link.

**Tests:**
- Tree rendering and expansion.
- Each menu action calls the right route.
- New project success and its 409 case.
- Delete confirm with the chat count.
- Move to project.
- The first-run empty state.
- The active-project bottom card.

## Task 6: UI — "Search in" scope chip and per-project labels

**Files:** `ui/src/Composer.tsx` (or ChatView), `ui/src/ScopeMenu.tsx` (new), `ui/src/SourcePanel.tsx` and the source cards (the project label), strings, styles, tests.

**Consumes:** Task 4's `PUT /api/chats/{id}/scope` and chat `scope`. Task 3's source `project` and the `notice` event.

**Produces:**
- **The "Search in" chip** sits next to the mode chip. It shows the home project's name, or "Name + 1" when the scope is wider.
  - It opens a menu with a checkbox per project, home first and marked "home". At least one must stay checked.
  - A change calls the scope route; on a new chat with no id yet, the choice is kept locally and sent after the chat is created.
  - It is disabled while answering.
- **Source cards and the source panel** show the project name as a prefix ("HR Documents · file.pdf") only when the answer's sources span more than one project.
- **The `stale_projects` notice** shows a dismissible line: "Skipped {names}: the index must be rebuilt."

**Tests:** the chip label, the toggling calls, the last box cannot be unchecked, project labels only for multi-project answers, and the notice.

## Task 7: Packaging, exit check, and docs

**Files:** `scripts/exe_smoke.py`, `README.md`, `docs/spikes/2026-10-m4-results.md` (new), and the spec §4–§6 wording, where M4 changes it.

**Work:**
- **exe_smoke:** build the eval corpus into two folders and create two projects through the API. Use "Company" with `company-history.txt`, `hr-leave-policy.docx` and `it-security-policy.md`, and "Contracts" with `apartment-lease.pdf`, `shipping-policy.pdf` and `furniture-warranty.txt`. Then check:
  - a chat in Company asks the lease question and gets not-found, or no lease source;
  - after `PUT scope` to Company + Contracts, the same question cites `apartment-lease.pdf`;
  - moving the chat to Contracts lists it under Contracts in `GET /api/chats`;
  - renaming a project works, and so does a rebuild of one project;
  - deleting Contracts returns `chats_deleted`.

  Record everything.
- **Build and run:** build with `./scripts/build.ps1`, then run the smoke and the selfcheck.
- **Results doc and README:** fill the results doc, including "Not done in M4". Write a README "Projects" section.
- **Exit criterion:**
  - The packaged smoke passes.
  - The controller does the browser check: create two projects, query each and both, then move and delete.
  - The real-window check is the user's.

## After M4

M5 is OCR: an engine spike, then integration (spec §10).
