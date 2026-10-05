"""The settings, key, model, and file-picker routes (M2 task 6)."""

import hashlib
import json
import logging
import threading
import time
from pathlib import Path

import httpx
import keyring
import pytest
from fakes import FakeEmbedder, FakeLocalLLM, MemoryKeyring, fake_spans
from fastapi.testclient import TestClient

from tamra import secrets as key_store
from tamra.core import Core
from tamra.llm.base import Chunk, ProviderError
from tamra.models.catalog import Catalog, ModelEntry, ModelFile
from tamra.models.hardware import Gpu, Hardware
from tamra.models.manager import ImportResult, ModelManager
from tamra.server import create_app

TOKEN = "secret"
AUTH = {"X-Tamra-Token": TOKEN}
KEY = "test-secret-key-abcd1234"  # a made-up value that is longer than 8 characters

SMALL = b"GGUF" + b"s" * 60
MEDIUM = b"GGUF" + b"m" * 80
EMB = b"emb-bytes" * 10
TOK = b"tok-bytes" * 5


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_catalog() -> Catalog:
    return Catalog(
        [
            ModelEntry(
                "emb",
                "embedding",
                "Emb",
                (
                    ModelFile("emb/model.onnx", len(EMB), _sha(EMB), "https://x/m.onnx"),
                    ModelFile("emb/tokenizer.json", len(TOK), _sha(TOK), "https://x/t.json"),
                ),
            ),
            ModelEntry(
                "small",
                "llm",
                "Small",
                (ModelFile("Small-Q4.gguf", len(SMALL), _sha(SMALL), "https://x/small.gguf"),),
                tier="small",
                license="apache-2.0",
                languages=("th", "en"),
                context_length=4096,
                thinking=True,
            ),
            ModelEntry(
                "medium",
                "llm",
                "Medium",
                (ModelFile("Medium-Q4.gguf", len(MEDIUM), _sha(MEDIUM), "https://x/medium.gguf"),),
                tier="medium",
                min_vram_gb=6,
            ),
        ]
    )


BODIES = {"https://x/small.gguf": SMALL, "https://x/medium.gguf": MEDIUM}


def gated_transport(gate: threading.Event) -> httpx.MockTransport:
    class Body(httpx.SyncByteStream):
        def __init__(self, data: bytes):
            self.data = data

        def __iter__(self):
            yield self.data[:8]
            gate.wait(10)  # the rest flows once the test lets it
            yield self.data[8:]

    return httpx.MockTransport(
        lambda request: httpx.Response(200, stream=Body(BODIES[str(request.url)]))
    )


class FakeApi:
    """An API provider that yields one chunk, or raises what it is given."""

    kind = "api"

    def __init__(self, label: str, error: Exception | None = None):
        self.label = label
        self.error = error
        self.calls: list[tuple[list, int]] = []
        self.generators_closed = 0
        self.closed = False

    def generate(self, messages, max_tokens=1024, *, think=False):
        self.calls.append((list(messages), max_tokens))
        try:
            if self.error is not None:
                raise self.error
            yield Chunk("text", "pong")
            yield Chunk("text", "never read")
        finally:
            self.generators_closed += 1

    def close(self) -> None:
        self.closed = True


class Api:
    """Builds FakeApi providers (the factory Core uses) and remembers them."""

    def __init__(self):
        self.error: Exception | None = None
        self.built: list[tuple[object, str, FakeApi]] = []

    def __call__(self, settings, key):
        provider = FakeApi(f"{settings.api_provider}:{settings.api_model}", self.error)
        self.built.append((settings, key, provider))
        return provider


@pytest.fixture
def keys():
    previous = keyring.get_keyring()
    memory = MemoryKeyring()
    keyring.set_keyring(memory)
    yield memory
    keyring.set_keyring(previous)


