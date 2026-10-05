import keyring
import pytest
from fakes import MemoryKeyring

from tamra import secrets


@pytest.fixture
def backend():
    previous = keyring.get_keyring()
    memory = MemoryKeyring()
    keyring.set_keyring(memory)
    yield memory
    keyring.set_keyring(previous)


def test_a_key_round_trips_under_the_tamra_service(backend):
    assert secrets.get_api_key("anthropic") is None
    secrets.set_api_key("anthropic", "test-key")
    assert secrets.get_api_key("anthropic") == "test-key"
    assert backend.store == {("Tamra", "anthropic"): "test-key"}


def test_providers_are_stored_separately(backend):
    secrets.set_api_key("anthropic", "test-key-a")
    secrets.set_api_key("openai", "test-key-b")
    assert secrets.get_api_key("anthropic") == "test-key-a"
    assert secrets.get_api_key("openai") == "test-key-b"


def test_setting_again_replaces_the_key(backend):
    secrets.set_api_key("anthropic", "test-key-1")
    secrets.set_api_key("anthropic", "test-key-2")
    assert secrets.get_api_key("anthropic") == "test-key-2"


def test_delete_removes_the_key_and_is_safe_when_there_is_none(backend):
    secrets.set_api_key("anthropic", "test-key")
    secrets.delete_api_key("anthropic")
    assert secrets.get_api_key("anthropic") is None
    secrets.delete_api_key("anthropic")  # nothing stored: no error


def test_an_empty_key_is_refused(backend):
    with pytest.raises(ValueError):
        secrets.set_api_key("anthropic", "")
    assert backend.store == {}


def test_the_fixture_backend_is_active(backend):
    assert keyring.get_keyring() is backend
