import time

import pytest
from fakes import FakeEmbedder, FakeLLM, FakeLocalLLM, fake_spans

from tamra.answer import AnswerSettings
from tamra.core import Core, _build_api_provider
from tamra.embedder import MODEL_ID
from tamra.llm.anthropic_api import AnthropicLLM
from tamra.llm.base import ProviderError
from tamra.llm.openai_compat import OpenAICompatibleLLM
from tamra.models.catalog import Catalog, ModelEntry, ModelFile
from tamra.models.hardware import Hardware
from tamra.models.manager import ModelManager
from tamra.settings import Settings


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


# --- the local model ---


def catalog_of(*ids):
    """A catalog of tiny LLM entries: each is a 5-byte file named <id>.gguf."""
    return Catalog(
        ModelEntry(i, "llm", i, (ModelFile(f"{i}.gguf", 5, "0" * 64, "http://example.invalid"),))
        for i in ids
    )


def install(models, name):
    models.mkdir(exist_ok=True)
    (models / name).write_bytes(b"12345")


def local_label(core):
    return core.local.label  # the stem of the model file the local LLM would serve


def test_the_local_model_is_the_selected_one_when_installed(tmp_path):
    models = tmp_path / "models"
    for name in ("a.gguf", "b.gguf", "loose.gguf"):
        install(models, name)
    c = make_core(tmp_path, llm=None, catalog=catalog_of("a", "b"))
    try:
        assert local_label(c) == "a"  # the first installed catalog model
        c.apply_settings({"local_model_id": "b"})
        assert local_label(c) == "b"
        c.apply_settings({"local_model_id": "not-installed"})
        assert local_label(c) == "a"
        c.apply_settings({"local_model_id": "import:LOOSE.gguf"})
        assert local_label(c) == "loose"
    finally:
        c.shutdown()


def test_the_local_model_falls_back_to_an_uncatalogued_gguf(tmp_path):
    install(tmp_path / "models", "dev-model.gguf")
    c = make_core(tmp_path, llm=None, catalog=catalog_of("a"))
    try:
        assert local_label(c) == "dev-model"
    finally:
        c.shutdown()


def test_the_dev_model_is_found_without_a_hard_coded_file_name(tmp_path):
    """exe_smoke starts Tamra on .models, whose only LLM is an uncatalogued dev GGUF."""
    install(tmp_path / "models", "qwen2.5-0.5b-instruct-q4_k_m.gguf")
    c = Core(tmp_path / "data", tmp_path / "models", tmp_path / "llama.exe")
    try:
        assert local_label(c) == "qwen2.5-0.5b-instruct-q4_k_m"
    finally:
        c.shutdown()


def test_no_installed_model_is_a_model_missing_error(tmp_path):
    c = make_core(tmp_path, llm=None, catalog=catalog_of("a"))
    try:
        with pytest.raises(ProviderError) as e:
            c.local.client()
        assert e.value.reason == "model_missing"
        assert str(e.value) == "No local model is installed."
    finally:
        c.shutdown()


def test_asking_without_a_local_model_reports_model_missing(tmp_path):
    c = indexed_core(tmp_path, llm=None, catalog=catalog_of("a"))
    try:
        chat, events = ask(c)
        assert events[-1]["reason"] == "model_missing"
        assert [m.role for m in c.store.list_messages(chat.id)] == ["user"]
        _, found = ask(c, mode="search")  # passages need no model
        assert [e["type"] for e in found] == ["sources", "done"]
    finally:
        c.shutdown()


# --- models and hardware ---


def test_the_model_manager_and_hardware_probe_are_available_but_lazy(tmp_path, monkeypatch):
    probes = []
    monkeypatch.setattr("tamra.core.detect", lambda exe: probes.append(exe) or Hardware(16.0))
    c = Core(tmp_path / "data", tmp_path / "models", tmp_path / "llama.exe")
    try:
        assert isinstance(c.models, ModelManager)
        assert probes == []  # not at startup
        assert c.hardware().ram_gb == 16.0
        assert c.hardware() is c.hardware()
        assert probes == [tmp_path / "llama.exe"]
    finally:
        c.shutdown()


# --- settings and provider swaps ---


class Keys:
    """An in-memory stand-in for the key lookups in Windows Credential Manager."""

    def __init__(self, monkeypatch, **keys):
        self.keys = keys
        self.reads = []
        monkeypatch.setattr("tamra.core.secrets.get_api_key", self.get)

    def get(self, provider):
        self.reads.append(provider)
        return self.keys.get(provider)


