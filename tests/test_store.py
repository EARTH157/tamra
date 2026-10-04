import sqlite3
import threading

import pytest
import sqlite_vec

from tamra import store


def test_connect_loads_sqlite_vec_and_trigram():
    conn = store.connect(":memory:")
    caps = store.capabilities(conn)
    assert caps["vec_version"].startswith("v")
    assert caps["fts5_trigram"] is True


def test_vec_knn_returns_nearest_first():
    conn = store.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE v USING vec0(embedding float[3])")
    for rowid, vec in enumerate([[1, 0, 0], [0, 1, 0], [0, 0, 1]], start=1):
        conn.execute(
            "INSERT INTO v(rowid, embedding) VALUES (?, ?)",
            (rowid, sqlite_vec.serialize_float32(vec)),
        )
    rows = conn.execute(
        "SELECT rowid FROM v WHERE embedding MATCH ? AND k = 2 ORDER BY distance",
        (sqlite_vec.serialize_float32([0.9, 0.1, 0.0]),),
    ).fetchall()
    assert [r[0] for r in rows] == [1, 2]


def test_trigram_fts_matches_thai_and_chinese_substrings():
    conn = store.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE f USING fts5(text, tokenize='trigram')")
    docs = ["สัญญาเช่าบ้านมีอายุสามปี", "The lease term is three years", "租赁期限为三年"]
    conn.executemany("INSERT INTO f(text) VALUES (?)", [(d,) for d in docs])

    def match(q: str) -> list[str]:
        return [r[0] for r in conn.execute("SELECT text FROM f WHERE f MATCH ?", (f'"{q}"',))]

    assert match("เช่าบ้าน") == [docs[0]]
    assert match("租赁期") == [docs[2]]
    assert match("lease term") == [docs[1]]


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
