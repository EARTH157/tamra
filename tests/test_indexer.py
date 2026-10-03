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
        "a.md": "indexed",
        "c.docx": "indexed",
        "sub/b.txt": "indexed",
    }
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

    try:
        indexer = Indexer(store, missing, lambda: fake_spans, MODEL)
        run(indexer)
        assert indexer.state().error.startswith("Embedding model unavailable")
        assert files(store)["a.md"].status == "pending"
    finally:
        store.close()


def test_a_lookup_error_while_indexing_fails_the_file_instead_of_hiding_it(env):
    docs, store, _, indexer = env

    class BrokenEmbedder:
        def embed(self, texts, batch_size=16):
            raise KeyError("boom")

    indexer._embedder = lambda: BrokenEmbedder()
    (docs / "a.md").write_text("Lease terms.", encoding="utf-8")
    run(indexer)
    record = files(store)["a.md"]
    assert record.status == "failed"
    assert "KeyError" in record.error


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