class ApiFactory:
    """Records the providers it builds instead of calling a real API."""

    def __init__(self):
        self.built = []

    def __call__(self, settings, key):
        label = f"{settings.api_provider}:{settings.api_model}"
        llm = FakeLLM(("From the cloud [1].",), kind="api", label=label)
        self.built.append((settings, key, llm))
        return llm


def indexed_core(tmp_path, **overrides):
    c = make_core(tmp_path, **overrides)
    c.start()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "lease.md").write_text("The lease term is three years.", encoding="utf-8")
    c.set_collection("Docs", str(docs))
    wait_until(lambda: indexed(c) == 1)
    return c


def ask(core, question="How long is the lease term?", **options):
    chat = core.store.create_chat()
    return chat, list(core.answers.ask(chat.id, question, **options))


def test_apply_settings_saves_validates_and_survives_a_restart(tmp_path):
    c = make_core(tmp_path)
    try:
        saved = c.apply_settings({"theme": "dark", "api_model": "other-model"})
        assert (saved.theme, saved.api_model) == ("dark", "other-model")
        assert c.settings == saved
        with pytest.raises(ValueError):
            c.apply_settings({"theme": "light", "mode": "cloud"})
        assert c.settings == saved
    finally:
        c.shutdown()
    again = make_core(tmp_path)
    try:
        assert again.settings == saved
    finally:
        again.shutdown()


def test_an_api_answer_uses_the_stored_key_and_is_saved_as_anthropic(tmp_path, monkeypatch):
    keys = Keys(monkeypatch, anthropic="test-key")
    factory = ApiFactory()
    c = indexed_core(tmp_path, api_factory=factory)
    try:
        c.apply_settings({"mode": "api"})
        chat, events = ask(c)
        assert events[-1]["type"] == "done"
        saved = c.store.list_messages(chat.id)[1]
        assert (saved.provider, saved.model) == ("anthropic", "anthropic:claude-sonnet-5-5")
        ((settings, key, _),) = factory.built
        assert (settings.api_model, key) == ("claude-sonnet-5-5", "test-key")
        assert keys.reads == ["anthropic"]
    finally:
        c.shutdown()


def test_a_missing_api_key_is_an_error_event_not_a_crash(tmp_path, monkeypatch):
    keys = Keys(monkeypatch)
    c = indexed_core(tmp_path, api_factory=ApiFactory())
    try:
        c.apply_settings({"mode": "api"})
        assert keys.reads == []  # switching the mode does not read the key
        chat, events = ask(c)
        assert events[-1] == {"type": "error", "message": "No API key is set.", "reason": "auth"}
        assert [m.role for m in c.store.list_messages(chat.id)] == ["user"]
    finally:
        c.shutdown()


def test_starting_in_api_mode_without_a_key_does_not_fail(tmp_path, monkeypatch):
    keys = Keys(monkeypatch)
    first = make_core(tmp_path)
    first.apply_settings({"mode": "api"})
    first.shutdown()
    c = make_core(tmp_path)
    try:
        assert c.settings.mode == "api"
        assert keys.reads == []
    finally:
        c.shutdown()


def test_the_provider_is_built_once_per_key_and_rebuilt_when_the_key_changes(tmp_path, monkeypatch):
    keys = Keys(monkeypatch, anthropic="key-1")
    factory = ApiFactory()
    c = indexed_core(tmp_path, api_factory=factory)
    try:
        c.apply_settings({"mode": "api"})
        ask(c)
        ask(c)
        assert len(factory.built) == 1
        keys.keys["anthropic"] = "key-2"
        ask(c)
        assert [key for _, key, _ in factory.built] == ["key-1", "key-2"]
        assert factory.built[0][2].closed  # the replaced one is closed once the answer ends
        assert not factory.built[1][2].closed
    finally:
        c.shutdown()
    assert factory.built[1][2].closed


