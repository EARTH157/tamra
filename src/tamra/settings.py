"""Persisted app settings: typed, validated, and stored through the Store as JSON values."""

import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict
from urllib.parse import urlparse

from tamra.store import Store

Mode = Literal["local", "api"]
ApiProvider = Literal["anthropic", "openai"]
Language = Literal["en", "th"]
Theme = Literal["light", "dark", "system"]
Accent = Literal["green", "blue", "orange", "purple", "slate"]
TextSize = Literal["small", "default", "large"]
Spacing = Literal["comfortable", "compact"]

_SHA256 = re.compile(r"[0-9a-f]{64}")


class LinkedModel(TypedDict):
    """A model file used where it is (not copied): its path, and its size and sha256 when linked."""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Settings:
    mode: Mode = "local"
    # A catalog id, "import:<file name>" for an uncatalogued file in Tamra's models folder, or
    # "file:<absolute path>" for an uncatalogued linked or scanned file.
    local_model_id: str | None = None
    # Files the user imported without copying. Only the import and unlink routes change it.
    linked_models: tuple[LinkedModel, ...] = ()
    api_provider: ApiProvider = "anthropic"
    api_model: str = "claude-sonnet-5-5"
    api_base_url: str = ""
    language: Language = "en"
    theme: Theme = "light"
    accent: Accent = "green"
    text_size: TextSize = "default"
    spacing: Spacing = "comfortable"
    ask_before_delete: bool = True
    # AI apps (MCP) may search every project while Tamra is open; checked on every MCP request.
    mcp_enabled: bool = False


_CHOICES: dict[str, tuple[str, ...]] = {
    "mode": ("local", "api"),
    "api_provider": ("anthropic", "openai"),
    "language": ("en", "th"),
    "theme": ("light", "dark", "system"),
    "accent": ("green", "blue", "orange", "purple", "slate"),
    "text_size": ("small", "default", "large"),
    "spacing": ("comfortable", "compact"),
}


def _is_valid_base_url(value: object) -> bool:
    """Empty (the provider's default), or an http(s) URL with a host and no credentials, query
    or fragment."""
    if not isinstance(value, str):
        return False
    if value == "":
        return True
    if value != value.strip():
        return False
    try:
        parts = urlparse(value)
        host = parts.hostname
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return False
    return (
        parts.scheme in ("http", "https")
        and bool(host)
        and "@" not in parts.netloc  # user:pass@host
        and not parts.query
        and not parts.fragment
        and "?" not in value
        and "#" not in value
    )


def _is_link(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"path", "size", "sha256"}:
        return False
    path, size, sha = value["path"], value["size"], value["sha256"]
    # No .gguf check: a file that matches the catalog by sha256 may have any name.
    return (
        isinstance(path, str)
        and Path(path).is_absolute()
        and isinstance(size, int)
        and not isinstance(size, bool)
        and size >= 0
        and isinstance(sha, str)
        and _SHA256.fullmatch(sha) is not None
    )


def _is_valid(field: str, value: object) -> bool:
    if field in _CHOICES:
        return isinstance(value, str) and value in _CHOICES[field]
    if field in ("ask_before_delete", "mcp_enabled"):
        return isinstance(value, bool)
    if field == "local_model_id":
        return value is None or (isinstance(value, str) and value != "")
    if field == "api_model":
        return isinstance(value, str) and value != ""
    if field == "api_base_url":
        return _is_valid_base_url(value)
    if field == "linked_models":
        return isinstance(value, (list, tuple)) and all(_is_link(v) for v in value)
    return isinstance(value, str)


_FIELDS = tuple(f.name for f in dataclasses.fields(Settings))


def load_settings(store: Store) -> Settings:
    """The stored settings; unknown keys are ignored and invalid values fall back to defaults."""
    stored = store.all_settings()
    valid = {k: v for k, v in stored.items() if k in _FIELDS and _is_valid(k, v)}
    if "linked_models" in valid:
        valid["linked_models"] = tuple(valid["linked_models"])  # stored as a JSON list
    return Settings(**valid)


def save_settings(store: Store, changes: dict[str, object]) -> Settings:
    """Validate every change, then store them all at once (nothing is written if any is invalid).

    Raises ValueError naming the first unknown or invalid field.
    """
    for field, value in changes.items():
        if field not in _FIELDS:
            raise ValueError(f"unknown setting: {field}")
        if not _is_valid(field, value):
            raise ValueError(f"invalid value for setting {field}: {value!r}")
    store.set_settings(changes)
    return load_settings(store)
