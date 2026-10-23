import dataclasses

import pytest

from tamra.settings import Settings, load_settings, save_settings
from tamra.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "tamra.db")
    yield s
    s.close()


def test_defaults_come_back_when_nothing_is_stored(store):
    settings = load_settings(store)
    assert settings == Settings()
    assert settings.mode == "local"
    assert settings.local_model_id is None
    assert settings.api_provider == "anthropic"
    assert settings.api_model == "claude-sonnet-5-5"
    assert settings.api_base_url == ""
    assert settings.language == "en"
    assert settings.theme == "light"
    assert settings.accent == "green"
    assert settings.text_size == "default"
    assert settings.spacing == "comfortable"
    assert settings.ask_before_delete is True
    assert settings.mcp_enabled is False


def test_settings_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Settings().mode = "api"


def test_every_field_round_trips(store):
    changes = {
        "mode": "api",
        "local_model_id": "import:my-model.gguf",
        "local_context": "32768",
        "api_provider": "openai",
        "api_model": "gpt-x",
        "api_base_url": "https://example.test/v1",
        "language": "th",
        "theme": "system",
        "accent": "purple",
        "text_size": "large",
        "spacing": "compact",
        "ask_before_delete": False,
        "linked_models": ({"path": "D:\\Models\\a.gguf", "size": 5, "sha256": "a" * 64},),
        "mcp_enabled": True,
    }
    assert set(changes) == {f.name for f in dataclasses.fields(Settings)}
    saved = save_settings(store, changes)
    assert dataclasses.asdict(saved) == changes
    assert dataclasses.asdict(load_settings(store)) == changes


def test_a_partial_change_keeps_the_other_fields(store):
    save_settings(store, {"theme": "dark", "accent": "blue"})
    saved = save_settings(store, {"accent": "orange"})
    assert saved.theme == "dark"
    assert saved.accent == "orange"
    assert load_settings(store).theme == "dark"


def test_local_model_id_can_be_cleared(store):
    save_settings(store, {"local_model_id": "some-model"})
    assert save_settings(store, {"local_model_id": None}).local_model_id is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mode", "cloud"),
        ("local_model_id", ""),
        ("local_model_id", 5),
        ("local_context", "12345"),
        ("local_context", 8192),
        ("api_provider", "gemini"),
        ("api_model", ""),
        ("api_model", 3),
        ("api_base_url", None),
        ("language", "zh"),
        ("theme", "blue"),
        ("accent", "red"),
        ("text_size", "huge"),
        ("spacing", "tight"),
        ("ask_before_delete", "yes"),
        ("ask_before_delete", 1),
    ],
)
def test_invalid_values_are_rejected_naming_the_field(store, field, value):
    with pytest.raises(ValueError, match=field):
        save_settings(store, {field: value})
    assert store.all_settings() == {}


def test_an_unknown_field_is_rejected(store):
    with pytest.raises(ValueError, match="volume"):
        save_settings(store, {"volume": 11})


def test_one_invalid_field_writes_nothing(store):
    with pytest.raises(ValueError, match="accent"):
        save_settings(store, {"theme": "dark", "accent": "red"})
    assert store.all_settings() == {}


def test_unknown_stored_keys_are_ignored(store):
    store.set_settings({"volume": 11, "theme": "dark"})
    settings = load_settings(store)
    assert settings.theme == "dark"
    assert settings == Settings(theme="dark")


def test_invalid_stored_values_fall_back_to_defaults(store):
    store.set_settings(
        {"mode": "cloud", "accent": "red", "ask_before_delete": "no", "theme": "dark"}
    )
    settings = load_settings(store)
    assert settings.mode == "local"
    assert settings.accent == "green"
    assert settings.ask_before_delete is True
    assert settings.theme == "dark"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://api.example.com",
        "https://api.example.com/v1/",
        "http://localhost:11434/v1",
        "http://127.0.0.1:1234",
    ],
)
def test_valid_api_base_urls_are_accepted(store, url):
    assert save_settings(store, {"api_base_url": url}).api_base_url == url


@pytest.mark.parametrize(
    "url",
    [
        "api.example.com",
        "ftp://api.example.com",
        "file:///C:/models",
        "https://",
        "https:///v1",
        "https://user:pass@api.example.com/v1",
        "https://user@api.example.com",
        "https://api.example.com/v1?key=abc",
        "https://api.example.com/v1?",
        "https://api.example.com/v1#frag",
        "https://api.example.com:notaport",
        "  ",
        " https://api.example.com",
        "https://api.example.com ",
        "https://api.example.com\n",
    ],
)
def test_invalid_api_base_urls_are_rejected(store, url):
    with pytest.raises(ValueError, match="api_base_url"):
        save_settings(store, {"api_base_url": url})
    assert store.all_settings() == {}


LINK = {"path": "D:\\Models\\a.gguf", "size": 5, "sha256": "a" * 64}


def test_linked_models_default_to_empty_and_load_as_a_tuple(store):
    assert load_settings(store).linked_models == ()
    save_settings(store, {"linked_models": [LINK]})  # a list, as JSON gives it back
    assert load_settings(store).linked_models == (LINK,)


@pytest.mark.parametrize(
    "value",
    [
        "D:\\a.gguf",
        [LINK, "x"],
        [{**LINK, "path": "relative.gguf"}],
        [{**LINK, "path": 5}],
        [{**LINK, "size": -1}],
        [{**LINK, "size": True}],
        [{**LINK, "sha256": "A" * 64}],
        [{**LINK, "sha256": "abc"}],
        [{**LINK, "extra": 1}],
        [{"path": LINK["path"], "size": 5}],
    ],
)
def test_bad_linked_models_are_refused(store, value):
    with pytest.raises(ValueError, match="linked_models"):
        save_settings(store, {"linked_models": value})


def test_mcp_enabled_must_be_a_bool(store):
    with pytest.raises(ValueError, match="mcp_enabled"):
        save_settings(store, {"mcp_enabled": "yes"})
    assert load_settings(store).mcp_enabled is False