def test_settings_pick_the_real_provider_and_its_base_url():
    anthropic = _build_api_provider(Settings(), "test-key")
    assert isinstance(anthropic, AnthropicLLM)
    assert anthropic.label == "claude-sonnet-5-5"
    custom = Settings(api_provider="openai", api_model="gpt-test", api_base_url="http://host:11434")
    openai = _build_api_provider(custom, "test-key")
    assert isinstance(openai, OpenAICompatibleLLM)
    assert (openai.kind, openai.label) == ("api", "gpt-test")
    assert str(openai._client.base_url).rstrip("/") == "http://host:11434"
    default = _build_api_provider(Settings(api_provider="openai", api_model="gpt-test"), "k")
    assert str(default._client.base_url).rstrip("/") == "https://api.openai.com"
    for provider in (anthropic, openai, default):
        provider.close()


def test_switching_providers_closes_the_old_one(tmp_path, monkeypatch):
    Keys(monkeypatch, anthropic="test-key")
    factory = ApiFactory()
    local = FakeLocalLLM()
    c = indexed_core(tmp_path, api_factory=factory, llm=local)
    try:
        ask(c)  # local
        assert not local.closed
        c.apply_settings({"local_model_id": "anything", "theme": "dark"})
        assert not local.closed  # a new model restarts llama-server on the next question only
        c.apply_settings({"mode": "api"})
        assert local.closed  # llama-server is stopped to free its memory
        ask(c)
        first = factory.built[0][2]
        assert not first.closed
        c.apply_settings({"api_model": "claude-other"})  # still API: the old client is dropped
        assert first.closed
        ask(c)
        second = factory.built[1][2]
        assert not second.closed
        c.apply_settings({"mode": "local"})
        assert second.closed
    finally:
        c.shutdown()


def test_a_provider_is_not_closed_under_an_answer_that_is_streaming(tmp_path, monkeypatch):
    Keys(monkeypatch, anthropic="test-key")
    local = FakeLocalLLM(FakeLLM(("one ", "two ", "three")))
    c = indexed_core(tmp_path, api_factory=ApiFactory(), llm=local)
    try:
        chat = c.store.create_chat()
        stream = c.answers.ask(chat.id, "How long is the lease term?")
        assert next(stream)["type"] == "sources"
        assert next(stream) == {"type": "token", "text": "one "}
        c.apply_settings({"mode": "api"})  # in the middle of the local answer
        assert not local.closed
        assert next(stream) == {"type": "token", "text": "two "}
        assert [e["type"] for e in stream][-1] == "done"
        assert local.closed  # closed as soon as the answer ended
        assert c.store.list_messages(chat.id)[1].provider == "local"
    finally:
        c.shutdown()


def test_switching_back_to_local_before_the_stream_ends_keeps_the_local_model(
    tmp_path, monkeypatch
):
    Keys(monkeypatch, anthropic="test-key")
    local = FakeLocalLLM(FakeLLM(("one ", "two ", "three")))
    c = indexed_core(tmp_path, api_factory=ApiFactory(), llm=local)
    try:
        chat = c.store.create_chat()
        stream = c.answers.ask(chat.id, "How long is the lease term?")
        next(stream)
        next(stream)
        c.apply_settings({"mode": "api"})
        c.apply_settings({"mode": "local"})
        assert [e["type"] for e in stream][-1] == "done"
        assert not local.closed  # the mode is local again, so llama-server stays up
        _, events = ask(c)
        assert events[-1]["type"] == "done"
        assert not local.closed
    finally:
        c.shutdown()


def test_unchanged_provider_settings_swap_nothing(tmp_path, monkeypatch):
    Keys(monkeypatch, anthropic="test-key")
    factory = ApiFactory()
    c = indexed_core(tmp_path, api_factory=factory)
    try:
        c.apply_settings({"mode": "api"})
        ask(c)
        c.apply_settings({"mode": "api", "api_model": c.settings.api_model, "theme": "dark"})
        assert not factory.built[0][2].closed
    finally:
        c.shutdown()


@pytest.mark.parametrize("error", [RuntimeError("backend exploded"), OSError("locked")])
def test_a_keyring_failure_is_an_auth_error_event_without_the_key(tmp_path, monkeypatch, error):
    def broken(provider):
        raise error

    monkeypatch.setattr("tamra.core.secrets.get_api_key", broken)
    c = indexed_core(tmp_path, api_factory=ApiFactory())
    try:
        c.apply_settings({"mode": "api"})
        _, events = ask(c)
        assert events[-1]["type"] == "error"
        assert events[-1]["reason"] == "auth"
        assert "exploded" not in events[-1]["message"]
    finally:
        c.shutdown()
