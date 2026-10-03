import sqlite3
from pathlib import Path

import sqlite_vec


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the Tamra database with sqlite-vec loaded. The only module that touches SQL."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def capabilities(conn: sqlite3.Connection) -> dict[str, str | bool]:
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._trigram_probe USING fts5(t, tokenize='trigram')")
        conn.execute("DROP TABLE temp._trigram_probe")
        trigram = True
    except sqlite3.OperationalError:
        trigram = False
    return {
        "sqlite_version": conn.execute("SELECT sqlite_version()").fetchone()[0],
        "vec_version": conn.execute("SELECT vec_version()").fetchone()[0],
        "fts5_trigram": trigram,
    }
