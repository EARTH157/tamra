import time

import pytest
from fakes import FakeEmbedder, FakeLocalLLM, fake_spans

from tamra.answer import AnswerSettings
from tamra.core import DEV_LLM_FILE, Core
from tamra.embedder import MODEL_ID


def make_core(tmp_path, **overrides):
    options = {
        "embedder_factory": FakeEmbedder,
        "token_spans_factory": lambda: fake_spans,
        "llm": FakeLocalLLM(),
        "answer_settings": AnswerSettings(min_similarity=0.3),
        "debounce": 0.2,
    }
    options.update(overrides)
    return Core(tmp_path / "data", tmp_path / "models", tmp_path / "llama.exe", **options)


@pytest.fixture
def core(tmp_path):
    c = make_core(tmp_path)
    c.start()
    yield c
    c.shutdown()


def wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    assert predicate()


def indexed(core):
    status = core.index_status()
    return status["counts"]["indexed"] if status else 0


def test_set_collection_checks_the_folder(core, tmp_path):
    with pytest.raises(ValueError, match="Folder not found"):
        core.set_collection("x", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="full folder path"):
        core.set_collection("x", "relative/dir")
    assert core.index_status() is None


def test_a_folder_is_indexed_and_questions_are_answered(core, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "lease.md").write_text("The lease term is three years.", encoding="utf-8")
    collection = core.set_collection("  ", str(docs))
    assert (collection.name, collection.embedding_model_id) == ("docs", MODEL_ID)
    wait_until(lambda: indexed(core) == 1)
    chat = core.store.create_chat()
    events = list(core.answers.ask(chat.id, "How long is the lease term?"))
    assert events[-1]["type"] == "done"


def test_new_files_are_picked_up_by_the_watcher(core, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    core.set_collection("Docs", str(docs))
    (docs / "late.md").write_text("Parking is free.", encoding="utf-8")
    wait_until(lambda: indexed(core) == 1)


def test_status_reports_a_stale_index_and_rebuild_fixes_it(core, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("Lease terms.", encoding="utf-8")
    core.set_collection("Docs", str(docs))
    wait_until(lambda: indexed(core) == 1)
    core.store.reset_index(core.store.get_collection().id, "old-model")
    status = core.index_status()
    assert status["stale"] is True
    assert set(status) == {"stale", "counts", "current", "error", "problems"}
    assert core.rebuild() is True
    wait_until(lambda: not core.index_status()["stale"] and indexed(core) == 1)


def test_the_embedder_is_loaded_once_and_shared(tmp_path):
    loads = []

    def factory():
        loads.append(1)
        return FakeEmbedder()

    c = make_core(tmp_path, embedder_factory=factory)
    try:
        assert c.embedder() is c.embedder()
        assert loads == [1]
    finally:
        c.shutdown()


def test_shutdown_closes_the_llm_and_a_fresh_core_reads_the_same_data(tmp_path):
    llm = FakeLocalLLM()
    c = make_core(tmp_path, llm=llm)
    c.start()
    c.store.create_chat("kept")
    c.shutdown()
    assert llm.closed
    again = make_core(tmp_path)
    try:
        assert [chat.title for chat in again.store.list_chats()] == ["kept"]
    finally:
        again.shutdown()


def test_the_default_llm_is_the_dev_model(tmp_path):
    c = Core(tmp_path / "data", tmp_path / "models", tmp_path / "llama.exe")
    try:
        assert c.llm.label == DEV_LLM_FILE.removesuffix(".gguf")
    finally:
        c.shutdown()
