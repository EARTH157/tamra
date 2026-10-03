# Tamra M1 — Core Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In `Tamra.exe`, pick a folder of mixed Thai/English/Chinese documents, ask a question, and get an answer with `[n]` citations whose source passages can be opened.

**Architecture:** The `tamra` package gains a storage package (`tamra.store`, the only code that runs SQL), an ingestion package (`tamra.ingest`: parsers, chunker, folder watcher, background indexer), a hybrid `retriever`, an `answer` service that streams cited answers from a locally managed `llama-server`, and a `Core` object that owns their lifecycle. FastAPI routes expose them to the React UI over JSON and Server-Sent Events, behind a host allowlist and a token gate. A retrieval eval measures hit@k and sets the "not found" threshold.

**Tech Stack:** Python 3.12 (uv), FastAPI, SQLite + sqlite-vec + FTS5 trigram, ONNX Runtime (bge-m3), llama.cpp `llama-server`, pypdfium2, python-docx, charset-normalizer, watchdog; React 19 + TypeScript 7 + Vite 8 + Vitest 5.

**Spec:** `docs/superpowers/specs/2026-10-02-tamra-design.md` (§3–§6, §12, §13 M1). Roadmap and decisions: `docs/superpowers/specs/2026-10-03-tamra-v1-roadmap-design.md`. M0 results, risks, and the deferred findings this plan absorbs: `docs/spikes/2026-10-m0-results.md`.

## Global Constraints

