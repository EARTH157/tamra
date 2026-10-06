import _winapi
import ctypes
import os
import shutil
import time

import pytest
from docgen import LATIN_THAI, make_docx, make_pdf
from fakes import FakeEmbedder, fake_spans

from tamra.ingest.indexer import Indexer, IndexerState, scan_folder
from tamra.store import Store

MODEL = "model-1"


@pytest.fixture
def env(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    store = Store.open(tmp_path / "tamra.db")
    embedder = FakeEmbedder()
    indexer = Indexer(store, lambda: embedder, lambda: fake_spans, MODEL)
    store.create_collection("Docs", str(docs), MODEL)
    yield docs, store, embedder, indexer
    indexer.stop()
    store.close()


def run(indexer):
    indexer.request_reconcile()
    indexer.process()


def files(store):
    return {f.rel_path: f for f in store.list_files(store.list_collections()[0].id)}


def keyword(store, word):
    return store.search_keyword([store.list_collections()[0].id], f'"{word[:3]}"', 10)


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
    assert indexer.state(store.list_collections()[0].id).error is None


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
    store.reset_index(store.list_collections()[0].id, "old-model")
    (docs / "a.md").write_text("Lease terms.", encoding="utf-8")
    run(indexer)
    assert store.list_files(store.list_collections()[0].id) == []
    indexer.request_rebuild(store.list_collections()[0].id)
    indexer.process()
    assert store.list_collections()[0].embedding_model_id == MODEL
    assert files(store)["a.md"].status == "indexed"


def test_a_missing_folder_is_reported_and_the_index_kept(env):
    docs, store, _, indexer = env
    (docs / "a.md").write_text("Lease terms.", encoding="utf-8")
    run(indexer)
    shutil.rmtree(docs)
    run(indexer)
    assert indexer.state(store.list_collections()[0].id).error.startswith("Folder not found")
    assert files(store)["a.md"].status == "indexed"


def test_an_unavailable_model_pauses_indexing_without_failing_files(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("Lease terms.", encoding="utf-8")
    store = Store.open(tmp_path / "tamra.db")
    store.create_collection("Docs", str(docs), MODEL)

    def missing():
        raise FileNotFoundError("model.onnx not found")

    try:
        indexer = Indexer(store, missing, lambda: fake_spans, MODEL)
        run(indexer)
        assert indexer.state(store.list_collections()[0].id).error.startswith(
            "Embedding model unavailable"
        )
        assert files(store)["a.md"].status == "pending"
    finally:
        store.close()


def test_stopping_between_embedding_batches_requeues_the_file(env):
    docs, store, _, indexer = env

    class StoppingEmbedder(FakeEmbedder):
        def embed(self, texts, batch_size=16):
            indexer._stopping.set()
            return super().embed(texts, batch_size)

    stopping = StoppingEmbedder()
    indexer._embedder = lambda: stopping
    # With the default 450-token limit, 300-word paragraphs cannot share a chunk: 20 chunks.
    paragraphs = [f"Paragraph{i} " + "word " * 299 for i in range(20)]
    (docs / "big.md").write_text("\n\n".join(paragraphs), encoding="utf-8")
    run(indexer)
    record = files(store)["big.md"]
    assert record.status == "pending"
    assert stopping.calls == 1
    assert store.search_keyword([store.list_collections()[0].id], '"Par"', 10) == []


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
    assert indexer.state(store.list_collections()[0].id).current is None


# --- several projects, one worker ---


@pytest.fixture
def two(tmp_path):
    folders = []
    for name in ("alpha", "beta"):
        folder = tmp_path / name
        folder.mkdir()
        folders.append(folder)
    store = Store.open(tmp_path / "tamra.db")
    embedder = FakeEmbedder()
    indexer = Indexer(store, lambda: embedder, lambda: fake_spans, MODEL)
    projects = [store.create_collection(f.name, str(f), MODEL) for f in folders]
    yield folders, projects, store, embedder, indexer
    indexer.stop()
    store.close()


def statuses(store, project):
    return {f.rel_path: f.status for f in store.list_files(project.id)}


def test_every_project_is_indexed_by_one_pass(two):
    (alpha, beta), (a, b), store, _, indexer = two
    (alpha / "a.md").write_text("Lease terms.", encoding="utf-8")
    (beta / "b.md").write_text("Parking rules.", encoding="utf-8")
    indexer.request_reconcile()
    indexer.process()
    assert statuses(store, a) == {"a.md": "indexed"}
    assert statuses(store, b) == {"b.md": "indexed"}
    assert indexer.state(a.id) == indexer.state(b.id) == IndexerState(None, None)


def test_the_background_worker_indexes_all_projects(two):
    (alpha, beta), (a, b), store, _, indexer = two
    (alpha / "a.md").write_text("Lease terms.", encoding="utf-8")
    (beta / "b.md").write_text("Parking rules.", encoding="utf-8")
    indexer.start()
    indexer.request_reconcile()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (
        set(statuses(store, a).values()) == {"indexed"}
        and set(statuses(store, b).values()) == {"indexed"}
    ):
        time.sleep(0.05)
    assert statuses(store, a) == {"a.md": "indexed"}
    assert statuses(store, b) == {"b.md": "indexed"}


def test_a_request_for_one_project_reconciles_only_that_project(two):
    (alpha, beta), (a, b), store, _, indexer = two
    indexer.request_reconcile()
    indexer.process()
    (alpha / "a.md").write_text("Lease terms.", encoding="utf-8")
    (beta / "b.md").write_text("Parking rules.", encoding="utf-8")
    indexer.request_reconcile(a.id)
    indexer.process()
    assert statuses(store, a) == {"a.md": "indexed"}
    assert statuses(store, b) == {}
    indexer.request_reconcile(b.id)
    indexer.process()
    assert statuses(store, b) == {"b.md": "indexed"}


def test_rebuilding_one_project_leaves_the_other_indexed(two):
    (alpha, beta), (a, b), store, embedder, indexer = two
    (alpha / "a.md").write_text("Lease terms.", encoding="utf-8")
    (beta / "b.md").write_text("Parking rules.", encoding="utf-8")
    indexer.request_reconcile()
    indexer.process()
    beta_hash = store.list_files(b.id)[0].content_hash
    calls = embedder.calls
    indexer.request_rebuild(a.id)
    indexer.process()
    assert embedder.calls == calls + 1  # only alpha's file was embedded again
    assert statuses(store, a) == {"a.md": "indexed"}
    assert [(f.status, f.content_hash) for f in store.list_files(b.id)] == [("indexed", beta_hash)]
    assert store.search_keyword([b.id], '"Par"', 10)


def test_a_missing_folder_blocks_only_its_own_project(two):
    (alpha, beta), (a, b), store, _, indexer = two
    (alpha / "a.md").write_text("Lease terms.", encoding="utf-8")
    indexer.request_reconcile()
    indexer.process()
    store.reset_index(a.id, MODEL)  # alpha's file is pending again
    shutil.rmtree(alpha)
    (beta / "b.md").write_text("Parking rules.", encoding="utf-8")
    indexer.request_reconcile()
    indexer.process()
    assert indexer.state(a.id).error.startswith("Folder not found")
    assert indexer.state(b.id) == IndexerState(None, None)
    assert statuses(store, a) == {"a.md": "pending"}  # waits for the folder, is not failed
    assert statuses(store, b) == {"b.md": "indexed"}


def test_a_stale_project_does_not_hold_back_the_others(two):
    (alpha, beta), (a, b), store, _, indexer = two
    store.reset_index(a.id, "old-model")
    (alpha / "a.md").write_text("Lease terms.", encoding="utf-8")
    (beta / "b.md").write_text("Parking rules.", encoding="utf-8")
    indexer.request_reconcile()
    indexer.process()
    assert statuses(store, a) == {}
    assert statuses(store, b) == {"b.md": "indexed"}


def test_an_unavailable_model_is_reported_for_each_project_with_waiting_files(tmp_path):
    store = Store.open(tmp_path / "tamra.db")
    try:
        projects = []
        for name, text in (("alpha", "Lease terms."), ("beta", None)):
            folder = tmp_path / name
            folder.mkdir()
            if text:
                (folder / "a.md").write_text(text, encoding="utf-8")
            projects.append(store.create_collection(name, str(folder), MODEL))

        def missing():
            raise FileNotFoundError("model.onnx not found")

        indexer = Indexer(store, missing, lambda: fake_spans, MODEL)
        indexer.request_reconcile()
        indexer.process()
        assert indexer.state(projects[0].id).error.startswith("Embedding model unavailable")
        assert indexer.state(projects[1].id).error is None  # nothing waiting there
    finally:
        store.close()


def test_state_names_the_file_of_the_project_being_indexed(two):
    (alpha, beta), (a, b), store, _, indexer = two
    (alpha / "a.md").write_text("Lease terms.", encoding="utf-8")
    seen = []

    class WatchingEmbedder(FakeEmbedder):
        def embed(self, texts, batch_size=16):
            seen.append((indexer.state(a.id).current, indexer.state(b.id).current))
            return super().embed(texts, batch_size)

    watching = WatchingEmbedder()
    indexer._embedder = lambda: watching
    indexer.request_reconcile()
    indexer.process()
    assert seen == [("a.md", None)]
    assert indexer.state(a.id).current is None


def test_a_forgotten_project_has_no_state(two):
    (alpha, _), (a, _), store, _, indexer = two
    shutil.rmtree(alpha)
    indexer.request_reconcile(a.id)
    indexer.process()
    assert indexer.state(a.id).error
    indexer.forget(a.id)
    assert indexer.state(a.id) == IndexerState(None, None)


def test_a_failure_in_a_project_deleted_meanwhile_is_not_reported(two, monkeypatch, caplog):
    _, (a, b), store, _, indexer = two

    def fail_after_delete(collection_id, folder):
        store.delete_collection(collection_id)
        raise RuntimeError("boom")

    monkeypatch.setattr(indexer, "_reconcile_project", fail_after_delete)
    indexer.request_reconcile(a.id)
    with caplog.at_level("ERROR", logger="tamra.ingest.indexer"):
        indexer.process()
    assert indexer.state(a.id).error is None
    assert "reconciling project" not in caplog.text
    assert [c.id for c in store.list_collections()] == [b.id]


def test_a_stopping_worker_does_not_start_the_next_project(two):
    (alpha, _), (a, b), store, _, indexer = two
    (alpha / "a.md").write_text("Lease terms.", encoding="utf-8")
    indexer.stop()
    indexer.request_reconcile()
    indexer.process()
    assert store.list_files(a.id) == [] and store.list_files(b.id) == []


# --- links ---


def test_scan_skips_directory_junctions_and_symlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("x", encoding="utf-8")
    folder = tmp_path / "docs"
    (folder / "real").mkdir(parents=True)
    (folder / "real" / "kept.md").write_text("x", encoding="utf-8")
    _winapi.CreateJunction(str(outside), str(folder / "junction"))
    assert (folder / "junction" / "secret.md").exists()  # the junction really leads outside
    assert set(scan_folder(folder)) == {"real/kept.md"}

    try:
        (folder / "dirlink").symlink_to(outside, target_is_directory=True)
        (folder / "filelink.md").symlink_to(outside / "secret.md")
    except OSError:
        return  # creating symlinks needs a privilege this account lacks
    assert set(scan_folder(folder)) == {"real/kept.md"}
