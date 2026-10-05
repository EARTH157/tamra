"""The models Tamra knows about: pinned files, sizes, sha256, and where to get them."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from tamra.download import sha256_file


@dataclass(frozen=True)
class ModelFile:
    path: str  # relative to the models directory, with forward slashes
    size: int
    sha256: str
    url: str


@dataclass(frozen=True)
class ModelEntry:
    id: str
    role: str  # "llm" | "embedding"
    name: str
    files: tuple[ModelFile, ...]
    tier: str | None = None  # llm only: "small" | "medium" | "large"
    license: str = ""
    languages: tuple[str, ...] = ()
    context_length: int = 0
    thinking: bool = False
    min_vram_gb: int = 0

    @property
    def main_file(self) -> ModelFile:
        return self.files[0]


class Catalog:
    def __init__(self, models: Iterable[ModelEntry]):
        self.models: tuple[ModelEntry, ...] = tuple(models)

    def llms(self) -> list[ModelEntry]:
        return [m for m in self.models if m.role == "llm"]

    def embedding(self) -> ModelEntry:
        for m in self.models:
            if m.role == "embedding":
                return m
        raise KeyError("embedding")

    def get(self, model_id: str) -> ModelEntry:
        for m in self.models:
            if m.id == model_id:
                return m
        raise KeyError(model_id)

    def installed(self, models_dir: Path) -> dict[str, Path]:
        """Entries whose every file is present with the pinned size, keyed by id.

        The value is the path of the entry's main file. The sha256 is not checked here;
        see verify().
        """
        found: dict[str, Path] = {}
        for m in self.models:
            if all(_has_size(models_dir / f.path, f.size) for f in m.files):
                found[m.id] = models_dir / m.main_file.path
        return found

    def uncatalogued(self, models_dir: Path) -> list[Path]:
        """*.gguf files directly in models_dir that no catalog entry names."""
        known = {Path(f.path).name.lower() for m in self.models for f in m.files}
        try:
            files = [p for p in models_dir.iterdir() if p.is_file()]
        except OSError:
            return []
        return sorted(
            p for p in files if p.suffix.lower() == ".gguf" and p.name.lower() not in known
        )

    def verify(self, model_id: str, path: Path) -> bool:
        """True when path's sha256 equals the pinned one.

        A single-file entry checks any path, so an imported file may have any name. For an
        entry with several files, path is matched to a file by its name (KeyError if none).
        """
        entry = self.get(model_id)
        if len(entry.files) == 1:
            pinned = entry.files[0]
        else:
            matches = [f for f in entry.files if Path(f.path).name == path.name]
            if not matches:
                raise KeyError(f"{model_id}: no file named {path.name}")
            pinned = matches[0]
        return sha256_file(path) == pinned.sha256


def _has_size(path: Path, size: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size == size
    except OSError:
        return False


def load_catalog() -> Catalog:
    """Load the catalog.json shipped inside the package."""
    text = resources.files("tamra.models").joinpath("catalog.json").read_text(encoding="utf-8")
    entries = []
    for raw in json.loads(text)["models"]:
        raw = dict(raw)
        raw["files"] = tuple(ModelFile(**f) for f in raw["files"])
        if "languages" in raw:
            raw["languages"] = tuple(raw["languages"])
        entries.append(ModelEntry(**raw))
    return Catalog(entries)