- Platform: Windows 10/11 x64 only. Python `>=3.12,<3.13` through uv; the repo `.venv` uses a uv-managed CPython 3.12. Never run `uv python install`, never delete or recreate `.venv`.
- License: Apache-2.0. Every bundled dependency must be Apache-2.0-compatible; no AGPL, GPL, or LGPL libraries. New runtime dependencies in this plan, and only these: `pypdfium2` (BSD-3-Clause / Apache-2.0), `python-docx` (MIT; brings `lxml`, BSD-3-Clause), `charset-normalizer` (MIT), `watchdog` (Apache-2.0). No new npm packages.
- Storage is one SQLite file, `data_dir()/tamra.db`. `tamra.store` is the only module that executes SQL. A file's chunk replacement happens in one transaction.
- Embedding is always local and uses one fixed model. Each collection records `embedding_model_id` (`tamra.embedder.MODEL_ID`). A collection whose id differs is stale: it is never indexed or searched until it is rebuilt.
- Local LLM = the pinned `llama-server.exe`, run as a child process and called through `OpenAICompatibleLLM`. Every LLM provider exposes `generate(messages, max_tokens) -> Iterator[str]`. No Python llama.cpp bindings.
- The API binds `127.0.0.1` only. Every `/api` request (HTTP and WebSocket) must carry `X-Tamra-Token`. Requests whose `Host` is not `127.0.0.1` or `localhost` are rejected.
- Every loopback HTTP call bypasses proxy settings (`trust_env=False`).
- User data lives under `%LOCALAPPDATA%\Tamra\` (override `TAMRA_DATA_DIR`); models live in `models_dir()` (Task 11). User document folders are never modified.
- Chunking: about 450 bge-m3 tokens per chunk with about 15% overlap (68 tokens). Retrieval: dense top 30 plus keyword top 30, fused with Reciprocal Rank Fusion (k = 60); the local model receives the top 4 chunks.
- Answers are written in the question's language. Code, comments, docs, UI strings, and commit messages are in English.
- Tests that need the downloaded models or binaries are marked `@pytest.mark.assets` (skipped by default; CI runs only unmarked tests). The assets are present on the dev machine (`.models/`, `vendor/llama/`).
- Before every commit: `uv run ruff format`, `uv run ruff check --fix` (it also fixes import order, including the test helpers `docgen` and `fakes`), `uv run ruff format --check`, the touched test files, then `uv run pytest` once. UI tasks also run `npm --prefix ui test` and `npm --prefix ui run build`. Formatting-only differences from the code in this plan are fine.
- From Task 12 on, `fastapi.testclient.TestClient` must be created with `base_url="http://127.0.0.1"` (the host allowlist rejects the default `testserver`).
- Never kill processes by image name; stop only processes you started, by PID.

## File Structure

```
src/tamra/store/__init__.py        public API of the storage package
src/tamra/store/db.py              connect(), capabilities()            (moved from store.py)
src/tamra/store/schema.py          SCHEMA_VERSION, migrate()            (DDL, PRAGMA user_version)
src/tamra/store/models.py          frozen dataclasses (Collection, FileRecord, ChunkInput, ...)
src/tamra/store/repo.py            Store: collections, files, chunks, search, chats, messages
src/tamra/ingest/__init__.py       (empty)
src/tamra/ingest/parsers.py        parse_file() for PDF/DOCX/TXT/MD -> ParsedDoc of Units; location()
src/tamra/ingest/chunker.py        chunk_document(), bge_token_spans()
src/tamra/ingest/indexer.py        scan_folder(), Indexer (reconcile + single background worker)
src/tamra/ingest/watcher.py        FolderWatcher (watchdog, debounced)
src/tamra/retriever.py             query_text(), fts_query(), hybrid_search(), best_similarity()
src/tamra/answer.py                detect_language(), location_label(), build_messages(), AnswerService
src/tamra/winjob.py                KillOnCloseJob (Windows Job Object)
src/tamra/llm/llama_server.py      + api_key, Job Object, alive(), shared timeout budget
src/tamra/llm/runtime.py           LocalLLM (lazy start/restart of llama-server)
src/tamra/core.py                  Core: store + indexer + watcher + answers + LLM lifecycle
src/tamra/logs.py                  setup_logging()
src/tamra/paths.py                 + models_dir(), safer data_dir()
src/tamra/server.py                routes + TrustedHost + TokenGate + SecurityHeaders
src/tamra/app.py                   run(): logging, Core, window with folder picker, clean shutdown
src/tamra/embedder.py              + MODEL_ID, file checks, injectable parts
src/tamra/selfcheck.py             probes go through Store (no SQL here any more)
tests/fakes.py                     FakeEmbedder, fake_spans, FakeLLM (test doubles)
tests/docgen.py                    make_pdf(), make_docx() fixtures (Windows fonts)
tests/test_store_repo.py, test_store_chats.py, test_parsers.py, test_chunker.py, test_indexer.py,
tests/test_watcher.py, test_retriever.py, test_winjob.py, test_runtime.py, test_answer.py,
tests/test_core.py, test_api.py, test_logs.py, test_app.py, test_eval.py
eval/corpus/*                      synthetic TH/EN/ZH documents (md/txt + json specs for docx/pdf)
eval/questions.jsonl               28 questions with expected file/page (4 off-topic)
scripts/build_eval_corpus.py       renders eval/corpus into a folder of real documents
scripts/eval_retrieval.py          hit@k and the not-found threshold sweep
scripts/exe_smoke.py               end-to-end check of the packaged exe through its API
ui/src/types.ts, api.ts, sse.ts, citations.ts, App.tsx, Setup.tsx, IndexStatus.tsx,
ui/src/ChatList.tsx, ChatView.tsx, AnswerText.tsx, styles.css, main.tsx (+ *.test.ts[x], test-utils.ts)
docs/spikes/2026-10-m1-results.md  eval numbers and the exit-criterion run
```

---

### Task 1: Storage package — schema, collections, files, chunks, search

**Files:**
- Move: `src/tamra/store.py` → `src/tamra/store/db.py` (with `git mv`, then edit)
- Create: `src/tamra/store/__init__.py`, `src/tamra/store/schema.py`, `src/tamra/store/models.py`, `src/tamra/store/repo.py`
- Modify: `tests/test_store.py` (add two tests; keep the M0 tests)
- Test: `tests/test_store_repo.py`

**Interfaces:**
- Consumes: nothing new (`sqlite_vec`, `numpy`).
- Produces (all importable from `tamra.store`):
  - `connect(path) -> sqlite3.Connection`, `capabilities(conn) -> dict` (unchanged names; `selfcheck.py` keeps working).
  - Dataclasses: `Collection(id, name, folder_path, embedding_model_id, created_at)`, `FileRecord(id, rel_path, size, mtime, content_hash, status, error, indexed_at)`, `ChunkInput(text, location)`, `ChunkRecord(id, file_id, rel_path, ord, text, location, file_hash)`, `Chat(id, title, created_at, updated_at)`, `SourceRecord(n, chunk_id, file_id, rel_path, text, location, file_hash)`, `MessageRecord(id, role, content, provider, model, created_at, sources)`.
  - `FILE_STATUSES = ("pending", "indexing", "indexed", "failed", "skipped")`.
  - `Store.open(path) -> Store`, `Store.close()`, `get_collection() -> Collection | None`, `replace_collection(name, folder_path, model_id) -> Collection`, `reset_index(collection_id, model_id)`, `list_files(collection_id) -> list[FileRecord]`, `next_pending_file(collection_id) -> FileRecord | None`, `add_file(collection_id, rel_path, size, mtime) -> int`, `update_file_stat(file_id, size, mtime, *, pending: bool)`, `set_file_status(file_id, status, error=None)`, `delete_file(file_id)`, `status_counts(collection_id) -> dict[str, int]`, `problem_files(collection_id, limit=50) -> list[FileRecord]`, `replace_file_chunks(file_id, chunks, vectors, *, content_hash, note)` (raises `LookupError` if the file row is gone), `get_chunks(ids) -> list[ChunkRecord]` (requested order), `search_dense(collection_id, vector, k) -> list[tuple[int, float]]` (chunk id, cosine similarity, best first), `search_keyword(collection_id, fts_query, k) -> list[int]` (chunk ids, BM25 order).
  - `location` values are plain dicts stored as JSON; their shapes are defined in Task 3.

- [ ] **Step 1: Move the M0 module into the package**

```powershell
New-Item -ItemType Directory -Force src/tamra/store | Out-Null
git mv src/tamra/store.py src/tamra/store/db.py
```

Replace `src/tamra/store/db.py` with (closes the connection when setup fails, and probes trigram support on a private connection so it is safe on a shared one):

```python
import sqlite3
from pathlib import Path

import sqlite_vec


def connect(path: Path | str) -> sqlite3.Connection:
    """Open SQLite with sqlite-vec loaded, WAL journaling, and foreign keys enforced."""
    conn = sqlite3.connect(path, check_same_thread=False)
    try:
        conn.enable_load_extension(True)
        try:
            sqlite_vec.load(conn)
        finally:
            conn.enable_load_extension(False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
    except BaseException:
        conn.close()
        raise
    return conn


def capabilities(conn: sqlite3.Connection) -> dict[str, str | bool]:
    """SQLite and sqlite-vec versions, and whether the FTS5 trigram tokenizer exists."""
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        trigram = True
    except sqlite3.OperationalError:
        trigram = False
    finally:
        probe.close()
    return {
        "sqlite_version": conn.execute("SELECT sqlite_version()").fetchone()[0],
        "vec_version": conn.execute("SELECT vec_version()").fetchone()[0],
        "fts5_trigram": trigram,
    }
```

`src/tamra/store/models.py`:

```python
from dataclasses import dataclass
from typing import Any

Location = dict[str, Any]


@dataclass(frozen=True)
class Collection:
    id: int
    name: str
    folder_path: str
    embedding_model_id: str
    created_at: str


@dataclass(frozen=True)
class FileRecord:
    id: int
    rel_path: str
    size: int
    mtime: float
    content_hash: str | None
    status: str
    error: str | None
    indexed_at: str | None


@dataclass(frozen=True)
class ChunkInput:
    text: str
    location: Location


@dataclass(frozen=True)
class ChunkRecord:
    id: int
    file_id: int
    rel_path: str
    ord: int
    text: str
    location: Location
    file_hash: str | None


@dataclass(frozen=True)
class Chat:
    id: int
    title: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SourceRecord:
    n: int
    chunk_id: int | None
    file_id: int | None
    rel_path: str
    text: str
    location: Location
    file_hash: str | None


@dataclass(frozen=True)
class MessageRecord:
    id: int
    role: str
    content: str
    provider: str | None
    model: str | None
    created_at: str
    sources: tuple[SourceRecord, ...] = ()
```

`src/tamra/store/schema.py` (spec §4; `message_sources.rel_path` is an addition so old chats can name a file that was deleted later; `chat_collections`, `settings`, and `pdf_char_boxes` arrive with the milestones that use them):

```python
import sqlite3

SCHEMA_VERSION = 1

_V1 = """
CREATE TABLE collections (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    folder_path TEXT NOT NULL,
    embedding_model_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    content_hash TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('pending', 'indexing', 'indexed', 'failed', 'skipped')),
    error TEXT,
    indexed_at TEXT,
    UNIQUE (collection_id, rel_path)
);
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL,
    text TEXT NOT NULL,
    location_json TEXT NOT NULL
);
CREATE INDEX chunks_by_file ON chunks(file_id);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    text, content='chunks', content_rowid='id', tokenize='trigram'
);
CREATE VIRTUAL TABLE chunk_vectors USING vec0(
    collection_id integer partition key,
    embedding float[1024] distance_metric=cosine
);
CREATE TRIGGER chunks_after_insert AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts (rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER chunks_after_delete AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts (chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
    DELETE FROM chunk_vectors WHERE rowid = old.id;
END;
CREATE TABLE chats (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    activity INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX messages_by_chat ON messages(chat_id, id);
CREATE TABLE message_sources (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    n INTEGER NOT NULL,
    chunk_id INTEGER,
    file_id INTEGER,
    rel_path TEXT NOT NULL,
    text_snapshot TEXT NOT NULL,
    location_json TEXT NOT NULL,
    file_hash_at_answer TEXT,
    PRIMARY KEY (message_id, n)
);
"""


def migrate(conn: sqlite3.Connection) -> None:
    """Bring the schema to SCHEMA_VERSION. Each step runs in one transaction."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema {version} is newer than this version of Tamra ({SCHEMA_VERSION})"
        )
    if version < 1:
        try:
            conn.executescript("BEGIN;\n" + _V1 + "\nPRAGMA user_version = 1;\nCOMMIT;")
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
```

`src/tamra/store/repo.py`:

```python
"""Store: one SQLite connection shared by API threads and the indexer, serialized by a lock."""

import json
import sqlite3
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from tamra.store.db import capabilities, connect
from tamra.store.models import ChunkInput, ChunkRecord, Collection, FileRecord
from tamra.store.schema import migrate

FILE_STATUSES = ("pending", "indexing", "indexed", "failed", "skipped")
_FILE_COLUMNS = "id, rel_path, size, mtime, content_hash, status, error, indexed_at"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


class Store:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.RLock()

    @classmethod
    def open(cls, path: Path | str) -> "Store":
        """Open (creating if needed) the database at path and bring its schema up to date."""
        conn = connect(path)
        try:
            migrate(conn)
        except BaseException:
            conn.close()
            raise
        return cls(conn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def capabilities(self) -> dict[str, str | bool]:
        with self._lock:
            return capabilities(self._conn)

    # Collections ----------------------------------------------------------------------------

    def get_collection(self) -> Collection | None:
        """The collection (M1 keeps exactly one), or None before a folder is chosen."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, folder_path, embedding_model_id, created_at"
                " FROM collections ORDER BY id LIMIT 1"
            ).fetchone()
        return Collection(*row) if row else None

    def replace_collection(self, name: str, folder_path: str, model_id: str) -> Collection:
        """Drop the existing collection and its whole index, then create this one."""
        created = _now()
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM collections")
            cursor = self._conn.execute(
                "INSERT INTO collections (name, folder_path, embedding_model_id, created_at)"
                " VALUES (?, ?, ?, ?)",
                (name, folder_path, model_id, created),
            )
        return Collection(cursor.lastrowid, name, folder_path, model_id, created)

    def reset_index(self, collection_id: int, model_id: str) -> None:
        """Rebuild: drop every chunk, mark every file pending, and record the current model."""
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM chunks"
                " WHERE file_id IN (SELECT id FROM files WHERE collection_id = ?)",
                (collection_id,),
            )
            self._conn.execute(
                "UPDATE files SET status = 'pending', error = NULL, content_hash = NULL,"
                " indexed_at = NULL WHERE collection_id = ?",
                (collection_id,),
            )
            self._conn.execute(
                "UPDATE collections SET embedding_model_id = ? WHERE id = ?",
                (model_id, collection_id),
            )

    # Files ----------------------------------------------------------------------------------

    def list_files(self, collection_id: int) -> list[FileRecord]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_FILE_COLUMNS} FROM files WHERE collection_id = ? ORDER BY rel_path",
                (collection_id,),
            ).fetchall()
        return [FileRecord(*row) for row in rows]

    def next_pending_file(self, collection_id: int) -> FileRecord | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_FILE_COLUMNS} FROM files"
                " WHERE collection_id = ? AND status = 'pending' ORDER BY id LIMIT 1",
                (collection_id,),
            ).fetchone()
        return FileRecord(*row) if row else None

    def add_file(self, collection_id: int, rel_path: str, size: int, mtime: float) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO files (collection_id, rel_path, size, mtime, status)"
                " VALUES (?, ?, ?, ?, 'pending')",
                (collection_id, rel_path, size, mtime),
            )
        return cursor.lastrowid

    def update_file_stat(self, file_id: int, size: int, mtime: float, *, pending: bool) -> None:
        """Record a new size/mtime; with pending=True the file is queued for indexing again."""
        with self._lock, self._conn:
            if pending:
                self._conn.execute(
                    "UPDATE files SET size = ?, mtime = ?, status = 'pending', error = NULL"
                    " WHERE id = ?",
                    (size, mtime, file_id),
                )
            else:
                self._conn.execute(
                    "UPDATE files SET size = ?, mtime = ? WHERE id = ?", (size, mtime, file_id)
                )

    def set_file_status(self, file_id: int, status: str, error: str | None = None) -> None:
        if status not in FILE_STATUSES:
            raise ValueError(f"unknown file status: {status}")
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE files SET status = ?, error = ? WHERE id = ?", (status, error, file_id)
            )

    def delete_file(self, file_id: int) -> None:
        """Remove a file; its chunks, keyword entries, and vectors go with it."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM files WHERE id = ?", (file_id,))

    def status_counts(self, collection_id: int) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, count(*) FROM files WHERE collection_id = ? GROUP BY status",
                (collection_id,),
            ).fetchall()
        counts = dict.fromkeys(FILE_STATUSES, 0)
        counts.update(dict(rows))
        return counts

    def problem_files(self, collection_id: int, limit: int = 50) -> list[FileRecord]:
        """Files with an error or a note (failed, skipped, or indexed with missing pages)."""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_FILE_COLUMNS} FROM files"
                " WHERE collection_id = ? AND error IS NOT NULL ORDER BY rel_path LIMIT ?",
                (collection_id, limit),
            ).fetchall()
        return [FileRecord(*row) for row in rows]

    # Chunks ---------------------------------------------------------------------------------

    def replace_file_chunks(
        self,
        file_id: int,
        chunks: Sequence[ChunkInput],
        vectors: np.ndarray,
        *,
        content_hash: str,
        note: str | None,
    ) -> None:
        """Swap a file's chunks and vectors and mark it indexed, in one transaction.

        Raises LookupError when the file row no longer exists (removed while being indexed).
        """
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT collection_id FROM files WHERE id = ?", (file_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f"file {file_id} no longer exists")
            self._conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
            for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
                cursor = self._conn.execute(
                    "INSERT INTO chunks (file_id, ord, text, location_json) VALUES (?, ?, ?, ?)",
                    (file_id, ordinal, chunk.text, json.dumps(chunk.location, ensure_ascii=False)),
                )
                self._conn.execute(
                    "INSERT INTO chunk_vectors (rowid, collection_id, embedding) VALUES (?, ?, ?)",
                    (cursor.lastrowid, row[0], _blob(vector)),
                )
            self._conn.execute(
                "UPDATE files SET status = 'indexed', error = ?, content_hash = ?, indexed_at = ?"
                " WHERE id = ?",
                (note, content_hash, _now(), file_id),
            )

    def get_chunks(self, ids: Sequence[int]) -> list[ChunkRecord]:
        """Chunks with their file's path and hash, in the requested order (missing ids skipped)."""
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id, c.file_id, f.rel_path, c.ord, c.text, c.location_json, f.content_hash"
                f" FROM chunks c JOIN files f ON f.id = c.file_id WHERE c.id IN ({marks})",
                list(ids),
            ).fetchall()
        by_id = {
            row[0]: ChunkRecord(row[0], row[1], row[2], row[3], row[4], json.loads(row[5]), row[6])
            for row in rows
        }
        return [by_id[i] for i in ids if i in by_id]

    def search_dense(
        self, collection_id: int, vector: np.ndarray, k: int
    ) -> list[tuple[int, float]]:
        """Nearest chunks as (chunk id, cosine similarity), most similar first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT rowid, distance FROM chunk_vectors"
                " WHERE embedding MATCH ? AND k = ? AND collection_id = ? ORDER BY distance",
                (_blob(vector), k, collection_id),
            ).fetchall()
        return [(rowid, 1.0 - distance) for rowid, distance in rows]

    def search_keyword(self, collection_id: int, fts_query: str, k: int) -> list[int]:
        """Chunk ids matching an FTS5 query (see retriever.fts_query), best BM25 first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id FROM chunks_fts"
                " JOIN chunks c ON c.id = chunks_fts.rowid"
                " JOIN files f ON f.id = c.file_id"
                " WHERE chunks_fts MATCH ? AND f.collection_id = ?"
                " ORDER BY bm25(chunks_fts) LIMIT ?",
                (fts_query, collection_id, k),
            ).fetchall()
        return [row[0] for row in rows]
```

`src/tamra/store/__init__.py`:

```python
"""Storage: the only module that executes SQL. One SQLite file holds everything (spec §4)."""

from tamra.store.db import capabilities, connect
from tamra.store.models import (
    Chat,
    ChunkInput,
    ChunkRecord,
    Collection,
    FileRecord,
    MessageRecord,
    SourceRecord,
)
from tamra.store.repo import FILE_STATUSES, Store

__all__ = [
    "FILE_STATUSES",
    "Chat",
    "ChunkInput",
    "ChunkRecord",
    "Collection",
    "FileRecord",
    "MessageRecord",
    "SourceRecord",
    "Store",
    "capabilities",
    "connect",
]
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_store.py` (add `import sqlite3`, `import threading`, and `import pytest` to its imports):

```python
def test_file_database_uses_wal_foreign_keys_and_threads(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    seen = []
    worker = threading.Thread(
        target=lambda: seen.append(conn.execute("SELECT vec_version()").fetchone()[0])
    )
    worker.start()
    worker.join()
    assert seen[0].startswith("v")
    conn.close()


def test_connect_releases_a_file_that_is_not_a_database(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a sqlite file " * 100)
    with pytest.raises(sqlite3.DatabaseError):
        store.connect(bad)
    bad.rename(tmp_path / "moved.db")  # Windows refuses this while a handle is still open
```

`tests/test_store_repo.py`:

```python
import sqlite3
import threading

import numpy as np
import pytest

from tamra.store import ChunkInput, Store

MODEL = "test-model"


def unit(*hot: int) -> np.ndarray:
    vector = np.zeros(1024, dtype=np.float32)
    vector[list(hot)] = 1.0
    return vector / np.linalg.norm(vector)


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "tamra.db")
    yield s
    s.close()


@pytest.fixture
def coll(store):
    return store.replace_collection("Docs", "C:/docs", MODEL)


def index(store, file_id, texts, vectors, content_hash="h1", note=None):
    chunks = [
        ChunkInput(text, {"kind": "text", "line_start": i + 1, "line_end": i + 1})
        for i, text in enumerate(texts)
    ]
    store.replace_file_chunks(
        file_id, chunks, np.stack(vectors), content_hash=content_hash, note=note
    )


def test_schema_is_created_once_and_versioned(tmp_path):
    path = tmp_path / "tamra.db"
    Store.open(path).close()
    again = Store.open(path)  # second open: migrate() has nothing to do
    assert again.get_collection() is None
    again.close()
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()


def test_a_newer_schema_is_refused(tmp_path):
    path = tmp_path / "tamra.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(RuntimeError, match="newer"):
        Store.open(path)


def test_replace_collection_drops_the_old_index(store, coll):
    file_id = store.add_file(coll.id, "a.md", 10, 1.0)
    index(store, file_id, ["alpha beta"], [unit(1)])
    new = store.replace_collection("Other", "D:/other", MODEL)
    assert store.get_collection() == new
    assert store.list_files(new.id) == []
    assert store.search_keyword(new.id, '"alp"', 10) == []
    assert store.search_dense(coll.id, unit(1), 10) == []


def test_file_lifecycle_and_counts(store, coll):
    a = store.add_file(coll.id, "a.md", 10, 1.0)
    b = store.add_file(coll.id, "sub/b.txt", 20, 2.0)
    assert [f.rel_path for f in store.list_files(coll.id)] == ["a.md", "sub/b.txt"]
    assert store.next_pending_file(coll.id).id == a
    store.set_file_status(a, "failed", "boom")
    assert store.next_pending_file(coll.id).id == b
    store.update_file_stat(a, 11, 3.0, pending=True)
    record = {f.id: f for f in store.list_files(coll.id)}[a]
    assert (record.size, record.mtime, record.status, record.error) == (11, 3.0, "pending", None)
    store.update_file_stat(b, 21, 4.0, pending=False)
    assert {f.id: f for f in store.list_files(coll.id)}[b].status == "pending"
    counts = store.status_counts(coll.id)
    assert counts == {"pending": 2, "indexing": 0, "indexed": 0, "failed": 0, "skipped": 0}
    store.delete_file(b)
    assert [f.id for f in store.list_files(coll.id)] == [a]
    with pytest.raises(ValueError):
        store.set_file_status(a, "weird")


def test_replace_file_chunks_indexes_text_and_vectors(store, coll):
    file_id = store.add_file(coll.id, "a.md", 10, 1.0)
    index(store, file_id, ["The lease term is three years", "Pets are allowed"],
          [unit(1), unit(2)], note="n")
    record = store.list_files(coll.id)[0]
    assert (record.status, record.content_hash, record.error) == ("indexed", "h1", "n")
    assert record.indexed_at
    dense = store.search_dense(coll.id, unit(1), 5)
    assert len(dense) == 2
    assert dense[0][1] == pytest.approx(1.0)
    hits = store.get_chunks(store.search_keyword(coll.id, '"lea" OR "ase"', 5))
    assert hits[0].text == "The lease term is three years"
    assert hits[0].rel_path == "a.md"
    assert hits[0].location == {"kind": "text", "line_start": 1, "line_end": 1}
    assert hits[0].file_hash == "h1"


def test_reindexing_a_file_replaces_its_chunks(store, coll):
    file_id = store.add_file(coll.id, "a.md", 10, 1.0)
    index(store, file_id, ["old words here"], [unit(1)])
    index(store, file_id, ["new words now"], [unit(2)], content_hash="h2")
    assert store.search_keyword(coll.id, '"old"', 5) == []
    assert len(store.search_keyword(coll.id, '"new"', 5)) == 1
    assert len(store.search_dense(coll.id, unit(1), 5)) == 1


def test_deleting_a_file_removes_chunks_keyword_entries_and_vectors(store, coll):
    file_id = store.add_file(coll.id, "a.md", 10, 1.0)
    index(store, file_id, ["lease"], [unit(1)])
    store.delete_file(file_id)
    assert store.search_keyword(coll.id, '"lea"', 5) == []
    assert store.search_dense(coll.id, unit(1), 5) == []


def test_replace_file_chunks_for_a_removed_file_raises_lookup_error(store, coll):
    file_id = store.add_file(coll.id, "a.md", 10, 1.0)
    store.delete_file(file_id)
    with pytest.raises(LookupError):
        index(store, file_id, ["x"], [unit(1)])


def test_get_chunks_keeps_the_requested_order(store, coll):
    file_id = store.add_file(coll.id, "a.md", 10, 1.0)
    index(store, file_id, ["one", "two", "three"], [unit(1), unit(2), unit(3)])
    ids = [chunk_id for chunk_id, _ in store.search_dense(coll.id, unit(1), 3)]
    forward = [c.text for c in store.get_chunks(ids)]
    backward = [c.text for c in store.get_chunks(ids[::-1])]
    assert backward == forward[::-1]
    assert store.get_chunks([]) == []


def test_reset_index_marks_everything_pending_for_the_new_model(store, coll):
    file_id = store.add_file(coll.id, "a.md", 10, 1.0)
    index(store, file_id, ["lease"], [unit(1)])
    store.reset_index(coll.id, "model-2")
    record = store.list_files(coll.id)[0]
    assert (record.status, record.content_hash, record.indexed_at) == ("pending", None, None)
    assert store.get_collection().embedding_model_id == "model-2"
    assert store.search_dense(coll.id, unit(1), 5) == []


def test_problem_files_lists_errors_and_notes(store, coll):
    a = store.add_file(coll.id, "a.pdf", 1, 1.0)
    b = store.add_file(coll.id, "b.md", 1, 1.0)
    store.add_file(coll.id, "c.md", 1, 1.0)
    store.set_file_status(a, "failed", "cannot open PDF")
    index(store, b, ["x"], [unit(1)], note="no text layer on pages 2")
    assert [(f.rel_path, f.status, f.error) for f in store.problem_files(coll.id)] == [
        ("a.pdf", "failed", "cannot open PDF"),
        ("b.md", "indexed", "no text layer on pages 2"),
    ]


def test_the_store_works_from_another_thread(store, coll):
    errors = []

    def worker():
        try:
            store.add_file(coll.id, "t.md", 1, 1.0)
        except Exception as e:  # surface any thread error in the main thread
            errors.append(e)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert errors == []
    assert len(store.list_files(coll.id)) == 1
```

- [ ] **Step 3: Run the tests to see the new ones fail**

Run: `uv run pytest tests/test_store.py tests/test_store_repo.py -v`
Expected: `tests/test_store_repo.py` fails at collection with `ImportError: cannot import name 'ChunkInput' from 'tamra.store'` (or `ModuleNotFoundError` for `tamra.store.models`) until all package files exist. Write Step 1's files after this run if you created the tests first; the M0 tests in `tests/test_store.py` must keep passing.

- [ ] **Step 4: Run all tests and lint**

Run: `uv run pytest tests/test_store.py tests/test_store_repo.py -v; uv run pytest; uv run ruff format; uv run ruff check; uv run ruff format --check`
Expected: all pass (the selfcheck tests still pass because `from tamra import store` keeps `connect` and `capabilities`).

- [ ] **Step 5: Commit**

```powershell
git add src/tamra/store tests/test_store.py tests/test_store_repo.py
git commit -m "feat(store): storage package with schema v1, files, chunks, and hybrid search primitives"
```

---

### Task 2: Storage — chats, messages, sources; selfcheck goes through Store

**Files:**
- Modify: `src/tamra/store/repo.py` (append chat methods), `src/tamra/selfcheck.py` (`_check_sqlite`, `_check_embedding`), `tests/test_selfcheck.py` (tighten one test)
- Test: `tests/test_store_chats.py`

**Interfaces:**
- Consumes: Task 1 `Store`, `ChunkInput`, models.
- Produces: `Store.create_chat(title="") -> Chat`, `get_chat(chat_id) -> Chat | None`, `list_chats() -> list[Chat]` (most recently used first, by the `activity` counter that creating a chat and adding a message raise; timestamps have one-second resolution and would tie), `delete_chat(chat_id) -> bool`, `add_user_message(chat_id, content) -> int` (sets an empty title from the first question: whitespace collapsed, at most 60 characters), `add_assistant_message(chat_id, content, *, provider, model, sources: Sequence[SourceRecord]) -> int`, `last_exchange(chat_id) -> tuple[str | None, str | None]` (latest user question and the assistant reply after it), `list_messages(chat_id) -> list[MessageRecord]` (oldest first, with sources ordered by `n`).

- [ ] **Step 1: Write the failing tests**

`tests/test_store_chats.py`:

```python
import numpy as np
import pytest

from tamra.store import ChunkInput, SourceRecord, Store


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "tamra.db")
    yield s
    s.close()


def source(n, text="The lease is three years.", rel_path="a.md"):
    return SourceRecord(
        n=n,
        chunk_id=None,
        file_id=None,
        rel_path=rel_path,
        text=text,
        location={"kind": "text", "line_start": n, "line_end": n},
        file_hash="h",
    )


def test_chats_are_listed_most_recent_first(store):
    first = store.create_chat()
    second = store.create_chat("Named")
    assert [c.id for c in store.list_chats()] == [second.id, first.id]
    store.add_user_message(first.id, "bump")
    assert store.list_chats()[0].id == first.id
    assert store.get_chat(second.id).title == "Named"
    assert store.get_chat(999) is None


def test_first_question_becomes_the_title(store):
    chat = store.create_chat()
    store.add_user_message(chat.id, "  What   is the\nlease term " + "x" * 80)
    store.add_user_message(chat.id, "second question")
    title = store.get_chat(chat.id).title
    assert title.startswith("What is the lease term ")
    assert len(title) == 60


def test_assistant_messages_keep_their_sources(store):
    chat = store.create_chat()
    store.add_user_message(chat.id, "How long is the lease?")
    message_id = store.add_assistant_message(
        chat.id, "Three years [1].", provider="local", model="qwen", sources=[source(1), source(2)]
    )
    messages = store.list_messages(chat.id)
    assert [m.role for m in messages] == ["user", "assistant"]
    answer = messages[1]
    assert (answer.id, answer.content, answer.provider, answer.model) == (
        message_id, "Three years [1].", "local", "qwen")
    assert [s.n for s in answer.sources] == [1, 2]
    assert answer.sources[0].location == {"kind": "text", "line_start": 1, "line_end": 1}
    assert messages[0].sources == ()


def test_last_exchange_returns_the_latest_question_and_its_answer(store):
    chat = store.create_chat()
    assert store.last_exchange(chat.id) == (None, None)
    store.add_user_message(chat.id, "q1")
    store.add_assistant_message(chat.id, "a1", provider=None, model=None, sources=[])
    store.add_user_message(chat.id, "q2")
    assert store.last_exchange(chat.id) == ("q2", None)
    store.add_assistant_message(chat.id, "a2", provider=None, model=None, sources=[])
    assert store.last_exchange(chat.id) == ("q2", "a2")


def test_deleting_a_chat_removes_its_messages(store):
    chat = store.create_chat()
    store.add_user_message(chat.id, "q")
    store.add_assistant_message(chat.id, "a", provider=None, model=None, sources=[source(1)])
    assert store.delete_chat(chat.id) is True
    assert store.delete_chat(chat.id) is False
    assert store.list_messages(chat.id) == []


def test_message_sources_outlive_the_indexed_file(store):
    coll = store.replace_collection("Docs", "C:/docs", "m")
    file_id = store.add_file(coll.id, "a.md", 1, 1.0)
    vector = np.zeros(1024, dtype=np.float32)
    vector[0] = 1.0
    store.replace_file_chunks(
        file_id, [ChunkInput("lease text", {"kind": "text", "line_start": 1, "line_end": 1})],
        vector[None, :], content_hash="h", note=None)
    chunk = store.get_chunks([store.search_dense(coll.id, vector, 1)[0][0]])[0]
    chat = store.create_chat()
    store.add_user_message(chat.id, "q")
    store.add_assistant_message(chat.id, "a [1]", provider="local", model="m", sources=[
        SourceRecord(1, chunk.id, file_id, "a.md", chunk.text, chunk.location, "h")])
    store.delete_file(file_id)
    kept = store.list_messages(chat.id)[1].sources[0]
    assert (kept.rel_path, kept.text, kept.chunk_id) == ("a.md", "lease text", chunk.id)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_store_chats.py -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'create_chat'`.

- [ ] **Step 3: Implement**

In `src/tamra/store/repo.py`, extend the models import to
`from tamra.store.models import Chat, ChunkInput, ChunkRecord, Collection, FileRecord, MessageRecord, SourceRecord`
(ruff format will wrap it) and append these methods to `Store`:

```python
    # Chats ----------------------------------------------------------------------------------

    def create_chat(self, title: str = "") -> Chat:
        created = _now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO chats (title, created_at, updated_at, activity)"
                " VALUES (?, ?, ?, (SELECT COALESCE(MAX(activity), 0) + 1 FROM chats))",
                (title, created, created),
            )
        return Chat(cursor.lastrowid, title, created, created)

    def get_chat(self, chat_id: int) -> Chat | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title, created_at, updated_at FROM chats WHERE id = ?", (chat_id,)
            ).fetchone()
        return Chat(*row) if row else None

    def list_chats(self) -> list[Chat]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, created_at, updated_at FROM chats ORDER BY activity DESC"
            ).fetchall()
        return [Chat(*row) for row in rows]

    def delete_chat(self, chat_id: int) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        return cursor.rowcount > 0

    def add_user_message(self, chat_id: int, content: str) -> int:
        """Store a question; the first question of an untitled chat becomes its title."""
        created = _now()
        title = " ".join(content.split())[:60]
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO messages (chat_id, role, content, created_at)"
                " VALUES (?, 'user', ?, ?)",
                (chat_id, content, created),
            )
            self._conn.execute(
                "UPDATE chats SET updated_at = ?,"
                " activity = (SELECT MAX(activity) FROM chats) + 1,"
                " title = CASE WHEN title = '' THEN ? ELSE title END WHERE id = ?",
                (created, title, chat_id),
            )
        return cursor.lastrowid

    def add_assistant_message(
        self,
        chat_id: int,
        content: str,
        *,
        provider: str | None,
        model: str | None,
        sources: Sequence[SourceRecord],
    ) -> int:
        """Store an answer with a snapshot of every source the model was given."""
        created = _now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO messages (chat_id, role, content, provider, model, created_at)"
                " VALUES (?, 'assistant', ?, ?, ?, ?)",
                (chat_id, content, provider, model, created),
            )
            message_id = cursor.lastrowid
            self._conn.executemany(
                "INSERT INTO message_sources (message_id, n, chunk_id, file_id, rel_path,"
                " text_snapshot, location_json, file_hash_at_answer)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        message_id,
                        s.n,
                        s.chunk_id,
                        s.file_id,
                        s.rel_path,
                        s.text,
                        json.dumps(s.location, ensure_ascii=False),
                        s.file_hash,
                    )
                    for s in sources
                ],
            )
            self._conn.execute(
                "UPDATE chats SET updated_at = ?,"
                " activity = (SELECT MAX(activity) FROM chats) + 1 WHERE id = ?",
                (created, chat_id),
            )
        return message_id

    def last_exchange(self, chat_id: int) -> tuple[str | None, str | None]:
        """The chat's latest question and the assistant reply that followed it, if any."""
        with self._lock:
            question = self._conn.execute(
                "SELECT id, content FROM messages WHERE chat_id = ? AND role = 'user'"
                " ORDER BY id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
            if question is None:
                return None, None
            answer = self._conn.execute(
                "SELECT content FROM messages WHERE chat_id = ? AND role = 'assistant' AND id > ?"
                " ORDER BY id LIMIT 1",
                (chat_id, question[0]),
            ).fetchone()
        return question[1], (answer[0] if answer else None)

    def list_messages(self, chat_id: int) -> list[MessageRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, role, content, provider, model, created_at FROM messages"
                " WHERE chat_id = ? ORDER BY id",
                (chat_id,),
            ).fetchall()
            source_rows = self._conn.execute(
                "SELECT s.message_id, s.n, s.chunk_id, s.file_id, s.rel_path, s.text_snapshot,"
                " s.location_json, s.file_hash_at_answer FROM message_sources s"
                " JOIN messages m ON m.id = s.message_id WHERE m.chat_id = ?"
                " ORDER BY s.message_id, s.n",
                (chat_id,),
            ).fetchall()
        sources: dict[int, list[SourceRecord]] = {}
        for row in source_rows:
            sources.setdefault(row[0], []).append(
                SourceRecord(row[1], row[2], row[3], row[4], row[5], json.loads(row[6]), row[7])
            )
        return [MessageRecord(*row, sources=tuple(sources.get(row[0], ()))) for row in rows]
```

Replace `_check_sqlite` and `_check_embedding` in `src/tamra/selfcheck.py` so the probes go through `Store` (CLAUDE.md: `store` is the only module that touches SQL):

```python
def _check_sqlite() -> dict:
    import numpy as np

    from tamra.store import ChunkInput, Store

    store = Store.open(":memory:")
    try:
        caps = store.capabilities()
        collection = store.replace_collection("selfcheck", ".", "selfcheck")
        file_id = store.add_file(collection.id, "probe.txt", 0, 0.0)
        vector = np.zeros(1024, dtype=np.float32)
        vector[0] = 1.0
        location = {"kind": "text", "line_start": 1, "line_end": 1}
        store.replace_file_chunks(
            file_id,
            [ChunkInput("selfcheck probe text", location)],
            vector[None, :],
            content_hash="-",
            note=None,
        )
        dense = store.search_dense(collection.id, vector, 1)
        keyword = store.search_keyword(collection.id, '"pro"', 1)
    finally:
        store.close()
    ok = bool(caps["fts5_trigram"]) and len(dense) == 1 and keyword == [dense[0][0]]
    return {"ok": ok, **caps}


def _check_embedding(model_dir: Path) -> dict:
    from tamra.embedder import Embedder
    from tamra.store import ChunkInput, Store

    t0 = time.perf_counter()
    embedder = Embedder(model_dir)
    load_s = time.perf_counter() - t0

    docs = ["แมวกำลังนอนหลับอยู่บนโซฟา", "汽车停在路边"]
    store = Store.open(":memory:")
    try:
        collection = store.replace_collection("selfcheck", ".", "selfcheck")
        file_id = store.add_file(collection.id, "probe.txt", 0, 0.0)
        chunks = [
            ChunkInput(text, {"kind": "text", "line_start": i + 1, "line_end": i + 1})
            for i, text in enumerate(docs)
        ]
        store.replace_file_chunks(
            file_id, chunks, embedder.embed(docs), content_hash="-", note=None
        )
        query = embedder.embed(["A cat sleeping on a couch"])[0]
        best = store.get_chunks([store.search_dense(collection.id, query, 1)[0][0]])[0]
    finally:
        store.close()

    t0 = time.perf_counter()
    embedder.embed([PASSAGE] * 32)
    rate = 32 / (time.perf_counter() - t0)
    return {
        "ok": best.text == docs[0],
        "load_s": round(load_s, 2),
        "passages_per_sec": round(rate, 2),
    }
```

In `tests/test_selfcheck.py`, `test_report_survives_non_cp1252_stdout`: replace its last line with these two lines so the printed copy is proven to be the whole report, not just ASCII:

```python
    assert buf.getvalue().isascii()
    assert json.loads(buf.getvalue().decode("ascii")) == fake_report
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_store_chats.py tests/test_selfcheck.py -v; uv run pytest -m assets tests/test_selfcheck.py -v`
Expected: all pass; the asset run passes `test_full_selfcheck` (the models are on disk).
Then: `uv run pytest; uv run ruff format; uv run ruff check; uv run ruff format --check`

- [ ] **Step 5: Commit**

```powershell
git add src/tamra/store/repo.py src/tamra/selfcheck.py tests/test_store_chats.py tests/test_selfcheck.py
git commit -m "feat(store): chats, messages, and answer sources; selfcheck probes use Store"
```

---

### Task 3: Document parsers (PDF, DOCX, TXT/MD) with locations

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (via `uv add`)
- Create: `src/tamra/ingest/__init__.py` (empty), `src/tamra/ingest/parsers.py`, `tests/docgen.py`
- Test: `tests/test_parsers.py`

**Interfaces:**
- Produces: `SUPPORTED` (frozenset of `.pdf .docx .txt .md`), `ParseError`, `Unit(text, page=0, char=0, paragraph=0, heading_path=(), line=0)`, `ParsedDoc(kind, units, note=None)` with `kind` in `"pdf" | "docx" | "text"`, `parse_file(path) -> ParsedDoc`, `decode_text(data: bytes) -> str`, and `location(kind, first, first_offset, last, last_offset) -> dict`. Location shapes (stored as `location_json`, spec §4):
  - pdf: `{"kind": "pdf", "page_start", "char_start", "page_end", "char_end"}` — 1-based pages; character offsets into pdfium's text for that page (M3 maps them to character boxes).
  - docx: `{"kind": "docx", "heading_path": [..], "paragraph_start", "paragraph_end"}` — 0-based indexes into `document.paragraphs`.
  - text: `{"kind": "text", "line_start", "line_end"}` — 1-based, inclusive.
- Produces (tests only): `tests/docgen.py` with `make_pdf(path, pages, font=LATIN_THAI) -> Path` (one inner list of lines per page; an empty list makes a page without text) and `make_docx(path, blocks) -> Path` (`(heading level, text)`, level 0 = body), plus the font paths `LATIN_THAI` (Tahoma) and `CJK` (Microsoft YaHei).

- [ ] **Step 1: Add the dependencies**

```powershell
uv add "pypdfium2>=5" "python-docx>=1.1" "charset-normalizer>=3.3"
```

- [ ] **Step 2: Add the fixture builder**

`tests/docgen.py` (pdfium builds the PDFs; a CID TrueType font keeps Thai/Chinese text extractable):

```python
"""Build small PDF and DOCX fixtures. Uses Windows system fonts (Tahoma, Microsoft YaHei)."""

import ctypes
from pathlib import Path

import docx
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

FONTS = Path("C:/Windows/Fonts")
LATIN_THAI = FONTS / "tahoma.ttf"
CJK = FONTS / "msyh.ttc"


def make_pdf(path: Path, pages: list[list[str]], font: Path = LATIN_THAI) -> Path:
    """Write a PDF: one inner list per page, one text line per item (empty list = no text)."""
    pdf = pdfium.PdfDocument.new()
    data = font.read_bytes()
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    handle = pdfium_c.FPDFText_LoadFont(
        pdf, buffer, len(data), pdfium_c.FPDF_FONT_TRUETYPE, True
    )
    if not handle:
        pdf.close()
        raise RuntimeError(f"pdfium could not load {font}")
    try:
        for lines in pages:
            page = pdf.new_page(595, 842)
            y = 800
            for line in lines:
                obj = pdfium_c.FPDFPageObj_CreateTextObj(pdf, handle, 12.0)
                text = ctypes.create_unicode_buffer(line)
                pdfium_c.FPDFText_SetText(obj, ctypes.cast(text, ctypes.POINTER(pdfium_c.FPDF_WCHAR)))
                pdfium_c.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 50, y)
                pdfium_c.FPDFPage_InsertObject(page, obj)
                y -= 20
            pdfium_c.FPDFPage_GenerateContent(page)
            page.close()
        path.parent.mkdir(parents=True, exist_ok=True)
        pdf.save(path)
    finally:
        pdfium_c.FPDFFont_Close(handle)
        pdf.close()
    return path


def make_docx(path: Path, blocks: list[tuple[int, str]]) -> Path:
    """Write a DOCX from (heading level, text) pairs; level 0 is a body paragraph."""
    document = docx.Document()
    for level, text in blocks:
        if level:
            document.add_heading(text, level=level)
        else:
            document.add_paragraph(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))
    return path
```

- [ ] **Step 3: Write the failing tests**

`tests/test_parsers.py`:

```python
import codecs

import pytest
from docgen import CJK, LATIN_THAI, make_docx, make_pdf

from tamra.ingest.parsers import ParseError, Unit, decode_text, location, parse_file


@pytest.fixture(autouse=True)
def _fonts():
    if not (LATIN_THAI.exists() and CJK.exists()):
        pytest.skip("Windows fonts Tahoma and Microsoft YaHei are needed to build PDFs")


def test_pdf_pages_become_units_with_page_numbers(tmp_path):
    path = make_pdf(
        tmp_path / "t.pdf",
        [["Lease agreement", "สัญญาเช่าบ้านมีอายุสามปี"], ["Page two English line."]],
    )
    doc = parse_file(path)
    assert (doc.kind, doc.note) == ("pdf", None)
    assert [u.page for u in doc.units] == [1, 2]
    assert "สัญญาเช่าบ้านมีอายุสามปี" in doc.units[0].text
    assert doc.units[1].text.strip() == "Page two English line."


def test_chinese_pdf_text_is_extracted(tmp_path):
    path = make_pdf(tmp_path / "z.pdf", [["租赁期限为三年。"]], font=CJK)
    assert parse_file(path).units[0].text.strip() == "租赁期限为三年。"


def test_pages_without_text_are_reported(tmp_path):
    doc = parse_file(make_pdf(tmp_path / "s.pdf", [["text page"], []]))
    assert [u.page for u in doc.units] == [1]
    assert doc.note == "no text layer on page(s) 2 (needs OCR)"


def test_a_broken_pdf_raises_parse_error(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7 this is not a real pdf")
    with pytest.raises(ParseError, match="cannot open PDF"):
        parse_file(bad)


def test_docx_paragraphs_carry_their_heading_path(tmp_path):
    path = make_docx(
        tmp_path / "d.docx",
        [
            (1, "บทที่ 1"),
            (0, "ย่อหน้าแรก"),
            (2, "Section 1.1"),
            (0, "Body"),
            (0, "   "),
            (1, "Chapter 2"),
            (0, "End"),
        ],
    )
    doc = parse_file(path)
    assert doc.kind == "docx"
    assert [(u.text, u.heading_path, u.paragraph) for u in doc.units] == [
        ("บทที่ 1", ("บทที่ 1",), 0),
        ("ย่อหน้าแรก", ("บทที่ 1",), 1),
        ("Section 1.1", ("บทที่ 1", "Section 1.1"), 2),
        ("Body", ("บทที่ 1", "Section 1.1"), 3),
        ("Chapter 2", ("Chapter 2",), 5),
        ("End", ("Chapter 2",), 6),
    ]


def test_a_broken_docx_raises_parse_error(tmp_path):
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"PK not really a zip")
    with pytest.raises(ParseError, match="cannot open DOCX"):
        parse_file(bad)


def test_text_paragraphs_have_line_numbers(tmp_path):
    path = tmp_path / "n.md"
    path.write_bytes("# Title\r\n\r\nFirst line\r\nsecond line\r\n\r\n\r\nLast".encode())
    doc = parse_file(path)
    assert doc.kind == "text"
    assert [(u.text, u.line) for u in doc.units] == [
        ("# Title", 1),
        ("First line\nsecond line", 3),
        ("Last", 7),
    ]


@pytest.mark.parametrize(
    "data",
    [
        "สัญญาเช่าบ้าน".encode(),
        codecs.BOM_UTF8 + "สัญญาเช่าบ้าน".encode(),
        "สัญญาเช่าบ้าน".encode("utf-16"),
        "สัญญาเช่าบ้าน".encode("tis-620"),
    ],
)
def test_decode_text_handles_utf_and_short_thai_legacy_files(data):
    assert decode_text(data) == "สัญญาเช่าบ้าน"


def test_decode_text_does_not_mistake_chinese_gbk_for_thai():
    text = (
        "卡诺家具产品保修条款：沙发框架保修五年，布料和海绵保修两年，餐桌和椅子保修一年。"
        "保修期内的维修服务免费，技术人员会在三个工作日内上门检查。"
    )
    assert decode_text(text.encode("gbk")) == text


def test_unsupported_types_raise_parse_error(tmp_path):
    path = tmp_path / "x.csv"
    path.write_text("a,b", encoding="utf-8")
    with pytest.raises(ParseError, match="unsupported"):
        parse_file(path)


def test_location_shapes():
    pdf_a, pdf_b = Unit("abcdef", page=2, char=10), Unit("ghij", page=3)
    assert location("pdf", pdf_a, 1, pdf_b, 4) == {
        "kind": "pdf", "page_start": 2, "char_start": 11, "page_end": 3, "char_end": 4}
    doc_a = Unit("x", paragraph=4, heading_path=("H", "S"))
    doc_b = Unit("y", paragraph=9, heading_path=("H", "T"))
    assert location("docx", doc_a, 0, doc_b, 1) == {
        "kind": "docx", "heading_path": ["H", "S"], "paragraph_start": 4, "paragraph_end": 9}
    text = Unit("one\ntwo\nthree", line=10)
    assert location("text", text, 4, text, 13) == {
        "kind": "text", "line_start": 11, "line_end": 12}
```

- [ ] **Step 4: Run the tests to see them fail**

Run: `uv run pytest tests/test_parsers.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'tamra.ingest'`.

- [ ] **Step 5: Implement**

`src/tamra/ingest/__init__.py`: empty file.

`src/tamra/ingest/parsers.py`:

```python
"""Parse PDF, DOCX, TXT, and MD files into text units that remember where they came from."""

import codecs
import re
from dataclasses import dataclass
from pathlib import Path

import docx
import pypdfium2 as pdfium
from charset_normalizer import from_bytes

SUPPORTED = frozenset({".pdf", ".docx", ".txt", ".md"})

_HEADING = re.compile(r"Heading (\d)")
_THAI_CONSONANTS = range(0x0E01, 0x0E2F)
_THAI_MARKS = frozenset([0x0E31, *range(0x0E34, 0x0E3B), *range(0x0E47, 0x0E4F)])


class ParseError(Exception):
    """The file could not be read as a document of its type."""


@dataclass(frozen=True)
class Unit:
    """A run of document text and the coordinates of its first character.

    pdf: page (1-based) and char (offset of text[0] in the page's extracted text).
    docx: paragraph (0-based index into document.paragraphs) and heading_path.
    text: line (1-based number of the line holding text[0]).
    """

    text: str
    page: int = 0
    char: int = 0
    paragraph: int = 0
    heading_path: tuple[str, ...] = ()
    line: int = 0


@dataclass(frozen=True)
class ParsedDoc:
    kind: str  # "pdf" | "docx" | "text"
    units: list[Unit]
    note: str | None = None  # shown next to the file, e.g. pages without a text layer


def location(kind: str, first: Unit, first_offset: int, last: Unit, last_offset: int) -> dict:
    """Where the text from first.text[first_offset] to last.text[last_offset - 1] sits."""
    if kind == "pdf":
        return {
            "kind": "pdf",
            "page_start": first.page,
            "char_start": first.char + first_offset,
            "page_end": last.page,
            "char_end": last.char + last_offset,
        }
    if kind == "docx":
        return {
            "kind": "docx",
            "heading_path": list(first.heading_path),
            "paragraph_start": first.paragraph,
            "paragraph_end": last.paragraph,
        }
    return {
        "kind": "text",
        "line_start": first.line + first.text.count("\n", 0, first_offset),
        "line_end": last.line + last.text.count("\n", 0, max(last_offset - 1, 0)),
    }


def parse_file(path: Path) -> ParsedDoc:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix == ".docx":
        return parse_docx(path)
    if suffix in (".txt", ".md"):
        return parse_text(path)
    raise ParseError(f"unsupported file type: {path.suffix}")


def parse_pdf(path: Path) -> ParsedDoc:
    """One unit per page with a text layer; pages without one are listed in the note."""
    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as e:
        raise ParseError(f"cannot open PDF: {e}") from e
    units: list[Unit] = []
    empty: list[int] = []
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range()
                finally:
                    textpage.close()
            finally:
                page.close()
            if text.strip():
                units.append(Unit(text=text, page=index + 1))
            else:
                empty.append(index + 1)
    finally:
        pdf.close()
    note = None
    if empty:
        note = "no text layer on page(s) " + ", ".join(map(str, empty)) + " (needs OCR)"
    return ParsedDoc("pdf", units, note)


def parse_docx(path: Path) -> ParsedDoc:
    """One unit per non-empty paragraph, with the headings above it. Tables are not read."""
    try:
        document = docx.Document(str(path))
    except Exception as e:  # python-docx raises several types for a broken package
        raise ParseError(f"cannot open DOCX: {e}") from e
    units: list[Unit] = []
    headings: list[str] = []
    for index, paragraph in enumerate(document.paragraphs):
        text = paragraph.text
        if not text.strip():
            continue
        style = paragraph.style.name if paragraph.style is not None else ""
        match = _HEADING.fullmatch(style)
        level = int(match.group(1)) if match else (1 if style == "Title" else 0)
        if level:
            headings = headings[: level - 1] + [text.strip()]
        units.append(Unit(text=text, paragraph=index, heading_path=tuple(headings)))
    return ParsedDoc("docx", units)


def parse_text(path: Path) -> ParsedDoc:
    """One unit per paragraph (a run of non-blank lines), with its first line number."""
    try:
        data = path.read_bytes()
    except OSError as e:
        raise ParseError(f"cannot read file: {e}") from e
    text = decode_text(data).replace("\r\n", "\n").replace("\r", "\n")
    units: list[Unit] = []
    block: list[str] = []
    start = 0
    for number, line in enumerate(text.split("\n"), start=1):
        if line.strip():
            if not block:
                start = number
            block.append(line)
        elif block:
            units.append(Unit(text="\n".join(block), line=start))
            block = []
    if block:
        units.append(Unit(text="\n".join(block), line=start))
    return ParsedDoc("text", units)


def decode_text(data: bytes) -> str:
    """Decode a text file: BOMs, then UTF-8, then Thai cp874 (TIS-620), then a detected codec."""
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8) :].decode("utf-8", errors="replace")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        thai = data.decode("cp874")
    except UnicodeDecodeError:
        thai = None
    if thai is not None and _reads_as_thai(thai):
        return thai
    best = from_bytes(data).best()
    return str(best) if best is not None else data.decode("latin-1")


def _reads_as_thai(text: str) -> bool:
    """Mostly Thai letters, with vowel and tone marks sitting on consonants.

    Other legacy encodings decoded as cp874 (GBK Chinese, for example) also produce Thai
    letters, but their marks land after arbitrary characters, so the second test fails.
    """
    letters = [c for c in text if c.isalpha()]
    thai = [c for c in letters if 0x0E00 <= ord(c) <= 0x0E7F]
    if not letters or len(thai) < 0.3 * len(letters):
        return False
    marks = placed = 0
    previous = 0
    for c in text:
        code = ord(c)
        if code in _THAI_MARKS:
            marks += 1
            if previous in _THAI_CONSONANTS or previous in _THAI_MARKS:
                placed += 1
        previous = code
    return marks == 0 or placed >= 0.95 * marks
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_parsers.py -v`
Expected: all pass. If `test_decode_text_does_not_mistake_chinese_gbk_for_thai` fails, print what `charset_normalizer.from_bytes(text.encode("gbk")).best()` returns and report it (do not loosen the test).
Then: `uv run pytest; uv run ruff format; uv run ruff check; uv run ruff format --check`

- [ ] **Step 7: Commit**

```powershell
git add pyproject.toml uv.lock src/tamra/ingest tests/docgen.py tests/test_parsers.py
git commit -m "feat(ingest): PDF, DOCX, and TXT/MD parsers with locations and Thai legacy decoding"
```

---

### Task 4: Token-based chunker

**Files:**
- Create: `src/tamra/ingest/chunker.py`
- Test: `tests/test_chunker.py`

**Interfaces:**
- Consumes: Task 3 `ParsedDoc`, `Unit`, `location`; Task 1 `ChunkInput`.
- Produces: `TokenSpans = Callable[[str], list[tuple[int, int]]]` (character spans of tokens, no special tokens); `CHUNK_TOKENS = 450`; `OVERLAP_TOKENS = 68`; `bge_token_spans(tokenizer_path: Path) -> TokenSpans`; `chunk_document(doc, spans, max_tokens=CHUNK_TOKENS, overlap=OVERLAP_TOKENS) -> list[ChunkInput]`. Chunk text joins pieces of different units with a blank line and normalises `\r\n` to `\n`.

- [ ] **Step 1: Write the failing tests**

`tests/test_chunker.py`:

```python
import re

import pytest

from tamra.ingest.chunker import CHUNK_TOKENS, bge_token_spans, chunk_document
from tamra.ingest.parsers import ParsedDoc, Unit


def words(text):
    return [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]


def chars(text):
    return [(m.start(), m.end()) for m in re.finditer(r"\S", text)]


def test_a_short_document_is_one_chunk():
    doc = ParsedDoc("text", [Unit("alpha beta", line=1), Unit("gamma", line=3)])
    [chunk] = chunk_document(doc, words)
    assert chunk.text == "alpha beta\n\ngamma"
    assert chunk.location == {"kind": "text", "line_start": 1, "line_end": 3}


def test_chunks_respect_the_limit_and_repeat_whole_paragraphs_as_overlap():
    units = [
        Unit(" ".join(f"p{i}w{j}" for j in range(30)), line=2 * i + 1) for i in range(40)
    ]
    chunks = chunk_document(ParsedDoc("text", units), words, max_tokens=450, overlap=68)
    assert len(chunks) > 2
    assert all(len(words(c.text)) <= 450 for c in chunks)
    for previous, following in zip(chunks, chunks[1:]):
        tail = "\n\n".join(previous.text.split("\n\n")[-2:])  # 60 tokens fit in 68
        assert following.text.startswith(tail)
    joined = "\n\n".join(c.text for c in chunks)
    assert all(u.text in joined for u in units)


def test_a_long_paragraph_is_split_at_sentence_ends():
    sentence = " ".join(["word"] * 20) + ". "
    unit = Unit(sentence * 40, line=1)  # 800 tokens, one paragraph
    chunks = chunk_document(ParsedDoc("text", [unit]), words, max_tokens=450, overlap=0)
    assert len(chunks) == 2
    assert all(c.text.endswith(".") for c in chunks)
    assert all(len(words(c.text)) <= 450 for c in chunks)


def test_text_without_any_separator_is_cut_at_token_boundaries():
    unit = Unit("ก" * 1000, line=1)  # one run, every character a token
    chunks = chunk_document(ParsedDoc("text", [unit]), chars, max_tokens=450, overlap=0)
    assert [len(c.text) for c in chunks] == [450, 450, 100]
    assert "".join(c.text for c in chunks) == unit.text


def test_pdf_chunks_span_pages_with_character_offsets():
    units = [Unit("Page one text", page=1), Unit("Page two text", page=2)]
    [chunk] = chunk_document(ParsedDoc("pdf", units), words)
    assert chunk.location == {
        "kind": "pdf", "page_start": 1, "char_start": 0, "page_end": 2, "char_end": 13}
    assert chunk.text == "Page one text\n\nPage two text"


def test_docx_chunks_record_heading_and_paragraph_range():
    units = [
        Unit("Intro", paragraph=0, heading_path=("H",)),
        Unit("Body", paragraph=1, heading_path=("H",)),
    ]
    [chunk] = chunk_document(ParsedDoc("docx", units), words)
    assert chunk.location == {
        "kind": "docx", "heading_path": ["H"], "paragraph_start": 0, "paragraph_end": 1}


def test_line_range_follows_line_breaks_inside_a_unit():
    [chunk] = chunk_document(ParsedDoc("text", [Unit("one\ntwo\nthree", line=10)]), words)
    assert chunk.location == {"kind": "text", "line_start": 10, "line_end": 12}


def test_windows_line_breaks_are_normalised():
    [chunk] = chunk_document(ParsedDoc("pdf", [Unit("a\r\nb", page=1)]), words)
    assert chunk.text == "a\nb"


def test_whitespace_only_units_produce_no_chunks():
    assert chunk_document(ParsedDoc("text", [Unit("   \n  ", line=1)]), words) == []


@pytest.mark.assets
def test_real_tokenizer_keeps_thai_chunks_near_the_limit(bge_dir):
    spans = bge_token_spans(bge_dir / "tokenizer.json")
    text = "สัญญาเช่าบ้านมีอายุสามปีนับจากวันที่ลงนาม " * 120
    chunks = chunk_document(ParsedDoc("text", [Unit(text, line=1)]), spans)
    assert len(chunks) > 1
    assert all(len(spans(c.text)) <= CHUNK_TOKENS + 8 for c in chunks)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_chunker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tamra.ingest.chunker'`.

- [ ] **Step 3: Implement**

`src/tamra/ingest/chunker.py`:

```python
"""Split parsed documents into ~450-token chunks with ~15% overlap, keeping locations (spec §5)."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tokenizers import Tokenizer

from tamra.ingest.parsers import ParsedDoc, Unit, location
from tamra.store import ChunkInput

TokenSpans = Callable[[str], list[tuple[int, int]]]

CHUNK_TOKENS = 450
OVERLAP_TOKENS = 68  # about 15%

# Where to split a unit that is too long, coarsest first; a split happens at the end of a match.
_SPLITTERS = (
    re.compile(r"\n[ \t]*\n\s*"),  # blank line
    re.compile(r"[.!?]+\s+|[。！？]+"),  # sentence end
    re.compile(r"\n"),  # line break
    re.compile(r"\s+"),  # whitespace (Thai separates phrases with spaces)
)


@dataclass(frozen=True)
class _Piece:
    unit: Unit
    start: int
    end: int
    tokens: int


def bge_token_spans(tokenizer_path: Path) -> TokenSpans:
    """Token spans from the bge-m3 tokenizer, with no truncation and no padding."""
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    tokenizer.no_truncation()
    tokenizer.no_padding()

    def spans(text: str) -> list[tuple[int, int]]:
        return tokenizer.encode(text, add_special_tokens=False).offsets

    return spans


def chunk_document(
    doc: ParsedDoc,
    spans: TokenSpans,
    max_tokens: int = CHUNK_TOKENS,
    overlap: int = OVERLAP_TOKENS,
) -> list[ChunkInput]:
    pieces = [
        _Piece(unit, start, end, tokens)
        for unit in doc.units
        for start, end, tokens in _split(unit.text, 0, len(unit.text), spans, max_tokens, 0)
        if tokens > 0
    ]
    chunks: list[ChunkInput] = []
    current: list[_Piece] = []
    size = 0
    for piece in pieces:
        if current and size + piece.tokens > max_tokens:
            chunks.append(_make_chunk(doc.kind, current))
            current, size = _tail(current, overlap)
            while current and size + piece.tokens > max_tokens:
                size -= current.pop(0).tokens
        current.append(piece)
        size += piece.tokens
    if current:
        chunks.append(_make_chunk(doc.kind, current))
    return chunks


def _split(
    text: str, start: int, end: int, spans: TokenSpans, limit: int, level: int
) -> list[tuple[int, int, int]]:
    """Cut text[start:end] into (start, end, tokens) pieces of at most `limit` tokens."""
    offsets = spans(text[start:end])
    if len(offsets) <= limit:
        return [(start, end, len(offsets))]
    if level < len(_SPLITTERS):
        cuts = [m.end() for m in _SPLITTERS[level].finditer(text, start, end)]
        bounds = sorted({start, end, *(c for c in cuts if start < c < end)})
        if len(bounds) > 2:
            return [
                piece
                for a, b in zip(bounds, bounds[1:])
                for piece in _split(text, a, b, spans, limit, level + 1)
            ]
        return _split(text, start, end, spans, limit, level + 1)
    pieces: list[tuple[int, int]] = []  # no separator left: cut at token boundaries
    piece_start = start
    for i in range(limit, len(offsets), limit):
        cut = start + offsets[i - 1][1]
        if cut > piece_start:
            pieces.append((piece_start, cut))
            piece_start = cut
    pieces.append((piece_start, end))
    return [(a, b, len(spans(text[a:b]))) for a, b in pieces]


def _tail(pieces: list[_Piece], overlap: int) -> tuple[list[_Piece], int]:
    """The trailing pieces that fit in the overlap budget; they open the next chunk."""
    kept: list[_Piece] = []
    size = 0
    for piece in reversed(pieces):
        if size + piece.tokens > overlap:
            break
        kept.insert(0, piece)
        size += piece.tokens
    return kept, size


def _make_chunk(kind: str, pieces: list[_Piece]) -> ChunkInput:
    parts: list[str] = []
    previous: _Piece | None = None
    for piece in pieces:
        if previous is not None and not (
            previous.unit is piece.unit and previous.end == piece.start
        ):
            parts.append("\n\n")
        parts.append(piece.unit.text[piece.start : piece.end])
        previous = piece
    text = "".join(parts).replace("\r\n", "\n").strip()
    first, last = pieces[0], pieces[-1]
    return ChunkInput(text, location(kind, first.unit, first.start, last.unit, last.end))
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_chunker.py -v; uv run pytest -m assets tests/test_chunker.py -v`
Expected: all pass (the asset test uses the real bge-m3 tokenizer).
Then: `uv run pytest; uv run ruff format; uv run ruff check; uv run ruff format --check`

- [ ] **Step 5: Commit**

```powershell
git add src/tamra/ingest/chunker.py tests/test_chunker.py
git commit -m "feat(ingest): token-based chunker with split hierarchy, overlap, and locations"
```

---

### Task 5: Indexer — reconcile and the background worker

**Files:**
- Create: `src/tamra/ingest/indexer.py`, `tests/fakes.py`
- Modify: `src/tamra/store/repo.py` (`set_file_status`), `tests/test_store_repo.py`
- Test: `tests/test_indexer.py`

**Interfaces:**
- Consumes: Task 1 `Store`; Task 3 `SUPPORTED`, `ParseError`, `parse_file`; Task 4 `TokenSpans`, `chunk_document`; M0 `tamra.download.sha256_file`.
- Produces:
  - `scan_folder(folder) -> dict[str, tuple[int, float]]` (forward-slash relative path → size, mtime; skips dot-names, `~$*`, hidden and system items, unsupported types).
  - `IndexerState(current: str | None, error: str | None)`.
  - `Indexer(store, embedder: Callable[[], EmbedderLike], token_spans: Callable[[], TokenSpans], model_id: str)` with `start()`, `stop(timeout=30.0)`, `request_reconcile()`, `request_rebuild()`, `state() -> IndexerState`, and `process()` (runs one pass on the calling thread; tests use it).
  - `EmbedderLike` protocol: `embed(texts: list[str], batch_size: int = 16) -> np.ndarray`.
- Produces (tests only): `tests/fakes.py` with `FakeEmbedder` (bag-of-words hashing vectors, `dim = 1024`, `calls` counter), `fake_spans(text)` (one token per whitespace-separated word), and `FakeLLM(tokens=...)` (yields the tokens, records `calls`).
- Behavior (spec §5): files are indexed one at a time (parse → chunk → embed → one-transaction write). Reconcile compares size and mtime and hashes only when they differ; files left in `indexing` go back to `pending`; removed files leave the index. A stale collection is not indexed until `request_rebuild()`. A missing folder or an unloadable embedding model stops the pass with `state().error` set and changes no file to `failed`. A file that becomes `failed` or `skipped` leaves the index: `Store.set_file_status` deletes its chunks and clears its content hash in the same transaction (its old chunks no longer match the file on disk, and a cleared hash makes the next change re-index it); `pending` and `indexing` keep the old chunks searchable until they are replaced.

- [ ] **Step 1: Add the shared test doubles**

`tests/fakes.py`:

```python
"""Test doubles shared by several test modules."""

import re
import zlib
from collections.abc import Iterator

import numpy as np


class FakeEmbedder:
    """Bag-of-words hashing embedder: texts that share words get similar unit vectors."""

    dim = 1024

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        self.calls += 1
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in set(re.findall(r"\w+", text.lower())):
                out[row, zlib.crc32(word.encode("utf-8")) % self.dim] += 1.0
            norm = np.linalg.norm(out[row])
            if norm:
                out[row] /= norm
            else:
                out[row, 0] = 1.0
        return out


def fake_spans(text: str) -> list[tuple[int, int]]:
    """One token per whitespace-separated word."""
    return [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]


class FakeLLM:
    """Yields fixed tokens and records the messages it was given."""

    def __init__(self, tokens: tuple[str, ...] = ("The lease is three years ", "[1]", ".")):
        self.tokens = tokens
        self.calls: list[list[dict]] = []

    def generate(self, messages, max_tokens: int = 1024) -> Iterator[str]:
        self.calls.append(list(messages))
        yield from self.tokens
```

- [ ] **Step 2: Write the failing tests**

`tests/test_indexer.py`:

```python
import ctypes
import os
import shutil
import time

import pytest
from docgen import LATIN_THAI, make_docx, make_pdf
from fakes import FakeEmbedder, fake_spans

from tamra.ingest.indexer import Indexer, scan_folder
from tamra.store import Store

MODEL = "model-1"


@pytest.fixture
def env(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    store = Store.open(tmp_path / "tamra.db")
    embedder = FakeEmbedder()
    indexer = Indexer(store, lambda: embedder, lambda: fake_spans, MODEL)
    store.replace_collection("Docs", str(docs), MODEL)
    yield docs, store, embedder, indexer
    indexer.stop()
    store.close()


def run(indexer):
    indexer.request_reconcile()
    indexer.process()


def files(store):
    return {f.rel_path: f for f in store.list_files(store.get_collection().id)}


def keyword(store, word):
    return store.search_keyword(store.get_collection().id, f'"{word[:3]}"', 10)


def test_new_documents_are_indexed(env):
    docs, store, _, indexer = env
    (docs / "a.md").write_text("# Lease\n\nThe lease term is three years.", encoding="utf-8")
    (docs / "sub").mkdir()
    (docs / "sub" / "b.txt").write_text("Parking is free.", encoding="utf-8")
    make_docx(docs / "c.docx", [(1, "Pets"), (0, "Cats are allowed.")])
    run(indexer)
    assert {k: v.status for k, v in files(store).items()} == {
        "a.md": "indexed", "c.docx": "indexed", "sub/b.txt": "indexed"}
    assert keyword(store, "lease") and keyword(store, "Parking") and keyword(store, "Cats")
    assert indexer.state().error is None


def test_unchanged_files_are_not_reindexed(env):
    docs, store, embedder, indexer = env
    path = docs / "a.md"
    path.write_text("Lease terms.", encoding="utf-8")
    run(indexer)
    calls = embedder.calls
    os.utime(path, (time.time() + 60, time.time() + 60))  # new mtime, same content
    run(indexer)
    assert embedder.calls == calls
    assert files(store)["a.md"].status == "indexed"


def test_changed_files_are_reindexed(env):
    docs, store, _, indexer = env
    path = docs / "a.md"
    path.write_text("Old wording here.", encoding="utf-8")
    run(indexer)
    path.write_text("Fresh wording now.", encoding="utf-8")
    os.utime(path, (time.time() + 60, time.time() + 60))
    run(indexer)
    assert keyword(store, "Fresh") and not keyword(store, "Old")


def test_deleted_files_leave_the_index(env):
    docs, store, _, indexer = env
    (docs / "a.md").write_text("Lease terms.", encoding="utf-8")
    run(indexer)
    (docs / "a.md").unlink()
    run(indexer)
    assert files(store) == {}
    assert not keyword(store, "Lease")


def test_scan_skips_lock_hidden_dot_and_unsupported_files(tmp_path):
    (tmp_path / "keep.md").write_text("x", encoding="utf-8")
    (tmp_path / "~$report.docx").write_bytes(b"lock")
    (tmp_path / ".notes.md").write_text("x", encoding="utf-8")
    (tmp_path / "table.csv").write_text("a,b", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "inside.md").write_text("x", encoding="utf-8")
    hidden = tmp_path / "hidden.md"
    hidden.write_text("x", encoding="utf-8")
    ctypes.windll.kernel32.SetFileAttributesW(str(hidden), 0x2)  # FILE_ATTRIBUTE_HIDDEN
    assert set(scan_folder(tmp_path)) == {"keep.md"}


def test_a_broken_file_fails_alone(env):
    docs, store, _, indexer = env
    (docs / "bad.pdf").write_bytes(b"%PDF-1.7 not really")
    (docs / "good.md").write_text("Lease terms.", encoding="utf-8")
    run(indexer)
    state = files(store)
    assert state["bad.pdf"].status == "failed"
    assert "cannot open PDF" in state["bad.pdf"].error
    assert state["good.md"].status == "indexed"


def test_a_pdf_without_text_is_skipped_with_a_note(env):
    if not LATIN_THAI.exists():
        pytest.skip("needs the Tahoma font")
    docs, store, _, indexer = env
    make_pdf(docs / "scan.pdf", [[]])
    run(indexer)
    record = files(store)["scan.pdf"]
    assert record.status == "skipped"
    assert "needs OCR" in record.error


def test_a_stale_collection_waits_for_a_rebuild(env):
    docs, store, _, indexer = env
    store.replace_collection("Docs", str(docs), "old-model")
    (docs / "a.md").write_text("Lease terms.", encoding="utf-8")
    run(indexer)
    assert store.list_files(store.get_collection().id) == []
    indexer.request_rebuild()
    indexer.process()
    assert store.get_collection().embedding_model_id == MODEL
    assert files(store)["a.md"].status == "indexed"


def test_a_missing_folder_is_reported_and_the_index_kept(env):
    docs, store, _, indexer = env
    (docs / "a.md").write_text("Lease terms.", encoding="utf-8")
    run(indexer)
    shutil.rmtree(docs)
    run(indexer)
    assert indexer.state().error.startswith("Folder not found")
    assert files(store)["a.md"].status == "indexed"


def test_an_unavailable_model_pauses_indexing_without_failing_files(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("Lease terms.", encoding="utf-8")
    store = Store.open(tmp_path / "tamra.db")
    store.replace_collection("Docs", str(docs), MODEL)

    def missing():
        raise FileNotFoundError("model.onnx not found")

    indexer = Indexer(store, missing, lambda: fake_spans, MODEL)
    run(indexer)
    assert indexer.state().error.startswith("Embedding model unavailable")
    assert files(store)["a.md"].status == "pending"
    store.close()


def test_interrupted_indexing_is_requeued(env):
    docs, store, _, indexer = env
    (docs / "a.md").write_text("Lease terms.", encoding="utf-8")
    run(indexer)
    record = files(store)["a.md"]
    store.set_file_status(record.id, "indexing")
    run(indexer)
    assert files(store)["a.md"].status == "indexed"


def test_a_file_that_loses_its_text_leaves_the_index(env):
    docs, store, _, indexer = env
    path = docs / "a.md"
    path.write_text("Lease terms.", encoding="utf-8")
    run(indexer)
    path.write_text("   \n  ", encoding="utf-8")
    os.utime(path, (time.time() + 60, time.time() + 60))
    run(indexer)
    record = files(store)["a.md"]
    assert (record.status, record.error) == ("skipped", "no text found")
    assert not keyword(store, "Lease")


def test_the_background_worker_indexes_and_stops(env):
    docs, store, _, indexer = env
    (docs / "a.md").write_text("Lease terms.", encoding="utf-8")
    indexer.start()
    indexer.request_reconcile()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and files(store).get("a.md", None) is None:
        time.sleep(0.05)
    while time.monotonic() < deadline and files(store)["a.md"].status != "indexed":
        time.sleep(0.05)
    assert files(store)["a.md"].status == "indexed"
    indexer.stop()
    assert indexer.state().current is None
```

Append to `tests/test_store_repo.py`:

```python
def test_failed_or_skipped_files_leave_the_index(store, coll):
    file_id = store.add_file(coll.id, "a.md", 10, 1.0)
    index(store, file_id, ["lease terms"], [unit(1)])
    store.set_file_status(file_id, "pending")
    assert store.search_dense(coll.id, unit(1), 5)  # pending keeps the old chunks searchable
    store.set_file_status(file_id, "skipped", "no text found")
    record = store.list_files(coll.id)[0]
    assert (record.status, record.content_hash, record.indexed_at) == ("skipped", None, None)
    assert store.search_dense(coll.id, unit(1), 5) == []
    assert store.search_keyword(coll.id, '"lea"', 5) == []
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/test_indexer.py tests/test_store_repo.py -v`
Expected: `tests/test_indexer.py` fails with `ModuleNotFoundError: No module named 'tamra.ingest.indexer'`; `test_failed_or_skipped_files_leave_the_index` fails (the chunks are still searchable).

- [ ] **Step 4: Implement**

In `src/tamra/store/repo.py`, replace `set_file_status`:

```python
    def set_file_status(self, file_id: int, status: str, error: str | None = None) -> None:
        """Set a file's status. A failed or skipped file leaves the index: its old chunks no
        longer match the file on disk. A pending or indexing file keeps them until replaced."""
        if status not in FILE_STATUSES:
            raise ValueError(f"unknown file status: {status}")
        with self._lock, self._conn:
            if status in ("failed", "skipped"):
                self._conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
                self._conn.execute(
                    "UPDATE files SET content_hash = NULL, indexed_at = NULL WHERE id = ?",
                    (file_id,),
                )
            self._conn.execute(
                "UPDATE files SET status = ?, error = ? WHERE id = ?", (status, error, file_id)
            )
```

`src/tamra/ingest/indexer.py`:

```python
"""Keep a collection's index in step with its folder: reconcile, then index one file at a time."""

import logging
import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from tamra.download import sha256_file
from tamra.ingest.chunker import TokenSpans, chunk_document
from tamra.ingest.parsers import SUPPORTED, ParseError, parse_file
from tamra.store import Store

log = logging.getLogger(__name__)


class EmbedderLike(Protocol):
    def embed(self, texts: list[str], batch_size: int = 16) -> np.ndarray: ...


@dataclass(frozen=True)
class IndexerState:
    current: str | None  # file being indexed right now
    error: str | None  # what stops indexing (missing folder, unavailable model)


class _Pause(Exception):
    """Indexing cannot continue until something outside the documents changes."""


def scan_folder(folder: Path) -> dict[str, tuple[int, float]]:
    """Supported documents under folder: forward-slash relative path -> (size, mtime).

    Skips dot-files and dot-folders, Office lock files (~$*), and hidden or system items.
    """
    found: dict[str, tuple[int, float]] = {}
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not _ignored(Path(root, d))]
        for name in names:
            path = Path(root, name)
            if path.suffix.lower() not in SUPPORTED or _ignored(path):
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            found[path.relative_to(folder).as_posix()] = (info.st_size, info.st_mtime)
    return found


def _ignored(path: Path) -> bool:
    if path.name.startswith((".", "~$")):
        return True
    try:
        attributes = path.stat().st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))


class Indexer:
    """The single background worker of spec §5. Requests are coalesced into passes."""

    def __init__(
        self,
        store: Store,
        embedder: Callable[[], EmbedderLike],
        token_spans: Callable[[], TokenSpans],
        model_id: str,
    ):
        self._store = store
        self._embedder = embedder
        self._token_spans_factory = token_spans
        self._spans: TokenSpans | None = None
        self._model_id = model_id
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._reconcile_requested = False
        self._rebuild_requested = False
        self._current: str | None = None
        self._error: str | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="tamra-indexer", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 30.0) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def request_reconcile(self) -> None:
        with self._lock:
            self._reconcile_requested = True
        self._wake.set()

    def request_rebuild(self) -> None:
        with self._lock:
            self._rebuild_requested = True
            self._reconcile_requested = True
        self._wake.set()

    def state(self) -> IndexerState:
        with self._lock:
            return IndexerState(self._current, self._error)

    def process(self) -> None:
        """Handle the pending requests and index pending files, on the calling thread."""
        with self._lock:
            rebuild, reconcile = self._rebuild_requested, self._reconcile_requested
            self._rebuild_requested = self._reconcile_requested = False
        collection = self._store.get_collection()
        if collection is not None and rebuild:
            self._store.reset_index(collection.id, self._model_id)
            collection = self._store.get_collection()
        if collection is None or collection.embedding_model_id != self._model_id:
            self._set_error(None)  # nothing to do; a stale collection reports itself
            return
        folder = Path(collection.folder_path)
        if not folder.is_dir():
            self._set_error(f"Folder not found: {folder}")
            return
        if reconcile:
            self._reconcile(collection.id, folder)
        self._set_error(None)
        try:
            self._index_pending(collection.id, folder)
        except _Pause as pause:
            self._set_error(str(pause))

    def _run(self) -> None:
        while not self._stopping.is_set():
            self._wake.wait()
            self._wake.clear()
            if self._stopping.is_set():
                return
            try:
                self.process()
            except Exception as e:  # keep the worker alive; the state says what went wrong
                log.exception("indexer pass failed")
                self._set_error(f"{type(e).__name__}: {e}")

    def _set_error(self, error: str | None) -> None:
        with self._lock:
            self._error = error

    def _token_spans(self) -> TokenSpans:
        if self._spans is None:
            self._spans = self._token_spans_factory()
        return self._spans

    def _reconcile(self, collection_id: int, folder: Path) -> None:
        on_disk = scan_folder(folder)
        known = {record.rel_path: record for record in self._store.list_files(collection_id)}
        for rel_path, record in known.items():
            if rel_path not in on_disk:
                self._store.delete_file(record.id)
        for rel_path, (size, mtime) in on_disk.items():
            record = known.get(rel_path)
            if record is None:
                self._store.add_file(collection_id, rel_path, size, mtime)
            elif record.status == "indexing":  # interrupted by a crash or shutdown
                self._store.update_file_stat(record.id, size, mtime, pending=True)
            elif (size, mtime) != (record.size, record.mtime):
                changed = (
                    record.content_hash is None
                    or _hash_or_none(folder / rel_path) != record.content_hash
                )
                self._store.update_file_stat(record.id, size, mtime, pending=changed)

    def _index_pending(self, collection_id: int, folder: Path) -> None:
        while not self._stopping.is_set():
            with self._lock:
                if self._reconcile_requested or self._rebuild_requested:
                    self._wake.set()  # a newer request wins; the worker loops back to it
                    return
            record = self._store.next_pending_file(collection_id)
            if record is None:
                return
            self._index_file(record.id, record.rel_path, folder / record.rel_path)

    def _index_file(self, file_id: int, rel_path: str, path: Path) -> None:
        self._store.set_file_status(file_id, "indexing")
        with self._lock:
            self._current = rel_path
        try:
            try:
                embedder = self._embedder()
                spans = self._token_spans()
            except Exception as e:
                self._store.set_file_status(file_id, "pending")
                raise _Pause(f"Embedding model unavailable: {e}") from e
            content_hash = sha256_file(path)
            doc = parse_file(path)
            chunks = chunk_document(doc, spans)
            if not chunks:
                self._store.set_file_status(file_id, "skipped", doc.note or "no text found")
                return
            vectors = embedder.embed([chunk.text for chunk in chunks])
            self._store.replace_file_chunks(
                file_id, chunks, vectors, content_hash=content_hash, note=doc.note
            )
        except _Pause:
            raise
        except LookupError:
            pass  # the file left the collection while it was being indexed
        except (ParseError, OSError) as e:
            self._store.set_file_status(file_id, "failed", str(e))
        except Exception as e:  # one bad file must not stop the others
            log.exception("indexing %s failed", rel_path)
            self._store.set_file_status(file_id, "failed", f"{type(e).__name__}: {e}")
        finally:
            with self._lock:
                self._current = None


def _hash_or_none(path: Path) -> str | None:
    try:
        return sha256_file(path)
    except OSError:
        return None
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_indexer.py tests/test_store_repo.py -v`
Expected: all pass.
Then: `uv run pytest; uv run ruff format; uv run ruff check; uv run ruff format --check`

- [ ] **Step 6: Commit**

```powershell
git add src/tamra/ingest/indexer.py src/tamra/store/repo.py tests/fakes.py tests/test_indexer.py tests/test_store_repo.py
git commit -m "feat(ingest): reconcile and background indexer with stale-model and missing-folder guards"
```

---

### Task 6: Folder watcher

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (via `uv add`)
- Create: `src/tamra/ingest/watcher.py`
- Test: `tests/test_watcher.py`

**Interfaces:**
- Produces: `FolderWatcher(on_change: Callable[[], None], debounce: float = 2.0)` with `watch(folder: Path)` (starts watching, replacing any previous folder; raises `OSError` if the folder cannot be watched) and `stop()`. `on_change` runs once the folder has been quiet for `debounce` seconds after changes (spec §5: about 2 s). Office lock files (`~$*`) and folder-timestamp events do not count.

- [ ] **Step 1: Add the dependency**

```powershell
uv add "watchdog>=6"
```

- [ ] **Step 2: Write the failing tests**

`tests/test_watcher.py`:

```python
import time

from tamra.ingest.watcher import FolderWatcher


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_a_burst_of_changes_fires_once(tmp_path):
    calls = []
    watcher = FolderWatcher(lambda: calls.append(1), debounce=0.3)
    watcher.watch(tmp_path)
    try:
        for i in range(5):
            (tmp_path / f"f{i}.md").write_text("x", encoding="utf-8")
        assert wait_for(lambda: calls)
        time.sleep(0.8)
        assert len(calls) == 1
    finally:
        watcher.stop()


def test_office_lock_files_are_ignored(tmp_path):
    calls = []
    watcher = FolderWatcher(lambda: calls.append(1), debounce=0.2)
    watcher.watch(tmp_path)
    try:
        (tmp_path / "~$report.docx").write_bytes(b"lock")
        time.sleep(1.0)
        assert calls == []
    finally:
        watcher.stop()


def test_a_stopped_watcher_stays_quiet(tmp_path):
    calls = []
    watcher = FolderWatcher(lambda: calls.append(1), debounce=0.2)
    watcher.watch(tmp_path)
    watcher.stop()
    (tmp_path / "late.md").write_text("x", encoding="utf-8")
    time.sleep(0.8)
    assert calls == []


def test_watch_moves_to_the_new_folder(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    calls = []
    watcher = FolderWatcher(lambda: calls.append(1), debounce=0.2)
    watcher.watch(first)
    watcher.watch(second)
    try:
        (first / "x.md").write_text("x", encoding="utf-8")
        time.sleep(0.8)
        assert calls == []
        (second / "y.md").write_text("y", encoding="utf-8")
        assert wait_for(lambda: calls)
    finally:
        watcher.stop()
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/test_watcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tamra.ingest.watcher'`.

- [ ] **Step 4: Implement**

`src/tamra/ingest/watcher.py`:

```python
"""Watch a collection folder and report changes once it has been quiet for a moment."""

import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


class FolderWatcher:
    def __init__(self, on_change: Callable[[], None], debounce: float = 2.0):
        self._on_change = on_change
        self._debounce = debounce
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._observer = None

    def watch(self, folder: Path) -> None:
        """Watch folder (recursively) instead of any folder watched before."""
        self.stop()
        observer = Observer()
        observer.schedule(_Handler(self._poke), str(folder), recursive=True)
        observer.daemon = True
        observer.start()
        with self._lock:
            self._observer = observer

    def stop(self) -> None:
        with self._lock:
            observer, self._observer = self._observer, None
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        if observer is not None:
            observer.stop()
            observer.join(timeout=5)

    def _poke(self) -> None:
        with self._lock:
            if self._observer is None:
                return
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            if self._timer is None:
                return
            self._timer = None
        self._on_change()


class _Handler(FileSystemEventHandler):
    def __init__(self, poke: Callable[[], None]):
        self._poke = poke

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type not in ("created", "modified", "deleted", "moved"):
            return  # open/close notifications do not change content
        if event.is_directory and event.event_type == "modified":
            return  # a folder's own timestamp; the file events inside it are what matter
        names = [Path(str(p)).name for p in (event.src_path, getattr(event, "dest_path", "")) if p]
        if names and all(name.startswith("~$") for name in names):
            return  # Office lock files
        self._poke()
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_watcher.py -v`
Expected: 4 passed.
Then: `uv run pytest; uv run ruff format; uv run ruff check; uv run ruff format --check`

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml uv.lock src/tamra/ingest/watcher.py tests/test_watcher.py
git commit -m "feat(ingest): debounced folder watcher"
```

---

### Task 7: Hybrid retriever

**Files:**
- Create: `src/tamra/retriever.py`
- Test: `tests/test_retriever.py`

**Interfaces:**
- Consumes: Task 1 `Store.search_dense`, `Store.search_keyword`.
- Produces: `RRF_K = 60`, `CANDIDATES = 30`, `MAX_FTS_TERMS = 64`; `Hit(chunk_id, score, similarity)` (`similarity` is the dense cosine similarity, `None` for keyword-only hits); `query_text(question, previous_question) -> str`; `fts_query(text, max_terms=MAX_FTS_TERMS) -> str | None` (quoted character trigrams joined by `OR`; `None` when no word has 3 characters — trigram FTS cannot match shorter strings, spec §14); `hybrid_search(store, collection_id, vector, fts, k=CANDIDATES) -> list[Hit]` (RRF-fused, best first); `best_similarity(hits) -> float` (0.0 when no dense hit).

- [ ] **Step 1: Write the failing tests**

`tests/test_retriever.py`:

```python
import numpy as np
import pytest
from fakes import FakeEmbedder

from tamra.retriever import Hit, best_similarity, fts_query, hybrid_search, query_text
from tamra.store import ChunkInput, Store


def test_query_text_adds_the_previous_question():
    assert query_text("and item 2?", None) == "and item 2?"
    assert query_text("and item 2?", "list the fees") == "list the fees\nand item 2?"


def test_fts_query_quotes_unique_trigrams():
    assert fts_query("Lease lease") == '"lea" OR "eas" OR "ase"'
    assert fts_query('say "hello"') == '"say" OR "hel" OR "ell" OR "llo"'
    assert fts_query("a ab, 12") is None


def test_fts_query_keeps_thai_marks_inside_words():
    grams = fts_query("เช่าบ้าน").split(" OR ")
    assert len(grams) == 6
    assert grams[0] == '"เช่"'


def test_fts_query_is_capped():
    assert fts_query("abcdefghijklmnopqrstuvwxyz" * 5, max_terms=10).count(" OR ") == 9


class _FakeStore:
    def __init__(self, dense, keyword):
        self.dense, self.keyword = dense, keyword

    def search_dense(self, collection_id, vector, k):
        return self.dense

    def search_keyword(self, collection_id, fts, k):
        if self.keyword is None:
            raise AssertionError("keyword search must not run without a query")
        return self.keyword


def test_rank_fusion_rewards_chunks_found_both_ways():
    store = _FakeStore(dense=[(1, 0.9), (2, 0.5)], keyword=[3, 1])
    hits = hybrid_search(store, 1, np.zeros(1024, dtype=np.float32), '"abc"')
    assert [h.chunk_id for h in hits] == [1, 3, 2]
    assert hits[0].score == pytest.approx(1 / 61 + 1 / 62)
    assert (hits[1].similarity, hits[2].similarity) == (None, 0.5)


def test_without_a_keyword_query_only_dense_results_count():
    store = _FakeStore(dense=[(5, 0.7)], keyword=None)
    hits = hybrid_search(store, 1, np.zeros(1024, dtype=np.float32), None)
    assert [(h.chunk_id, h.similarity) for h in hits] == [(5, 0.7)]


def test_best_similarity():
    assert best_similarity([]) == 0.0
    assert best_similarity([Hit(1, 0.1, None), Hit(2, 0.05, 0.42), Hit(3, 0.02, 0.61)]) == 0.61


def test_hybrid_search_finds_the_matching_chunk_in_a_real_store(tmp_path):
    store = Store.open(tmp_path / "t.db")
    collection = store.replace_collection("Docs", "C:/docs", "m")
    embedder = FakeEmbedder()
    texts = [
        "The lease term is three years",
        "Parking costs fifty baht",
        "Pets are welcome in the garden",
    ]
    file_id = store.add_file(collection.id, "a.md", 1, 1.0)
    chunks = [
        ChunkInput(t, {"kind": "text", "line_start": i + 1, "line_end": i + 1})
        for i, t in enumerate(texts)
    ]
    store.replace_file_chunks(
        file_id, chunks, embedder.embed(texts), content_hash="h", note=None
    )
    question = "how long is the lease term"
    hits = hybrid_search(store, collection.id, embedder.embed([question])[0], fts_query(question))
    assert store.get_chunks([hits[0].chunk_id])[0].text == "The lease term is three years"
    assert hits[0].similarity > 0.3
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    store.close()
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_retriever.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tamra.retriever'`.

- [ ] **Step 3: Implement**

`src/tamra/retriever.py`:

```python
"""Hybrid retrieval (spec §6): dense KNN plus FTS5 trigram keywords, fused with RRF."""

from dataclasses import dataclass

import numpy as np

from tamra.store import Store

RRF_K = 60
CANDIDATES = 30
MAX_FTS_TERMS = 64


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    score: float  # Reciprocal Rank Fusion score
    similarity: float | None  # cosine similarity from the dense search; None if keyword-only


def query_text(question: str, previous_question: str | None) -> str:
    """The latest question plus the previous one, so short follow-ups stay on topic."""
    if not previous_question:
        return question
    return f"{previous_question}\n{question}"


def fts_query(text: str, max_terms: int = MAX_FTS_TERMS) -> str | None:
    """OR the text's character trigrams; None when no word reaches 3 characters.

    The trigram index matches any 3-character substring and BM25 weighs rare trigrams more,
    so this also works for Thai and Chinese text, which has no spaces between words.
    """
    grams: list[str] = []
    seen: set[str] = set()
    for word in _words(text):
        for i in range(len(word) - 2):
            gram = word[i : i + 3]
            if gram not in seen:
                seen.add(gram)
                grams.append(gram)
    if not grams:
        return None
    return " OR ".join('"' + gram.replace('"', '""') + '"' for gram in grams[:max_terms])


def _words(text: str) -> list[str]:
    """Runs of letters, digits, and Thai characters (vowel and tone marks included)."""
    words: list[str] = []
    current: list[str] = []
    for char in text.lower():
        if char.isalnum() or 0x0E00 <= ord(char) <= 0x0E7F:
            current.append(char)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def hybrid_search(
    store: Store, collection_id: int, vector: np.ndarray, fts: str | None, k: int = CANDIDATES
) -> list[Hit]:
    """Dense and keyword candidates fused by Reciprocal Rank Fusion, best first."""
    dense = store.search_dense(collection_id, vector, k)
    keyword = store.search_keyword(collection_id, fts, k) if fts else []
    similarity = dict(dense)
    scores: dict[int, float] = {}
    for rank, (chunk_id, _) in enumerate(dense, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    for rank, chunk_id in enumerate(keyword, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    hits = [Hit(chunk_id, score, similarity.get(chunk_id)) for chunk_id, score in scores.items()]
    return sorted(hits, key=lambda hit: (-hit.score, hit.chunk_id))


def best_similarity(hits: list[Hit]) -> float:
    return max((h.similarity for h in hits if h.similarity is not None), default=0.0)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_retriever.py -v`
Expected: all pass.
Then: `uv run pytest; uv run ruff format; uv run ruff check --fix; uv run ruff format --check`

- [ ] **Step 5: Commit**

```powershell
git add src/tamra/retriever.py tests/test_retriever.py
git commit -m "feat(retriever): hybrid dense + trigram keyword search with reciprocal rank fusion"
```

---

### Task 8: Owned llama-server — Job Object, per-launch API key, LocalLLM

**Files:**
- Create: `src/tamra/winjob.py`, `src/tamra/llm/runtime.py`
- Modify: `src/tamra/llm/llama_server.py`, `src/tamra/llm/openai_compat.py` (remove dead code), `tests/test_llama_server.py`, `tests/test_openai_compat.py`
- Test: `tests/test_winjob.py`, `tests/test_runtime.py`

**Interfaces:**
- Consumes: M0 `LlamaServer`, `LlamaServerError`, `OpenAICompatibleLLM`.
- Produces:
  - `tamra.winjob.KillOnCloseJob()` with `add(proc: subprocess.Popen)` and `close()` (idempotent; closing kills every process still in the job — so llama-server dies with Tamra even after a crash).
  - `LlamaServer(exe, model, log_file, ctx_size=4096, gpu=True, device=None, api_key=None)`: passes `--api-key <key>` when given; puts the child in a `KillOnCloseJob` (logs a warning and carries on if Windows refuses); `alive() -> bool`; `start(timeout)` now shares one budget across the GPU and CPU attempts (GPU attempt gets 60% when a CPU retry follows); log-file and folder errors raise `LlamaServerError`.
  - `tamra.llm.runtime.LocalLLM(exe, model, log_file, ctx_size=8192, server_factory=LlamaServer)` with `client() -> OpenAICompatibleLLM` (starts llama-server on first use with a fresh random API key; restarts it if it died; serialized by a lock), `close()`, `label` (model file stem), `base_url` (`None` when not running). `client()` raises `LlamaServerError("Local model not found: <path>")` when the GGUF is missing.

- [ ] **Step 1: Write the failing tests**

`tests/test_winjob.py` (`ping.exe` is used because it has no child processes):

```python
import os
import subprocess
from pathlib import Path

from tamra.winjob import KillOnCloseJob

PING = Path(os.environ["SystemRoot"]) / "System32" / "PING.EXE"


def test_closing_the_job_kills_its_process():
    proc = subprocess.Popen(
        [str(PING), "-n", "60", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        job = KillOnCloseJob()
        job.add(proc)
        job.close()
        assert proc.wait(timeout=10) is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_close_is_idempotent():
    job = KillOnCloseJob()
    job.close()
    job.close()
```

`tests/test_runtime.py`:

```python
import pytest

from tamra.llm.llama_server import LlamaServerError
from tamra.llm.runtime import LocalLLM


class FakeServer:
    instances: list["FakeServer"] = []

    def __init__(self, exe, model, log_file, ctx_size, api_key):
        self.api_key = api_key
        self.base_url = "http://127.0.0.1:9"
        self.started = self.stopped = False
        self.dead = False
        FakeServer.instances.append(self)

    def start(self):
        self.started = True
        return self

    def alive(self):
        return self.started and not self.stopped and not self.dead

    def stop(self):
        self.stopped = True


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"gguf")
    FakeServer.instances = []
    return path


def test_the_server_starts_on_first_use_with_a_random_key(model, tmp_path):
    llm = LocalLLM(tmp_path / "x.exe", model, tmp_path / "l.log", server_factory=FakeServer)
    assert FakeServer.instances == [] and llm.base_url is None
    client = llm.client()
    assert llm.client() is client
    [server] = FakeServer.instances
    assert server.started and len(server.api_key) >= 32
    assert llm.base_url == server.base_url
    assert llm.label == "m"
    llm.close()
    assert server.stopped and llm.base_url is None


def test_a_dead_server_is_replaced_with_a_new_key(model, tmp_path):
    llm = LocalLLM(tmp_path / "x.exe", model, tmp_path / "l.log", server_factory=FakeServer)
    llm.client()
    FakeServer.instances[0].dead = True
    llm.client()
    first, second = FakeServer.instances
    assert first.stopped and second.started
    assert first.api_key != second.api_key
    llm.close()


def test_a_missing_model_is_reported(tmp_path):
    llm = LocalLLM(tmp_path / "x.exe", tmp_path / "none.gguf", tmp_path / "l.log",
                   server_factory=FakeServer)
    with pytest.raises(LlamaServerError, match="Local model not found"):
        llm.client()


@pytest.mark.assets
def test_the_real_server_requires_the_key(llama_exe, qwen_gguf, tmp_path):
    import httpx

    llm = LocalLLM(llama_exe, qwen_gguf, tmp_path / "llama.log")
    try:
        text = "".join(
            llm.client().generate([{"role": "user", "content": "Reply with one word: OK"}], 8)
        )
        assert text.strip()
        anonymous = httpx.get(f"{llm.base_url}/v1/models", trust_env=False, timeout=5)
        assert anonymous.status_code == 401
    finally:
        llm.close()
```

Append to `tests/test_llama_server.py`:

```python
def test_the_api_key_is_passed_when_given(tmp_path):
    keyed = LlamaServer(Path("x.exe"), Path("m.gguf"), tmp_path / "l.txt", api_key="k1")
    args = keyed.args(gpu=True)
    assert args[args.index("--api-key") + 1] == "k1"
    assert args[-2:] == ["-ngl", "99"]
    plain = LlamaServer(Path("x.exe"), Path("m.gguf"), tmp_path / "l.txt")
    assert "--api-key" not in plain.args(gpu=True)


def test_an_unwritable_log_raises_llama_server_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    srv = LlamaServer(Path("x.exe"), Path("m.gguf"), blocker / "logs" / "l.txt")
    with pytest.raises(LlamaServerError, match="cannot write"):
        srv.start(timeout=1)
```

Also in `tests/test_llama_server.py`, extend the existing stub tests (they use `_StubLlamaServer` and the `spawned` fixture):
- In the test where the GPU attempt fails and the CPU attempt succeeds, after `start()` assert `srv.alive() is True` and `srv._job is not None` (the child is in a kill-on-close job); after `srv.stop()` assert `srv.alive() is False` and `srv._job is None`.
- Add a test where both attempts hang and `start(timeout=2)` is called: it must raise `LlamaServerError` and take less than 3.5 seconds in total (`time.monotonic()` before and after), proving the two attempts share one budget.
- In `test_proxy_bypass`, also `monkeypatch.delenv("NO_PROXY", raising=False)` and `monkeypatch.delenv("no_proxy", raising=False)`, so an inherited bypass cannot make the test pass without the fix.

Append to `tests/test_openai_compat.py` (add `import threading` and `import time` to its imports):

```python
def test_tokens_arrive_before_the_stream_ends():
    gate = threading.Event()

    def body():
        yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
        gate.wait(5)
        yield (
            b'data: {"choices":[{"delta":{"content":"second"},"finish_reason":"stop"}]}\n\n'
            b"data: [DONE]\n\n"
        )

    llm = OpenAICompatibleLLM(
        "http://llm.test",
        "m",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body())),
    )
    stream = llm.generate([{"role": "user", "content": "hi"}])
    started = time.monotonic()
    assert next(stream) == "first"
    assert time.monotonic() - started < 2  # did not wait for the rest of the body
    gate.set()
    assert list(stream) == ["second"]


def test_closing_the_stream_closes_the_response():
    closed = threading.Event()

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'

        def close(self):
            closed.set()

    llm = OpenAICompatibleLLM(
        "http://llm.test",
        "m",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Body())),
    )
    stream = llm.generate([{"role": "user", "content": "hi"}])
    assert next(stream) == "a"
    stream.close()
    assert closed.is_set()


def test_thai_and_chinese_tokens_stream_intact():
    body = (
        'data: {"choices":[{"delta":{"content":"สัญญาเช่า"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"三年"},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    ).encode()
    llm = OpenAICompatibleLLM(
        "http://llm.test",
        "m",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body)),
    )
    assert list(llm.generate([{"role": "user", "content": "hi"}])) == ["สัญญาเช่า", "三年"]
```

In `tests/test_openai_compat.py`, `test_loopback_client_bypasses_proxy`: also `monkeypatch.delenv("NO_PROXY", raising=False)` and `monkeypatch.delenv("no_proxy", raising=False)`.

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_winjob.py tests/test_runtime.py tests/test_llama_server.py tests/test_openai_compat.py -v`
Expected: `test_winjob.py` and `test_runtime.py` fail at collection (`ModuleNotFoundError`); the new `test_llama_server.py` tests fail (`unexpected keyword argument 'api_key'`, no `alive`, no shared budget). The three new `test_openai_compat.py` tests may already pass — they pin behavior the next step must keep.

- [ ] **Step 3: Implement**

`src/tamra/winjob.py`:

```python
"""A Windows Job Object that kills its processes when its last handle closes.

Tamra holds the only handle, so when Tamra exits or crashes, Windows ends llama-server too.
"""

import ctypes
import subprocess
from ctypes import wintypes

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
_kernel32.CreateJobObjectW.restype = wintypes.HANDLE
_kernel32.SetInformationJobObject.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
]
_kernel32.SetInformationJobObject.restype = wintypes.BOOL
_kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
_kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


class KillOnCloseJob:
    def __init__(self) -> None:
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def add(self, proc: subprocess.Popen) -> None:
        if not _kernel32.AssignProcessToJobObject(self._handle, int(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        """Close the job; any process still in it is killed."""
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = None
```

In `src/tamra/llm/llama_server.py`:
- add `import logging`, `from tamra.winjob import KillOnCloseJob`, and `log = logging.getLogger(__name__)`;
- give `__init__` a final parameter `api_key: str | None = None`, store `self.api_key = api_key`, and initialise `self._job: KillOnCloseJob | None = None`;
- replace `args`, `start`, and `stop`, and add `alive`:

```python
    def args(self, gpu: bool) -> list[str]:
        args = [str(self.exe), "-m", str(self.model), "-c", str(self.ctx_size)]
        args += ["--host", "127.0.0.1", "--port", str(self.port)]
        if self.api_key:
            args += ["--api-key", self.api_key]
        if not gpu:
            return args + ["--device", "none"]
        if self.device:
            args += ["--device", self.device]
        return args + ["-ngl", "99"]

    def start(self, timeout: float = 120.0) -> "LlamaServer":
        """Start llama-server, GPU first and then CPU, within one overall time budget."""
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise LlamaServerError(f"cannot write the log at {self.log_file}: {e}") from e
        deadline = time.monotonic() + timeout
        attempts = [True, False] if self.gpu else [False]
        for gpu in attempts:
            remaining = deadline - time.monotonic()
            budget = remaining * 0.6 if gpu and len(attempts) > 1 else remaining
            try:
                try:
                    self._log = self.log_file.open("ab")
                except OSError as e:
                    raise LlamaServerError(f"cannot write the log at {self.log_file}: {e}") from e
                try:
                    self._proc = subprocess.Popen(
                        self.args(gpu),
                        stdout=self._log,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except OSError as e:
                    raise LlamaServerError(f"cannot launch {self.exe}: {e}") from e
                self._job = _kill_on_close(self._proc)
                if self._wait_healthy(budget):
                    self.gpu_used = gpu
                    return self
                self.stop()
            except BaseException:
                self.stop()
                raise
        raise LlamaServerError(f"llama-server failed to start; see {self.log_file}")

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        self._proc = None
        if self._job is not None:
            self._job.close()
            self._job = None
        if self._log is not None:
            self._log.close()
            self._log = None
```

and add this module-level helper at the end of the file:

```python
def _kill_on_close(proc: subprocess.Popen) -> KillOnCloseJob | None:
    """Tie proc's lifetime to Tamra's. If Windows refuses, keep running without the job."""
    job = None
    try:
        job = KillOnCloseJob()
        job.add(proc)
        return job
    except OSError as e:
        if job is not None:
            job.close()
        log.warning("llama-server was not placed in a kill-on-close job: %s", e)
        return None
```

In `src/tamra/llm/openai_compat.py`, `generate()`: delete the no-op `except LLMError: raise` clause (lines 125-126) and the dead `found_completion = True` that sits right before `return` in the `[DONE]` branch (line 83). Nothing else changes.

`src/tamra/llm/runtime.py`:

```python
"""The local LLM: owns the llama-server child process and hands out a client for it."""

import secrets
import threading
from collections.abc import Callable
from pathlib import Path

from tamra.llm.llama_server import LlamaServer, LlamaServerError
from tamra.llm.openai_compat import OpenAICompatibleLLM


class LocalLLM:
    def __init__(
        self,
        exe: Path,
        model: Path,
        log_file: Path,
        ctx_size: int = 8192,
        server_factory: Callable[..., LlamaServer] = LlamaServer,
    ):
        self._exe, self._model, self._log_file = exe, model, log_file
        self._ctx_size = ctx_size
        self._factory = server_factory
        self._lock = threading.Lock()
        self._server: LlamaServer | None = None
        self._client: OpenAICompatibleLLM | None = None

    @property
    def label(self) -> str:
        return self._model.stem

    @property
    def base_url(self) -> str | None:
        with self._lock:
            return self._server.base_url if self._server is not None else None

    def client(self) -> OpenAICompatibleLLM:
        """A client for the running server, starting (or restarting) llama-server if needed."""
        with self._lock:
            if self._server is not None and not self._server.alive():
                self._close_locked()
            if self._client is None:
                if not self._model.is_file():
                    raise LlamaServerError(f"Local model not found: {self._model}")
                key = secrets.token_urlsafe(32)
                server = self._factory(
                    self._exe, self._model, self._log_file, ctx_size=self._ctx_size, api_key=key
                )
                server.start()
                self._server = server
                self._client = OpenAICompatibleLLM(server.base_url, "local", api_key=key)
            return self._client

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._server is not None:
            self._server.stop()
            self._server = None
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_winjob.py tests/test_runtime.py tests/test_llama_server.py tests/test_openai_compat.py -v; uv run pytest -m assets tests/test_runtime.py tests/test_llama_server.py -v`
Expected: all pass, including the real llama-server tests (the server refuses `/v1/models` without the key).
Then: `uv run pytest; uv run ruff format; uv run ruff check --fix; uv run ruff format --check`

- [ ] **Step 5: Commit**

```powershell
git add src/tamra/winjob.py src/tamra/llm tests/test_winjob.py tests/test_runtime.py tests/test_llama_server.py tests/test_openai_compat.py
git commit -m "feat(llm): app-owned llama-server with per-launch API key and kill-on-close job"
```

---

### Task 9: Answer service

**Files:**
- Create: `src/tamra/answer.py`
- Test: `tests/test_answer.py`

**Interfaces:**
- Consumes: Task 1/2 `Store` (`get_collection`, `get_chat`, `last_exchange`, `add_user_message`, `add_assistant_message`, `get_chunks`), `SourceRecord`; Task 7 `query_text`, `fts_query`, `hybrid_search`, `best_similarity`; M0 `LLMError`, `LlamaServerError`, `Message`.
- Produces:
  - `LANGUAGE_NAMES`, `NOT_FOUND` (per language: `th`, `en`, `zh`), `SYSTEM_PROMPT`.
  - `AnswerSettings(top_k=4, min_similarity=0.5, max_tokens=1024)` (Task 16 tunes `min_similarity`).
  - `detect_language(text) -> "th" | "zh" | "en"`; `location_label(location) -> str` (`"p. 3"`, `"pp. 3-4"`, `"Leave > Sick, paras. 5-7"`, `"para. 1"`, `"line 7"`, `"lines 7-9"`); `source_payload(source: SourceRecord) -> dict` with keys `n`, `file`, `label`, `text`, `location`; `build_messages(question, previous, sources, language) -> list[Message]`.
  - `AnswerService(store, embed_query: Callable[[str], np.ndarray], llm: Callable[[], LLMLike], *, model_id: str, llm_label: Callable[[], str], settings: AnswerSettings | None = None)` with `ask(chat_id, question) -> Iterator[dict]` and `cancel()`.
  - Event dicts, in order: `{"type": "sources", "sources": [source_payload...]}`, then `{"type": "token", "text": str}` (repeated), then `{"type": "error", "message": str}` if something failed, then `{"type": "done", "message_id": int}` when an answer was saved. Errors before answering (no folder, stale index, unknown chat, busy, embedding model unavailable) yield a single `error` event.
- Behavior (spec §6): the question is saved first; retrieval uses the question plus the previous question; when the best dense similarity is below `min_similarity`, the fixed "not found" text in the question's language is the answer and the LLM is not called; otherwise the top `top_k` chunks become numbered sources, the answer streams, and the answer is saved with every source the model was given, even when cancelled or closed part-way (the user saw it). One answer runs at a time.

- [ ] **Step 1: Write the failing tests**

`tests/test_answer.py`:

```python
import pytest
from fakes import FakeEmbedder, FakeLLM

from tamra.answer import (
    AnswerService,
    AnswerSettings,
    detect_language,
    location_label,
)
from tamra.llm.llama_server import LlamaServerError
from tamra.llm.openai_compat import LLMError
from tamra.store import ChunkInput, Store


@pytest.fixture
def env(tmp_path):
    store = Store.open(tmp_path / "t.db")
    collection = store.replace_collection("Docs", "C:/docs", "m")
    embedder = FakeEmbedder()
    texts = ["The lease term is three years", "Parking costs fifty baht"]
    file_id = store.add_file(collection.id, "lease.md", 1, 1.0)
    chunks = [
        ChunkInput(t, {"kind": "text", "line_start": i + 1, "line_end": i + 1})
        for i, t in enumerate(texts)
    ]
    store.replace_file_chunks(file_id, chunks, embedder.embed(texts), content_hash="h", note=None)

    def make(llm):
        return AnswerService(
            store,
            lambda text: embedder.embed([text])[0],
            lambda: llm() if callable(llm) and not hasattr(llm, "generate") else llm,
            model_id="m",
            llm_label=lambda: "fake-model",
            settings=AnswerSettings(min_similarity=0.3),
        )

    yield store, make, store.create_chat()
    store.close()


def test_an_answer_streams_sources_then_tokens_and_is_saved(env):
    store, make, chat = env
    llm = FakeLLM()
    events = list(make(llm).ask(chat.id, "How long is the lease term?"))
    assert [e["type"] for e in events] == ["sources", "token", "token", "token", "done"]
    first = events[0]["sources"][0]
    assert (first["n"], first["file"], first["label"]) == (1, "lease.md", "line 1")
    assert first["text"] == "The lease term is three years"
    saved = store.list_messages(chat.id)
    assert [m.role for m in saved] == ["user", "assistant"]
    assert saved[1].content == "The lease is three years [1]."
    assert (saved[1].provider, saved[1].model) == ("local", "fake-model")
    assert [s.n for s in saved[1].sources] == [1, 2]
    assert events[-1]["message_id"] == saved[1].id
    system = llm.calls[0][0]["content"]
    assert "[1] lease.md (line 1)" in system
    assert "in English" in system


def test_an_unrelated_question_gets_not_found_without_the_llm(env):
    store, make, chat = env
    llm = FakeLLM()
    events = list(make(llm).ask(chat.id, "ใครเป็นผู้จัดการฝ่ายขาย"))
    assert [e["type"] for e in events] == ["sources", "token", "done"]
    assert events[1]["text"] == "ไม่พบข้อมูลนี้ในเอกสาร"
    assert llm.calls == []
    assert store.list_messages(chat.id)[1].content == "ไม่พบข้อมูลนี้ในเอกสาร"


def test_follow_ups_carry_the_previous_exchange(env):
    store, make, chat = env
    llm = FakeLLM()
    service = make(llm)
    list(service.ask(chat.id, "How long is the lease term?"))
    list(service.ask(chat.id, "and parking?"))
    second = llm.calls[1]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[1]["content"] == "How long is the lease term?"
    assert second[3]["content"] == "and parking?"


def test_cancel_stops_generation_and_keeps_the_partial_answer(env):
    store, make, chat = env
    service = make(FakeLLM(("one ", "two ", "three ")))
    stream = service.ask(chat.id, "How long is the lease term?")
    assert next(stream)["type"] == "sources"
    assert next(stream) == {"type": "token", "text": "one "}
    service.cancel()
    assert [e["type"] for e in stream] == ["done"]
    assert store.list_messages(chat.id)[1].content == "one "


def test_closing_the_stream_saves_the_partial_answer_and_frees_the_service(env):
    store, make, chat = env
    service = make(FakeLLM())
    stream = service.ask(chat.id, "How long is the lease term?")
    next(stream)
    next(stream)
    stream.close()
    assert store.list_messages(chat.id)[1].content == "The lease is three years "
    assert [e["type"] for e in service.ask(chat.id, "lease term?")][-1] == "done"


def test_a_second_question_while_answering_is_refused(env):
    store, make, chat = env
    service = make(FakeLLM())
    stream = service.ask(chat.id, "How long is the lease term?")
    next(stream)
    assert list(service.ask(chat.id, "lease?")) == [
        {"type": "error", "message": "Another answer is still being written."}
    ]
    list(stream)


def test_llm_errors_are_reported_and_partial_text_kept(env):
    store, make, chat = env

    class Broken:
        def generate(self, messages, max_tokens=1024):
            yield "partial "
            raise LLMError("HTTP 500: boom")

    events = list(make(Broken()).ask(chat.id, "How long is the lease term?"))
    assert [e["type"] for e in events][-2:] == ["error", "done"]
    assert events[-2]["message"] == "HTTP 500: boom"
    assert store.list_messages(chat.id)[1].content == "partial "


def test_a_server_that_cannot_start_is_reported(env):
    store, make, chat = env

    def broken():
        raise LlamaServerError("Local model not found: m.gguf")

    events = list(make(broken).ask(chat.id, "How long is the lease term?"))
    assert events[-1] == {"type": "error", "message": "Local model not found: m.gguf"}
    assert [m.role for m in store.list_messages(chat.id)] == ["user"]


def test_problems_before_answering_are_single_error_events(tmp_path):
    store = Store.open(tmp_path / "t.db")
    service = AnswerService(
        store, lambda t: None, lambda: FakeLLM(), model_id="m", llm_label=lambda: "x"
    )
    chat = store.create_chat()
    assert list(service.ask(chat.id, "q")) == [
        {"type": "error", "message": "Choose a folder of documents first."}
    ]
    store.replace_collection("Docs", "C:/docs", "other-model")
    assert list(service.ask(chat.id, "q"))[0]["message"].startswith("The index was built with")
    store.replace_collection("Docs", "C:/docs", "m")
    assert list(service.ask(999, "q")) == [{"type": "error", "message": "Chat 999 not found."}]
    store.close()


def test_detect_language():
    assert detect_language("ลาพักร้อนได้กี่วัน") == "th"
    assert detect_language("沙发保修几年？") == "zh"
    assert detect_language("How many days?") == "en"
    assert detect_language("1234 ?") == "en"
    assert detect_language("Tamra ตอบคำถามจากเอกสาร") == "th"


def test_location_labels():
    pdf = {"kind": "pdf", "page_start": 3, "page_end": 3, "char_start": 0, "char_end": 5}
    assert location_label(pdf) == "p. 3"
    assert location_label({**pdf, "page_end": 4}) == "pp. 3-4"
    docx = {"kind": "docx", "heading_path": ["Leave", "Sick"],
            "paragraph_start": 4, "paragraph_end": 6}
    assert location_label(docx) == "Leave > Sick, paras. 5-7"
    assert location_label({**docx, "heading_path": [], "paragraph_end": 4}) == "para. 5"
    assert location_label({"kind": "text", "line_start": 7, "line_end": 7}) == "line 7"
    assert location_label({"kind": "text", "line_start": 7, "line_end": 9}) == "lines 7-9"
```

The fixture's `make(llm)` takes either an object with `generate` (used as the LLM) or a zero-argument callable that builds one (used to simulate a server that fails to start).

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_answer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tamra.answer'`.

- [ ] **Step 3: Implement**

`src/tamra/answer.py`:

```python
"""Answering (spec §6): retrieve, prompt with numbered sources, stream, and save the answer."""

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from tamra.llm.llama_server import LlamaServerError
from tamra.llm.openai_compat import LLMError, Message
from tamra.retriever import best_similarity, fts_query, hybrid_search, query_text
from tamra.store import SourceRecord, Store

LANGUAGE_NAMES = {"th": "Thai", "en": "English", "zh": "Chinese"}
NOT_FOUND = {
    "th": "ไม่พบข้อมูลนี้ในเอกสาร",
    "en": "Not found in the documents.",
    "zh": "文档中未找到相关信息。",
}
SYSTEM_PROMPT = """You answer questions using only the numbered sources below.
Rules:
- Use only facts stated in the sources. If they do not answer the question, say so plainly.
- Cite each sentence that uses a source with its number in brackets, like [1] or [2][3].
- Write the whole answer in {language}, the language of the question.
- Be concise.

Sources:

{sources}"""


class LLMLike(Protocol):
    def generate(self, messages: list[Message], max_tokens: int = 1024) -> Iterator[str]: ...


@dataclass(frozen=True)
class AnswerSettings:
    top_k: int = 4  # spec §6: about 4 sources for small local models
    min_similarity: float = 0.5  # below this best dense similarity: "not found" (Task 16 tunes)
    max_tokens: int = 1024


def detect_language(text: str) -> str:
    """'th', 'zh', or 'en': whichever script dominates (English when there is no script)."""
    thai = sum(1 for c in text if 0x0E00 <= ord(c) <= 0x0E7F)
    cjk = sum(1 for c in text if 0x4E00 <= ord(c) <= 0x9FFF or 0x3400 <= ord(c) <= 0x4DBF)
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    count, language = max((thai, "th"), (cjk, "zh"), (latin, "en"))
    return language if count else "en"


def location_label(location: dict) -> str:
    """A short human-readable location: p. 3, Heading > Sub, para. 5, lines 7-9."""
    kind = location.get("kind")
    if kind == "pdf":
        start, end = location["page_start"], location["page_end"]
        return f"p. {start}" if start == end else f"pp. {start}-{end}"
    if kind == "docx":
        start, end = location["paragraph_start"] + 1, location["paragraph_end"] + 1
        paragraphs = f"para. {start}" if start == end else f"paras. {start}-{end}"
        path = " > ".join(location.get("heading_path") or [])
        return f"{path}, {paragraphs}" if path else paragraphs
    if kind == "text":
        start, end = location["line_start"], location["line_end"]
        return f"line {start}" if start == end else f"lines {start}-{end}"
    return ""


def source_payload(source: SourceRecord) -> dict:
    """What the UI shows for a source."""
    return {
        "n": source.n,
        "file": source.rel_path,
        "label": location_label(source.location),
        "text": source.text,
        "location": source.location,
    }


def build_messages(
    question: str,
    previous: tuple[str | None, str | None],
    sources: list[SourceRecord],
    language: str,
) -> list[Message]:
    blocks = "\n\n".join(
        f"[{s.n}] {s.rel_path} ({location_label(s.location)})\n{s.text}" for s in sources
    )
    messages: list[Message] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(language=LANGUAGE_NAMES[language], sources=blocks),
        }
    ]
    previous_question, previous_answer = previous
    if previous_question and previous_answer:
        messages.append({"role": "user", "content": previous_question})
        messages.append({"role": "assistant", "content": previous_answer[:600]})
    messages.append({"role": "user", "content": question})
    return messages


class AnswerService:
    def __init__(
        self,
        store: Store,
        embed_query: Callable[[str], np.ndarray],
        llm: Callable[[], LLMLike],
        *,
        model_id: str,
        llm_label: Callable[[], str],
        settings: AnswerSettings | None = None,
    ):
        self._store = store
        self._embed_query = embed_query
        self._llm = llm
        self._model_id = model_id
        self._llm_label = llm_label
        self._settings = settings or AnswerSettings()
        self._busy = threading.Lock()
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Stop the answer being written; what was written so far is kept."""
        self._cancel.set()

    def ask(self, chat_id: int, question: str) -> Iterator[dict]:
        if not self._busy.acquire(blocking=False):
            yield {"type": "error", "message": "Another answer is still being written."}
            return
        self._cancel.clear()
        try:
            yield from self._ask(chat_id, question)
        finally:
            self._busy.release()

    def _ask(self, chat_id: int, question: str) -> Iterator[dict]:
        collection = self._store.get_collection()
        if collection is None:
            yield {"type": "error", "message": "Choose a folder of documents first."}
            return
        if collection.embedding_model_id != self._model_id:
            yield {
                "type": "error",
                "message": "The index was built with a different embedding model. "
                "Rebuild the index.",
            }
            return
        if self._store.get_chat(chat_id) is None:
            yield {"type": "error", "message": f"Chat {chat_id} not found."}
            return
        previous = self._store.last_exchange(chat_id)
        self._store.add_user_message(chat_id, question)
        language = detect_language(question)
        query = query_text(question, previous[0])
        try:
            vector = self._embed_query(query)
        except Exception as e:  # the model files are missing or cannot be loaded
            yield {"type": "error", "message": f"Embedding model unavailable: {e}"}
            return
        hits = hybrid_search(self._store, collection.id, vector, fts_query(query))
        if best_similarity(hits) < self._settings.min_similarity:
            text = NOT_FOUND[language]
            message_id = self._store.add_assistant_message(
                chat_id, text, provider=None, model=None, sources=[]
            )
            yield {"type": "sources", "sources": []}
            yield {"type": "token", "text": text}
            yield {"type": "done", "message_id": message_id}
            return
        chunks = self._store.get_chunks([hit.chunk_id for hit in hits[: self._settings.top_k]])
        sources = [
            SourceRecord(n, c.id, c.file_id, c.rel_path, c.text, c.location, c.file_hash)
            for n, c in enumerate(chunks, start=1)
        ]
        yield {"type": "sources", "sources": [source_payload(s) for s in sources]}
        messages = build_messages(question, previous, sources, language)
        parts: list[str] = []
        error: str | None = None
        message_id: int | None = None
        try:
            try:
                client = self._llm()
                for token in client.generate(messages, self._settings.max_tokens):
                    if self._cancel.is_set():
                        break
                    parts.append(token)
                    yield {"type": "token", "text": token}
            except (LLMError, LlamaServerError) as e:
                error = str(e)
        finally:  # also runs when the consumer closes the stream: keep what the user saw
            content = "".join(parts)
            if content:
                message_id = self._store.add_assistant_message(
                    chat_id, content, provider="local", model=self._llm_label(), sources=sources
                )
        if error:
            yield {"type": "error", "message": error}
        if message_id is not None:
            yield {"type": "done", "message_id": message_id}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_answer.py -v`
Expected: all pass.
Then: `uv run pytest; uv run ruff format; uv run ruff check --fix; uv run ruff format --check`

- [ ] **Step 5: Commit**

```powershell
git add src/tamra/answer.py tests/test_answer.py
git commit -m "feat(answer): cited answers over SSE-ready events with not-found, cancel, and saved sources"
```

---

### Task 10: Embedder and selfcheck hardening

**Files:**
- Modify: `src/tamra/embedder.py`, `src/tamra/selfcheck.py`, `src/tamra/__main__.py`, `tests/conftest.py`, `tests/test_embedder.py` (rewrite), `tests/test_selfcheck.py`

**Interfaces:**
- Consumes: Task 2's `selfcheck.py`.
- Produces:
  - `tamra.embedder.MODEL_ID = "bge-m3-int8@Xenova/bge-m3:4de13258"` (the export pinned in `scripts/assets.json`; collections record it).
  - `Embedder(tokenizer: tokenizers.Tokenizer, session, max_length=512)` (`session` is anything with onnxruntime's `get_inputs()` and `run(output_names, feeds)`); `Embedder.load(model_dir, max_length=512) -> Embedder` (raises `FileNotFoundError` naming the missing `tokenizer.json` or `model.onnx`); `embed(texts, batch_size=16)` raises `ValueError` when `batch_size < 1`. Every caller now uses `Embedder.load(model_dir)`.
  - `run_selfcheck(embed_model_dir, llm_model, llama_exe, log_dir=None)`: `log_dir` defaults to `data_dir() / "logs"`, resolved only when the LLM check runs, so `tamra selfcheck` no longer creates the data folder.
- Absorbs these M0 review findings: embedder testable without assets, a multi-batch test, `batch_size` validation, the missing file named in errors, the `tokenizer.json` fixture guard, a stub-server test for `_check_llm`, the selfcheck `tokens` metric defined, and no eager `data_dir()` in the CLI.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_embedder.py`:

```python
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from tamra.embedder import MODEL_ID, Embedder

ROOT = Path(__file__).resolve().parents[1]
VOCAB = {"<s>": 0, "<pad>": 1, "<unk>": 3, "hello": 5, "world": 6, "tamra": 7}


def tiny_tokenizer() -> Tokenizer:
    tokenizer = Tokenizer(models.WordLevel(VOCAB, unk_token="<unk>"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    return tokenizer


class FakeSession:
    """Stands in for onnxruntime: each text's CLS row encodes its first token id."""

    def __init__(self, inputs=("input_ids", "attention_mask")):
        self.inputs = [SimpleNamespace(name=name) for name in inputs]
        self.feeds: list[dict] = []

    def get_inputs(self):
        return self.inputs

    def run(self, output_names, feeds):
        self.feeds.append(feeds)
        ids = feeds["input_ids"]
        hidden = np.zeros((*ids.shape, 1024), dtype=np.float32)
        hidden[:, 0, 0] = 3.0
        hidden[:, 0, 1] = 4.0 * ids[:, 0]
        return [hidden]


def test_batches_keep_the_input_order_and_rows_are_normalised():
    session = FakeSession()
    vectors = Embedder(tiny_tokenizer(), session).embed(
        ["hello", "world", "tamra", "hello world", "world"], batch_size=2
    )
    assert vectors.shape == (5, 1024)
    assert vectors.dtype == np.float32
    assert [len(feeds["input_ids"]) for feeds in session.feeds] == [2, 2, 1]
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-6)
    np.testing.assert_allclose(
        vectors[:, 1] / vectors[:, 0], [20 / 3, 24 / 3, 28 / 3, 20 / 3, 24 / 3], rtol=1e-5
    )


def test_a_batch_is_padded_and_masked():
    session = FakeSession()
    Embedder(tiny_tokenizer(), session).embed(["hello", "hello world tamra"])
    feeds = session.feeds[0]
    assert feeds["input_ids"].tolist() == [[5, 1, 1], [5, 6, 7]]
    assert feeds["attention_mask"].tolist() == [[1, 0, 0], [1, 1, 1]]
    assert "token_type_ids" not in feeds


def test_token_type_ids_are_sent_when_the_model_takes_them():
    session = FakeSession(inputs=("input_ids", "attention_mask", "token_type_ids"))
    Embedder(tiny_tokenizer(), session).embed(["hello world"])
    assert session.feeds[0]["token_type_ids"].tolist() == [[0, 0]]


def test_long_texts_are_truncated_to_max_length():
    session = FakeSession()
    Embedder(tiny_tokenizer(), session, max_length=2).embed(["hello world tamra"])
    assert session.feeds[0]["input_ids"].tolist() == [[5, 6]]


def test_no_texts_need_no_model_call():
    session = FakeSession()
    assert Embedder(tiny_tokenizer(), session).embed([]).shape == (0, 1024)
    assert session.feeds == []


def test_batch_size_must_be_positive():
    with pytest.raises(ValueError, match="batch_size"):
        Embedder(tiny_tokenizer(), FakeSession()).embed(["hello"], batch_size=0)


def test_load_names_the_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="tokenizer.json"):
        Embedder.load(tmp_path)
    tiny_tokenizer().save(str(tmp_path / "tokenizer.json"))
    with pytest.raises(FileNotFoundError, match="model.onnx"):
        Embedder.load(tmp_path)


def test_model_id_names_the_pinned_export():
    assets = json.loads((ROOT / "scripts" / "assets.json").read_text(encoding="utf-8"))
    revision = MODEL_ID.rsplit(":", 1)[1]
    assert f"/Xenova/bge-m3/resolve/{revision}" in assets["bge-m3-model"]["url"]


@pytest.fixture(scope="module")
def embedder(bge_dir):
    return Embedder.load(bge_dir)


@pytest.mark.assets
def test_shape_dtype_and_normalisation(embedder):
    vecs = embedder.embed(["hello", "สวัสดี", "你好"])
    assert vecs.shape == (3, 1024)
    assert vecs.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-4)


@pytest.mark.assets
def test_cross_lingual_similarity(embedder):
    th, en_same, zh_same, en_other = embedder.embed(
        [
            "แมวกำลังนอนหลับอยู่บนโซฟา",
            "A cat is sleeping on the sofa",
            "猫正在沙发上睡觉",
            "The stock market fell sharply today",
        ]
    )
    assert th @ en_same > th @ en_other + 0.1
    assert th @ zh_same > th @ en_other + 0.1


@pytest.mark.assets
def test_padding_changes_an_embedding_only_slightly(embedder):
    # The int8 export is dynamically quantized, so a vector depends a little on its batch
    # (M0 measured cosine ~0.989; the roadmap accepts this and lets the eval judge retrieval).
    short = "Tamra answers questions about documents."
    alone = embedder.embed([short])[0]
    batched = embedder.embed([short, short + " " + "padding forces longer batch. " * 20])[0]
    assert float(alone @ batched) >= 0.98
```

Append to `tests/test_selfcheck.py`:

```python
def test_selfcheck_cli_does_not_create_the_data_folder(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("TAMRA_DATA_DIR", str(data))
    assert main(["selfcheck", "--report", str(tmp_path / "r.json")]) == 0
    assert not data.exists()


def test_llm_check_reports_timings_from_a_stub_server(tmp_path, monkeypatch):
    import tamra.llm.llama_server as llama_server
    import tamra.llm.openai_compat as openai_compat

    class StubServer:
        base_url = "http://127.0.0.1:9"
        gpu_used = True

        def __init__(self, exe, model, log_file):
            self.log_file = log_file

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class StubClient:
        closed = False

        def __init__(self, base_url, model):
            pass

        def generate(self, messages, max_tokens=1024):
            yield from ["1", ",", " 2"]

        def close(self):
            StubClient.closed = True

    monkeypatch.setattr(llama_server, "LlamaServer", StubServer)
    monkeypatch.setattr(openai_compat, "OpenAICompatibleLLM", StubClient)
    report = run_selfcheck(None, tmp_path / "m.gguf", tmp_path / "x.exe", tmp_path)
    llm = report["checks"]["llm"]
    assert report["ok"] is True
    assert (llm["tokens"], llm["gpu_used"]) == (3, True)
    assert llm["first_token_s"] >= 0
    assert llm["tokens_per_sec"] > 0
    assert StubClient.closed
```

In `tests/conftest.py`, make `bge_dir` also require the tokenizer:

```python
@pytest.fixture(scope="session")
def bge_dir() -> Path:
    model_dir = ROOT / ".models" / "bge-m3"
    _require(model_dir / "tokenizer.json")
    return _require(model_dir / "model.onnx").parent
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_embedder.py tests/test_selfcheck.py -v`
Expected: `test_embedder.py` fails at collection (`ImportError: cannot import name 'MODEL_ID'`); `test_selfcheck_cli_does_not_create_the_data_folder` fails (the CLI creates the folder). The stub-server test may already pass; it pins `_check_llm`'s report.

- [ ] **Step 3: Implement**

Replace `src/tamra/embedder.py`:

```python
"""bge-m3 dense embeddings with ONNX Runtime on the CPU."""

from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MODEL_ID = "bge-m3-int8@Xenova/bge-m3:4de13258"  # the export pinned in scripts/assets.json
MODEL_FILES = ("tokenizer.json", "model.onnx")


class Embedder:
    """bge-m3 dense embeddings: CLS token of last_hidden_state, L2-normalised."""

    dim = 1024

    def __init__(self, tokenizer: Tokenizer, session, max_length: int = 512):
        """session: an onnxruntime.InferenceSession (or anything with get_inputs and run)."""
        self._tok = tokenizer
        self._tok.enable_truncation(max_length)
        self._tok.enable_padding(pad_id=tokenizer.token_to_id("<pad>"), pad_token="<pad>")
        self._session = session
        self._input_names = {i.name for i in session.get_inputs()}

    @classmethod
    def load(cls, model_dir: Path, max_length: int = 512) -> "Embedder":
        """Load tokenizer.json and model.onnx from model_dir."""
        for name in MODEL_FILES:
            if not (model_dir / name).is_file():
                raise FileNotFoundError(f"embedding model file not found: {model_dir / name}")
        tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        session = ort.InferenceSession(
            str(model_dir / "model.onnx"), providers=["CPUExecutionProvider"]
        )
        return cls(tokenizer, session, max_length)

    def embed(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        if batch_size < 1:
            raise ValueError(f"batch_size must be at least 1, got {batch_size}")
        out = []
        for start in range(0, len(texts), batch_size):
            encodings = self._tok.encode_batch(texts[start : start + batch_size])
            ids = np.array([e.ids for e in encodings], dtype=np.int64)
            feeds = {
                "input_ids": ids,
                "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
            }
            if "token_type_ids" in self._input_names:
                feeds["token_type_ids"] = np.zeros_like(ids)
            hidden = self._session.run(None, feeds)[0]
            cls = hidden[:, 0, :]
            out.append(cls / np.linalg.norm(cls, axis=1, keepdims=True))
        if not out:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack(out).astype(np.float32)
```

In `src/tamra/selfcheck.py`:
- in `_check_embedding`, replace `embedder = Embedder(model_dir)` with `embedder = Embedder.load(model_dir)`;
- replace `_check_llm`'s signature and its lines up to and including the `with LlamaServer(...) as srv:` line with:

```python
def _check_llm(llama_exe: Path, model: Path, log_dir: Path | None) -> dict:
    """Start llama-server and stream a short reply.

    `tokens` counts streamed text chunks; llama-server streams one token per chunk.
    """
    from tamra.llm.llama_server import LlamaServer
    from tamra.llm.openai_compat import OpenAICompatibleLLM
    from tamra.paths import data_dir

    log_file = (log_dir if log_dir is not None else data_dir() / "logs") / "llama-server.log"
    t0 = time.perf_counter()
    with LlamaServer(llama_exe, model, log_file) as srv:
```

  (the rest of the function body is unchanged);
- replace `run_selfcheck` with:

```python
def run_selfcheck(
    embed_model_dir: Path | None,
    llm_model: Path | None,
    llama_exe: Path,
    log_dir: Path | None = None,
) -> dict:
    """Run the checks; log_dir (for llama-server's log) defaults to data_dir()/logs."""
    checks = {"sqlite": _guard(_check_sqlite)}
    if embed_model_dir is not None:
        checks["embedding"] = _guard(lambda: _check_embedding(embed_model_dir))
    if llm_model is not None:
        checks["llm"] = _guard(lambda: _check_llm(llama_exe, llm_model, log_dir))
    return {"ok": all(c["ok"] for c in checks.values()), "checks": checks}
```

In `src/tamra/__main__.py`, the selfcheck branch no longer passes a log folder:

```python
    if args.command == "selfcheck":
        from tamra.paths import resource_dir
        from tamra.selfcheck import run_selfcheck

        report = run_selfcheck(
            args.embed_model_dir,
            args.llm_model,
            resource_dir() / "vendor" / "llama" / "llama-server.exe",
        )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_embedder.py tests/test_selfcheck.py -v; uv run pytest -m assets tests/test_embedder.py tests/test_selfcheck.py -v`
Expected: all pass, including the asset tests (padding cosine ≥ 0.98, full selfcheck).
Then: `uv run pytest; uv run ruff format; uv run ruff check --fix; uv run ruff format --check`

- [ ] **Step 5: Commit**

```powershell
git add src/tamra/embedder.py src/tamra/selfcheck.py src/tamra/__main__.py tests/conftest.py tests/test_embedder.py tests/test_selfcheck.py
git commit -m "feat(embedder): model id, injectable parts, and file checks; selfcheck without eager data folder"
```

---

### Task 11: Core, file logging, and model/data folders

**Files:**
- Create: `src/tamra/core.py`, `src/tamra/logs.py`
- Modify: `src/tamra/paths.py`, `tests/fakes.py` (add `FakeLocalLLM`), `tests/test_paths.py`
- Test: `tests/test_core.py`, `tests/test_logs.py`

**Interfaces:**
- Consumes: Tasks 1–10 (`Store`, `Indexer`, `FolderWatcher`, `AnswerService`, `AnswerSettings`, `LocalLLM`, `bge_token_spans`, `Embedder.load`, `MODEL_ID`).
- Produces:
  - `tamra.paths.data_dir()` (a blank `TAMRA_DATA_DIR` is ignored, a relative one is made absolute, a missing `LOCALAPPDATA` raises `RuntimeError` naming `TAMRA_DATA_DIR`); `tamra.paths.models_dir()` (`TAMRA_MODELS_DIR`, else the repo `.models` when running from source and it exists, else `data_dir()/models`).
  - `tamra.logs.setup_logging(log_dir, *, console=False) -> Path` (rotating `tamra.log`; calling it again replaces Tamra's handlers; `uvicorn.access` at WARNING) and `shutdown_logging()`.
  - `tamra.core.DEV_LLM_FILE = "qwen2.5-0.5b-instruct-q4_k_m.gguf"` (the local model until M2's catalog).
  - `Core(data_dir, models_dir, llama_exe, *, embedder_factory=None, token_spans_factory=None, llm=None, answer_settings=None, debounce=2.0)` with attributes `store`, `indexer`, `watcher`, `answers`, `llm`, and methods `embedder()` (loaded on first use, shared), `start()`, `set_collection(name, folder) -> Collection` (raises `ValueError` for a relative or missing folder; a blank name becomes the folder's name), `rebuild() -> bool`, `index_status() -> dict | None` (keys `stale`, `counts`, `current`, `error`, `problems`; each problem has `rel_path`, `status`, `error`), `shutdown()`.
  - `llm` objects provide `client()`, `close()`, and `label` (`LocalLLM` in production; `tests/fakes.py` `FakeLocalLLM` in tests).

- [ ] **Step 1: Write the failing tests**

Append to `tests/fakes.py`:

```python
class FakeLocalLLM:
    """Stands in for tamra.llm.runtime.LocalLLM."""

    label = "fake-model"

    def __init__(self, llm: "FakeLLM | None" = None):
        self.llm = llm or FakeLLM()
        self.closed = False

    def client(self) -> "FakeLLM":
        return self.llm

    def close(self) -> None:
        self.closed = True
```

Append to `tests/test_paths.py` (add `import sys` and `import pytest` to its imports):

```python
def test_a_blank_override_falls_back_to_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("TAMRA_DATA_DIR", "  ")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.data_dir() == tmp_path / "Tamra"


def test_a_relative_override_is_made_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAMRA_DATA_DIR", "rel")
    assert paths.data_dir() == tmp_path / "rel"


def test_a_missing_localappdata_says_what_to_do(monkeypatch):
    monkeypatch.delenv("TAMRA_DATA_DIR", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(RuntimeError, match="TAMRA_DATA_DIR"):
        paths.data_dir()


def test_resource_dir_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.resource_dir() == tmp_path


def test_models_dir_prefers_the_override(monkeypatch, tmp_path):
    monkeypatch.setenv("TAMRA_MODELS_DIR", str(tmp_path / "m"))
    assert paths.models_dir() == tmp_path / "m"


def test_models_dir_uses_the_repo_models_from_source(monkeypatch, tmp_path):
    monkeypatch.delenv("TAMRA_MODELS_DIR", raising=False)
    monkeypatch.setattr(paths, "resource_dir", lambda: tmp_path)
    (tmp_path / ".models").mkdir()
    assert paths.models_dir() == tmp_path / ".models"


def test_models_dir_defaults_to_the_data_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("TAMRA_MODELS_DIR", raising=False)
    monkeypatch.setattr(paths, "resource_dir", lambda: tmp_path)
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path / "data"))
    assert paths.models_dir() == tmp_path / "data" / "models"
```

`tests/test_logs.py`:

```python
import logging

from tamra.logs import setup_logging, shutdown_logging


def flush():
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_logs_go_to_a_file(tmp_path):
    try:
        path = setup_logging(tmp_path / "logs")
        logging.getLogger("tamra.test").info("hello log")
        flush()
        assert "hello log" in path.read_text(encoding="utf-8")
        assert logging.getLogger("uvicorn.access").level == logging.WARNING
    finally:
        shutdown_logging()


def test_setting_up_again_does_not_duplicate_lines(tmp_path):
    try:
        setup_logging(tmp_path / "a")
        path = setup_logging(tmp_path / "b")
        logging.getLogger("tamra.test").info("only once")
        flush()
        assert path.read_text(encoding="utf-8").count("only once") == 1
    finally:
        shutdown_logging()
```

`tests/test_core.py`:

```python
import time

import pytest
from fakes import FakeEmbedder, FakeLocalLLM, fake_spans

from tamra.answer import AnswerSettings
from tamra.core import DEV_LLM_FILE, Core
from tamra.embedder import MODEL_ID


def make_core(tmp_path, **overrides):
    options = {
        "embedder_factory": FakeEmbedder,
        "token_spans_factory": lambda: fake_spans,
        "llm": FakeLocalLLM(),
        "answer_settings": AnswerSettings(min_similarity=0.3),
        "debounce": 0.2,
    }
    options.update(overrides)
    return Core(tmp_path / "data", tmp_path / "models", tmp_path / "llama.exe", **options)


@pytest.fixture
def core(tmp_path):
    c = make_core(tmp_path)
    c.start()
    yield c
    c.shutdown()


def wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    assert predicate()


def indexed(core):
    status = core.index_status()
    return status["counts"]["indexed"] if status else 0


def test_set_collection_checks_the_folder(core, tmp_path):
    with pytest.raises(ValueError, match="Folder not found"):
        core.set_collection("x", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="full folder path"):
        core.set_collection("x", "relative/dir")
    assert core.index_status() is None


def test_a_folder_is_indexed_and_questions_are_answered(core, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "lease.md").write_text("The lease term is three years.", encoding="utf-8")
    collection = core.set_collection("  ", str(docs))
    assert (collection.name, collection.embedding_model_id) == ("docs", MODEL_ID)
    wait_until(lambda: indexed(core) == 1)
    chat = core.store.create_chat()
    events = list(core.answers.ask(chat.id, "How long is the lease term?"))
    assert events[-1]["type"] == "done"


def test_new_files_are_picked_up_by_the_watcher(core, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    core.set_collection("Docs", str(docs))
    (docs / "late.md").write_text("Parking is free.", encoding="utf-8")
    wait_until(lambda: indexed(core) == 1)


def test_status_reports_a_stale_index_and_rebuild_fixes_it(core, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("Lease terms.", encoding="utf-8")
    core.set_collection("Docs", str(docs))
    wait_until(lambda: indexed(core) == 1)
    core.store.reset_index(core.store.get_collection().id, "old-model")
    status = core.index_status()
    assert status["stale"] is True
    assert set(status) == {"stale", "counts", "current", "error", "problems"}
    assert core.rebuild() is True
    wait_until(lambda: not core.index_status()["stale"] and indexed(core) == 1)


def test_the_embedder_is_loaded_once_and_shared(tmp_path):
    loads = []

    def factory():
        loads.append(1)
        return FakeEmbedder()

    c = make_core(tmp_path, embedder_factory=factory)
    try:
        assert c.embedder() is c.embedder()
        assert loads == [1]
    finally:
        c.shutdown()


def test_shutdown_closes_the_llm_and_a_fresh_core_reads_the_same_data(tmp_path):
    llm = FakeLocalLLM()
    c = make_core(tmp_path, llm=llm)
    c.start()
    c.store.create_chat("kept")
    c.shutdown()
    assert llm.closed
    again = make_core(tmp_path)
    try:
        assert [chat.title for chat in again.store.list_chats()] == ["kept"]
    finally:
        again.shutdown()


def test_the_default_llm_is_the_dev_model(tmp_path):
    c = Core(tmp_path / "data", tmp_path / "models", tmp_path / "llama.exe")
    try:
        assert c.llm.label == DEV_LLM_FILE.removesuffix(".gguf")
    finally:
        c.shutdown()
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_paths.py tests/test_logs.py tests/test_core.py -v`
Expected: the new path tests fail (`AttributeError: module 'tamra.paths' has no attribute 'models_dir'`, wrong blank/relative handling); `test_logs.py` and `test_core.py` fail at collection (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Replace `data_dir` in `src/tamra/paths.py`, and add `models_dir` after `resource_dir`:

```python
def data_dir() -> Path:
    """Per-user data directory (%LOCALAPPDATA%\\Tamra), overridable via TAMRA_DATA_DIR."""
    override = os.environ.get("TAMRA_DATA_DIR", "").strip()
    if override:
        base = Path(override).expanduser().absolute()
    else:
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if not local:
            raise RuntimeError(
                "LOCALAPPDATA is not set; set TAMRA_DATA_DIR to the folder Tamra should use"
            )
        base = Path(local) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base
```

```python
def models_dir() -> Path:
    """TAMRA_MODELS_DIR, else the repo's .models (from source), else data_dir()/models."""
    override = os.environ.get("TAMRA_MODELS_DIR", "").strip()
    if override:
        return Path(override).expanduser().absolute()
    if not getattr(sys, "frozen", False):
        dev = resource_dir() / ".models"
        if dev.is_dir():
            return dev
    return data_dir() / "models"
```

`src/tamra/logs.py`:

```python
"""File logging under data_dir()/logs. The windowed exe has no console, so this is its record."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_installed: list[logging.Handler] = []


def setup_logging(log_dir: Path, *, console: bool = False) -> Path:
    """Send INFO and above to log_dir/tamra.log (rotated); optionally also to the console."""
    shutdown_logging()
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "tamra.log"
    handlers: list[logging.Handler] = [
        RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    ]
    if console:
        handlers.append(logging.StreamHandler())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in handlers:
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
        _installed.append(handler)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # the UI polls status
    return path


def shutdown_logging() -> None:
    """Remove and close the handlers setup_logging() added."""
    root = logging.getLogger()
    while _installed:
        handler = _installed.pop()
        root.removeHandler(handler)
        handler.close()
```

`src/tamra/core.py`:

```python
"""Core: owns the store, the indexer and folder watcher, the answer service, and the local LLM."""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

from tamra.answer import AnswerService, AnswerSettings
from tamra.embedder import MODEL_ID, Embedder
from tamra.ingest.chunker import TokenSpans, bge_token_spans
from tamra.ingest.indexer import EmbedderLike, Indexer
from tamra.ingest.watcher import FolderWatcher
from tamra.llm.runtime import LocalLLM
from tamra.store import Collection, Store

log = logging.getLogger(__name__)

DEV_LLM_FILE = "qwen2.5-0.5b-instruct-q4_k_m.gguf"  # until M2 brings the model catalog


class Core:
    def __init__(
        self,
        data_dir: Path,
        models_dir: Path,
        llama_exe: Path,
        *,
        embedder_factory: Callable[[], EmbedderLike] | None = None,
        token_spans_factory: Callable[[], TokenSpans] | None = None,
        llm=None,
        answer_settings: AnswerSettings | None = None,
        debounce: float = 2.0,
    ):
        data_dir.mkdir(parents=True, exist_ok=True)
        bge = models_dir / "bge-m3"
        self.store = Store.open(data_dir / "tamra.db")
        self._embedder_factory = embedder_factory or (lambda: Embedder.load(bge))
        self._embedder: EmbedderLike | None = None
        self._embedder_lock = threading.Lock()
        spans_factory = token_spans_factory or (lambda: bge_token_spans(bge / "tokenizer.json"))
        self.llm = llm or LocalLLM(
            llama_exe, models_dir / DEV_LLM_FILE, data_dir / "logs" / "llama-server.log"
        )
        self.indexer = Indexer(self.store, self.embedder, spans_factory, MODEL_ID)
        self.watcher = FolderWatcher(self.indexer.request_reconcile, debounce=debounce)
        self.answers = AnswerService(
            self.store,
            self._embed_query,
            self.llm.client,
            model_id=MODEL_ID,
            llm_label=lambda: self.llm.label,
            settings=answer_settings,
        )

    def embedder(self) -> EmbedderLike:
        """The embedding model, loaded on first use and shared by indexing and questions."""
        with self._embedder_lock:
            if self._embedder is None:
                self._embedder = self._embedder_factory()
            return self._embedder

    def _embed_query(self, text: str) -> np.ndarray:
        return self.embedder().embed([text])[0]

    def start(self) -> None:
        self.indexer.start()
        collection = self.store.get_collection()
        if collection is not None:
            self._watch(collection)
            self.indexer.request_reconcile()

    def set_collection(self, name: str, folder: str) -> Collection:
        """Index this folder from now on (M1 keeps one collection; chats are kept)."""
        path = Path(folder.strip()).expanduser()
        if not path.is_absolute():
            raise ValueError(
                "Choose a full folder path, for example C:\\Users\\me\\Documents."
            )
        if not path.is_dir():
            raise ValueError(f"Folder not found: {folder}")
        collection = self.store.replace_collection(name.strip() or path.name, str(path), MODEL_ID)
        self._watch(collection)
        self.indexer.request_reconcile()
        return collection

    def rebuild(self) -> bool:
        if self.store.get_collection() is None:
            return False
        self.indexer.request_rebuild()
        return True

    def index_status(self) -> dict | None:
        collection = self.store.get_collection()
        if collection is None:
            return None
        state = self.indexer.state()
        return {
            "stale": collection.embedding_model_id != MODEL_ID,
            "counts": self.store.status_counts(collection.id),
            "current": state.current,
            "error": state.error,
            "problems": [
                {"rel_path": f.rel_path, "status": f.status, "error": f.error}
                for f in self.store.problem_files(collection.id)
            ],
        }

    def shutdown(self) -> None:
        self.watcher.stop()
        self.answers.cancel()
        self.indexer.stop()
        self.llm.close()
        self.store.close()

    def _watch(self, collection: Collection) -> None:
        try:
            self.watcher.watch(Path(collection.folder_path))
        except OSError as e:  # e.g. an unplugged drive: the indexer reports the missing folder
            self.watcher.stop()
            log.warning("cannot watch %s: %s", collection.folder_path, e)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_paths.py tests/test_logs.py tests/test_core.py -v`
Expected: all pass.
Then: `uv run pytest; uv run ruff format; uv run ruff check --fix; uv run ruff format --check`

- [ ] **Step 5: Commit**

```powershell
git add src/tamra/core.py src/tamra/logs.py src/tamra/paths.py tests/fakes.py tests/test_paths.py tests/test_logs.py tests/test_core.py
git commit -m "feat(core): Core lifecycle, file logging, and the models folder"
```

---

### Task 12: API routes and request security

**Files:**
- Modify: `src/tamra/server.py` (rewrite), `tests/test_server.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: Task 11 `Core`; Task 9 `source_payload`.
- Produces: `create_app(token, ui_dir, core=None, pick_folder=None) -> FastAPI` (raises `ValueError` for an empty token and `RuntimeError` when `ui_dir` has no `index.html`; `pick_folder` is a zero-argument callable returning a folder path or `None`); `ALLOWED_HOSTS`; `CONTENT_SECURITY_POLICY`; ASGI middlewares `TokenGate` and `SecurityHeaders`. Routes (JSON unless noted; all under the token gate):
  - `GET /api/health` → `{"status": "ok", "version"}`
  - `POST /api/pick-folder` → `{"folder_path": str | null}` (`null` when cancelled); 501 when the app has no window (dev mode)
  - `GET /api/collection` → `{"collection": null | {id, name, folder_path, created_at}, "index": null | Core.index_status()}`
  - `PUT /api/collection` body `{name, folder_path}` → same shape; 400 `{"detail"}` for a bad folder
  - `POST /api/collection/rebuild` → 202 `{"status": "rebuilding"}`; 404 when no folder is chosen
  - `GET /api/chats` → `{"chats": [{id, title, created_at, updated_at}]}`; `POST /api/chats` → 201 chat
  - `GET /api/chats/{id}` → chat plus `"messages": [{id, role, content, provider, model, created_at, sources: [source_payload]}]`; 404 if unknown
  - `DELETE /api/chats/{id}` → 204; 404 if unknown
  - `POST /api/chats/{id}/messages` body `{content}` (1–4000 characters) → `text/event-stream`, one `data: <json event>` block per Task 9 event
  - `POST /api/answer/cancel` → 204
- Security: requests whose `Host` is not in `ALLOWED_HOSTS` get 400 (DNS rebinding); every path whose normalised form is `/api` or starts with `/api/` needs `X-Tamra-Token`, for HTTP (401) and WebSocket (closed with 1008); every HTTP response carries the CSP, `X-Content-Type-Options: nosniff`, and `Referrer-Policy: no-referrer`. The folder picker is an API route rather than a pywebview JS bridge because pywebview builds its bridge with `new Function`, which this CSP blocks.

- [ ] **Step 1: Write the failing tests**

In `tests/test_server.py`: add `import pytest` and `from starlette.websockets import WebSocketDisconnect`, add this helper, and change every `TestClient(create_app(...))` in the file to `make_client(...)` with the same `ui_dir`:

```python
def make_client(ui_dir=None, **options):
    return TestClient(create_app("secret", ui_dir, **options), base_url="http://127.0.0.1")
```

In `test_wait_until_up_bypasses_proxy`, also `monkeypatch.delenv("NO_PROXY", raising=False)` and `monkeypatch.delenv("no_proxy", raising=False)`. Then append:

```python
AUTH = {"X-Tamra-Token": "secret"}


def test_foreign_host_headers_are_rejected():
    response = make_client().get("/api/health", headers={**AUTH, "Host": "evil.example"})
    assert response.status_code == 400


def test_localhost_is_an_allowed_host():
    client = TestClient(create_app("secret", ui_dir=None), base_url="http://localhost")
    assert client.get("/api/health", headers=AUTH).status_code == 200


def test_double_slash_and_bare_api_paths_are_gated():
    client = make_client()
    # An absolute URL keeps the double slash (httpx would merge "//api/..." into the base URL).
    assert client.get("http://127.0.0.1//api/health").status_code == 401
    assert client.get("/api").status_code == 401


def test_websockets_under_api_need_the_token():
    # websocket_connect ignores base_url, so the allowed Host is given explicitly.
    with pytest.raises(WebSocketDisconnect) as closed:
        with make_client().websocket_connect("/api/ws", headers={"host": "127.0.0.1"}):
            pass
    assert closed.value.code == 1008


def test_an_empty_token_is_refused():
    with pytest.raises(ValueError):
        create_app("", ui_dir=None)


def test_responses_carry_security_headers(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Tamra</h1>", encoding="utf-8")
    client = make_client(tmp_path)
    for response in (client.get("/"), client.get("/api/health")):
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_a_missing_ui_build_is_reported(tmp_path):
    with pytest.raises(RuntimeError, match="npm --prefix ui run build"):
        create_app("secret", ui_dir=tmp_path / "dist")


def test_the_folder_picker_needs_a_window():
    assert make_client().post("/api/pick-folder", headers=AUTH).status_code == 501


def test_the_folder_picker_returns_the_choice():
    client = make_client(pick_folder=lambda: "C:/docs")
    assert client.post("/api/pick-folder", headers=AUTH).json() == {"folder_path": "C:/docs"}
    cancelled = make_client(pick_folder=lambda: None)
    assert cancelled.post("/api/pick-folder", headers=AUTH).json() == {"folder_path": None}
```

`tests/test_api.py`:

```python
import json
import time

import pytest
from fakes import FakeEmbedder, FakeLocalLLM, fake_spans
from fastapi.testclient import TestClient

from tamra.answer import AnswerSettings
from tamra.core import Core
from tamra.server import create_app

TOKEN = "secret"
AUTH = {"X-Tamra-Token": TOKEN}


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
    client = TestClient(create_app(TOKEN, None, core), base_url="http://127.0.0.1")
    yield client, tmp_path
    core.shutdown()


def events_of(response):
    return [
        json.loads(line[len("data:") :])
        for line in response.iter_lines()
        if line.startswith("data:")
    ]


def wait_for_indexed(client, count, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        index = client.get("/api/collection", headers=AUTH).json()["index"]
        if index and index["counts"]["indexed"] == count:
            return index
        time.sleep(0.05)
    raise AssertionError("indexing did not finish")


def test_choose_a_folder_ask_and_review_the_chat(api):
    client, tmp_path = api
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "lease.md").write_text("The lease term is three years.", encoding="utf-8")
    empty = client.get("/api/collection", headers=AUTH).json()
    assert empty == {"collection": None, "index": None}
    put = client.put(
        "/api/collection", headers=AUTH, json={"name": "Docs", "folder_path": str(docs)}
    )
    assert put.status_code == 200
    assert put.json()["collection"]["name"] == "Docs"
    index = wait_for_indexed(client, 1)
    assert index["stale"] is False

    chat = client.post("/api/chats", headers=AUTH)
    assert chat.status_code == 201
    chat_id = chat.json()["id"]
    with client.stream(
        "POST",
        f"/api/chats/{chat_id}/messages",
        headers=AUTH,
        json={"content": "How long is the lease term?"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = events_of(response)
    assert events[0]["type"] == "sources"
    assert events[0]["sources"][0]["file"] == "lease.md"
    assert events[-1]["type"] == "done"

    detail = client.get(f"/api/chats/{chat_id}", headers=AUTH).json()
    assert detail["title"] == "How long is the lease term?"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["sources"][0]["label"] == "line 1"
    assert client.get("/api/chats", headers=AUTH).json()["chats"][0]["id"] == chat_id
    assert client.delete(f"/api/chats/{chat_id}", headers=AUTH).status_code == 204
    assert client.get(f"/api/chats/{chat_id}", headers=AUTH).status_code == 404
    assert client.delete(f"/api/chats/{chat_id}", headers=AUTH).status_code == 404


def test_a_bad_folder_is_a_400(api):
    client, tmp_path = api
    response = client.put(
        "/api/collection", headers=AUTH, json={"name": "x", "folder_path": str(tmp_path / "nope")}
    )
    assert response.status_code == 400
    assert "Folder not found" in response.json()["detail"]


def test_rebuild_and_cancel(api):
    client, tmp_path = api
    assert client.post("/api/collection/rebuild", headers=AUTH).status_code == 404
    docs = tmp_path / "docs"
    docs.mkdir()
    client.put("/api/collection", headers=AUTH, json={"name": "d", "folder_path": str(docs)})
    assert client.post("/api/collection/rebuild", headers=AUTH).status_code == 202
    assert client.post("/api/answer/cancel", headers=AUTH).status_code == 204


def test_an_empty_question_is_rejected(api):
    client, _ = api
    chat_id = client.post("/api/chats", headers=AUTH).json()["id"]
    response = client.post(f"/api/chats/{chat_id}/messages", headers=AUTH, json={"content": ""})
    assert response.status_code == 422


def test_data_routes_need_the_token(api):
    client, _ = api
    assert client.get("/api/chats").status_code == 401
    assert client.post("/api/chats/1/messages", json={"content": "q"}).status_code == 401
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_server.py tests/test_api.py -v`
Expected: the new server tests fail (no host check, `/api` and `//api` not gated, no CSP, no WebSocket gate, no picker route) and `tests/test_api.py` fails (`create_app()` takes no `core`).

- [ ] **Step 3: Implement**

Replace `src/tamra/server.py`:

```python
"""HTTP API on 127.0.0.1 (spec §2-§3): token-gated JSON routes, SSE answers, and the built UI."""

import json
import secrets
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import tamra
from tamra.answer import source_payload
from tamra.core import Core
from tamra.store import Chat, Collection

ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'none'"
)


class TokenGate:
    """Reject /api requests, HTTP and WebSocket, that lack the per-launch token."""

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and _is_api(scope["path"]):
            supplied = dict(scope["headers"]).get(b"x-tamra-token", b"")
            if not secrets.compare_digest(supplied, self.token):
                if scope["type"] == "http":
                    response = JSONResponse({"detail": "invalid token"}, status_code=401)
                    await response(scope, receive, send)
                else:
                    await receive()  # websocket.connect
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)


def _is_api(path: str) -> bool:
    path = "/" + path.lstrip("/")
    return path == "/api" or path.startswith("/api/")


class SecurityHeaders:
    """Add the Content-Security-Policy and related headers to every HTTP response."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [
                    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class CollectionBody(BaseModel):
    name: str = Field(default="", max_length=200)
    folder_path: str = Field(min_length=1, max_length=1000)


class QuestionBody(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


def create_app(
    token: str,
    ui_dir: Path | None,
    core: Core | None = None,
    pick_folder: Callable[[], str | None] | None = None,
) -> FastAPI:
    if not token:
        raise ValueError("the API token must not be empty")  # an empty token would match ""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": tamra.__version__}

    @app.post("/api/pick-folder")
    def choose_folder() -> dict:
        if pick_folder is None:
            raise HTTPException(
                status_code=501, detail="The folder picker is only available in the Tamra window."
            )
        return {"folder_path": pick_folder()}

    if core is not None:
        _add_routes(app, core)
    if ui_dir is not None:
        if not (ui_dir / "index.html").is_file():
            raise RuntimeError(
                f"The user interface is missing ({ui_dir / 'index.html'}). "
                "Build it with: npm --prefix ui run build"
            )
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
    # The last one added runs first: response headers, then the host check, then the token.
    app.add_middleware(TokenGate, token=token)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    app.add_middleware(SecurityHeaders)
    return app


async def _sse(events: Iterator[dict]) -> AsyncIterator[str]:
    """Server-Sent Events from a blocking event iterator, advanced on worker threads.

    When the client goes away the response is cancelled; closing the iterator then lets the
    answer service keep the partial answer and stop generating.
    """
    try:
        while True:
            event = await anyio.to_thread.run_sync(next, events, None)
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    finally:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(events.close)


def _collection_payload(collection: Collection) -> dict:
    return {
        "id": collection.id,
        "name": collection.name,
        "folder_path": collection.folder_path,
        "created_at": collection.created_at,
    }


def _chat_payload(chat: Chat) -> dict:
    return {
        "id": chat.id,
        "title": chat.title,
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
    }


def _add_routes(app: FastAPI, core: Core) -> None:
    @app.get("/api/collection")
    def get_collection() -> dict:
        collection = core.store.get_collection()
        if collection is None:
            return {"collection": None, "index": None}
        return {"collection": _collection_payload(collection), "index": core.index_status()}

    @app.put("/api/collection")
    def put_collection(body: CollectionBody) -> dict:
        try:
            collection = core.set_collection(body.name, body.folder_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"collection": _collection_payload(collection), "index": core.index_status()}

    @app.post("/api/collection/rebuild", status_code=202)
    def rebuild() -> dict:
        if not core.rebuild():
            raise HTTPException(status_code=404, detail="No folder has been chosen yet.")
        return {"status": "rebuilding"}

    @app.get("/api/chats")
    def list_chats() -> dict:
        return {"chats": [_chat_payload(chat) for chat in core.store.list_chats()]}

    @app.post("/api/chats", status_code=201)
    def create_chat() -> dict:
        return _chat_payload(core.store.create_chat())

    @app.get("/api/chats/{chat_id}")
    def get_chat(chat_id: int) -> dict:
        chat = core.store.get_chat(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        messages = [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "provider": m.provider,
                "model": m.model,
                "created_at": m.created_at,
                "sources": [source_payload(s) for s in m.sources],
            }
            for m in core.store.list_messages(chat_id)
        ]
        return {**_chat_payload(chat), "messages": messages}

    @app.delete("/api/chats/{chat_id}", status_code=204)
    def delete_chat(chat_id: int) -> Response:
        if not core.store.delete_chat(chat_id):
            raise HTTPException(status_code=404, detail="Chat not found.")
        return Response(status_code=204)

    @app.post("/api/chats/{chat_id}/messages")
    def ask(chat_id: int, body: QuestionBody) -> StreamingResponse:
        return StreamingResponse(
            _sse(core.answers.ask(chat_id, body.content)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/answer/cancel", status_code=204)
    def cancel() -> Response:
        core.answers.cancel()
        return Response(status_code=204)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_server.py tests/test_api.py -v`
Expected: all pass.
Then: `uv run pytest; uv run ruff format; uv run ruff check --fix; uv run ruff format --check`

- [ ] **Step 5: Commit**

```powershell
git add src/tamra/server.py tests/test_server.py tests/test_api.py
git commit -m "feat(api): collection, chat, streaming answer, and folder-picker routes behind host and token gates"
```

---

### Task 13: App wiring — Core lifecycle, folder picker, clean shutdown

**Files:**
- Modify: `src/tamra/app.py` (rewrite)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: Task 11 `Core`, `setup_logging`, `data_dir`, `models_dir`; Task 12 `create_app`.
- Produces: `build_core() -> Core`; `FolderPicker` (callable; its `window` attribute is set once the window exists; returns the chosen folder or `None`); `_wait_until_up(port, token, thread=None, timeout=15.0)` (fails fast with "is port … already in use?" when the server thread dies); `run(dev=False)`: logs to `data_dir()/logs/tamra.log`, starts `Core`, serves the API (with the folder picker in window mode), shows the window or waits in dev mode, always stops the server and shuts `Core` down (Ctrl+C in dev mode stops cleanly), and in window mode shows a Windows message box when startup fails (the windowed exe has no console).

- [ ] **Step 1: Write the failing tests**

`tests/test_app.py`:

```python
import threading

import pytest
import webview

from tamra import app
from tamra.net import free_port


class FakeWindow:
    def __init__(self, result):
        self.result = result
        self.kinds = []

    def create_file_dialog(self, kind):
        self.kinds.append(kind)
        return self.result


def test_the_folder_picker_returns_the_chosen_folder():
    picker = app.FolderPicker()
    assert picker() is None  # no window yet
    picker.window = FakeWindow(("C:/docs",))
    assert picker() == "C:/docs"
    assert picker.window.kinds == [webview.FileDialog.FOLDER]


def test_the_folder_picker_handles_cancel():
    picker = app.FolderPicker()
    picker.window = FakeWindow(None)
    assert picker() is None


def test_wait_until_up_notices_a_dead_server_thread():
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    with pytest.raises(RuntimeError, match="already in use"):
        app._wait_until_up(free_port(), "token", dead, timeout=5)


def test_build_core_uses_the_configured_folders(monkeypatch, tmp_path):
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAMRA_MODELS_DIR", str(tmp_path / "models"))
    core = app.build_core()
    try:
        assert (tmp_path / "data" / "tamra.db").exists()
        assert core.llm.label == "qwen2.5-0.5b-instruct-q4_k_m"
    finally:
        core.shutdown()
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_app.py -v`
Expected: FAIL with `AttributeError: module 'tamra.app' has no attribute 'FolderPicker'`.

- [ ] **Step 3: Implement**

Replace `src/tamra/app.py`:

```python
"""Desktop entry: start Core and the API on 127.0.0.1, then show the window (or dev mode)."""

import ctypes
import logging
import secrets
import threading
import time

import httpx
import uvicorn

from tamra.core import Core
from tamra.logs import setup_logging
from tamra.net import free_port
from tamra.paths import data_dir, models_dir, resource_dir
from tamra.server import create_app

log = logging.getLogger(__name__)

DEV_PORT = 8765
DEV_TOKEN = "dev"


def build_core() -> Core:
    """Core with data in data_dir(), models in models_dir(), and the bundled llama-server."""
    return Core(data_dir(), models_dir(), resource_dir() / "vendor" / "llama" / "llama-server.exe")


class FolderPicker:
    """The Windows folder picker on the app window. The API calls it from a worker thread."""

    def __init__(self) -> None:
        self.window = None  # set once the window exists

    def __call__(self) -> str | None:
        if self.window is None:
            return None
        import webview

        chosen = self.window.create_file_dialog(webview.FileDialog.FOLDER)
        return str(chosen[0]) if chosen else None


def _wait_until_up(
    port: int, token: str, thread: threading.Thread | None = None, timeout: float = 15.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if thread is not None and not thread.is_alive():
            raise RuntimeError(f"Tamra core stopped while starting (is port {port} already in use?)")
        try:
            r = httpx.get(
                f"http://127.0.0.1:{port}/api/health",
                headers={"X-Tamra-Token": token},
                trust_env=False,
            )
            if r.status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Tamra core did not start")


def run(dev: bool = False) -> None:
    setup_logging(data_dir() / "logs", console=dev)
    port, token = (DEV_PORT, DEV_TOKEN) if dev else (free_port(), secrets.token_urlsafe(32))
    ui_dir = None if dev else resource_dir() / "ui" / "dist"
    picker = None if dev else FolderPicker()
    core = build_core()
    try:
        core.start()
        config = uvicorn.Config(
            create_app(token, ui_dir, core, pick_folder=picker),
            host="127.0.0.1",
            port=port,
            log_level="warning",
            log_config=None,
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, name="tamra-api", daemon=True)
        thread.start()
        try:
            _wait_until_up(port, token, thread)
            if picker is None:
                _wait_in_dev_mode(port, token, thread)
            else:
                _show_window(port, token, picker)
        finally:
            server.should_exit = True
            thread.join(timeout=5)
    except Exception as e:
        log.exception("Tamra stopped because of an error")
        if not dev:
            _alert(f"Tamra could not start:\n\n{e}\n\nDetails: {data_dir() / 'logs' / 'tamra.log'}")
        raise
    finally:
        core.shutdown()


def _wait_in_dev_mode(port: int, token: str, thread: threading.Thread) -> None:
    print(f"Tamra core on http://127.0.0.1:{port} (token: {token})")
    print("Run `npm --prefix ui run dev` and open http://localhost:5173/#token=dev")
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        print("Stopping Tamra core...")


def _show_window(port: int, token: str, picker: FolderPicker) -> None:
    import webview  # heavy; not needed in dev mode or for selfcheck

    picker.window = webview.create_window(
        "Tamra",
        f"http://127.0.0.1:{port}/#token={token}",
        width=1200,
        height=800,
        min_size=(800, 560),
    )
    webview.start()


def _alert(message: str) -> None:
    """A Windows message box: the windowed exe has no console to print to."""
    ctypes.windll.user32.MessageBoxW(None, message, "Tamra", 0x10)  # MB_ICONERROR
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_app.py tests/test_server.py -v`
Expected: all pass.
Then: `uv run pytest; uv run ruff format; uv run ruff check --fix; uv run ruff format --check`

- [ ] **Step 5: Smoke-test dev mode against the real models**

Port 8765 must be free (check with `Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue`; if something listens there, report it and skip this step — do not stop it). Start the core with a scratch data folder and the repo models, as a background process whose PID you record (PowerShell):

```powershell
$scratch = Join-Path $env:TEMP "tamra-m1-dev"
New-Item -ItemType Directory -Force "$scratch\docs" | Out-Null
Set-Content -Encoding utf8 "$scratch\docs\lease.md" "สัญญาเช่าบ้านฉบับนี้มีอายุสามปี ค่าเช่าเดือนละ 12,000 บาท"
$env:TAMRA_DATA_DIR = "$scratch\data"
$core = Start-Process uv -ArgumentList "run","tamra","--dev" -PassThru -WindowStyle Hidden
Remove-Item Env:TAMRA_DATA_DIR
```

Then, with `$h = @{ "X-Tamra-Token" = "dev" }`:
- `Invoke-RestMethod -Method Put http://127.0.0.1:8765/api/collection -Headers $h -ContentType "application/json" -Body (@{ name = "Smoke"; folder_path = "$scratch\docs" } | ConvertTo-Json)`
- poll `Invoke-RestMethod http://127.0.0.1:8765/api/collection -Headers $h` until `index.counts.indexed` is 1 (first use loads bge-m3: allow about 30 s);
- `$chat = Invoke-RestMethod -Method Post http://127.0.0.1:8765/api/chats -Headers $h`;
- write the question as UTF-8 without a BOM, `[IO.File]::WriteAllText("$scratch\q.json", '{"content":"ค่าเช่าเดือนละเท่าไร"}', [Text.UTF8Encoding]::new($false))`, and run `curl.exe -s -N -H "X-Tamra-Token: dev" -H "Content-Type: application/json" --data-binary "@$scratch\q.json" http://127.0.0.1:8765/api/chats/$($chat.id)/messages`.

Expected: a `sources` event naming `lease.md`, `token` events, and a `done` event (the 0.5B model's Thai is weak; this step checks the mechanics). `$core` is the `uv` launcher; stop it and its children with `taskkill /PID $($core.Id) /T /F`, confirm nothing listens on 8765, and delete `$scratch`. Put the outputs in the report.

- [ ] **Step 6: Commit**

```powershell
git add src/tamra/app.py tests/test_app.py
git commit -m "feat(app): run Core with the API and window, folder picker, and clean shutdown"
```

---

### Task 14: UI client layer — token, API calls, SSE, citations

**Files:**
- Create: `ui/src/types.ts`, `ui/src/sse.ts`, `ui/src/citations.ts`, `ui/src/sse.test.ts`, `ui/src/citations.test.ts`
- Modify: `ui/src/api.ts` (rewrite), `ui/src/api.test.ts` (rewrite), `ui/src/main.tsx`, `ui/src/App.tsx` (one call)

**Interfaces:**
- Consumes: Task 12's routes and event shapes.
- Produces:
  - `types.ts`: `FileCounts`, `ProblemFile`, `IndexStatus`, `Collection`, `CollectionState`, `Chat`, `Source`, `Message`, `ChatDetail`, `AnswerEvent` (the JSON shapes of Task 12).
  - `api.ts`: `tokenFromHash(hash) -> string | null` (`null` also for a malformed escape); `initToken()` (reads `#token=` once at startup, removes the fragment from the address with `history.replaceState`, and keeps the token in `sessionStorage` so reloading the window still works); `ApiError` (`status`, `message` = the server's `detail` when it sends one); `api<T>(method, path, body?, fetchImpl = fetch) -> Promise<T>` (JSON in and out; `undefined` for 204); `streamAnswer(chatId, content, onEvent, fetchImpl = fetch) -> Promise<void>` (POSTs the question and calls `onEvent` for every SSE event).
  - `sse.ts`: `createSseParser(onData) -> (chunk: string) => void`.
  - `citations.ts`: `Segment` and `splitCitations(text, sourceCount) -> Segment[]` (`[n]`, `[1][2]`, and `[1, 2]` become citations; numbers outside `1..sourceCount` stay text).
- Absorbs the M0 UI findings: the token is read once and dropped from the URL, and a malformed `#token=` escape no longer throws. (Plain-text rendering and the CSP are covered by Tasks 12 and 15.)

- [ ] **Step 1: Write the failing tests**

`ui/src/sse.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { createSseParser } from "./sse";

describe("createSseParser", () => {
  it("emits each event once it is complete, across chunk boundaries", () => {
    const seen: string[] = [];
    const feed = createSseParser((data) => seen.push(data));
    feed('data: {"a":1}\n\ndata: {"b"');
    expect(seen).toEqual(['{"a":1}']);
    feed(":2}\n");
    expect(seen).toEqual(['{"a":1}']);
    feed("\n");
    expect(seen).toEqual(['{"a":1}', '{"b":2}']);
  });

  it("joins multi-line data and ignores comments", () => {
    const seen: string[] = [];
    const feed = createSseParser((data) => seen.push(data));
    feed(": keep-alive\n\ndata: one\ndata: two\n\n");
    expect(seen).toEqual(["one\ntwo"]);
  });
});
```

`ui/src/citations.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { splitCitations } from "./citations";

describe("splitCitations", () => {
  it("turns [n] markers into citations", () => {
    expect(splitCitations("Three years [1].", 2)).toEqual([
      { kind: "text", text: "Three years " },
      { kind: "cite", n: 1 },
      { kind: "text", text: "." },
    ]);
  });

  it("handles adjacent and comma-separated markers", () => {
    expect(splitCitations("A [1][2] B [1, 2]", 2)).toEqual([
      { kind: "text", text: "A " },
      { kind: "cite", n: 1 },
      { kind: "cite", n: 2 },
      { kind: "text", text: " B " },
      { kind: "cite", n: 1 },
      { kind: "cite", n: 2 },
    ]);
  });

  it("leaves numbers without a source as text", () => {
    expect(splitCitations("See [7] and [0].", 4)).toEqual([
      { kind: "text", text: "See [7] and [0]." },
    ]);
  });

  it("works inside Thai and Chinese text", () => {
    expect(splitCitations("สามปี[1]三年[2]", 2)).toEqual([
      { kind: "text", text: "สามปี" },
      { kind: "cite", n: 1 },
      { kind: "text", text: "三年" },
      { kind: "cite", n: 2 },
    ]);
  });
});
```

Replace `ui/src/api.test.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, initToken, streamAnswer, tokenFromHash } from "./api";
import type { AnswerEvent } from "./types";

describe("tokenFromHash", () => {
  it("reads the token from the fragment", () => {
    expect(tokenFromHash("#token=abc")).toBe("abc");
    expect(tokenFromHash("#x=1&token=a%2Bb")).toBe("a+b");
    expect(tokenFromHash("")).toBeNull();
  });

  it("returns null for a malformed escape", () => {
    expect(tokenFromHash("#token=%E0%A4%A")).toBeNull();
  });
});

describe("initToken", () => {
  beforeEach(() => sessionStorage.clear());

  it("reads the token once and removes it from the address", async () => {
    window.location.hash = "#token=t0k";
    initToken();
    expect(window.location.hash).toBe("");
    const fetchImpl = vi.fn(async () => new Response("{}"));
    await api("GET", "/api/health", undefined, fetchImpl);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/health",
      expect.objectContaining({ headers: { "X-Tamra-Token": "t0k" } }),
    );
  });

  it("keeps the token when the window is reloaded", async () => {
    window.location.hash = "#token=again";
    initToken();
    initToken(); // a reload: the fragment is gone, the session still has the token
    const fetchImpl = vi.fn(async () => new Response("{}"));
    await api("GET", "/api/health", undefined, fetchImpl);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/health",
      expect.objectContaining({ headers: { "X-Tamra-Token": "again" } }),
    );
  });
});

describe("api", () => {
  beforeEach(() => {
    sessionStorage.clear();
    window.location.hash = "#token=t0k";
    initToken();
  });

  it("sends JSON bodies and returns JSON", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ id: 1 })));
    await expect(api("PUT", "/api/collection", { folder_path: "C:/d" }, fetchImpl)).resolves.toEqual(
      { id: 1 },
    );
    expect(fetchImpl).toHaveBeenCalledWith("/api/collection", {
      method: "PUT",
      headers: { "X-Tamra-Token": "t0k", "Content-Type": "application/json" },
      body: '{"folder_path":"C:/d"}',
    });
  });

  it("returns nothing for 204", async () => {
    const fetchImpl = vi.fn(async () => new Response(null, { status: 204 }));
    await expect(api("DELETE", "/api/chats/1", undefined, fetchImpl)).resolves.toBeUndefined();
  });

  it("raises the server's message", async () => {
    const fetchImpl = vi.fn(
      async () => new Response(JSON.stringify({ detail: "Folder not found: X" }), { status: 400 }),
    );
    const error = await api("PUT", "/api/collection", {}, fetchImpl).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(400);
    expect((error as ApiError).message).toBe("Folder not found: X");
  });

  it("falls back to the status when the body is not JSON", async () => {
    const fetchImpl = vi.fn(async () => new Response("no", { status: 401 }));
    await expect(api("GET", "/api/health", undefined, fetchImpl)).rejects.toThrow("HTTP 401");
  });
});

describe("streamAnswer", () => {
  it("parses events when a Thai character is split across chunks", async () => {
    const bytes = new TextEncoder().encode(
      'data: {"type":"token","text":"สาม"}\n\ndata: {"type":"done","message_id":7}\n\n',
    );
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, 31)); // ends inside the first Thai character
        controller.enqueue(bytes.slice(31));
        controller.close();
      },
    });
    const fetchImpl = vi.fn(async () => new Response(body));
    const events: AnswerEvent[] = [];
    await streamAnswer(3, "q", (event) => events.push(event), fetchImpl);
    expect(events).toEqual([
      { type: "token", text: "สาม" },
      { type: "done", message_id: 7 },
    ]);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/chats/3/messages",
      expect.objectContaining({ method: "POST", body: '{"content":"q"}' }),
    );
  });

  it("raises an HTTP error before streaming", async () => {
    const fetchImpl = vi.fn(async () => new Response("{}", { status: 422 }));
    await expect(streamAnswer(3, "", () => {}, fetchImpl)).rejects.toThrow("HTTP 422");
  });
});
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `npm --prefix ui test`
Expected: FAIL — `./sse` and `./citations` do not exist, and `api.ts` has no `api`, `initToken`, `ApiError`, or `streamAnswer`.

- [ ] **Step 3: Implement**

`ui/src/types.ts`:

```ts
// JSON shapes of the Tamra core API (src/tamra/server.py).

export type FileCounts = {
  pending: number;
  indexing: number;
  indexed: number;
  failed: number;
  skipped: number;
};

export type ProblemFile = { rel_path: string; status: string; error: string | null };

export type IndexStatus = {
  stale: boolean;
  counts: FileCounts;
  current: string | null;
  error: string | null;
  problems: ProblemFile[];
};

export type Collection = { id: number; name: string; folder_path: string; created_at: string };

export type CollectionState = { collection: Collection | null; index: IndexStatus | null };

export type Chat = { id: number; title: string; created_at: string; updated_at: string };

export type Source = {
  n: number;
  file: string;
  label: string;
  text: string;
  location: Record<string, unknown>;
};

export type Message = {
  id: number;
  role: "user" | "assistant";
  content: string;
  provider: string | null;
  model: string | null;
  created_at: string;
  sources: Source[];
};

export type ChatDetail = Chat & { messages: Message[] };

export type AnswerEvent =
  | { type: "sources"; sources: Source[] }
  | { type: "token"; text: string }
  | { type: "error"; message: string }
  | { type: "done"; message_id: number };
```

`ui/src/sse.ts`:

```ts
/**
 * Incremental Server-Sent Events parser. Feed it decoded text as it arrives; it calls onData
 * with the data of each complete event (several data lines are joined with "\n").
 */
