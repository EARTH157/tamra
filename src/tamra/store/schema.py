import sqlite3

SCHEMA_VERSION = 4

_V1 = """
CREATE TABLE collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    folder_path TEXT NOT NULL,
    embedding_model_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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


_V2 = """
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
"""

# v3: many projects (collections). A chat has a home project and a search scope of one or more
# projects. SQLite cannot add a constraint to an existing table without rebuilding it, and
# dropping the parent of the files table would cascade, so name uniqueness is a unique index.
_V3 = """
ALTER TABLE chats ADD COLUMN collection_id INTEGER REFERENCES collections(id) ON DELETE CASCADE;
CREATE INDEX chats_by_collection ON chats(collection_id);
CREATE TABLE chat_collections (
    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    PRIMARY KEY (chat_id, collection_id)
);
CREATE INDEX chat_collections_by_collection ON chat_collections(collection_id);
-- M1-M3 databases hold at most one collection, so renaming duplicates is only defensive.
UPDATE collections SET name = name || ' (' || id || ')'
    WHERE id NOT IN (SELECT MIN(id) FROM collections GROUP BY name COLLATE NOCASE);
CREATE UNIQUE INDEX collections_name_nocase ON collections(name COLLATE NOCASE);
CREATE INDEX files_pending ON files(id) WHERE status = 'pending';
UPDATE chats SET collection_id = (SELECT id FROM collections ORDER BY id LIMIT 1);
INSERT INTO chat_collections (chat_id, collection_id)
    SELECT id, collection_id FROM chats WHERE collection_id IS NOT NULL;
"""

# v4: OCR (M5). The text and character boxes of each page read by OCR, and the files whose last
# parse left pages for OCR because the models were missing (M4 noted those as "(needs OCR)").
_V4 = """
CREATE TABLE ocr_pages (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    text TEXT NOT NULL,
    boxes BLOB NOT NULL,
    PRIMARY KEY (file_id, page)
);
ALTER TABLE files ADD COLUMN needs_ocr INTEGER NOT NULL DEFAULT 0;
UPDATE files SET needs_ocr = 1 WHERE error LIKE '%(needs OCR)%';
"""


def _run_in_transaction(conn: sqlite3.Connection, script: str, version: int) -> None:
    try:
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise


def migrate(conn: sqlite3.Connection) -> None:
    """Bring the schema to SCHEMA_VERSION in one transaction: a new database is created at the
    latest version, and an older one is upgraded in place."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema {version} is newer than this version of Tamra ({SCHEMA_VERSION})"
        )
    steps = [_V1, _V2, _V3, _V4]  # steps[n] upgrades schema n to n + 1
    if version < SCHEMA_VERSION:
        _run_in_transaction(conn, "\n".join(steps[version:]), SCHEMA_VERSION)
