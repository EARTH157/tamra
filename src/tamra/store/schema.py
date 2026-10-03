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