export function createSseParser(onData: (data: string) => void): (chunk: string) => void {
  let buffer = "";
  return (chunk: string) => {
    buffer += chunk;
    let end = buffer.indexOf("\n\n");
    while (end >= 0) {
      const data = buffer
        .slice(0, end)
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).replace(/^ /, ""))
        .join("\n");
      buffer = buffer.slice(end + 2);
      if (data) onData(data);
      end = buffer.indexOf("\n\n");
    }
  };
}
```

`ui/src/citations.ts`:

```ts
export type Segment = { kind: "text"; text: string } | { kind: "cite"; n: number };

const MARKER = /\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]/g;

/** Split answer text into plain text and [n] citations. Numbers without a source stay text. */
export function splitCitations(text: string, sourceCount: number): Segment[] {
  const segments: Segment[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    const numbers = match[1].split(",").map((part) => Number(part.trim()));
    if (numbers.some((n) => n < 1 || n > sourceCount)) continue;
    const start = match.index ?? 0;
    if (start > last) segments.push({ kind: "text", text: text.slice(last, start) });
    for (const n of numbers) segments.push({ kind: "cite", n });
    last = start + match[0].length;
  }
  if (last < text.length) segments.push({ kind: "text", text: text.slice(last) });
  return segments;
}
```

Replace `ui/src/api.ts`:

```ts
import { createSseParser } from "./sse";
import type { AnswerEvent } from "./types";

