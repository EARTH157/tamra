import numpy as np
import pytest
from fakes import FakeEmbedder

from tamra.store import ChunkInput, OcrPage, Store
from tamra.store.db import connect
from tamra.store.schema import _V1, _V2, _V3, SCHEMA_VERSION

LOC = {"kind": "pdf", "page_start": 2, "page_end": 2, "char_start": 0, "char_end": 5, "ocr": True}


def boxes(text: str) -> bytes:
    return np.tile(np.array([0.1, 0.2, 0.3, 0.4], "<f4"), (len(text), 1)).tobytes()


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "t.db")
    yield s
    s.close()


def add(store, rel_path="scan.pdf"):
    collection = (
        store.list_collections()[0]
        if store.list_collections()
        else (store.create_collection("Docs", "C:/docs", "m"))
    )
    return collection, store.add_file(collection.id, rel_path, 10, 1.0)


def index(store, file_id, pages=(), needs_ocr=False, text="hello"):
    store.replace_file_chunks(
        file_id,
        [ChunkInput(text, LOC)],
        FakeEmbedder().embed([text]),
        content_hash="h",
        note=None,
        ocr_pages=pages,
        needs_ocr=needs_ocr,
    )


def test_ocr_pages_are_written_with_the_chunks_and_replaced_with_them(store):
    _, file_id = add(store)
    index(store, file_id, [OcrPage(2, "ab\ncd", boxes("ab\ncd")), OcrPage(1, "x", boxes("x"))])
    assert [p.page for p in store.ocr_pages(file_id)] == [1, 2]
    page = store.ocr_page(file_id, 2)
    assert page.text == "ab\ncd" and len(page.boxes) == 16 * 5
    assert store.ocr_page(file_id, 3) is None
    index(store, file_id, [OcrPage(3, "z", boxes("z"))])
    assert [p.page for p in store.ocr_pages(file_id)] == [3]


def test_box_bytes_must_match_the_text(store):
    _, file_id = add(store)
    with pytest.raises(ValueError):
        index(store, file_id, [OcrPage(1, "abc", boxes("ab"))])
    assert store.ocr_pages(file_id) == []


def test_ocr_pages_go_with_the_file_a_failure_and_a_rebuild(store):
    collection, file_id = add(store)
    index(store, file_id, [OcrPage(1, "x", boxes("x"))])
    store.set_file_status(file_id, "skipped", "no text found", needs_ocr=True)
    assert store.ocr_pages(file_id) == [] and store.get_file(file_id).needs_ocr
    index(store, file_id, [OcrPage(1, "x", boxes("x"))])
    store.reset_index(collection.id, "m")
    assert store.ocr_pages(file_id) == [] and not store.get_file(file_id).needs_ocr
    index(store, file_id, [OcrPage(1, "x", boxes("x"))])
    store.delete_file(file_id)
    assert store.ocr_pages(file_id) == []


def test_needs_ocr_is_recorded_and_requeued(store):
    _, waiting = add(store, "scan.pdf")
    _, skipped = add(store, "blank.pdf")
    _, done = add(store, "text.pdf")
    index(store, waiting, needs_ocr=True)
    store.set_file_status(skipped, "skipped", "no text layer (needs OCR)", needs_ocr=True)
    index(store, done)
    assert store.get_file(waiting).needs_ocr and not store.get_file(done).needs_ocr
    assert store.requeue_ocr_files() == 2
    assert {store.get_file(i).status for i in (waiting, skipped)} == {"pending"}
    assert store.get_file(done).status == "indexed"
    index(store, waiting)  # read again with the models: the flag clears
    assert not store.get_file(waiting).needs_ocr


def test_a_v3_database_upgrades_and_flags_files_that_waited_for_ocr(tmp_path):
    path = tmp_path / "old.db"
    conn = connect(path)
    conn.executescript("BEGIN;\n" + _V1 + _V2 + _V3 + "\nPRAGMA user_version = 3;\nCOMMIT;")
    conn.execute(
        "INSERT INTO collections (name, folder_path, embedding_model_id, created_at)"
        " VALUES ('Docs', 'C:/docs', 'm', 'now')"
    )
    conn.execute(
        "INSERT INTO files (collection_id, rel_path, size, mtime, status, error)"
        " VALUES (1, 'scan.pdf', 1, 1.0, 'indexed', 'no text layer on page(s) 2 (needs OCR)'),"
        " (1, 'a.pdf', 1, 1.0, 'indexed', NULL)"
    )
    conn.commit()
    conn.close()
    store = Store.open(path)
    try:
        files = {f.rel_path: f for f in store.list_files(1)}
        assert files["scan.pdf"].needs_ocr and not files["a.pdf"].needs_ocr
        assert store.ocr_pages(files["scan.pdf"].id) == []
    finally:
        store.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 4
    conn.close()
