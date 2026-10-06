"""Store: one SQLite connection shared by API threads and the indexer, serialized by a lock."""

import json
import sqlite3
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

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
from tamra.store.schema import migrate

FILE_STATUSES = ("pending", "indexing", "indexed", "failed", "skipped")
_FILE_COLUMNS = "id, collection_id, rel_path, size, mtime, content_hash, status, error, indexed_at"
_COLLECTION_COLUMNS = "id, name, folder_path, embedding_model_id, created_at"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def _source(row: Sequence) -> SourceRecord:
    """A SourceRecord from (n, chunk_id, file_id, rel_path, text_snapshot, location_json, hash)."""
    return SourceRecord(row[0], row[1], row[2], row[3], row[4], json.loads(row[5]), row[6])


class NameTaken(ValueError):
    """Another project already has this name (names are compared without regard to case)."""

    def __init__(self) -> None:
        super().__init__("name taken")


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

    def list_collections(self) -> list[Collection]:
        """Every project, ordered by name (case-insensitively)."""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLLECTION_COLUMNS} FROM collections ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
        return [Collection(*row) for row in rows]

    def get_collection(self, collection_id: int) -> Collection | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLLECTION_COLUMNS} FROM collections WHERE id = ?", (collection_id,)
            ).fetchone()
        return Collection(*row) if row else None

    def _name_taken(self, name: str, *, excluding: int | None = None) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM collections WHERE name = ? COLLATE NOCASE AND id IS NOT ?",
            (name, excluding),
        ).fetchone()
        return row is not None

    def create_collection(self, name: str, folder_path: str, model_id: str) -> Collection:
        """Add a project. Raises NameTaken (a ValueError) if the name is taken, in any case."""
        created = _now()
        with self._lock, self._conn:
            if self._name_taken(name):
                raise NameTaken
            cursor = self._conn.execute(
                "INSERT INTO collections (name, folder_path, embedding_model_id, created_at)"
                " VALUES (?, ?, ?, ?)",
                (name, folder_path, model_id, created),
            )
        return Collection(cursor.lastrowid, name, folder_path, model_id, created)

    def rename_collection(self, collection_id: int, name: str) -> bool:
        """Rename a project; False if unknown, NameTaken (a ValueError) when the name is taken."""
        with self._lock, self._conn:
            if self._name_taken(name, excluding=collection_id):
                raise NameTaken
            cursor = self._conn.execute(
                "UPDATE collections SET name = ? WHERE id = ?", (name, collection_id)
            )
        return cursor.rowcount > 0

    def _drop_index(self, collection_id: int) -> None:
        """Delete a project's files, chunks, keyword entries and vectors (the caller holds a
        transaction). The chunk delete trigger removes each chunk's vector; vec0 has no foreign
        keys, so the rows left for this collection are removed explicitly as well."""
        self._conn.execute(
            "DELETE FROM chunks WHERE file_id IN (SELECT id FROM files WHERE collection_id = ?)",
            (collection_id,),
        )
        self._conn.execute("DELETE FROM chunk_vectors WHERE collection_id = ?", (collection_id,))
        self._conn.execute("DELETE FROM files WHERE collection_id = ?", (collection_id,))

    def set_collection_folder(
        self, collection_id: int, folder_path: str, model_id: str
    ) -> Collection | None:
        """Point a project at another folder: its whole index is dropped in the same transaction.
        None if the project is unknown."""
        with self._lock, self._conn:
            if self.get_collection(collection_id) is None:
                return None
            self._drop_index(collection_id)
            self._conn.execute(
                "UPDATE collections SET folder_path = ?, embedding_model_id = ? WHERE id = ?",
                (folder_path, model_id, collection_id),
            )
        return self.get_collection(collection_id)

    def delete_collection(self, collection_id: int) -> int | None:
        """Delete a project with its index and its home chats; returns how many chats went
        (0 for a project with none), or None if the project is unknown.

        Other chats that also searched it lose it from their scope; one left with an empty scope
        falls back to its home project.
        """
        with self._lock, self._conn:
            if self.get_collection(collection_id) is None:
                return None
            deleted = self._conn.execute(
                "DELETE FROM chats WHERE collection_id = ?", (collection_id,)
            ).rowcount
            self._drop_index(collection_id)
            self._conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
            self._conn.execute(
                "INSERT INTO chat_collections (chat_id, collection_id)"
                " SELECT id, collection_id FROM chats WHERE collection_id IS NOT NULL"
                " AND id NOT IN (SELECT chat_id FROM chat_collections)"
            )
        return deleted

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

    def get_file(self, file_id: int) -> FileRecord | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_FILE_COLUMNS} FROM files WHERE id = ?", (file_id,)
            ).fetchone()
        return FileRecord(*row) if row else None

    def next_pending_file(self, collection_id: int) -> FileRecord | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_FILE_COLUMNS} FROM files"
                " WHERE collection_id = ? AND status = 'pending' ORDER BY id LIMIT 1",
                (collection_id,),
            ).fetchone()
        return FileRecord(*row) if row else None

    def next_pending_file_any(
        self, model_id: str, exclude: Sequence[int] = ()
    ) -> FileRecord | None:
        """The oldest pending file across the projects indexed with this embedding model.
        A project with another model is stale: it is skipped until it is rebuilt, and so are
        the projects named in exclude."""
        columns = ", ".join(f"f.{name.strip()}" for name in _FILE_COLUMNS.split(","))
        marks = ",".join("?" * len(exclude))
        skip = f" AND f.collection_id NOT IN ({marks})" if exclude else ""
        with self._lock:
            row = self._conn.execute(
                f"SELECT {columns} FROM files f JOIN collections c ON c.id = f.collection_id"
                f" WHERE f.status = 'pending' AND c.embedding_model_id = ?{skip}"
                " ORDER BY f.id LIMIT 1",
                (model_id, *exclude),
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
        self, collection_ids: Sequence[int], vector: np.ndarray, k: int
    ) -> list[tuple[int, float]]:
        """Nearest chunks of these projects as (chunk id, cosine similarity), most similar first.

        The vec0 partition key needs an equality, so each project is searched on its own and the
        results are merged.
        """
        blob = _blob(vector)
        found: list[tuple[int, float]] = []
        with self._lock:
            for collection_id in dict.fromkeys(collection_ids):
                rows = self._conn.execute(
                    "SELECT rowid, distance FROM chunk_vectors"
                    " WHERE embedding MATCH ? AND k = ? AND collection_id = ? ORDER BY distance",
                    (blob, k, collection_id),
                ).fetchall()
                found.extend((rowid, 1.0 - distance) for rowid, distance in rows)
        found.sort(key=lambda hit: (-hit[1], hit[0]))
        return found[:k]

    def search_keyword(self, collection_ids: Sequence[int], fts_query: str, k: int) -> list[int]:
        """Chunk ids of these projects matching an FTS5 query (see retriever.fts_query), best
        BM25 first."""
        ids = list(dict.fromkeys(collection_ids))
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id FROM chunks_fts"
                " JOIN chunks c ON c.id = chunks_fts.rowid"
                " JOIN files f ON f.id = c.file_id"
                f" WHERE chunks_fts MATCH ? AND f.collection_id IN ({marks})"
                " ORDER BY bm25(chunks_fts), c.id LIMIT ?",
                (fts_query, *ids, k),
            ).fetchall()
        return [row[0] for row in rows]

    # Settings -------------------------------------------------------------------------------

    def get_setting(self, key: str) -> object | None:
        """A stored setting's value, or None when the key has never been stored."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value_json FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def set_settings(self, values: dict[str, object]) -> None:
        """Store several settings (each value as JSON) in one transaction."""
        rows = [(key, json.dumps(value, ensure_ascii=False)) for key, value in values.items()]
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO settings (key, value_json) VALUES (?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value_json = excluded.value_json",
                rows,
            )

    def all_settings(self) -> dict[str, object]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value_json FROM settings").fetchall()
        return {key: json.loads(value) for key, value in rows}

    # Chats ----------------------------------------------------------------------------------

    def _chats(self, where: str = "", params: tuple = ()) -> list[Chat]:
        """Chats (newest activity first) with their search scopes."""
        rows = self._conn.execute(
            "SELECT id, title, created_at, updated_at, collection_id"
            f" FROM chats{where} ORDER BY activity DESC",
            params,
        ).fetchall()
        scopes: dict[int, list[int]] = {}
        for chat_id, collection_id in self._conn.execute(
            "SELECT chat_id, collection_id FROM chat_collections"
            f" WHERE chat_id IN (SELECT id FROM chats{where}) ORDER BY collection_id",
            params,
        ):
            scopes.setdefault(chat_id, []).append(collection_id)
        return [Chat(*row, scope=scopes.get(row[0], [])) for row in rows]

    def _require_collections(self, collection_ids: Sequence[int]) -> None:
        for collection_id in collection_ids:
            found = self._conn.execute(
                "SELECT 1 FROM collections WHERE id = ?", (collection_id,)
            ).fetchone()
            if found is None:
                raise ValueError(f"unknown project: {collection_id}")

    def create_chat(self, title: str = "", collection_id: int | None = None) -> Chat:
        """A new chat; given a project, it is the chat's home and its whole search scope."""
        created = _now()
        with self._lock, self._conn:
            if collection_id is not None:
                self._require_collections([collection_id])
            cursor = self._conn.execute(
                "INSERT INTO chats (title, created_at, updated_at, activity, collection_id)"
                " VALUES (?, ?, ?, (SELECT COALESCE(MAX(activity), 0) + 1 FROM chats), ?)",
                (title, created, created, collection_id),
            )
            if collection_id is not None:
                self._conn.execute(
                    "INSERT INTO chat_collections (chat_id, collection_id) VALUES (?, ?)",
                    (cursor.lastrowid, collection_id),
                )
        scope = [] if collection_id is None else [collection_id]
        return Chat(cursor.lastrowid, title, created, created, collection_id, scope)

    def adopt_homeless_chats(self, collection_id: int) -> int:
        """Give every chat that has no home project this one as its home and its search scope;
        returns how many were adopted. ValueError if the project is unknown."""
        with self._lock, self._conn:
            self._require_collections([collection_id])
            chat_ids = [
                row[0]
                for row in self._conn.execute("SELECT id FROM chats WHERE collection_id IS NULL")
            ]
            self._conn.execute(
                "UPDATE chats SET collection_id = ? WHERE collection_id IS NULL", (collection_id,)
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO chat_collections (chat_id, collection_id) VALUES (?, ?)",
                [(chat_id, collection_id) for chat_id in chat_ids],
            )
        return len(chat_ids)

    def get_chat(self, chat_id: int) -> Chat | None:
        with self._lock:
            chats = self._chats(" WHERE id = ?", (chat_id,))
        return chats[0] if chats else None

    def list_chats(self) -> list[Chat]:
        with self._lock:
            return self._chats()

    def move_chat(self, chat_id: int, collection_id: int) -> bool:
        """Make a project the chat's home; False if the chat is unknown (ValueError if the
        project is).

        When the old home was the only project searched, the scope follows the move; otherwise
        the new home is added to the scope.
        """
        with self._lock, self._conn:
            self._require_collections([collection_id])
            chat = self._conn.execute(
                "SELECT collection_id FROM chats WHERE id = ?", (chat_id,)
            ).fetchone()
            if chat is None:
                return False
            scope = [
                row[0]
                for row in self._conn.execute(
                    "SELECT collection_id FROM chat_collections WHERE chat_id = ?", (chat_id,)
                )
            ]
            self._conn.execute(
                "UPDATE chats SET collection_id = ? WHERE id = ?", (collection_id, chat_id)
            )
            if scope == [chat[0]]:
                self._conn.execute("DELETE FROM chat_collections WHERE chat_id = ?", (chat_id,))
            self._conn.execute(
                "INSERT OR IGNORE INTO chat_collections (chat_id, collection_id) VALUES (?, ?)",
                (chat_id, collection_id),
            )
        return True

    def set_chat_scope(self, chat_id: int, collection_ids: Sequence[int]) -> bool:
        """Set the projects a chat searches. The list must be non-empty and name existing
        projects (ValueError otherwise); False if the chat is unknown."""
        ids = list(dict.fromkeys(collection_ids))
        if not ids:
            raise ValueError("a chat must search at least one project")
        with self._lock, self._conn:
            self._require_collections(ids)
            if not self._conn.execute("SELECT 1 FROM chats WHERE id = ?", (chat_id,)).fetchone():
                return False
            self._conn.execute("DELETE FROM chat_collections WHERE chat_id = ?", (chat_id,))
            self._conn.executemany(
                "INSERT INTO chat_collections (chat_id, collection_id) VALUES (?, ?)",
                [(chat_id, collection_id) for collection_id in ids],
            )
        return True

    def rename_chat(self, chat_id: int, title: str) -> bool:
        """Set a chat's title (whitespace collapsed, at most 60 characters); False if unknown."""
        cleaned = " ".join(title.split())[:60].rstrip()  # the cut may land on a space
        if not cleaned:
            raise ValueError("The chat title must not be empty.")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE chats SET title = ? WHERE id = ?", (cleaned, chat_id)
            )
        return cursor.rowcount > 0

    def delete_chat(self, chat_id: int) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        return cursor.rowcount > 0

    def delete_all_chats(self) -> int:
        """Delete every chat with its messages and saved sources, in one transaction.

        Returns how many chats were deleted.
        """
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM chats")
        return cursor.rowcount

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

    def get_message(self, message_id: int) -> MessageRecord | None:
        """One message with its saved sources, or None if there is no such message."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, role, content, provider, model, created_at FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
            if row is None:
                return None
            source_rows = self._conn.execute(
                "SELECT n, chunk_id, file_id, rel_path, text_snapshot, location_json,"
                " file_hash_at_answer FROM message_sources WHERE message_id = ? ORDER BY n",
                (message_id,),
            ).fetchall()
        return MessageRecord(*row, sources=tuple(_source(r) for r in source_rows))

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
            sources.setdefault(row[0], []).append(_source(row[1:]))
        return [MessageRecord(*row, sources=tuple(sources.get(row[0], ()))) for row in rows]