const STORAGE_KEY = "tamra.token";
let token = "";

/** The per-launch API token in a `#token=` URL fragment; null if absent or malformed. */
export function tokenFromHash(hash: string): string | null {
  const match = /(?:^#|&)token=([^&]*)/.exec(hash);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

/**
 * Read the token once at startup and remove it from the address bar. It is kept in
 * sessionStorage so that reloading the window keeps working.
 */
export function initToken(): void {
  const fromHash = tokenFromHash(window.location.hash);
  if (window.location.hash) {
    history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  try {
    if (fromHash !== null) sessionStorage.setItem(STORAGE_KEY, fromHash);
    token = fromHash ?? sessionStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    token = fromHash ?? ""; // storage unavailable: a reload will need the token again
  }
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function errorOf(response: Response): Promise<ApiError> {
  let message = `HTTP ${response.status}`;
  try {
    const data: unknown = await response.json();
    if (data && typeof data === "object" && "detail" in data && typeof data.detail === "string") {
      message = data.detail;
    }
  } catch {
    // not JSON: keep the status
  }
  return new ApiError(response.status, message);
}

/** Call the Tamra core: JSON in and out, undefined for 204, ApiError on failure. */
export async function api<T>(
  method: string,
  path: string,
  body?: unknown,
  fetchImpl: typeof fetch = fetch,
): Promise<T> {
  const headers: Record<string, string> = { "X-Tamra-Token": token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetchImpl(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await errorOf(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Ask a question in a chat and call onEvent for every event of the streamed answer. */
export async function streamAnswer(
  chatId: number,
  content: string,
  onEvent: (event: AnswerEvent) => void,
  fetchImpl: typeof fetch = fetch,
): Promise<void> {
  const response = await fetchImpl(`/api/chats/${chatId}/messages`, {
    method: "POST",
    headers: { "X-Tamra-Token": token, "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!response.ok) throw await errorOf(response);
  if (!response.body) throw new ApiError(response.status, "The answer stream is empty.");
  const feed = createSseParser((data) => onEvent(JSON.parse(data) as AnswerEvent));
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    feed(decoder.decode(value, { stream: true }));
  }
  feed(decoder.decode());
}
```

`ui/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { initToken } from "./api";

initToken();
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

In `ui/src/App.tsx`, import `api` instead of `apiGet` and replace the call with `api<Health>("GET", "/api/health")` (Task 15 rewrites this file).

- [ ] **Step 4: Run the tests and the build**

Run: `npm --prefix ui test; npm --prefix ui run build`
Expected: all Vitest tests pass; `tsc` (which also type-checks the test files) and `vite build` succeed.
If `npm` cannot write `ui/node_modules` because a Vite dev server holds a file, do not stop that process: report it. `npm test` and `npm run build` do not reinstall packages, so they should still run.
Then: `uv run pytest` (unchanged Python must still pass).

- [ ] **Step 5: Commit**

```powershell
git add ui/src/types.ts ui/src/sse.ts ui/src/citations.ts ui/src/sse.test.ts ui/src/citations.test.ts ui/src/api.ts ui/src/api.test.ts ui/src/main.tsx ui/src/App.tsx
git commit -m "feat(ui): API client with one-time token, SSE answer stream, and citation parsing"
```

---

### Task 15: UI screens — folder setup, index status, chats, cited answers

**Files:**
- Create: `ui/src/test-utils.ts`, `ui/src/AnswerText.tsx`, `ui/src/Setup.tsx`, `ui/src/IndexStatus.tsx`, `ui/src/ChatList.tsx`, `ui/src/ChatView.tsx`, `ui/src/styles.css`, `ui/src/AnswerText.test.tsx`, `ui/src/Setup.test.tsx`, `ui/src/IndexStatus.test.tsx`, `ui/src/ChatView.test.tsx`, `ui/src/App.test.tsx`
- Modify: `ui/src/App.tsx` (rewrite), `ui/src/main.tsx` (import the stylesheet)

**Interfaces:**
- Consumes: Task 14's `api`, `streamAnswer`, `splitCitations`, and types; Task 12's routes.
- Produces (React components, default exports):
  - `App`: polls `GET /api/collection` every 2 s; shows `Setup` until a folder is chosen (or while changing it); then the header with `IndexStatus`, the `ChatList` sidebar, and `ChatView`. "New chat" shows an empty chat; the chat is created on its first question, so no empty chats pile up. While an answer streams, chat switching and folder changes are disabled.
  - `Setup({ onDone, onCancel? })`: a folder path field, "Browse…" (`POST /api/pick-folder`), and "Use this folder" (`PUT /api/collection`); shows the server's error.
  - `IndexStatus({ collection, index, disabled, onChangeFolder, onRebuild })`: "X of N files indexed · indexing <file>", the index error, a "Rebuild index" button when the index is stale, and the problem files in a `<details>` list.
  - `ChatList({ chats, activeId, disabled, onSelect, onNew, onDelete })`.
  - `ChatView({ chatId, createChat, onBusyChange, onAnswered })`: messages, the streaming answer, Stop (`POST /api/answer/cancel`), and a source panel that opens from `[n]` buttons or the source list. Enter sends; Shift+Enter adds a line; Enter while an IME is composing (Thai/Chinese input) does not send.
  - `AnswerText({ text, sourceCount, onCite })`: answer text as plain text (never HTML) with `[n]` buttons.
  - `test-utils.ts` (tests only): `mount`, `settle`, `click`, `typeInto`, `json`, `sse`, `mockFetch`.
- UI strings are English (Global Constraints). All document and model text is rendered as React text nodes, so markup in a document or an answer is shown, never executed.

- [ ] **Step 1: Add the test helpers**

`ui/src/test-utils.ts`:

```ts
import { act, type ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { vi } from "vitest";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

export type Mounted = { container: HTMLElement; unmount: () => Promise<void> };

/** Render into a fresh container and let effects and mocked requests settle. */
export async function mount(element: ReactElement): Promise<Mounted> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(element);
  });
  await settle();
  return {
    container,
    unmount: async () => {
      await act(async () => {
        root.unmount();
      });
      container.remove();
    },
  };
}

/** Let pending promises (mocked fetches, streamed bodies) and the renders they cause finish. */
export async function settle(): Promise<void> {
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

export async function click(element: Element | null | undefined): Promise<void> {
  if (!(element instanceof HTMLElement)) throw new Error("nothing to click");
  await act(async () => {
    element.click();
  });
  await settle();
}

/** Type into a React-controlled input or textarea. */
export async function typeInto(element: Element | null | undefined, value: string): Promise<void> {
  if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement)) {
    throw new Error("not a text field");
  }
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), "value")?.set;
  await act(async () => {
    setter?.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

export function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function sse(events: unknown[]): Response {
  const body = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

export type Call = { key: string; body: unknown };

/** Replace fetch with routes keyed by "METHOD /path"; an unknown route fails the test. */
export function mockFetch(routes: Record<string, () => Response>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const route = routes[key];
      if (!route) throw new Error(`unexpected request: ${key}`);
      return route();
    }),
  );
  return calls;
}
```

- [ ] **Step 2: Write the failing tests**

`ui/src/AnswerText.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import AnswerText from "./AnswerText";
import { click, type Mounted, mount } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
});

describe("AnswerText", () => {
  it("renders [n] markers as buttons that open the source", async () => {
    const onCite = vi.fn();
    view = await mount(<AnswerText text="Three years [2]." sourceCount={2} onCite={onCite} />);
    const button = view.container.querySelector("button.cite");
    expect(button?.textContent).toBe("2");
    expect(view.container.textContent).toBe("Three years 2.");
    await click(button);
    expect(onCite).toHaveBeenCalledWith(2);
  });

  it("shows markup from the model as text", async () => {
    const text = '<img src=x onerror="alert(1)">';
    view = await mount(<AnswerText text={text} sourceCount={0} onCite={() => {}} />);
    expect(view.container.querySelector("img")).toBeNull();
    expect(view.container.textContent).toBe(text);
  });
});
```

`ui/src/Setup.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import Setup from "./Setup";
import { click, json, type Mounted, mockFetch, mount, typeInto } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
});

const state = {
  collection: { id: 1, name: "docs", folder_path: "C:/docs", created_at: "t" },
  index: null,
};

describe("Setup", () => {
  it("indexes the typed folder", async () => {
    const calls = mockFetch({ "PUT /api/collection": () => json(state) });
    const onDone = vi.fn();
    view = await mount(<Setup onDone={onDone} />);
    await typeInto(view.container.querySelector("input"), "  C:/docs ");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(calls).toEqual([
      { key: "PUT /api/collection", body: { name: "", folder_path: "C:/docs" } },
    ]);
    expect(onDone).toHaveBeenCalledWith(state);
  });

  it("shows why a folder was refused", async () => {
    mockFetch({ "PUT /api/collection": () => json({ detail: "Folder not found: C:/nope" }, 400) });
    view = await mount(<Setup onDone={vi.fn()} />);
    await typeInto(view.container.querySelector("input"), "C:/nope");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(view.container.querySelector('[role="alert"]')?.textContent).toBe(
      "Folder not found: C:/nope",
    );
  });

  it("fills the path from the folder picker", async () => {
    mockFetch({ "POST /api/pick-folder": () => json({ folder_path: "D:/picked" }) });
    view = await mount(<Setup onDone={vi.fn()} />);
    await click(view.container.querySelector('button[type="button"]'));
    expect(view.container.querySelector("input")?.value).toBe("D:/picked");
  });
});
```

`ui/src/IndexStatus.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import IndexStatus from "./IndexStatus";
import { click, type Mounted, mount } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
});

const collection = { id: 1, name: "Contracts", folder_path: "C:/contracts", created_at: "t" };
const counts = { pending: 1, indexing: 1, indexed: 3, failed: 1, skipped: 0 };

describe("IndexStatus", () => {
  it("shows progress and the files that need attention", async () => {
    const index = {
      stale: false,
      counts,
      current: "b.pdf",
      error: null,
      problems: [{ rel_path: "bad.pdf", status: "failed", error: "cannot open PDF" }],
    };
    view = await mount(
      <IndexStatus
        collection={collection}
        index={index}
        disabled={false}
        onChangeFolder={vi.fn()}
        onRebuild={vi.fn()}
      />,
    );
    const text = view.container.textContent ?? "";
    expect(text).toContain("3 of 6 files indexed · indexing b.pdf");
    expect(text).toContain("1 file(s) need attention");
    expect(text).toContain("bad.pdf");
  });

  it("offers a rebuild when the index is stale", async () => {
    const onRebuild = vi.fn();
    const index = { stale: true, counts, current: null, error: null, problems: [] };
    view = await mount(
      <IndexStatus
        collection={collection}
        index={index}
        disabled={false}
        onChangeFolder={vi.fn()}
        onRebuild={onRebuild}
      />,
    );
    const buttons = [...view.container.querySelectorAll("button")];
    await click(buttons.find((b) => b.textContent === "Rebuild index"));
    expect(onRebuild).toHaveBeenCalled();
  });
});
```

`ui/src/ChatView.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import ChatView from "./ChatView";
import { click, json, type Mounted, mockFetch, mount, sse, typeInto } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
});

const chat = { id: 5, title: "", created_at: "t", updated_at: "t" };
const source = {
  n: 1,
  file: "lease.pdf",
  label: "p. 2",
  text: "The lease term is three years.",
  location: { kind: "pdf" },
};

describe("ChatView", () => {
  it("streams an answer and opens its source", async () => {
    let saved = false;
    mockFetch({
      "GET /api/chats/5": () =>
        json({
          ...chat,
          messages: saved
            ? [
                {
                  id: 1,
                  role: "user",
                  content: "How long?",
                  provider: null,
                  model: null,
                  created_at: "t",
                  sources: [],
                },
                {
                  id: 2,
                  role: "assistant",
                  content: "Three years [1].",
                  provider: "local",
                  model: "qwen",
                  created_at: "t",
                  sources: [source],
                },
              ]
            : [],
        }),
      "POST /api/chats/5/messages": () => {
        saved = true;
        return sse([
          { type: "sources", sources: [source] },
          { type: "token", text: "Three years " },
          { type: "token", text: "[1]." },
          { type: "done", message_id: 2 },
        ]);
      },
    });
    const onAnswered = vi.fn();
    view = await mount(
      <ChatView chatId={5} createChat={vi.fn()} onBusyChange={vi.fn()} onAnswered={onAnswered} />,
    );
    await typeInto(view.container.querySelector("textarea"), "How long?");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(onAnswered).toHaveBeenCalled();
    expect(view.container.querySelector(".message.assistant")?.textContent).toContain(
      "Three years 1.",
    );
    await click(view.container.querySelector("button.cite"));
    expect(view.container.querySelector(".source-panel")?.textContent).toContain(
      "The lease term is three years.",
    );
  });

  it("creates the chat on the first question", async () => {
    const calls = mockFetch({
      "POST /api/chats/9/messages": () => sse([{ type: "done", message_id: 1 }]),
      "GET /api/chats/9": () => json({ ...chat, id: 9, messages: [] }),
    });
    const createChat = vi.fn(async () => 9);
    view = await mount(
      <ChatView chatId={null} createChat={createChat} onBusyChange={vi.fn()} onAnswered={vi.fn()} />,
    );
    await typeInto(view.container.querySelector("textarea"), "first question");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(createChat).toHaveBeenCalledTimes(1);
    expect(calls.map((c) => c.key)).toEqual(["POST /api/chats/9/messages", "GET /api/chats/9"]);
  });

  it("shows an error from the core", async () => {
    mockFetch({
      "GET /api/chats/5": () => json({ ...chat, messages: [] }),
      "POST /api/chats/5/messages": () =>
        sse([{ type: "error", message: "Choose a folder of documents first." }]),
    });
    view = await mount(
      <ChatView chatId={5} createChat={vi.fn()} onBusyChange={vi.fn()} onAnswered={vi.fn()} />,
    );
    await typeInto(view.container.querySelector("textarea"), "q");
    await click(view.container.querySelector('button[type="submit"]'));
    expect(view.container.querySelector('[role="alert"]')?.textContent).toBe(
      "Choose a folder of documents first.",
    );
  });
});
```

`ui/src/App.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { json, type Mounted, mockFetch, mount } from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("asks for a folder first", async () => {
    mockFetch({
      "GET /api/collection": () => json({ collection: null, index: null }),
      "GET /api/chats": () => json({ chats: [] }),
    });
    view = await mount(<App />);
    expect(view.container.textContent).toContain("Choose a folder of documents");
  });

  it("shows the index and the chats once a folder is chosen", async () => {
    mockFetch({
      "GET /api/collection": () =>
        json({
          collection: { id: 1, name: "Contracts", folder_path: "C:/c", created_at: "t" },
          index: {
            stale: false,
            counts: { pending: 0, indexing: 0, indexed: 2, failed: 0, skipped: 0 },
            current: null,
            error: null,
            problems: [],
          },
        }),
      "GET /api/chats": () =>
        json({ chats: [{ id: 3, title: "Lease length", created_at: "t", updated_at: "t" }] }),
    });
    view = await mount(<App />);
    expect(view.container.textContent).toContain("2 of 2 files indexed");
    expect(view.container.textContent).toContain("Lease length");
  });
});
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `npm --prefix ui test`
Expected: the new test files fail to import the components (`Failed to resolve import "./AnswerText"` and the like).

- [ ] **Step 4: Implement**

`ui/src/AnswerText.tsx`:

```tsx
import { splitCitations } from "./citations";

type Props = { text: string; sourceCount: number; onCite: (n: number) => void };

/** Answer text as plain text, with [n] markers as buttons that open source n. */
export default function AnswerText({ text, sourceCount, onCite }: Props) {
  return (
    <div className="answer-text">
      {splitCitations(text, sourceCount).map((segment, i) =>
        segment.kind === "text" ? (
          <span key={i}>{segment.text}</span>
        ) : (
          <button key={i} type="button" className="cite" onClick={() => onCite(segment.n)}>
            {segment.n}
          </button>
        ),
      )}
    </div>
  );
}
```

`ui/src/Setup.tsx`:

```tsx
import { type FormEvent, useState } from "react";
import { api } from "./api";
import type { CollectionState } from "./types";

type Props = { onDone: (state: CollectionState) => void; onCancel?: () => void };

/** Choose the folder of documents to index. */
export default function Setup({ onDone, onCancel }: Props) {
  const [folder, setFolder] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  async function browse() {
    setError(null);
    try {
      const picked = await api<{ folder_path: string | null }>("POST", "/api/pick-folder");
      if (picked.folder_path) setFolder(picked.folder_path);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setWorking(true);
    setError(null);
    try {
      const state = await api<CollectionState>("PUT", "/api/collection", {
        name: "",
        folder_path: folder.trim(),
      });
      onDone(state);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWorking(false);
    }
  }

  return (
    <form className="setup" onSubmit={submit}>
      <h2>Choose a folder of documents</h2>
      <p className="muted">
        Tamra indexes the PDF, Word, text, and Markdown files in this folder and its subfolders.
        It never changes them.
      </p>
      <div className="setup-row">
        <input
          aria-label="Folder path"
          placeholder="Full folder path"
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
        />
        <button type="button" onClick={browse}>
          Browse…
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="setup-row">
        <button type="submit" disabled={working || !folder.trim()}>
          Use this folder
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
```

`ui/src/IndexStatus.tsx`:

```tsx
import type { Collection, IndexStatus as IndexState } from "./types";

type Props = {
  collection: Collection;
  index: IndexState | null;
  disabled: boolean;
  onChangeFolder: () => void;
  onRebuild: () => void;
};

/** The chosen folder, indexing progress, and files that could not be indexed fully. */
export default function IndexStatus({
  collection,
  index,
  disabled,
  onChangeFolder,
  onRebuild,
}: Props) {
  const counts = index?.counts;
  const total = counts ? Object.values(counts).reduce((sum, n) => sum + n, 0) : 0;
  const waiting = counts ? counts.pending + counts.indexing : 0;
  return (
    <section className="index-status">
      <div className="index-folder">
        <strong>{collection.name}</strong>
        <span className="muted">{collection.folder_path}</span>
        <button type="button" onClick={onChangeFolder} disabled={disabled}>
          Change folder
        </button>
      </div>
      {index?.stale ? (
        <p className="warning">
          This index was built with a different embedding model. Rebuild it to search again.{" "}
          <button type="button" onClick={onRebuild} disabled={disabled}>
            Rebuild index
          </button>
        </p>
      ) : (
        <p className="muted">
          {counts ? `${counts.indexed} of ${total} files indexed` : "Checking files…"}
          {waiting > 0 && index?.current ? ` · indexing ${index.current}` : ""}
        </p>
      )}
      {index?.error && <p className="error">{index.error}</p>}
      {index && index.problems.length > 0 && (
        <details className="problems">
          <summary>{index.problems.length} file(s) need attention</summary>
          <ul>
            {index.problems.map((problem) => (
              <li key={problem.rel_path}>
                {problem.rel_path}{" "}
                <span className="muted">
                  ({problem.status}) {problem.error}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
```

`ui/src/ChatList.tsx`:

```tsx
import type { Chat } from "./types";

type Props = {
  chats: Chat[];
  activeId: number | null;
  disabled: boolean;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete: (id: number) => void;
};

export default function ChatList({ chats, activeId, disabled, onSelect, onNew, onDelete }: Props) {
  return (
    <nav className="chat-list">
      <button type="button" className="new-chat" onClick={onNew} disabled={disabled}>
        New chat
      </button>
      <ul>
        {chats.map((chat) => (
          <li key={chat.id} className={chat.id === activeId ? "active" : undefined}>
            <button
              type="button"
              className="chat-title"
              onClick={() => onSelect(chat.id)}
              disabled={disabled}
            >
              {chat.title || "New chat"}
            </button>
            <button
              type="button"
              className="delete"
              aria-label={`Delete ${chat.title || "chat"}`}
              onClick={() => onDelete(chat.id)}
              disabled={disabled}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
```

`ui/src/ChatView.tsx`:

```tsx
import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import AnswerText from "./AnswerText";
import { api, streamAnswer } from "./api";
import type { ChatDetail, Message, Source } from "./types";

type Pending = { question: string; sources: Source[]; text: string; error: string | null };

type Props = {
  chatId: number | null;
  createChat: () => Promise<number>;
  onBusyChange: (busy: boolean) => void;
  onAnswered: () => void;
};

function sourceOf(sources: Source[], n: number): Source | null {
  return sources.find((source) => source.n === n) ?? null;
}

/** One chat: its messages, the answer being streamed, the question box, and a source panel. */
export default function ChatView({ chatId, createChat, onBusyChange, onAnswered }: Props) {
  const [detail, setDetail] = useState<ChatDetail | null>(null);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [opened, setOpened] = useState<Source | null>(null);
  const asking = useRef(false);

  useEffect(() => {
    if (asking.current) return; // this chat was just created for the question being answered
    setOpened(null);
    setNotice(null);
    setDetail(null);
    if (chatId === null) return;
    let current = true;
    api<ChatDetail>("GET", `/api/chats/${chatId}`)
      .then((loaded) => {
        if (current) setDetail(loaded);
      })
      .catch((e: Error) => {
        if (current) setNotice(e.message);
      });
    return () => {
      current = false;
    };
  }, [chatId]);

  async function ask(event?: FormEvent) {
    event?.preventDefault();
    const text = question.trim();
    if (!text || asking.current) return;
    asking.current = true;
    setQuestion("");
    setNotice(null);
    setOpened(null);
    let state: Pending = { question: text, sources: [], text: "", error: null };
    setPending(state);
    onBusyChange(true);
    try {
      const id = chatId ?? (await createChat());
      await streamAnswer(id, text, (e) => {
        if (e.type === "sources") state = { ...state, sources: e.sources };
        else if (e.type === "token") state = { ...state, text: state.text + e.text };
        else if (e.type === "error") state = { ...state, error: e.message };
        setPending(state);
      });
      setDetail(await api<ChatDetail>("GET", `/api/chats/${id}`));
    } catch (e) {
      state = { ...state, error: (e as Error).message };
    } finally {
      asking.current = false;
      setNotice(state.error);
      setPending(null);
      onBusyChange(false);
      onAnswered();
    }
  }

  function stop() {
    api("POST", "/api/answer/cancel").catch((e: Error) => setNotice(e.message));
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void ask();
    }
  }

  const messages: Message[] = detail?.messages ?? [];
  const ready = chatId === null || detail !== null;
  return (
    <div className="chat-view">
      <div className="messages">
        {ready && messages.length === 0 && !pending && (
          <p className="muted empty">
            Ask a question about your documents. Answers cite their sources, like [1].
          </p>
        )}
        {messages.map((message) => (
          <MessageView key={message.id} message={message} onOpen={setOpened} />
        ))}
        {pending && (
          <>
            <div className="message user">{pending.question}</div>
            <div className="message assistant">
              <AnswerText
                text={pending.text || "…"}
                sourceCount={pending.sources.length}
                onCite={(n) => setOpened(sourceOf(pending.sources, n))}
              />
              <SourceList sources={pending.sources} onOpen={setOpened} />
            </div>
          </>
        )}
        {notice && (
          <p className="error" role="alert">
            {notice}
          </p>
        )}
      </div>
      {opened && <SourcePanel source={opened} onClose={() => setOpened(null)} />}
      <form className="ask" onSubmit={ask}>
        <textarea
          aria-label="Question"
          placeholder="Ask about your documents (Enter to send, Shift+Enter for a new line)"
          value={question}
          maxLength={4000}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={!ready}
        />
        {pending ? (
          <button type="button" onClick={stop}>
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!ready || !question.trim()}>
            Ask
          </button>
        )}
      </form>
    </div>
  );
}

function MessageView({ message, onOpen }: { message: Message; onOpen: (source: Source) => void }) {
  if (message.role === "user") return <div className="message user">{message.content}</div>;
  return (
    <div className="message assistant">
      <AnswerText
        text={message.content}
        sourceCount={message.sources.length}
        onCite={(n) => {
          const source = sourceOf(message.sources, n);
          if (source) onOpen(source);
        }}
      />
      <SourceList sources={message.sources} onOpen={onOpen} />
      {message.model && <p className="muted model">{message.model}</p>}
    </div>
  );
}

function SourceList({ sources, onOpen }: { sources: Source[]; onOpen: (source: Source) => void }) {
  if (sources.length === 0) return null;
  return (
    <ol className="source-list">
      {sources.map((source) => (
        <li key={source.n}>
          <button type="button" onClick={() => onOpen(source)}>
            [{source.n}] {source.file}
            {source.label ? ` · ${source.label}` : ""}
          </button>
        </li>
      ))}
    </ol>
  );
}

function SourcePanel({ source, onClose }: { source: Source; onClose: () => void }) {
  return (
    <aside className="source-panel" aria-label="Source">
      <header>
        <strong>
          [{source.n}] {source.file}
        </strong>
        <span className="muted">{source.label}</span>
        <button type="button" aria-label="Close source" onClick={onClose}>
          ×
        </button>
      </header>
      <p className="source-text">{source.text}</p>
    </aside>
  );
}
```

Replace `ui/src/App.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import ChatList from "./ChatList";
import ChatView from "./ChatView";
import IndexStatus from "./IndexStatus";
import Setup from "./Setup";
import type { Chat, CollectionState } from "./types";

const POLL_MS = 2000;

export default function App() {
  const [state, setState] = useState<CollectionState | null>(null);
  const [offline, setOffline] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [changingFolder, setChangingFolder] = useState(false);

  const refreshCollection = useCallback(async () => {
    try {
      setState(await api<CollectionState>("GET", "/api/collection"));
      setOffline(null);
    } catch (e) {
      setOffline((e as Error).message);
    }
  }, []);

  const refreshChats = useCallback(async () => {
    try {
      setChats((await api<{ chats: Chat[] }>("GET", "/api/chats")).chats);
    } catch (e) {
      setProblem((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void refreshCollection();
    void refreshChats();
    const timer = window.setInterval(() => void refreshCollection(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [refreshCollection, refreshChats]);

  async function createChat(): Promise<number> {
    const chat = await api<Chat>("POST", "/api/chats");
    setChats((list) => [chat, ...list]);
    setActiveId(chat.id);
    return chat.id;
  }

  async function deleteChat(id: number) {
    if (!window.confirm("Delete this chat?")) return;
    try {
      await api("DELETE", `/api/chats/${id}`);
      setChats((list) => list.filter((chat) => chat.id !== id));
      setActiveId((active) => (active === id ? null : active));
    } catch (e) {
      setProblem((e as Error).message);
    }
  }

  async function rebuild() {
    try {
      await api("POST", "/api/collection/rebuild");
      await refreshCollection();
    } catch (e) {
      setProblem((e as Error).message);
    }
  }

  if (state === null) {
    return (
      <main className="center">
        {offline ? `Tamra core unreachable: ${offline}` : "Connecting to Tamra…"}
      </main>
    );
  }
  const collection = state.collection;
  if (collection === null || changingFolder) {
    return (
      <main className="center">
        <Setup
          onDone={(next) => {
            setState(next);
            setChangingFolder(false);
          }}
          onCancel={collection ? () => setChangingFolder(false) : undefined}
        />
      </main>
    );
  }
  return (
    <div className="app">
      <header className="app-header">
        <h1>Tamra</h1>
        <IndexStatus
          collection={collection}
          index={state.index}
          disabled={busy}
          onChangeFolder={() => setChangingFolder(true)}
          onRebuild={() => void rebuild()}
        />
      </header>
      {(offline || problem) && (
        <p className="error banner" role="alert">
          {offline ? `Tamra core unreachable: ${offline}` : problem}
        </p>
      )}
      <ChatList
        chats={chats}
        activeId={activeId}
        disabled={busy}
        onSelect={(id) => {
          setProblem(null);
          setActiveId(id);
        }}
        onNew={() => {
          setProblem(null);
          setActiveId(null);
        }}
        onDelete={(id) => void deleteChat(id)}
      />
      <ChatView
        chatId={activeId}
        createChat={createChat}
        onBusyChange={setBusy}
        onAnswered={() => void refreshChats()}
      />
    </div>
  );
}
```

`ui/src/styles.css`:

```css
:root {
  font-family: "Segoe UI", "Leelawadee UI", "Microsoft YaHei UI", system-ui, sans-serif;
  font-size: 15px;
  color: #1f2328;
  background: #f6f7f9;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
}

button,
input,
textarea {
  font: inherit;
}

button {
  cursor: pointer;
}

button:disabled {
  cursor: default;
  opacity: 0.5;
}

.muted {
  color: #6a737d;
}

.error {
  color: #b42318;
}

.warning {
  color: #8a5a00;
}

.center {
  max-width: 640px;
  margin: 15vh auto;
  padding: 0 24px;
}

.setup-row {
  display: flex;
  gap: 8px;
  margin: 12px 0;
}

.setup input {
  flex: 1;
  padding: 8px;
}

.app {
  display: grid;
  grid-template-columns: 260px 1fr;
  grid-template-rows: auto auto 1fr;
  height: 100vh;
}

.app-header {
  grid-column: 1 / -1;
  display: flex;
  gap: 24px;
  align-items: flex-start;
  padding: 12px 20px;
  background: #fff;
  border-bottom: 1px solid #e3e6ea;
}

.app-header h1 {
  margin: 0;
  font-size: 20px;
}

.banner {
  grid-column: 1 / -1;
  margin: 0;
  padding: 8px 20px;
  background: #fff4f2;
}

.index-folder {
  display: flex;
  gap: 8px;
  align-items: baseline;
  flex-wrap: wrap;
}

.index-status p {
  margin: 4px 0 0;
}

.problems ul {
  margin: 4px 0;
  padding-left: 20px;
  max-height: 120px;
  overflow: auto;
}

.chat-list {
  grid-row: 3;
  overflow: auto;
  padding: 12px;
  background: #fff;
  border-right: 1px solid #e3e6ea;
}

.new-chat {
  width: 100%;
  padding: 8px;
}

.chat-list ul {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
}

.chat-list li {
  display: flex;
  align-items: center;
  border-radius: 6px;
}

.chat-list li.active {
  background: #eef2ff;
}

.chat-title {
  flex: 1;
  overflow: hidden;
  padding: 8px;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  background: none;
  border: 0;
}

.delete {
  padding: 4px 8px;
  color: #6a737d;
  background: none;
  border: 0;
}

.chat-view {
  grid-row: 3;
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-rows: 1fr auto;
  min-height: 0;
}

.messages {
  grid-row: 1;
  grid-column: 1;
  min-height: 0;
  overflow: auto;
  padding: 20px;
}

.message {
  max-width: 760px;
  margin: 0 0 16px;
  padding: 12px 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  border-radius: 10px;
}

.message.user {
  width: fit-content;
  margin-left: auto;
  background: #e8eefc;
}

.message.assistant {
  background: #fff;
  border: 1px solid #e3e6ea;
}

.cite {
  margin: 0 2px;
  padding: 0 6px;
  font-size: 12px;
  color: #2b3fd1;
  vertical-align: 1px;
  background: #eef2ff;
  border: 1px solid #9aa9ff;
  border-radius: 10px;
}

.source-list {
  margin: 8px 0 0;
  padding: 0;
  font-size: 13px;
  white-space: normal;
  list-style: none;
}

.source-list button {
  padding: 2px 0;
  color: #2b3fd1;
  text-align: left;
  background: none;
  border: 0;
}

.model {
  margin: 6px 0 0;
  font-size: 12px;
}

.source-panel {
  grid-row: 1 / 3;
  grid-column: 2;
  width: 360px;
  overflow: auto;
  padding: 16px;
  background: #fff;
  border-left: 1px solid #e3e6ea;
}

.source-panel header {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.source-panel header button {
  margin-left: auto;
  font-size: 18px;
  background: none;
  border: 0;
}

.source-text {
  line-height: 1.6;
  white-space: pre-wrap;
}

.ask {
  grid-row: 2;
  grid-column: 1;
  display: flex;
  gap: 8px;
  padding: 12px 20px;
  background: #fff;
  border-top: 1px solid #e3e6ea;
}

.ask textarea {
  flex: 1;
  height: 64px;
  padding: 8px;
  resize: none;
}
```

In `ui/src/main.tsx`, add `import "./styles.css";` after the other imports.

- [ ] **Step 5: Run the tests and the build**

Run: `npm --prefix ui test; npm --prefix ui run build`
Expected: all Vitest tests pass; `tsc` and `vite build` succeed.
Then: `uv run pytest`

- [ ] **Step 6: Check the UI against the real core**

Start the core in dev mode as in Task 13 Step 5 (scratch data folder, PID recorded; skip if port 8765 is taken). If a Vite dev server already answers on `http://localhost:5173` (it may be the user's own; do not stop it), use it; otherwise start `npm --prefix ui run dev` as a background process and record its PID. Open `http://localhost:5173/#token=dev` in the built-in browser pane and check, with screenshots in the report: the folder setup screen; typing the scratch `docs` folder; the index status reaching "1 of 1 files indexed"; asking a Thai question; the streamed answer with its source list; a `[n]` button or source entry opening the source panel; the chat appearing in the sidebar with its title; and the address bar no longer containing the token. Stop only the processes you started, by PID, and delete the scratch folder.

- [ ] **Step 7: Commit**

```powershell
git add ui/src
git commit -m "feat(ui): folder setup, index status, chat list, and streamed answers with source chips"
```

---

### Task 16: Retrieval eval and the "not found" threshold

**Files:**
- Create: `eval/corpus/` (10 files below), `eval/questions.jsonl`, `scripts/build_eval_corpus.py`, `scripts/eval_retrieval.py`, `docs/spikes/2026-10-m1-results.md`
- Modify: `pyproject.toml` (pytest `pythonpath`), `src/tamra/answer.py` (`AnswerSettings.min_similarity` default and its comment)
- Test: `tests/test_eval.py`

**Interfaces:**
- Consumes: `tests/docgen.py` (Task 3), `Indexer` (Task 5), `bge_token_spans` (Task 4), `hybrid_search`, `fts_query`, `best_similarity` (Task 7), `Embedder.load`, `MODEL_ID` (Task 10), `AnswerSettings` (Task 9).
- Produces:
  - `scripts/build_eval_corpus.py`: `CORPUS` (the `eval/corpus` path), `targets(corpus=CORPUS) -> list[str]` (sorted document names), `build(out_dir, corpus=CORPUS) -> list[Path]`. Plain `.md`/`.txt` files are copied; `<name>.json` specs render to `<name>`: `.docx` from `{"blocks": [[level, text], ...]}`, `.pdf` from `{"font": "latin_thai" | "cjk", "pages": [[line, ...], ...]}`, `.txt` from `{"encoding": ..., "text": ...}` (a legacy-encoded file).
  - `scripts/eval_retrieval.py`: `QUESTIONS`, `load_questions(path=QUESTIONS) -> list[dict]` (keys `id`, `lang`, `question`, `file`, `page`; `file` is `None` for off-topic questions), `is_hit(question, chunk) -> bool`, `first_hit_rank(question, chunks) -> int | None`, `hit_rates(results, ks=(1, 4, 10)) -> dict[str, float]`, `sweep_thresholds(results, low=0.20, high=0.80, step=0.01) -> dict` (`best_accuracy`, `range`, `recommended`), `run_eval(corpus_dir, questions, model_dir) -> dict` (`model_id`, `files`, `hit_rates`, `hit_rates_by_language`, `threshold`, `questions`), and a CLI.
  - `AnswerSettings.min_similarity` set from the eval (spec §6, §12).
  - `docs/spikes/2026-10-m1-results.md` with the eval numbers.

- [ ] **Step 1: Add the corpus and the questions**

Create these files with exactly this content (UTF-8).

`eval/corpus/hr-leave-policy.docx.json`:

```json
{
  "blocks": [
    [1, "นโยบายการลาของพนักงาน บริษัท สยามเทค โซลูชั่น จำกัด"],
    [2, "การลาพักร้อน"],
    [0, "พนักงานที่ผ่านการทดลองงานแล้วมีสิทธิลาพักร้อนปีละ 12 วันทำงาน ต้องยื่นคำขอผ่านระบบ HR ล่วงหน้าอย่างน้อย 7 วัน วันลาพักร้อนที่ไม่ได้ใช้สะสมไปปีถัดไปได้ไม่เกิน 6 วัน"],
    [2, "การลาป่วย"],
    [0, "พนักงานลาป่วยได้ตามจริงโดยได้รับค่าจ้างไม่เกิน 30 วันทำงานต่อปี หากลาป่วยติดต่อกันตั้งแต่ 3 วันขึ้นไปต้องแนบใบรับรองแพทย์"],
    [2, "การลาคลอด"],
    [0, "พนักงานหญิงมีสิทธิลาคลอดได้ 98 วัน โดยบริษัทจ่ายค่าจ้างให้ 45 วัน ส่วนที่เหลือเบิกจากประกันสังคม พนักงานชายลาไปช่วยดูแลภรรยาและบุตรแรกเกิดได้ 5 วันทำงาน"],
    [2, "การลาบวช"],
    [0, "พนักงานที่ทำงานครบ 1 ปีมีสิทธิลาบวชได้ 15 วันโดยได้รับค่าจ้าง ใช้สิทธิได้ครั้งเดียวตลอดการเป็นพนักงาน"]
  ]
}
```

`eval/corpus/apartment-lease.pdf.json`:

```json
{
  "font": "latin_thai",
  "pages": [
    [
      "Residential Lease Agreement - Riverside Residence, Unit 1204",
      "Landlord: Riverside Property Co., Ltd.  Tenant: Ms. Kanya Srisuk",
      "1. Term. The lease term is three years, starting on 1 March 2026.",
      "2. Rent. The monthly rent is 18,500 baht, due on the 5th day of each month.",
      "   A late payment fee of 200 baht per day applies after the due date.",
      "3. Deposit. The tenant pays a security deposit equal to two months' rent",
      "   (37,000 baht). It is returned within 30 days after the tenant moves out."
    ],
    [
      "4. Pets. Cats are allowed. Dogs and other animals are not allowed.",
      "5. Parking. One parking space on level B2 is included in the rent.",
      "6. Early termination. Either party may end the lease with 60 days' written notice.",
      "   If the tenant ends the lease within the first year, the deposit is forfeited.",
      "7. Maintenance. The landlord repairs the air conditioner and water heater at no cost."
    ]
  ]
}
```

`eval/corpus/furniture-warranty.txt`:

```text
卡诺家具产品保修条款

沙发框架保修五年，布料和海绵保修两年。
餐桌和椅子保修一年，床垫保修十年。

保修期内的维修服务免费。客户拨打客服热线 400-820-1234 后，技术人员会在三个工作日内上门检查。

以下情况不在保修范围内：人为损坏、宠物抓咬、阳光长期直射造成的褪色，以及自行拆装造成的问题。
```

`eval/corpus/it-security-policy.md`:

```markdown
# IT Security Policy

## Passwords

Passwords must be at least 14 characters long. Do not reuse any of your last five passwords.
IT approves two password managers: Bitwarden and 1Password.

## Multi-factor authentication

Multi-factor authentication (MFA) is required for email, the VPN, and the HR system. Use the
Microsoft Authenticator app. Codes sent by SMS are not accepted.

## Laptops

Every company laptop must use BitLocker disk encryption. Lock your screen when you leave your
desk; laptops also lock automatically after 5 minutes without activity.

## Reporting incidents

Report a lost or stolen device, or a suspected phishing email, to security@siamtech.example
within one hour. Outside office hours, call the security hotline at extension 4455.
```

`eval/corpus/travel-expenses.pdf.json`:

```json
{
  "font": "latin_thai",
  "pages": [
    [
      "ระเบียบการเบิกค่าใช้จ่ายในการเดินทางไปปฏิบัติงาน",
      "1. เบี้ยเลี้ยงเดินทางในประเทศวันละ 270 บาท",
      "2. ค่าที่พักในกรุงเทพฯ เบิกได้ไม่เกินคืนละ 1,500 บาท",
      "   ค่าที่พักต่างจังหวัดเบิกได้ไม่เกินคืนละ 1,200 บาท",
      "3. การใช้รถยนต์ส่วนตัวเบิกค่าน้ำมันได้กิโลเมตรละ 4 บาท"
    ],
    [
      "4. การเดินทางโดยเครื่องบินต้องได้รับอนุมัติจากผู้อำนวยการฝ่ายก่อน",
      "   และเบิกได้เฉพาะชั้นประหยัดเท่านั้น",
      "5. ต้องยื่นเบิกค่าใช้จ่ายพร้อมใบเสร็จภายใน 30 วันหลังเดินทางกลับ",
      "6. ค่าเครื่องดื่มแอลกอฮอล์เบิกไม่ได้ทุกกรณี"
    ]
  ]
}
```

`eval/corpus/air-purifier-manual.docx.json`:

```json
{
  "blocks": [
    [1, "AirPure 400 空气净化器使用说明"],
    [2, "滤网更换"],
    [0, "HEPA 滤网建议每六个月更换一次。滤网指示灯变红时，请及时更换滤网。"],
    [2, "运行模式"],
    [0, "睡眠模式下噪音仅为 22 分贝，指示灯自动熄灭。自动模式会根据 PM2.5 浓度调节风速。"],
    [2, "童锁"],
    [0, "长按电源键三秒即可开启或关闭童锁。"],
    [2, "规格"],
    [0, "洁净空气量（CADR）为每小时 400 立方米，适用面积 28 至 48 平方米，额定功率 45 瓦。"]
  ]
}
```

`eval/corpus/canteen-rules.txt.json` (rendered as a TIS-620/cp874 file, to exercise legacy Thai decoding):

```json
{
  "encoding": "cp874",
  "text": "ระเบียบโรงอาหารพนักงาน\n\nโรงอาหารเปิดให้บริการเวลา 07.00 ถึง 15.00 น. ทุกวันจันทร์ถึงวันศุกร์\nอาหารกลางวันราคาชุดละ 45 บาท ชำระเงินด้วยบัตรพนักงานเท่านั้น\n\nห้ามนำอาหารจากโรงอาหารขึ้นไปรับประทานในห้องประชุม\nกรุณาแยกขยะเศษอาหารและขวดพลาสติกก่อนทิ้ง\n"
}
```

`eval/corpus/shipping-policy.pdf.json`:

```json
{
  "font": "cjk",
  "pages": [
    [
      "卡诺家具配送与退货政策",
      "订单满 299 元免运费，未满 299 元收取 20 元运费。",
      "上海市内三到五个工作日送达，其他城市七到十个工作日送达。",
      "收货后七天内可无理由退货，定制家具除外。",
      "大件家具提供免费送货上门和安装服务。"
    ]
  ]
}
```

`eval/corpus/meeting-notes-2026-09.md` (a distractor: no question targets it):

```markdown
# Team meeting notes, 15 September 2026

- The office moves to the 9th floor of Sathorn Square on 1 November. Packing starts on 25 October.
- The second-floor printer is broken. Use the printer near the pantry until the technician visits.
- The quarterly team lunch is on 30 September at noon. Vegetarian options will be available.
- New laptops for the design team arrive in October.
- Next meeting: 22 September, 10:00, meeting room B.
```

`eval/corpus/company-history.txt` (a distractor):

```text
ประวัติบริษัท สยามเทค โซลูชั่น

บริษัทก่อตั้งขึ้นในปี 2553 ที่จังหวัดเชียงใหม่ โดยเริ่มจากทีมพัฒนาซอฟต์แวร์ 5 คน
ในปี 2558 บริษัทย้ายสำนักงานใหญ่มาที่กรุงเทพมหานคร และเปิดสาขาที่ขอนแก่นในปี 2562
ปัจจุบันบริษัทมีพนักงานประมาณ 120 คน ให้บริการระบบบัญชีและระบบบริหารงานบุคคลแก่ลูกค้ากว่า 300 ราย
```

`eval/questions.jsonl` (24 answerable questions, many cross-language, and 4 off-topic ones):

```json
{"id": "th-leave-annual", "lang": "th", "question": "พนักงานลาพักร้อนได้ปีละกี่วัน", "file": "hr-leave-policy.docx", "page": null}
{"id": "th-leave-sick-certificate", "lang": "th", "question": "ลาป่วยกี่วันขึ้นไปต้องมีใบรับรองแพทย์", "file": "hr-leave-policy.docx", "page": null}
{"id": "en-leave-maternity", "lang": "en", "question": "How many days of maternity leave does the company pay for?", "file": "hr-leave-policy.docx", "page": null}
{"id": "en-lease-rent", "lang": "en", "question": "How much is the monthly rent and when is it due?", "file": "apartment-lease.pdf", "page": 1}
{"id": "en-lease-dog", "lang": "en", "question": "Can I keep a dog in the apartment?", "file": "apartment-lease.pdf", "page": 2}
{"id": "th-lease-notice", "lang": "th", "question": "ถ้าจะยกเลิกสัญญาเช่าก่อนกำหนดต้องแจ้งล่วงหน้ากี่วัน", "file": "apartment-lease.pdf", "page": 2}
{"id": "zh-lease-deposit", "lang": "zh", "question": "租房押金是多少？", "file": "apartment-lease.pdf", "page": 1}
{"id": "zh-warranty-sofa", "lang": "zh", "question": "沙发框架保修几年？", "file": "furniture-warranty.txt", "page": null}
{"id": "zh-warranty-pets", "lang": "zh", "question": "宠物抓坏的沙发可以免费维修吗？", "file": "furniture-warranty.txt", "page": null}
{"id": "en-warranty-mattress", "lang": "en", "question": "How long is the mattress warranty?", "file": "furniture-warranty.txt", "page": null}
{"id": "en-security-password", "lang": "en", "question": "What is the minimum password length?", "file": "it-security-policy.md", "page": null}
{"id": "en-security-lost-laptop", "lang": "en", "question": "Who do I tell if I lose my laptop, and how quickly?", "file": "it-security-policy.md", "page": null}
{"id": "th-security-mfa-app", "lang": "th", "question": "ต้องใช้แอปอะไรในการยืนยันตัวตนแบบหลายขั้นตอน", "file": "it-security-policy.md", "page": null}
{"id": "th-travel-hotel", "lang": "th", "question": "ค่าที่พักในกรุงเทพฯ เบิกได้คืนละเท่าไร", "file": "travel-expenses.pdf", "page": 1}
{"id": "th-travel-deadline", "lang": "th", "question": "ต้องยื่นเบิกค่าเดินทางภายในกี่วัน", "file": "travel-expenses.pdf", "page": 2}
{"id": "en-travel-mileage", "lang": "en", "question": "What is the mileage rate when I use my own car for work travel?", "file": "travel-expenses.pdf", "page": 1}
{"id": "zh-travel-hotel", "lang": "zh", "question": "出差住曼谷的酒店每晚可以报销多少钱？", "file": "travel-expenses.pdf", "page": 1}
{"id": "zh-purifier-filter", "lang": "zh", "question": "滤网多久更换一次？", "file": "air-purifier-manual.docx", "page": null}
{"id": "zh-purifier-child-lock", "lang": "zh", "question": "怎么开启童锁？", "file": "air-purifier-manual.docx", "page": null}
{"id": "th-purifier-noise", "lang": "th", "question": "เครื่องฟอกอากาศเสียงดังกี่เดซิเบลในโหมดนอนหลับ", "file": "air-purifier-manual.docx", "page": null}
{"id": "th-canteen-price", "lang": "th", "question": "อาหารกลางวันที่โรงอาหารราคาชุดละเท่าไร", "file": "canteen-rules.txt", "page": null}
{"id": "en-canteen-hours", "lang": "en", "question": "What time does the staff canteen open?", "file": "canteen-rules.txt", "page": null}
{"id": "zh-shipping-free", "lang": "zh", "question": "订单满多少元免运费？", "file": "shipping-policy.pdf", "page": 1}
{"id": "en-shipping-custom-return", "lang": "en", "question": "Can custom-made furniture be returned?", "file": "shipping-policy.pdf", "page": 1}
{"id": "none-tom-yum", "lang": "th", "question": "สูตรต้มยำกุ้งน้ำข้นต้องใช้วัตถุดิบอะไรบ้าง", "file": null, "page": null}
{"id": "none-world-cup", "lang": "en", "question": "Who won the 2022 FIFA World Cup?", "file": null, "page": null}
{"id": "none-weather", "lang": "zh", "question": "明天北京的天气怎么样？", "file": null, "page": null}
{"id": "none-mercury", "lang": "en", "question": "What is the boiling point of mercury?", "file": null, "page": null}
```

In `pyproject.toml`, `[tool.pytest.ini_options]`, add `pythonpath = ["scripts"]` so tests can import the eval scripts.

- [ ] **Step 2: Write the failing tests**

`tests/test_eval.py`:

```python
import pytest
from build_eval_corpus import build, targets
from docgen import CJK, LATIN_THAI
from eval_retrieval import first_hit_rank, hit_rates, load_questions, run_eval, sweep_thresholds

from tamra.ingest.parsers import parse_file
from tamra.store import ChunkRecord


def test_questions_are_well_formed():
    questions = load_questions()
    ids = [q["id"] for q in questions]
    assert len(ids) == len(set(ids))
    assert {q["lang"] for q in questions} == {"th", "en", "zh"}
    assert all(set(q) == {"id", "lang", "question", "file", "page"} for q in questions)
    names = set(targets())
    answerable = [q for q in questions if q["file"] is not None]
    assert len(answerable) >= 20
    assert len(questions) - len(answerable) >= 4
    assert all(q["file"] in names for q in answerable)
    assert all(q["page"] is None or q["file"].endswith(".pdf") for q in questions)


@pytest.mark.skipif(
    not (LATIN_THAI.exists() and CJK.exists()), reason="needs Tahoma and Microsoft YaHei"
)
def test_the_corpus_renders_into_parseable_documents(tmp_path):
    paths = build(tmp_path)
    assert sorted(p.name for p in paths) == targets()
    for path in paths:
        assert parse_file(path).units, path.name
    canteen = parse_file(tmp_path / "canteen-rules.txt")
    assert "โรงอาหาร" in canteen.units[0].text


def chunk(rel_path, location):
    return ChunkRecord(1, 1, rel_path, 0, "text", location, None)


def test_first_hit_rank_checks_the_file_and_page():
    page_one = {"kind": "pdf", "page_start": 1, "page_end": 1, "char_start": 0, "char_end": 9}
    both_pages = {**page_one, "page_end": 2}
    chunks = [
        chunk("other.md", {"kind": "text", "line_start": 1, "line_end": 2}),
        chunk("lease.pdf", page_one),
        chunk("lease.pdf", both_pages),
    ]
    assert first_hit_rank({"file": "lease.pdf", "page": 2}, chunks) == 3
    assert first_hit_rank({"file": "other.md", "page": None}, chunks) == 1
    assert first_hit_rank({"file": "missing.md", "page": None}, chunks) is None


def test_hit_rates_count_only_answerable_questions():
    results = [
        {"answerable": True, "rank": 1},
        {"answerable": True, "rank": 3},
        {"answerable": True, "rank": None},
        {"answerable": False, "rank": None},
    ]
    assert hit_rates(results, ks=(1, 4)) == {"hit@1": 1 / 3, "hit@4": 2 / 3}


def test_the_threshold_sweep_separates_answerable_from_off_topic_questions():
    results = [
        {"answerable": True, "best_similarity": 0.62},
        {"answerable": True, "best_similarity": 0.55},
        {"answerable": False, "best_similarity": 0.41},
        {"answerable": False, "best_similarity": 0.35},
    ]
    sweep = sweep_thresholds(results)
    assert sweep["best_accuracy"] == 1.0
    assert sweep["range"] == [0.42, 0.55]
    assert 0.42 <= sweep["recommended"] <= 0.55


@pytest.mark.assets
def test_retrieval_meets_the_m1_floor(bge_dir, tmp_path):
    corpus = tmp_path / "corpus"
    build(corpus)
    report = run_eval(corpus, load_questions(), bge_dir)
    assert set(report["files"].values()) == {"indexed"}
    assert report["hit_rates"]["hit@4"] >= 0.85
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/test_eval.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'build_eval_corpus'`.

- [ ] **Step 4: Implement**

`scripts/build_eval_corpus.py`:

```python
"""Render eval/corpus into a folder of real documents for the retrieval eval (spec §12).

    uv run python scripts/build_eval_corpus.py OUT_DIR

Plain .md and .txt files are copied. A "<name>.json" spec becomes "<name>":
  .docx  {"blocks": [[heading_level, text], ...]}            level 0 is a body paragraph
  .pdf   {"font": "latin_thai" | "cjk", "pages": [[line, ...], ...]}
  .txt   {"encoding": "cp874", "text": "..."}                 a legacy-encoded text file
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "eval" / "corpus"
sys.path.insert(0, str(ROOT / "tests"))  # docgen builds the PDF and DOCX files

from docgen import CJK, LATIN_THAI, make_docx, make_pdf  # noqa: E402

FONTS = {"latin_thai": LATIN_THAI, "cjk": CJK}


def targets(corpus: Path = CORPUS) -> list[str]:
    """The names of the documents the corpus renders to, sorted."""
    return sorted(p.name.removesuffix(".json") for p in corpus.iterdir() if p.is_file())


def build(out_dir: Path, corpus: Path = CORPUS) -> list[Path]:
    """Render every corpus entry into out_dir and return the written paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for source in sorted(p for p in corpus.iterdir() if p.is_file()):
        if source.suffix != ".json":
            written.append(Path(shutil.copy2(source, out_dir / source.name)))
            continue
        target = out_dir / source.name.removesuffix(".json")
        spec = json.loads(source.read_text(encoding="utf-8"))
        if target.suffix == ".docx":
            make_docx(target, [(level, text) for level, text in spec["blocks"]])
        elif target.suffix == ".pdf":
            make_pdf(target, spec["pages"], FONTS[spec["font"]])
        elif target.suffix == ".txt":
            target.write_bytes(spec["text"].encode(spec["encoding"]))
        else:
            raise ValueError(f"no renderer for {source.name}")
        written.append(target)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the eval corpus into a folder")
    parser.add_argument("out_dir", type=Path)
    for path in build(parser.parse_args().out_dir):
        print(path)


if __name__ == "__main__":
    main()
```

`scripts/eval_retrieval.py`:

```python
"""Retrieval eval (spec §12): hit@k on eval/questions.jsonl and the "not found" threshold.

    uv run python scripts/eval_retrieval.py [--out results.json]

Needs the bge-m3 model (scripts/fetch_assets.py). Renders eval/corpus into a temporary folder,
indexes it with the real embedder, and runs every question through hybrid search.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from build_eval_corpus import build

from tamra.embedder import MODEL_ID, Embedder
from tamra.ingest.chunker import bge_token_spans
from tamra.ingest.indexer import Indexer
from tamra.retriever import best_similarity, fts_query, hybrid_search
from tamra.store import ChunkRecord, Store

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "eval" / "questions.jsonl"
MODEL_DIR = ROOT / ".models" / "bge-m3"
K_VALUES = (1, 4, 10)


def load_questions(path: Path = QUESTIONS) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def is_hit(question: dict, chunk: ChunkRecord) -> bool:
    """The chunk comes from the expected file and covers the expected page, if one is given."""
    if chunk.rel_path != question["file"]:
        return False
    page = question.get("page")
    if page is None:
        return True
    return chunk.location.get("page_start", 0) <= page <= chunk.location.get("page_end", 0)


def first_hit_rank(question: dict, chunks: list[ChunkRecord]) -> int | None:
    for rank, chunk in enumerate(chunks, start=1):
        if is_hit(question, chunk):
            return rank
    return None


def hit_rates(results: list[dict], ks: tuple[int, ...] = K_VALUES) -> dict[str, float]:
    """hit@k over the answerable questions."""
    answerable = [r for r in results if r["answerable"]]
    return {
        f"hit@{k}": sum(1 for r in answerable if r["rank"] is not None and r["rank"] <= k)
        / len(answerable)
        for k in ks
    }


def sweep_thresholds(
    results: list[dict], low: float = 0.20, high: float = 0.80, step: float = 0.01
) -> dict:
    """Accuracy of the "not found" gate (answer when best similarity >= t) for each t.

    Returns the best accuracy, the lowest and highest threshold reaching it, and the middle
    one of those thresholds as the recommendation.
    """
    scored = []
    for i in range(round((high - low) / step) + 1):
        t = round(low + i * step, 2)
        correct = sum(1 for r in results if (r["best_similarity"] >= t) == r["answerable"])
        scored.append((t, correct / len(results)))
    best = max(accuracy for _, accuracy in scored)
    winners = [t for t, accuracy in scored if accuracy == best]
    return {
        "best_accuracy": best,
        "range": [winners[0], winners[-1]],
        "recommended": winners[len(winners) // 2],
    }


def run_eval(corpus_dir: Path, questions: list[dict], model_dir: Path = MODEL_DIR) -> dict:
    """Index corpus_dir with the real embedder, then retrieve for every question."""
    embedder = Embedder.load(model_dir)
    spans = bge_token_spans(model_dir / "tokenizer.json")
    with tempfile.TemporaryDirectory() as tmp:
        store = Store.open(Path(tmp) / "eval.db")
        try:
            collection = store.replace_collection("eval", str(corpus_dir), MODEL_ID)
            indexer = Indexer(store, lambda: embedder, lambda: spans, MODEL_ID)
            indexer.request_reconcile()
            indexer.process()
            files = {f.rel_path: f.status for f in store.list_files(collection.id)}
            results = []
            for q in questions:
                vector = embedder.embed([q["question"]])[0]
                hits = hybrid_search(store, collection.id, vector, fts_query(q["question"]))
                chunks = store.get_chunks([h.chunk_id for h in hits])
                results.append(
                    {
                        "id": q["id"],
                        "lang": q["lang"],
                        "answerable": q["file"] is not None,
                        "rank": first_hit_rank(q, chunks) if q["file"] else None,
                        "best_similarity": round(best_similarity(hits), 4),
                    }
                )
        finally:
            store.close()
    languages = sorted({r["lang"] for r in results if r["answerable"]})
    return {
        "model_id": MODEL_ID,
        "files": files,
        "hit_rates": hit_rates(results),
        "hit_rates_by_language": {
            lang: hit_rates([r for r in results if r["lang"] == lang]) for lang in languages
        },
        "threshold": sweep_thresholds(results),
        "questions": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Tamra retrieval eval")
    parser.add_argument("--out", type=Path, help="also write the full results as JSON")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        corpus_dir = Path(tmp) / "corpus"
        build(corpus_dir)
        report = run_eval(corpus_dir, load_questions())
    print("files:", report["files"])
    print("all:", " ".join(f"{k}={v:.2f}" for k, v in report["hit_rates"].items()))
    for lang, rates in report["hit_rates_by_language"].items():
        print(f"{lang}:", " ".join(f"{k}={v:.2f}" for k, v in rates.items()))
    threshold = report["threshold"]
    print(
        f"not-found threshold: recommended {threshold['recommended']}"
        f" (best accuracy {threshold['best_accuracy']:.2f}"
        f" from {threshold['range'][0]} to {threshold['range'][1]})"
    )
    for r in report["questions"]:
        print(f"  {r['id']:<28} rank={r['rank']} best_similarity={r['best_similarity']}")
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_eval.py -v; uv run pytest -m assets tests/test_eval.py -v`
Expected: all pass. If the asset test misses the hit@4 floor, report the numbers (do not lower the floor).
Then: `uv run pytest; uv run ruff format; uv run ruff check --fix; uv run ruff format --check`

- [ ] **Step 6: Run the eval and set the threshold**

Run: `uv run python scripts/eval_retrieval.py --out "$env:TEMP\tamra-eval.json"`

Set the default of `AnswerSettings.min_similarity` in `src/tamra/answer.py` to
`min(recommended, round(lowest best_similarity among answerable questions - 0.05, 2))`. The margin keeps a question like the eval's answerable ones from being refused; an off-topic question that slips through still meets the prompt rule "if the sources do not answer the question, say so plainly". Replace the field's comment with `# below this best dense similarity: "not found" (M1 eval; docs/spikes/2026-10-m1-results.md)`. The tests that pass `AnswerSettings(min_similarity=0.3)` explicitly are unaffected.

Write `docs/spikes/2026-10-m1-results.md` from the eval output:

```markdown
# M1 results

## Retrieval eval

- Corpus: `eval/corpus`, 10 documents (Thai, English, and Chinese; PDF, DOCX, Markdown, UTF-8
  text, and a TIS-620 text file), 2 of them distractors with no questions.
- Questions: `eval/questions.jsonl`, 24 answerable (many cross-language) and 4 off-topic.
- Embedding model: (MODEL_ID). Command: `uv run python scripts/eval_retrieval.py`.

| Questions | hit@1 | hit@4 | hit@10 |
|---|---|---|---|
| all | | | |
| th | | | |
| en | | | |
| zh | | | |

(One row per language from the eval output; values to two decimals.)

**"Not found" threshold.** The sweep's best accuracy, the threshold range that reaches it,
its recommendation, the lowest best similarity among answerable questions, the highest among
off-topic questions, and the value set in `AnswerSettings.min_similarity` with the rule
above.

**Misses.** Each answerable question whose expected file was not in the top 4, with its rank.
```

Fill every table cell and paragraph with the measured values (replace the parenthesised notes; leave no blank cells).

- [ ] **Step 7: Commit**

```powershell
git add eval scripts/build_eval_corpus.py scripts/eval_retrieval.py tests/test_eval.py pyproject.toml src/tamra/answer.py docs/spikes/2026-10-m1-results.md
git commit -m "feat(eval): TH/EN/ZH retrieval eval with hit@k and the tuned not-found threshold"
```

---

### Task 17: Packaged app — document libraries, end-to-end smoke test, M1 exit

**Files:**
- Modify: `src/tamra/selfcheck.py` (documents probe), `tests/test_selfcheck.py`, `README.md`, `CLAUDE.md` (Commands and module list), `docs/spikes/2026-10-m1-results.md`
- Create: `scripts/exe_smoke.py`
- Modify only if the packaged checks show a missing module or file: `packaging/tamra.spec`

**Interfaces:**
- Consumes: everything above; `scripts/build_eval_corpus.build`, `scripts/eval_retrieval.load_questions` (Task 16); `tamra.answer.NOT_FOUND`.
- Produces:
  - `run_selfcheck(...)` always includes `checks["documents"]`: pdfium, python-docx (with its template), and charset-normalizer load and work. The CI `package` job runs selfcheck on the packaged exe, so a bundling gap in these libraries fails CI.
  - `scripts/exe_smoke.py [--exe PATH] [--out PATH]`: the M1 exit check through the packaged exe's API; exit code 0 when no problem was found.

- [ ] **Step 1: Write the failing test**

In `tests/test_selfcheck.py`, rename `test_sqlite_only_selfcheck` to `test_selfcheck_without_models` and make its key assertion `assert set(report["checks"]) == {"sqlite", "documents"}`. Append:

```python
def test_document_libraries_are_checked(tmp_path):
    report = run_selfcheck(None, None, tmp_path / "x.exe", tmp_path)
    assert report["checks"]["documents"] == {"ok": True}
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_selfcheck.py -v`
Expected: the two tests fail (`KeyError: 'documents'`, and the check set has only `sqlite`).

- [ ] **Step 3: Implement**

Add to `src/tamra/selfcheck.py`, after `_check_sqlite`:

```python
def _check_documents() -> dict:
    """The document libraries load and work: pdfium, python-docx's template, charset detection."""
    import io

    import docx
    import pypdfium2 as pdfium
    from charset_normalizer import from_bytes

    pdf = pdfium.PdfDocument.new()
    try:
        pdf.new_page(200, 200).close()
        buffer = io.BytesIO()
        pdf.save(buffer)
    finally:
        pdf.close()
    reopened = pdfium.PdfDocument(buffer.getvalue())
    try:
        pages = len(reopened)
    finally:
        reopened.close()

    document = docx.Document()
    document.add_paragraph("Tamra")
    stream = io.BytesIO()
    document.save(stream)
    stream.seek(0)
    text = docx.Document(stream).paragraphs[0].text

    detected = from_bytes("Tamra ตอบคำถามจากเอกสาร".encode()).best()
    return {"ok": pages == 1 and text == "Tamra" and detected is not None}
```

and in `run_selfcheck`, right after the `sqlite` entry: `checks["documents"] = _guard(_check_documents)`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_selfcheck.py -v`
Expected: all pass.
Then: `uv run pytest; uv run ruff format; uv run ruff check --fix; uv run ruff format --check`

- [ ] **Step 5: Write the end-to-end smoke script**

`scripts/exe_smoke.py`:

```python
"""End-to-end check of the packaged app through its API (the M1 exit criterion).

    uv run python scripts/exe_smoke.py [--exe dist/Tamra/Tamra.exe] [--out report.json]

Starts Tamra.exe in dev mode (API only on 127.0.0.1:8765, token "dev") with a scratch data
folder and the repo's models, indexes the eval corpus, asks one question in each language and
one off-topic question, checks the streamed answers and the saved chats, adds a file to check
the folder watcher, then ends the exe by PID and checks that its llama-server ended with it.
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from build_eval_corpus import build
from eval_retrieval import load_questions

from tamra.answer import NOT_FOUND

ROOT = Path(__file__).resolve().parents[1]
PORT = 8765
SMOKE_IDS = ("th-leave-annual", "en-lease-rent", "zh-warranty-sofa", "none-world-cup")
CITATION = re.compile(r"\[(\d+)\]")


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def llama_server_pids() -> set[int]:
    """PIDs of running llama-server.exe processes (read only: nothing is stopped by name)."""
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    pids = set()
    for line in out.splitlines():
        fields = [field.strip('"') for field in line.split('","')]
        if len(fields) > 1 and fields[1].isdigit():
            pids.add(int(fields[1]))
    return pids


def wait_for(check, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.5)
    raise SystemExit(f"timed out waiting for {what}")


def healthy(client: httpx.Client) -> bool:
    try:
        return client.get("/api/health").status_code == 200
    except httpx.TransportError:
        return False


def settled(client: httpx.Client, total: int) -> dict | None:
    """The index status once every file has been handled, else None."""
    index = client.get("/api/collection").json()["index"]
    if index is None:
        return None
    if index["error"]:
        raise SystemExit(f"indexing stopped: {index['error']}")
    counts = index["counts"]
    handled = counts["indexed"] + counts["failed"] + counts["skipped"]
    if counts["pending"] == 0 and counts["indexing"] == 0 and handled == total:
        return index
    return None


def ask(client: httpx.Client, chat_id: int, question: str) -> list[dict]:
    events = []
    with client.stream(
        "POST", f"/api/chats/{chat_id}/messages", json={"content": question}, timeout=300
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:") :]))
    return events


def check_answer(question: dict, events: list[dict]) -> dict:
    sources = next((e["sources"] for e in events if e["type"] == "sources"), [])
    text = "".join(e["text"] for e in events if e["type"] == "token")
    errors = [e["message"] for e in events if e["type"] == "error"]
    problems = []
    if errors:
        problems.append(f"error events: {errors}")
    if not events or events[-1]["type"] != "done":
        problems.append("the stream did not end with a done event")
    if question["file"] is None:
        if sources:
            problems.append("an off-topic question got sources")
        if text != NOT_FOUND[question["lang"]]:
            problems.append("an off-topic question did not get the not-found reply")
    else:
        if question["file"] not in {s["file"] for s in sources}:
            problems.append(f"{question['file']} is not among the sources")
        if not text.strip():
            problems.append("empty answer")
    cited = any(1 <= int(n) <= len(sources) for n in CITATION.findall(text))
    return {
        "id": question["id"],
        "answer": text[:400],
        "sources": [s["file"] for s in sources],
        "cited": cited,
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end check of the packaged Tamra.exe")
    parser.add_argument("--exe", type=Path, default=ROOT / "dist" / "Tamra" / "Tamra.exe")
    parser.add_argument("--out", type=Path, help="also write the report as JSON")
    args = parser.parse_args()
    if not args.exe.is_file():
        raise SystemExit(f"missing {args.exe}; build it with ./scripts/build.ps1")
    if port_in_use(PORT):
        raise SystemExit(f"port {PORT} is in use; stop the other Tamra dev server first")
    questions = {q["id"]: q for q in load_questions()}
    report: dict = {"exe": str(args.exe), "answers": []}
    problems: list[str] = []
    before = llama_server_pids()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        docs = Path(tmp) / "docs"
        build(docs)
        total = len(list(docs.iterdir()))
        env = {
            **os.environ,
            "TAMRA_DATA_DIR": str(Path(tmp) / "data"),
            "TAMRA_MODELS_DIR": str(ROOT / ".models"),
        }
        proc = subprocess.Popen([str(args.exe), "--dev"], env=env)
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{PORT}",
                headers={"X-Tamra-Token": "dev"},
                trust_env=False,
                timeout=30,
            ) as client:
                started = time.monotonic()
                wait_for(lambda: healthy(client), 60, "the API")
                report["startup_s"] = round(time.monotonic() - started, 1)
                client.put(
                    "/api/collection", json={"name": "Smoke", "folder_path": str(docs)}
                ).raise_for_status()
                started = time.monotonic()
                index = wait_for(lambda: settled(client, total), 900, "indexing")
                report["index_s"] = round(time.monotonic() - started, 1)
                report["index_counts"] = index["counts"]
                if index["counts"]["indexed"] != total:
                    problems.append(f"not every file was indexed: {index['problems']}")
                for question_id in SMOKE_IDS:
                    question = questions[question_id]
                    chat_id = client.post("/api/chats").json()["id"]
                    started = time.monotonic()
                    result = check_answer(question, ask(client, chat_id, question["question"]))
                    result["seconds"] = round(time.monotonic() - started, 1)
                    saved = client.get(f"/api/chats/{chat_id}").json()["messages"]
                    answers = [m for m in saved if m["role"] == "assistant"]
                    if not answers or (question["file"] and not answers[-1]["sources"]):
                        result["problems"].append("the answer or its sources were not saved")
                    report["answers"].append(result)
                (docs / "watcher-check.md").write_text(
                    "The rooftop garden opens at 6 pm on Fridays.", encoding="utf-8"
                )
                started = time.monotonic()
                wait_for(lambda: settled(client, total + 1), 120, "the folder watcher")
                report["watcher_s"] = round(time.monotonic() - started, 1)
        finally:
            proc.kill()  # a hard kill: llama-server must still end with Tamra (Job Object)
            proc.wait(timeout=30)
        time.sleep(2)
    left = sorted(llama_server_pids() - before)
    if left:
        problems.append(f"llama-server still running after Tamra ended: {left}")
    for answer in report["answers"]:
        problems += [f"{answer['id']}: {p}" for p in answer["problems"]]
    answerable = [a for a in report["answers"] if questions[a["id"]]["file"]]
    report["cited_answers"] = f"{sum(a['cited'] for a in answerable)} of {len(answerable)}"
    if not any(a["cited"] for a in answerable):
        problems.append("no answer contained a [n] citation")
    report["problems"] = problems
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))  # ASCII-escaped, so any console can print it
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Build the exe and check it**

Build: `./scripts/build.ps1`. If its `npm ci` step fails because a running Vite dev server holds a file in `ui/node_modules`, do not stop that process; run the remaining steps yourself: `npm --prefix ui run build`, `uv run python scripts/fetch_assets.py llama`, `uv run pyinstaller packaging/tamra.spec --noconfirm --distpath dist --workpath build`.

Packaged selfcheck with the models:

```powershell
$report = Join-Path $env:TEMP "tamra-selfcheck.json"
$p = Start-Process dist\Tamra\Tamra.exe -ArgumentList "selfcheck","--embed-model-dir",".models\bge-m3","--llm-model",".models\qwen2.5-0.5b-instruct-q4_k_m.gguf","--report",$report -Wait -PassThru
Get-Content $report; $p.ExitCode
```

Expected: exit code 0 and `ok: true` for `sqlite`, `documents`, `embedding`, and `llm`.

End-to-end: `uv run python scripts/exe_smoke.py --out "$env:TEMP\tamra-smoke.json"` (port 8765 must be free; if something listens there, report it rather than stopping it).
Expected: exit code 0. If a module or data file is missing from the bundle (an import error or a failed `documents` check in the packaged app), add it to `packaging/tamra.spec` (`hiddenimports`, or `datas` via `collect_data_files`), rebuild, and rerun both checks. If the only problem is "no answer contained a [n] citation", do not change the prompt in this task: record it in the results doc and report it.

- [ ] **Step 7: Update the docs**

`README.md`:
- Replace the status note with: `> **Status: pre-alpha.** Milestone M1 works: index a folder, ask in Thai, English, or Chinese, and get answers with [n] citations from a small local model. There is no installer yet (M6). See the [roadmap](#roadmap).`
- Add this section before "## Roadmap":

```markdown
## Try it from source

You need Windows 10 or 11, [uv](https://docs.astral.sh/uv/), and Node.js 24.

    uv sync
    uv run python scripts/fetch_assets.py   # llama.cpp, bge-m3, and a small test model (~1.1 GB)
    npm --prefix ui ci
    npm --prefix ui run build
    uv run tamra

Choose a folder of PDF, Word, text, or Markdown files, wait for indexing, and ask a question.
`./scripts/build.ps1` packages the same app as `dist\Tamra\Tamra.exe`. M1 uses Qwen2.5-0.5B,
a very small model, so expect rough answers, especially in Thai and Chinese; M2 adds larger
models and cloud APIs.
```

- In the Thai section, replace the sentence `ตอนนี้อยู่ในขั้นออกแบบ ยังไม่มีเวอร์ชันให้ใช้งาน` with `ตอนนี้ (M1) ใช้งานจากซอร์สโค้ดได้แล้ว: เลือกโฟลเดอร์เอกสาร ถามคำถาม แล้วได้คำตอบพร้อมอ้างอิง [n] ยังไม่มีตัวติดตั้ง`.

`CLAUDE.md`:
- In the Commands block, add after the `fetch_assets.py` line:

```powershell
uv run python scripts/eval_retrieval.py   # retrieval eval: hit@k and the not-found threshold (needs .models)
uv run python scripts/exe_smoke.py        # end-to-end check of dist/Tamra/Tamra.exe (needs .models)
```

- In the module list under Architecture, add this line after the `store` line:

```markdown
- `core`: owns the store, the indexer and watcher, the answer service, and the local LLM's lifecycle.
```

`docs/spikes/2026-10-m1-results.md`: append a section `## Exit criterion` with the packaged selfcheck numbers (embedding passages/s, LLM start time and tokens/s, whether the GPU was used), the smoke report's timings (startup, indexing, each answer, watcher), each smoke question's answer (first 200 characters) with its sources and whether it cited `[n]`, the citation count, and whether llama-server ended with the killed exe.

- [ ] **Step 8: Commit**

```powershell
git add src/tamra/selfcheck.py tests/test_selfcheck.py scripts/exe_smoke.py README.md CLAUDE.md docs/spikes/2026-10-m1-results.md
git commit -m "feat(packaging): document-library selfcheck, packaged end-to-end smoke test, and M1 docs"
```

(Also add `packaging/tamra.spec` if Step 6 changed it.)

---

## M1 exit check in the window

After Task 17, the controller (not a task subagent) confirms the M1 exit criterion in the real window: launch `dist\Tamra\Tamra.exe` with `TAMRA_DATA_DIR` set to a scratch folder and `TAMRA_MODELS_DIR` set to the repo's `.models`; choose a folder with the eval corpus (rendered by `scripts/build_eval_corpus.py`) through "Browse…"; wait for indexing; ask a Thai, an English, and a Chinese question; open a source from an answer. Capture screenshots (computer use, with the user's approval of the app), close the window, and confirm that no `llama-server.exe` started by this run is left (compare PIDs before and after; stop nothing by name). Record the result in `docs/spikes/2026-10-m1-results.md`.

## After M1

- Open the PR `m1-core-loop` → `main`; merge it with a merge commit when CI is green and the final review is clean (roadmap process).
- Next: the M2 plan (cloud API providers, settings, model catalog with download and import, hardware tiers, LLM picks per tier). Model downloads larger than the dev assets need the user's approval first.
- The M2 items in "Deferred review findings by milestone" (`docs/spikes/2026-10-m0-results.md`) still apply, plus anything the M1 reviews defer.
