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