@pytest.fixture
def env(tmp_path, keys, monkeypatch):
    probes = []
    monkeypatch.setattr(
        "tamra.core.detect",
        lambda exe: probes.append(exe) or Hardware(15.9, [Gpu("NVIDIA RTX 4070", 12282)]),
    )
    api = Api()
    models = tmp_path / "models"
    models.mkdir()
    core = Core(
        tmp_path / "data",
        models,
        tmp_path / "llama.exe",
        embedder_factory=FakeEmbedder,
        token_spans_factory=lambda: fake_spans,
        llm=FakeLocalLLM(),
        api_factory=api,
        catalog=make_catalog(),
    )
    core.start()
    client = TestClient(create_app(TOKEN, None, core), base_url="http://127.0.0.1")
    client.api, client.probes, client.models_dir = api, probes, models
    yield client, core, tmp_path
    core.shutdown()


# --- the token gate ---------------------------------------------------------------------

ROUTES = [
    ("GET", "/api/settings", None),
    ("PUT", "/api/settings", {"theme": "dark"}),
    ("PUT", "/api/settings/api-key", {"provider": "anthropic", "key": "x"}),
    ("POST", "/api/settings/test-connection", None),
    ("GET", "/api/models", None),
    ("POST", "/api/models/small/download", None),
    ("DELETE", "/api/models/small/download", None),
    ("POST", "/api/models/import", {"path": "C:/m.gguf"}),
    ("POST", "/api/pick-file", None),
    ("DELETE", "/api/chats", None),
    ("POST", "/api/open-data-folder", None),
    ("POST", "/api/chats/1/messages", {"content": "q", "mode": "search"}),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_new_route_needs_the_token(env, method, path, body):
    client, _, _ = env
    assert client.request(method, path, json=body).status_code == 401
    wrong = client.request(method, path, json=body, headers={"X-Tamra-Token": "wrong"})
    assert wrong.status_code == 401


# --- settings ---------------------------------------------------------------------------


def test_get_settings_has_every_setting_and_the_key_state(env):
    client, _, tmp_path = env
    response = client.get("/api/settings", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "mode": "local",
        "local_model_id": None,
        "api_provider": "anthropic",
        "api_model": "claude-sonnet-5-5",
        "api_base_url": "",
        "language": "en",
        "theme": "light",
        "accent": "green",
        "text_size": "default",
        "spacing": "comfortable",
        "ask_before_delete": True,
        "api_key_set": False,
        "api_key_hint": None,
        "data_dir": str(tmp_path / "data"),
    }


def test_put_settings_takes_a_partial_body_and_returns_the_new_settings(env):
    client, core, _ = env
    response = client.put(
        "/api/settings", headers=AUTH, json={"theme": "dark", "api_model": "other-model"}
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["theme"], body["api_model"], body["language"]) == ("dark", "other-model", "en")
    assert "api_key_set" in body
    assert core.settings.theme == "dark"
    assert client.get("/api/settings", headers=AUTH).json() == body
    assert client.put("/api/settings", headers=AUTH, json={}).json() == body


def test_put_settings_names_the_bad_field_and_changes_nothing(env):
    client, core, _ = env
    bad = client.put("/api/settings", headers=AUTH, json={"theme": "dark", "mode": "cloud"})
    assert bad.status_code == 400
    assert "mode" in bad.json()["detail"]
    assert core.settings.theme == "light"  # the valid change was not applied either
    unknown = client.put("/api/settings", headers=AUTH, json={"nope": 1})
    assert unknown.status_code == 400
    assert "nope" in unknown.json()["detail"]
    url = client.put("/api/settings", headers=AUTH, json={"api_base_url": "ftp://x"})
    assert url.status_code == 400
    assert "api_base_url" in url.json()["detail"]
    assert client.put("/api/settings", headers=AUTH, json=["theme"]).status_code == 422


def test_put_settings_cannot_carry_an_api_key(env, keys):
    client, _, _ = env
    response = client.put("/api/settings", headers=AUTH, json={"api_key": KEY})
    assert response.status_code == 400
    assert keys.store == {}


# --- API keys ---------------------------------------------------------------------------


def test_a_key_is_stored_in_the_key_store_and_only_its_last_four_characters_show(env, keys):
    client, _, _ = env
    put = client.put(
        "/api/settings/api-key", headers=AUTH, json={"provider": "anthropic", "key": f"  {KEY}\n"}
    )
    assert put.status_code == 204
    assert put.content == b""
    assert keys.store == {("Tamra", "anthropic"): KEY}  # stripped
    settings = client.get("/api/settings", headers=AUTH).json()
    assert settings["api_key_set"] is True
    assert settings["api_key_hint"] == KEY[-4:]
    assert KEY not in json.dumps(settings)


def test_the_key_state_follows_the_selected_provider(env):
    client, _, _ = env
    client.put("/api/settings/api-key", headers=AUTH, json={"provider": "openai", "key": KEY})
    assert client.get("/api/settings", headers=AUTH).json()["api_key_set"] is False
    after = client.put("/api/settings", headers=AUTH, json={"api_provider": "openai"}).json()
    assert (after["api_key_set"], after["api_key_hint"]) == (True, KEY[-4:])


def test_a_short_key_has_no_hint(env):
    client, _, _ = env
    for short in ("12345678", "abcd"):
        client.put(
            "/api/settings/api-key", headers=AUTH, json={"provider": "anthropic", "key": short}
        )
        settings = client.get("/api/settings", headers=AUTH).json()
        assert (settings["api_key_set"], settings["api_key_hint"]) == (True, None)


def test_an_empty_key_deletes_the_stored_key(env, keys):
    client, _, _ = env
    client.put("/api/settings/api-key", headers=AUTH, json={"provider": "anthropic", "key": KEY})
    for blank in ("", "   "):
        client.put(
            "/api/settings/api-key", headers=AUTH, json={"provider": "anthropic", "key": KEY}
        )
        response = client.put(
            "/api/settings/api-key", headers=AUTH, json={"provider": "anthropic", "key": blank}
        )
        assert response.status_code == 204
        assert keys.store == {}
    again = client.put(
        "/api/settings/api-key", headers=AUTH, json={"provider": "anthropic", "key": ""}
    )
    assert again.status_code == 204  # nothing stored: still fine
    assert client.get("/api/settings", headers=AUTH).json()["api_key_set"] is False


def test_setting_or_removing_a_key_retires_the_cached_provider(env):
    client, core, _ = env

    def put(key):
        response = client.put(
            "/api/settings/api-key", headers=AUTH, json={"provider": "anthropic", "key": key}
        )
        assert response.status_code == 204

    put(KEY)
    client.post("/api/settings/test-connection", headers=AUTH)
    ((_, _, first),) = client.api.built
    assert core._api is first
    put(KEY)  # the same key again: the provider is still dropped
    assert first.closed and core._api is None
    client.post("/api/settings/test-connection", headers=AUTH)
    assert len(client.api.built) == 2
    second = client.api.built[1][2]
    put("")  # removing the key drops it too
    assert second.closed and core._api is None
    refused = client.post("/api/settings/test-connection", headers=AUTH).json()
    assert (refused["ok"], refused["reason"]) == (False, "auth")


def test_an_unknown_provider_is_a_400_and_stores_nothing(env, keys):
    client, _, _ = env
    for provider in ("google", "", "Anthropic"):
        response = client.put(
            "/api/settings/api-key", headers=AUTH, json={"provider": provider, "key": KEY}
        )
        assert response.status_code == 400
    assert keys.store == {}
    assert (
        client.put("/api/settings/api-key", headers=AUTH, json={"provider": "openai"}).status_code
        == 422
    )


def test_the_key_is_in_no_response_and_no_log_line(env, caplog, monkeypatch):
    client, _, _ = env
    caplog.set_level(logging.DEBUG)
    seen = []

    def call(method, path, **kwargs):
        response = client.request(method, path, headers=AUTH, **kwargs)
        seen.append(response.text)
        return response

    call("PUT", "/api/settings/api-key", json={"provider": "anthropic", "key": KEY})
    call("PUT", "/api/settings/api-key", json={"provider": "nope", "key": KEY})
    call("PUT", "/api/settings/api-key", json={"provider": "anthropic", "key": KEY, "x": 1})
    call("PUT", "/api/settings/api-key", json={"key": KEY})  # 422: the input is not echoed
    call("PUT", "/api/settings/api-key", json={"provider": 5, "key": KEY})
    call("GET", "/api/settings")
    call("PUT", "/api/settings", json={"mode": "api"})
    call("POST", "/api/settings/test-connection")
    call("GET", "/api/models")

    def refuse(provider, key):
        raise RuntimeError(f"backend failed on {key}")

    monkeypatch.setattr(key_store, "set_api_key", refuse)
    failed = call("PUT", "/api/settings/api-key", json={"provider": "anthropic", "key": KEY})
    assert failed.status_code == 500
    assert failed.json() == {"detail": "The API key could not be saved to the system."}
    assert KEY not in "".join(seen)
    assert KEY not in caplog.text
    assert "RuntimeError" in caplog.text  # the failure is logged, by type only


def test_an_unreadable_key_store_reads_as_no_key(env, monkeypatch):
    client, _, _ = env

    def broken(provider):
        raise OSError("credential manager is unavailable")

    monkeypatch.setattr(key_store, "get_api_key", broken)
    settings = client.get("/api/settings", headers=AUTH).json()
    assert (settings["api_key_set"], settings["api_key_hint"]) == (False, None)


# --- test connection --------------------------------------------------------------------


def test_the_connection_test_sends_one_token_and_closes_the_stream(env):
    client, _, _ = env
    client.put("/api/settings/api-key", headers=AUTH, json={"provider": "anthropic", "key": KEY})
    response = client.post("/api/settings/test-connection", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "reason": None, "message": "Connected."}
    ((settings, key, provider),) = client.api.built
    assert (settings.api_provider, key) == ("anthropic", KEY)
    assert provider.calls == [([{"role": "user", "content": "ping"}], 1)]
    assert provider.generators_closed == 1


def test_the_connection_test_uses_the_api_settings_even_in_local_mode(env):
    client, core, _ = env
    assert core.settings.mode == "local"
    client.put("/api/settings", headers=AUTH, json={"api_provider": "openai", "api_model": "m"})
    client.put("/api/settings/api-key", headers=AUTH, json={"provider": "openai", "key": KEY})
    assert client.post("/api/settings/test-connection", headers=AUTH).json()["ok"] is True
    assert client.api.built[0][0].api_provider == "openai"


def test_the_connection_test_without_a_key_is_an_auth_failure(env):
    client, _, _ = env
    response = client.post("/api/settings/test-connection", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"ok": False, "reason": "auth", "message": "No API key is set."}
    assert client.api.built == []


def test_a_provider_error_is_reported_with_its_reason(env):
    client, _, _ = env
    client.put("/api/settings/api-key", headers=AUTH, json={"provider": "anthropic", "key": KEY})
    client.api.error = ProviderError("The API key was rejected.", "auth")
    response = client.post("/api/settings/test-connection", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "reason": "auth",
        "message": "The API key was rejected.",
    }
    assert client.api.built[0][2].generators_closed == 1


def test_a_failing_close_cannot_turn_the_connection_test_into_a_500(env, caplog):
    client, core, _ = env
    caplog.set_level(logging.DEBUG)
    client.put("/api/settings/api-key", headers=AUTH, json={"provider": "anthropic", "key": KEY})

    class BadClose:
        def __iter__(self):
            return self

        def __next__(self):
            return Chunk("text", "pong")

        def close(self):
            raise RuntimeError(f"close failed with {KEY}")

    provider = FakeApi("p")
    provider.generate = lambda messages, max_tokens=1024, think=False: BadClose()
    core._api_factory = lambda settings, key: provider
    response = client.post("/api/settings/test-connection", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "RuntimeError" in caplog.text
    assert KEY not in caplog.text


def test_an_unexpected_failure_is_never_a_500_and_never_leaks_its_text(env, caplog):
    client, _, _ = env
    caplog.set_level(logging.DEBUG)
    client.put("/api/settings/api-key", headers=AUTH, json={"provider": "anthropic", "key": KEY})
    client.api.error = RuntimeError(f"boom with {KEY}")
    response = client.post("/api/settings/test-connection", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "reason": "other",
        "message": "The connection test failed.",
    }
    assert KEY not in response.text
    assert KEY not in caplog.text


# --- models -----------------------------------------------------------------------------


def test_get_models_lists_the_catalog_hardware_and_active_model(env):
    client, core, _ = env
    (client.models_dir / "Small-Q4.gguf").write_bytes(SMALL)
    (client.models_dir / "mine.gguf").write_bytes(b"GGUF1234")
    (client.models_dir / "notes.txt").write_text("not a model")
    response = client.get("/api/models", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    idle = {"state": "idle", "done": 0, "total": 0, "error": None}
    assert body == {
        "hardware": {
            "ram_gb": 15.9,
            "gpus": [{"name": "NVIDIA RTX 4070", "vram_mb": 12282, "integrated": False}],
        },
        "recommended_tier": "medium",
        "active": {"mode": "local", "label": "Small", "id": "small"},
        "gpu_offload": None,
        "local": [
            {
                "id": "small",
                "name": "Small",
                "size": len(SMALL),
                "installed": True,
                **idle,
                "tier": "small",
                "recommended": False,
                "license": "apache-2.0",
                "languages": ["th", "en"],
                "context_length": 4096,
                "thinking": True,
                "min_vram_gb": 0,
            },
            {
                "id": "medium",
                "name": "Medium",
                "size": len(MEDIUM),
                "installed": False,
                **idle,
                "tier": "medium",
                "recommended": True,
                "license": "",
                "languages": [],
                "context_length": 0,
                "thinking": False,
                "min_vram_gb": 6,
            },
        ],
        "uncatalogued": [
            {"id": "import:mine.gguf", "name": "mine", "file": "mine.gguf", "size": 8}
        ],
        "embedding": {
            "id": "emb",
            "name": "Emb",
            "size": len(EMB) + len(TOK),
            "installed": False,
            **idle,
        },
    }
    core.apply_settings({"mode": "api", "api_model": "claude-x"})
    active = client.get("/api/models", headers=AUTH).json()["active"]
    assert active == {"mode": "api", "label": "claude-x", "id": "small"}  # local mode's model


def test_get_models_marks_integrated_gpus(env):
    client, core, _ = env
    core._hardware = Hardware(
        15.0,
        [
            Gpu("AMD Radeon(TM) 780M Graphics", 8094),
            Gpu("NVIDIA GeForce RTX 5060 Laptop GPU", 7899),
        ],
    )
    gpus = client.get("/api/models", headers=AUTH).json()["hardware"]["gpus"]
    assert [(g["name"], g["integrated"]) for g in gpus] == [
        ("AMD Radeon(TM) 780M Graphics", True),
        ("NVIDIA GeForce RTX 5060 Laptop GPU", False),
    ]


def test_put_settings_refuses_the_read_only_data_dir(env):
    client, _, _ = env
    response = client.put("/api/settings", headers=AUTH, json={"data_dir": "C:/elsewhere"})
    assert response.status_code == 400


def test_get_models_probes_the_hardware_once_and_reports_gpu_offload(env):
    client, core, tmp_path = env
    for _ in range(3):
        assert client.get("/api/models", headers=AUTH).status_code == 200
    assert client.probes == [tmp_path / "llama.exe"]
    core.local.gpu_offload = True
    assert client.get("/api/models", headers=AUTH).json()["gpu_offload"] is True


def test_get_models_never_hashes_a_file(env, monkeypatch):
    client, _, _ = env
    (client.models_dir / "Small-Q4.gguf").write_bytes(SMALL)

    def forbidden(*args, **kwargs):
        raise AssertionError("a file was hashed")

    monkeypatch.setattr("tamra.models.catalog.sha256_file", forbidden)
    monkeypatch.setattr("tamra.models.manager.sha256_file", forbidden)
    assert client.get("/api/models", headers=AUTH).json()["local"][0]["installed"] is True


def active_of(client):
    return client.get("/api/models", headers=AUTH).json()["active"]


def test_active_model_is_the_selected_one_when_it_is_installed(env):
    client, _, _ = env
    (client.models_dir / "Small-Q4.gguf").write_bytes(SMALL)
    (client.models_dir / "Medium-Q4.gguf").write_bytes(MEDIUM)
    (client.models_dir / "Mine.gguf").write_bytes(b"GGUF1234")
    assert active_of(client)["id"] == "small"  # nothing selected: the first installed
    client.put("/api/settings", headers=AUTH, json={"local_model_id": "medium"})
    assert active_of(client) == {"mode": "local", "label": "Medium", "id": "medium"}
    client.put("/api/settings", headers=AUTH, json={"local_model_id": "import:mine.GGUF"})
    assert active_of(client) == {"mode": "local", "label": "Mine", "id": "import:Mine.gguf"}
    client.put("/api/settings", headers=AUTH, json={"local_model_id": "import:gone.gguf"})
    assert active_of(client)["id"] == "small"  # the selection is not installed: the fallback


def test_active_model_with_one_catalog_model_installed_and_none_selected(env):
    client, _, _ = env
    (client.models_dir / "Medium-Q4.gguf").write_bytes(MEDIUM)
    assert active_of(client) == {"mode": "local", "label": "Medium", "id": "medium"}


def test_active_model_is_the_import_id_when_only_an_uncatalogued_file_is_installed(env):
    client, _, _ = env
    (client.models_dir / "Mine.gguf").write_bytes(b"GGUF1234")
    assert active_of(client) == {"mode": "local", "label": "Mine", "id": "import:Mine.gguf"}


def test_active_model_is_null_when_no_local_model_is_installed(env):
    client, core, _ = env
    assert active_of(client) == {"mode": "local", "label": None, "id": None}
    core.apply_settings({"mode": "api", "api_model": "claude-x"})
    assert active_of(client) == {"mode": "api", "label": "claude-x", "id": None}


def test_a_model_downloads_reports_progress_and_can_be_cancelled(env):
    client, core, _ = env
    gate = threading.Event()
    core.models = ModelManager(client.models_dir, make_catalog(), transport=gated_transport(gate))
    started = client.post("/api/models/small/download", headers=AUTH)
    assert started.status_code == 202
    assert started.json() == {"status": "downloading"}
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        entry = client.get("/api/models", headers=AUTH).json()["local"][0]
        if entry["done"] > 0:
            break
        time.sleep(0.01)
    assert (entry["state"], entry["installed"], entry["total"]) == (
        "downloading",
        False,
        len(SMALL),
    )
    busy = client.post("/api/models/medium/download", headers=AUTH)
    assert busy.status_code == 409
    cancelled = client.delete("/api/models/small/download", headers=AUTH)
    assert cancelled.status_code == 204
    gate.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        entry = client.get("/api/models", headers=AUTH).json()["local"][0]
        if entry["state"] == "idle":
            break
        time.sleep(0.01)
    assert (entry["state"], entry["installed"], entry["error"]) == ("idle", False, None)


def test_a_finished_download_shows_as_installed(env):
    client, core, _ = env
    gate = threading.Event()
    gate.set()
    core.models = ModelManager(client.models_dir, make_catalog(), transport=gated_transport(gate))
    assert client.post("/api/models/medium/download", headers=AUTH).status_code == 202
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        entry = client.get("/api/models", headers=AUTH).json()["local"][1]
        if entry["installed"]:
            break
        time.sleep(0.01)
    assert (entry["installed"], entry["state"]) == (True, "idle")


def test_an_installed_model_is_not_downloaded_again(env):
    client, core, _ = env
    (client.models_dir / "Small-Q4.gguf").write_bytes(SMALL)
    started = []
    core.models.start_download = started.append
    response = client.post("/api/models/small/download", headers=AUTH)
    assert response.status_code == 409
    assert response.json() == {"detail": "Already installed."}
    assert started == []


def test_unknown_models_are_404_for_download_and_cancel(env):
    client, _, _ = env
    assert client.post("/api/models/nope/download", headers=AUTH).status_code == 404
    assert client.post("/api/models/import:x.gguf/download", headers=AUTH).status_code == 404
    assert client.delete("/api/models/nope/download", headers=AUTH).status_code == 404
    assert client.delete("/api/models/small/download", headers=AUTH).status_code == 204  # idle


def test_import_copies_a_gguf_and_returns_an_id_the_settings_accept(env):
    client, core, tmp_path = env
    source = tmp_path / "elsewhere" / "My Model.gguf"
    source.parent.mkdir()
    source.write_bytes(b"GGUF-custom")
    response = client.post("/api/models/import", headers=AUTH, json={"path": str(source)})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "import:My Model.gguf"
    assert body["catalogued"] is False
    assert body["warning"]
    assert (client.models_dir / "My Model.gguf").read_bytes() == b"GGUF-custom"
    chosen = client.put("/api/settings", headers=AUTH, json={"local_model_id": body["id"]})
    assert chosen.status_code == 200
    listed = client.get("/api/models", headers=AUTH).json()["uncatalogued"]
    assert [m["id"] for m in listed] == [body["id"]]


def test_import_of_a_catalog_file_returns_the_catalog_id(env):
    client, _, tmp_path = env
    source = tmp_path / "anything.bin"
    source.write_bytes(MEDIUM)
    body = client.post("/api/models/import", headers=AUTH, json={"path": str(source)}).json()
    assert (body["id"], body["catalogued"], body["warning"]) == ("medium", True, None)
    assert (client.models_dir / "Medium-Q4.gguf").read_bytes() == MEDIUM


def test_import_validates_the_path_and_reports_refusals(env):
    client, _, tmp_path = env
    not_gguf = tmp_path / "notes.txt"
    not_gguf.write_text("hello")
    fake = tmp_path / "fake.gguf"
    fake.write_bytes(b"nope")
    for path in ("relative/m.gguf", str(tmp_path / "missing.gguf"), str(tmp_path)):
        response = client.post("/api/models/import", headers=AUTH, json={"path": path})
        assert response.status_code == 400, path
    for path in (not_gguf, fake):
        response = client.post("/api/models/import", headers=AUTH, json={"path": str(path)})
        assert response.status_code == 400
        assert response.json()["detail"]
    assert client.post("/api/models/import", headers=AUTH, json={"path": ""}).status_code == 422
    assert list(client.models_dir.iterdir()) == []


def test_import_echoes_the_trimmed_path_in_its_400s(env):
    client, _, tmp_path = env
    missing = str(tmp_path / "missing.gguf")
    response = client.post("/api/models/import", headers=AUTH, json={"path": f"  {missing}  "})
    assert response.status_code == 400
    assert response.json()["detail"] == f"File not found: {missing}"
    relative = client.post("/api/models/import", headers=AUTH, json={"path": " m.gguf "})
    assert relative.status_code == 400


@pytest.mark.parametrize("prefix", ["\\\\?\\", "\\\\.\\"])
def test_import_refuses_device_and_extended_length_paths(env, prefix):
    client, _, tmp_path = env
    source = tmp_path / "m.gguf"
    source.write_bytes(b"GGUF-custom")
    path = prefix + str(source)
    response = client.post("/api/models/import", headers=AUTH, json={"path": path})
    assert response.status_code == 400
    assert path in response.json()["detail"]
    assert list(client.models_dir.iterdir()) == []


def test_import_accepts_a_unc_path_shape(env, monkeypatch):
    client, core, _ = env
    seen = []
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(core.models, "import_file", lambda path: seen.append(path) or _imported())
    response = client.post("/api/models/import", headers=AUTH, json={"path": UNC})
    assert response.status_code == 200
    assert str(seen[0]) == UNC


UNC = r"\\host\share\m.gguf"


def _imported():
    return ImportResult(id="import:m.gguf", path=Path("m.gguf"), catalogued=False)


def test_a_destination_error_is_a_json_500_with_a_fixed_message(env, monkeypatch, caplog):
    client, core, tmp_path = env
    source = tmp_path / "m.gguf"
    source.write_bytes(b"GGUF-custom")

    def full_disk(path):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(core.models, "import_file", full_disk)
    response = client.post("/api/models/import", headers=AUTH, json={"path": str(source)})
    assert response.status_code == 500
    assert response.json() == {
        "detail": "The model file could not be copied. "
        "Check free disk space and that the model is not in use."
    }


# --- pickers and the data folder ---------------------------------------------------------


def test_the_file_picker_needs_a_window_and_returns_the_choice(env):
    client, core, _ = env
    assert client.post("/api/pick-file", headers=AUTH).status_code == 501
    with_picker = TestClient(
        create_app(TOKEN, None, core, pick_file=lambda: "C:/m.gguf"), base_url="http://127.0.0.1"
    )
    assert with_picker.post("/api/pick-file", headers=AUTH).json() == {"file_path": "C:/m.gguf"}
    cancelled = TestClient(
        create_app(TOKEN, None, core, pick_file=lambda: None), base_url="http://127.0.0.1"
    )
    assert cancelled.post("/api/pick-file", headers=AUTH).json() == {"file_path": None}


def test_the_data_folder_opens_only_in_window_mode_and_takes_no_path(env):
    client, core, _ = env
    assert client.post("/api/open-data-folder", headers=AUTH).status_code == 501
    opened = []
    windowed = TestClient(
        create_app(TOKEN, None, core, open_data_folder=lambda: opened.append("data")),
        base_url="http://127.0.0.1",
    )
    response = windowed.post("/api/open-data-folder", headers=AUTH, json={"path": "C:/Windows"})
    assert response.status_code == 204
    assert opened == ["data"]

    def fail():
        raise OSError("no handler")

    broken = TestClient(
        create_app(TOKEN, None, core, open_data_folder=fail), base_url="http://127.0.0.1"
    )
    assert broken.post("/api/open-data-folder", headers=AUTH).status_code == 500


# --- chats ------------------------------------------------------------------------------


def test_delete_all_chats_removes_every_chat_and_message(env):
    client, core, _ = env
    first = client.post("/api/chats", headers=AUTH).json()["id"]
    client.post("/api/chats", headers=AUTH)
    core.store.add_user_message(first, "a question")
    assert client.delete("/api/chats", headers=AUTH).status_code == 204
    assert client.get("/api/chats", headers=AUTH).json() == {"chats": []}
    assert core.store.list_messages(first) == []
    assert client.delete("/api/chats", headers=AUTH).status_code == 204  # none left: still fine


def test_chats_are_not_deleted_while_an_answer_is_being_written(env):
    client, core, _ = env
    chat_id = client.post("/api/chats", headers=AUTH).json()["id"]
    answer = core.answers.ask(chat_id, "q")
    next(answer)  # the answer has started: it stays busy until the stream ends
    busy = client.delete("/api/chats", headers=AUTH)
    assert busy.status_code == 409
    assert busy.json()["detail"]
    assert len(client.get("/api/chats", headers=AUTH).json()["chats"]) == 1
    answer.close()
    assert client.delete("/api/chats", headers=AUTH).status_code == 204


def test_a_question_passes_its_mode_and_thinking_flag_on(env, monkeypatch):
    client, core, _ = env
    asked = []

    def fake_ask(chat_id, content, **options):
        asked.append((chat_id, content, options))
        yield {"type": "done", "message_id": 1}

    monkeypatch.setattr(core.answers, "ask", fake_ask)
    client.post("/api/chats/1/messages", headers=AUTH, json={"content": "q"})
    client.post(
        "/api/chats/2/messages",
        headers=AUTH,
        json={"content": "r", "mode": "search", "think": True},
    )
    assert asked == [
        (1, "q", {"mode": "answer", "think": False}),
        (2, "r", {"mode": "search", "think": True}),
    ]
    bad = client.post("/api/chats/1/messages", headers=AUTH, json={"content": "q", "mode": "x"})
    assert bad.status_code == 422
