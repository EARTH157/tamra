"""GGUF chat models that other local AI tools downloaded: Ollama, LM Studio, the Hugging Face cache.

Only reads: nothing here writes, moves or deletes another tool's files.
"""

import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MODEL_LAYER = "application/vnd.ollama.image.model"
_SPLIT = re.compile(r"-(\d{5})-of-\d{5}\.gguf$", re.IGNORECASE)
_HEX64 = re.compile(r"[0-9a-f]{64}")
_LMSTUDIO_DEPTH = 3  # models/<publisher>/<repo>/<file>.gguf, with one folder to spare


@dataclass(frozen=True)
class ExternalModel:
    source: str  # "ollama" | "lmstudio" | "huggingface"
    name: str  # "qwen3:8b" for Ollama, else the file stem
    path: Path  # the GGUF file itself
    sha256: str | None  # only when the store names the file by its hash (Ollama, HF blobs)


def scan(home: Path, env: Mapping[str, str]) -> list[ExternalModel]:
    """Every chat model the three tools hold. A store that is missing or broken adds nothing."""
    return [*_ollama(home, env), *_lmstudio(home), *_huggingface(home, env)]


def _is_gguf(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(4) == b"GGUF"
    except OSError:
        return False


def _wanted(path: Path) -> bool:
    """A model file to list: a .gguf that is not a vision projector or a later split part."""
    name = path.name.lower()
    if not name.endswith(".gguf") or name.startswith("mmproj"):
        return False
    split = _SPLIT.search(name)
    return split is None or split.group(1) == "00001"


def _files(root: Path) -> list[Path]:
    try:
        return sorted(p for p in root.rglob("*") if p.is_file())
    except OSError as e:
        log.debug("cannot list %s: %s", root, e)
        return []


# --- Ollama ---


def _blob(root: Path, digest: object) -> Path:
    """The blob a "sha256:<hex>" digest names (ValueError for any other digest)."""
    algorithm, _, hex_digest = str(digest).partition(":")
    if algorithm != "sha256" or not _HEX64.fullmatch(hex_digest):
        raise ValueError(f"unexpected digest {digest!r}")
    return root / "blobs" / f"sha256-{hex_digest}"


def _for_llama_cpp(doc: dict[str, Any]) -> bool:
    # Manifests before Ollama's multi-runner format name no runner: those are llama.cpp GGUFs.
    return doc.get("runner", "llamacpp") == "llamacpp" and doc.get("format", "gguf") == "gguf"


def _model_blob(root: Path, manifest: Path) -> Path | None:
    """The GGUF a manifest's model layer names, or None when the model is not for llama.cpp.

    A manifest list (manifests-v2) is followed to its llama.cpp entry. A vision model's projector
    layer is ignored: llama-server runs the text model without it. Raises on a broken manifest.
    """
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    if "manifests" in doc:
        entry = next((m for m in doc["manifests"] if _for_llama_cpp(m)), None)
        if entry is None:
            return None
        doc = json.loads(_blob(root, entry["digest"]).read_text(encoding="utf-8"))
    if not _for_llama_cpp(doc):
        return None
    digest = next(layer["digest"] for layer in doc["layers"] if layer["mediaType"] == _MODEL_LAYER)
    return _blob(root, digest)


def _ollama(home: Path, env: Mapping[str, str]) -> list[ExternalModel]:
    configured = env.get("OLLAMA_MODELS")
    root = Path(configured) if configured else home / ".ollama" / "models"
    names: dict[Path, str] = {}  # blob -> model:tag
    for manifest in [*_files(root / "manifests"), *_files(root / "manifests-v2")]:
        try:
            blob = _model_blob(root, manifest)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration) as e:
            log.debug("skipping the Ollama manifest %s: %s", manifest, e)
            continue
        if blob is None:
            continue
        name = f"{manifest.parent.name}:{manifest.name}"
        if names.get(blob, ":latest").endswith(":latest"):  # prefer a tag that says the size
            names[blob] = name
    return [
        ExternalModel("ollama", name, blob, blob.name.removeprefix("sha256-"))
        for blob, name in names.items()
        if _is_gguf(blob)
    ]


# --- LM Studio ---


def _gguf_under(root: Path, depth: int) -> list[Path]:
    """Model files in root and at most `depth` folders below it."""
    found: list[Path] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return found
    for entry in entries:
        try:
            if entry.is_dir():
                if depth > 0:
                    found += _gguf_under(entry, depth - 1)
            elif _wanted(entry) and _is_gguf(entry):
                found.append(entry)
        except OSError:
            continue
    return found


def _lmstudio_roots(home: Path) -> list[Path]:
    """The default models folders, the one a portable install points to, and the downloads
    folder set in LM Studio's settings."""
    roots = [home / ".lmstudio" / "models", home / ".cache" / "lm-studio" / "models"]
    try:
        pointed = (home / ".lmstudio-home-pointer").read_text(encoding="utf-8").strip()
    except OSError:
        pointed = ""
    if pointed:
        roots.append(Path(pointed) / "models")
    try:
        settings = json.loads((home / ".lmstudio" / "settings.json").read_text(encoding="utf-8"))
        downloads = settings.get("downloadsFolder")
    except (OSError, ValueError, AttributeError):
        downloads = None
    if isinstance(downloads, str) and downloads:
        roots.append(Path(downloads))
    return roots


def _lmstudio(home: Path) -> list[ExternalModel]:
    seen: set[str] = set()
    found: list[ExternalModel] = []
    for root in _lmstudio_roots(home):
        for path in _gguf_under(root, _LMSTUDIO_DEPTH):
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:  # the pointer and the setting often name the default folder
                seen.add(key)
                found.append(ExternalModel("lmstudio", path.stem, path, None))
    return found


# --- Hugging Face ---


def _hub(home: Path, env: Mapping[str, str]) -> Path:
    if env.get("HF_HUB_CACHE"):
        return Path(env["HF_HUB_CACHE"])
    if env.get("HF_HOME"):
        return Path(env["HF_HOME"]) / "hub"
    return home / ".cache" / "huggingface" / "hub"


def _blob_hash(path: Path) -> str | None:
    """The sha256 of a snapshot file that links to blobs/<sha256>; None for a plain file."""
    try:
        if not path.is_symlink():
            return None
        target = path.resolve()
    except OSError:
        return None
    if target.parent.name == "blobs" and _HEX64.fullmatch(target.name):
        return target.name
    return None


def _huggingface(home: Path, env: Mapping[str, str]) -> list[ExternalModel]:
    try:
        repos = sorted(_hub(home, env).glob("models--*"))
    except OSError:
        return []
    found: list[ExternalModel] = []
    for repo in repos:
        for path in _files(repo / "snapshots"):
            if _wanted(path) and _is_gguf(path):
                found.append(ExternalModel("huggingface", path.stem, path, _blob_hash(path)))
    return found
