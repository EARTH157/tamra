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
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
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


def test_ids_are_not_reused_after_the_collection_is_replaced(store, coll):
    old_file = store.add_file(coll.id, "a.md", 10, 1.0)
    index(store, old_file, ["alpha beta"], [unit(1)])
    old_chunk = store.search_keyword(coll.id, '"alp"', 10)[0]
    new = store.replace_collection("Other", "D:/other", MODEL)
    new_file = store.add_file(new.id, "b.md", 10, 1.0)
    index(store, new_file, ["gamma delta"], [unit(2)])
    new_chunk = store.search_keyword(new.id, '"gam"', 10)[0]
    assert new.id > coll.id
    assert new_file > old_file
    assert new_chunk > old_chunk


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
    index(
        store,
        file_id,
        ["The lease term is three years", "Pets are allowed"],
        [unit(1), unit(2)],
        note="n",
    )
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
