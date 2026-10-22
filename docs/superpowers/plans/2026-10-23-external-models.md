# External Local Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tamra runs GGUF chat models from Ollama, LM Studio and the Hugging Face cache where they already are, and Import remembers an LLM file's path instead of copying it.

**Architecture:**
- A read-only scanner, `tamra/models/external.py`, lists GGUF files in the three tools' stores.
- `Core` combines four sources into one ordered list of `LocalChoice`s: Tamra's `models\` folder, the files the user linked (a server-owned setting, `linked_models`), and the scanned files. `resolve_local_model`, the model labels and `GET /api/models` all read that one list.
- On import, `Core` links an LLM file when it is on a fixed drive and copies it when it is on a removable drive. The embedding and OCR models are still always copied.

**Tech Stack:** Python 3.12 (uv, pytest, ruff), FastAPI, `ctypes` (standard library) for the drive type, React + TypeScript (Vitest).

**Spec:** `docs/superpowers/specs/2026-10-23-external-models-design.md`

## Global Constraints

- Every dependency must be Apache-2.0-compatible. This plan adds no new dependency.
- The embedding model is always local and always the fixed, pinned catalog model. Embedding and OCR files are always copied into `models\` and must match their sha256.
- The local LLM is llama.cpp's `llama-server.exe` only. Do not add Python llama.cpp bindings.
- User data lives under `%LOCALAPPDATA%\Tamra\` (override: `TAMRA_DATA_DIR`). Tamra never writes to, moves or deletes another tool's files, and never modifies the user's document folders.
- Code, comments, docs and commit messages are in English. Every UI string is added to both `ui/src/strings/en.ts` and `ui/src/strings/th.ts`. `th` is a `Record<TranslationKey, string>`, so a missing key fails the type check.
- Commands: `uv run pytest`, `uv run ruff check`, `uv run ruff format`, `npm --prefix ui test`, `npm --prefix ui run build`. Run `uv run ruff format` on touched Python files before each commit.
- End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Tests must never scan the real machine's Ollama, LM Studio or Hugging Face folders. Task 6 adds an autouse fixture that guarantees this.
- **Branch.** Start from the commit that holds the spec (`6853b8b`, on `logo-c`). Do not include the uncommitted M7/M8 working-tree changes in these commits. Use a worktree (superpowers:using-git-worktrees) or ask the user first.

**Deliberate deviations from the spec** (Task 11 records both in the spec):
- When a file is copied rather than linked, the import note keeps today's wording ("Imported {file}. …"). The spec's "Copied {file} from the removable drive" message would need the server to tell a removable-drive copy apart from an embedding copy, and nothing else needs that.
- A `linked_models` path need not end in `.gguf`. A file that matches the catalog by sha256 may have any name; `catalog.verify` already allows that for imports. Uncatalogued files are still refused unless they are `.gguf`, by `import_file`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/tamra/models/external.py` (new) | Scan Ollama, LM Studio and HF stores and return `ExternalModel`s. Read-only. |
| `src/tamra/drives.py` (new) | `is_removable(path)`: whether a path is on a removable or CD-ROM drive |
| `src/tamra/settings.py` | New `linked_models` setting: validation, and normalisation to a tuple |
| `src/tamra/llm/base.py`, `src/tamra/llm/llama_server.py` | `model_unsupported` reason, and detection of "unknown model architecture" |
| `src/tamra/models/manager.py` | `import_file(path, *, link_llm)`, which validates and hashes a file without copying it |
| `src/tamra/core.py` | Scan cache, `LocalChoice` list, resolution, labels, `import_model`, `unlink_model` |
| `src/tamra/server.py` | Import through `Core`, the `unlink` route, `source`/`path` in `/api/models`, `linked_models` kept out of `PUT`/`GET /api/settings` |
| `tests/conftest.py` | Autouse fixture: no real scan in tests |
| `ui/src/types.ts`, `ui/src/test-models.ts`, `ui/src/models.ts` | `ModelSource`, the new fields, fixtures, `SOURCE_KEYS` |
| `ui/src/SettingsLocal.tsx`, `ui/src/ModelMenu.tsx`, `ui/src/ChatView.tsx` | Source labels, "Remove from list", import notes, the `model_unsupported` notice |
| `ui/src/strings/en.ts`, `ui/src/strings/th.ts` | New strings |
| `docs/superpowers/specs/2026-10-02-tamra-design.md` | The Import bullet |

---

### Task 1: The external-model scanner

**Files:**
- Create: `src/tamra/models/external.py`
- Test: `tests/test_external.py`

**Interfaces:**
- Produces:
  - `ExternalModel(source: str, name: str, path: Path, sha256: str | None)` is a frozen dataclass. `source` is one of `"ollama"`, `"lmstudio"`, `"huggingface"`.
  - `scan(home: Path, env: Mapping[str, str]) -> list[ExternalModel]` returns Ollama models first, then LM Studio, then Hugging Face, each in sorted path order.

- [ ] **Step 1: Write the failing tests**

```python
"""The scanner for GGUF models that other local AI tools downloaded."""

import json
import os

import pytest

from tamra.models.external import ExternalModel, scan

HEX_A = "a" * 64
HEX_B = "b" * 64
GGUF = b"GGUF" + b"\0" * 12


def ollama_model(root, name, tag, digest_hex, *, projector=False, blob=GGUF):
    """One manifest under root/manifests/registry.ollama.ai/library/<name>/<tag>, and its blob."""
    manifest = root / "manifests" / "registry.ollama.ai" / "library" / name / tag
    manifest.parent.mkdir(parents=True, exist_ok=True)
    layers = [{"mediaType": "application/vnd.ollama.image.model", "digest": f"sha256:{digest_hex}"}]
    if projector:
        layers.append({"mediaType": "application/vnd.ollama.image.projector", "digest": "sha256:" + "c" * 64})
    manifest.write_text(json.dumps({"schemaVersion": 2, "layers": layers}), encoding="utf-8")
    (root / "blobs").mkdir(parents=True, exist_ok=True)
    if blob is not None:
        (root / "blobs" / f"sha256-{digest_hex}").write_bytes(blob)
    return root / "blobs" / f"sha256-{digest_hex}"


def gguf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(GGUF)
    return path


def test_nothing_installed_gives_nothing(tmp_path):
    assert scan(tmp_path, {}) == []


def test_an_ollama_model_is_its_blob_named_by_tag(tmp_path):
    root = tmp_path / ".ollama" / "models"
    blob = ollama_model(root, "qwen3", "8b", HEX_A)
    assert scan(tmp_path, {}) == [ExternalModel("ollama", "qwen3:8b", blob, HEX_A)]


def test_ollama_models_honour_OLLAMA_MODELS(tmp_path):
    root = tmp_path / "elsewhere"
    blob = ollama_model(root, "qwen3", "8b", HEX_A)
    assert scan(tmp_path, {"OLLAMA_MODELS": str(root)}) == [
        ExternalModel("ollama", "qwen3:8b", blob, HEX_A)
    ]


def test_tags_that_share_a_blob_are_listed_once_under_the_tag_that_is_not_latest(tmp_path):
    root = tmp_path / ".ollama" / "models"
    ollama_model(root, "qwen3", "latest", HEX_A)
    blob = ollama_model(root, "qwen3", "8b", HEX_A)
    assert scan(tmp_path, {}) == [ExternalModel("ollama", "qwen3:8b", blob, HEX_A)]


def test_vision_models_bad_manifests_and_non_gguf_blobs_are_skipped(tmp_path):
    root = tmp_path / ".ollama" / "models"
    ollama_model(root, "llava", "7b", HEX_A, projector=True)
    ollama_model(root, "broken", "1b", HEX_B, blob=b"not a model")
    bad = root / "manifests" / "registry.ollama.ai" / "library" / "bad" / "1b"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json", encoding="utf-8")
    missing = ollama_model(root, "gone", "1b", "d" * 64, blob=None)
    assert not missing.exists()
    assert scan(tmp_path, {}) == []


@pytest.mark.parametrize(
    "folder", [(".lmstudio", "models"), (".cache", "lm-studio", "models"), ("portable", "models")]
)
def test_lm_studio_models_are_found_in_every_known_folder(tmp_path, folder):
    (tmp_path / ".lmstudio-home-pointer").write_text(str(tmp_path / "portable"), encoding="utf-8")
    model = gguf(tmp_path.joinpath(*folder, "lmstudio-community", "Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf"))
    assert scan(tmp_path, {}) == [ExternalModel("lmstudio", "Qwen3-8B-Q4_K_M", model, None)]


def test_projectors_later_split_parts_and_non_gguf_files_are_skipped(tmp_path):
    repo = tmp_path / ".lmstudio" / "models" / "pub" / "repo"
    gguf(repo / "mmproj-model-f16.gguf")
    first = gguf(repo / "Big-Q4_K_M-00001-of-00002.gguf")
    gguf(repo / "Big-Q4_K_M-00002-of-00002.gguf")
    (repo / "fake.gguf").write_bytes(b"nope")
    (repo / "README.md").write_text("hi", encoding="utf-8")
    assert scan(tmp_path, {}) == [ExternalModel("lmstudio", first.stem, first, None)]


def test_lm_studio_files_deeper_than_three_folders_are_not_listed(tmp_path):
    gguf(tmp_path / ".lmstudio" / "models" / "a" / "b" / "c" / "d" / "deep.gguf")
    assert scan(tmp_path, {}) == []


def test_a_plain_hugging_face_snapshot_file_has_no_known_hash(tmp_path):
    snap = tmp_path / ".cache" / "huggingface" / "hub" / "models--Qwen--Qwen3-8B-GGUF" / "snapshots" / "rev1"
    model = gguf(snap / "Qwen3-8B-Q4_K_M.gguf")
    assert scan(tmp_path, {}) == [ExternalModel("huggingface", "Qwen3-8B-Q4_K_M", model, None)]


def test_hugging_face_honours_HF_HUB_CACHE_then_HF_HOME(tmp_path):
    hub = tmp_path / "hub-cache"
    model = gguf(hub / "models--x--y" / "snapshots" / "r" / "y.gguf")
    assert scan(tmp_path, {"HF_HUB_CACHE": str(hub)})[0].path == model
    home_hub = tmp_path / "hf-home" / "hub"
    other = gguf(home_hub / "models--x--z" / "snapshots" / "r" / "z.gguf")
    assert scan(tmp_path, {"HF_HOME": str(tmp_path / "hf-home")})[0].path == other


def test_a_symlinked_hugging_face_file_takes_its_hash_from_the_blob_name(tmp_path):
    repo = tmp_path / ".cache" / "huggingface" / "hub" / "models--x--y"
    blob = repo / "blobs" / HEX_A
    gguf(blob)
    link = repo / "snapshots" / "r" / "y.gguf"
    link.parent.mkdir(parents=True)
    try:
        os.symlink(blob, link)
    except OSError:
        pytest.skip("this OS account cannot create symlinks")
    assert scan(tmp_path, {}) == [ExternalModel("huggingface", "y", link, HEX_A)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_external.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tamra.models.external'`

- [ ] **Step 3: Write the scanner**

```python
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

log = logging.getLogger(__name__)

_MODEL_LAYER = "application/vnd.ollama.image.model"
_PROJECTOR_LAYER = "application/vnd.ollama.image.projector"
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


def _ollama(home: Path, env: Mapping[str, str]) -> list[ExternalModel]:
    configured = env.get("OLLAMA_MODELS")
    root = Path(configured) if configured else home / ".ollama" / "models"
    by_blob: dict[Path, tuple[str, str]] = {}  # blob -> (name, sha256)
    for manifest in _files(root / "manifests"):
        try:
            layers = json.loads(manifest.read_text(encoding="utf-8"))["layers"]
            types = {layer["mediaType"] for layer in layers}
            digest = next(layer["digest"] for layer in layers if layer["mediaType"] == _MODEL_LAYER)
        except (OSError, ValueError, KeyError, TypeError, StopIteration) as e:
            log.debug("skipping the Ollama manifest %s: %s", manifest, e)
            continue
        if _PROJECTOR_LAYER in types:
            continue  # a vision model: llama-server would need its projector too
        algorithm, _, hex_digest = str(digest).partition(":")
        if algorithm != "sha256" or not _HEX64.fullmatch(hex_digest):
            continue
        blob = root / "blobs" / f"sha256-{hex_digest}"
        name = f"{manifest.parent.name}:{manifest.name}"
        previous = by_blob.get(blob)
        if previous is None or previous[0].endswith(":latest"):
            by_blob[blob] = (name, hex_digest)
    return [
        ExternalModel("ollama", name, blob, sha)
        for blob, (name, sha) in by_blob.items()
        if _is_gguf(blob)
    ]


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


def _lmstudio(home: Path) -> list[ExternalModel]:
    roots = [home / ".lmstudio" / "models", home / ".cache" / "lm-studio" / "models"]
    try:
        pointed = (home / ".lmstudio-home-pointer").read_text(encoding="utf-8").strip()
    except OSError:
        pointed = ""
    if pointed:
        roots.append(Path(pointed) / "models")
    seen: set[str] = set()
    found: list[ExternalModel] = []
    for root in roots:
        for path in _gguf_under(root, _LMSTUDIO_DEPTH):
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:  # the home pointer may name ~/.lmstudio itself
                seen.add(key)
                found.append(ExternalModel("lmstudio", path.stem, path, None))
    return found


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_external.py -v`
Expected: all PASS (the symlink test may be SKIPPED).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src/tamra/models/external.py tests/test_external.py
uv run ruff check src/tamra/models/external.py tests/test_external.py
git add src/tamra/models/external.py tests/test_external.py
git commit -m "feat: find GGUF models in Ollama, LM Studio and the Hugging Face cache

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The `linked_models` setting

**Files:**
- Modify: `src/tamra/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces:
  - `LinkedModel` is a `TypedDict` with `path: str` (absolute, any extension), `size: int` and `sha256: str` (64 lowercase hex characters).
  - `Settings.linked_models: tuple[LinkedModel, ...] = ()`.
  - `load_settings` always returns a tuple for it. JSON storage gives back a list, and `load_settings` converts it.

- [ ] **Step 1: Write the failing tests**

In `tests/test_settings.py`, add `"linked_models"` to `test_every_field_round_trips`. The `changes` dict gains:

```python
        "linked_models": ({"path": "D:\\Models\\a.gguf", "size": 5, "sha256": "a" * 64},),
```

Then append:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_settings.py -v`
Expected: FAIL. The new tests fail with `unknown setting: linked_models`, and `test_every_field_round_trips` fails on its field-set assertion.

- [ ] **Step 3: Add the setting**

In `src/tamra/settings.py`, add `import re` and `from pathlib import Path` to the imports, and add `TypedDict` to the `typing` import. Then add, above `class Settings`:

```python
_SHA256 = re.compile(r"[0-9a-f]{64}")


class LinkedModel(TypedDict):
    """A model file used where it is (not copied): its path, and its size and sha256 when linked."""

    path: str
    size: int
    sha256: str
```

Add the field after `local_model_id`, and update that field's comment:

```python
    # A catalog id, "import:<file name>" for an uncatalogued file in Tamra's models folder, or
    # "file:<absolute path>" for an uncatalogued linked or scanned file.
    local_model_id: str | None = None
    # Files the user imported without copying. Only the import and unlink routes change it.
    linked_models: tuple[LinkedModel, ...] = ()
```

Add a validator below `_is_valid_base_url`:

```python
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
```

In `_is_valid`, add before the final `return`:

```python
    if field == "linked_models":
        return isinstance(value, (list, tuple)) and all(_is_link(v) for v in value)
```

In `load_settings`, convert the JSON list before building the settings:

```python
    valid = {k: v for k, v in stored.items() if k in _FIELDS and _is_valid(k, v)}
    if "linked_models" in valid:
        valid["linked_models"] = tuple(valid["linked_models"])  # stored as a JSON list
    return Settings(**valid)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_settings.py tests/test_store_settings.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src/tamra/settings.py tests/test_settings.py
uv run ruff check src/tamra/settings.py tests/test_settings.py
git add src/tamra/settings.py tests/test_settings.py
git commit -m "feat: add the linked_models setting for model files used in place

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The removable-drive check

**Files:**
- Create: `src/tamra/drives.py`
- Test: `tests/test_drives.py`

**Interfaces:**
- Produces:
  - `is_removable(path: Path) -> bool` is True only when Windows reports `DRIVE_REMOVABLE` (2) or `DRIVE_CDROM` (5).
  - It returns False on any failure, and on other operating systems.

- [ ] **Step 1: Write the failing tests**

```python
import sys

import pytest

import tamra.drives as drives
from tamra.drives import is_removable


@pytest.mark.parametrize(("kind", "removable"), [(2, True), (5, True), (3, False), (4, False), (0, False)])
def test_only_removable_and_cd_drives_count_as_removable(tmp_path, monkeypatch, kind, removable):
    monkeypatch.setattr(drives, "_drive_type", lambda root: kind)
    assert is_removable(tmp_path / "m.gguf") is removable


def test_the_drive_root_is_what_windows_is_asked_about(monkeypatch):
    asked = []
    monkeypatch.setattr(drives, "_drive_type", lambda root: asked.append(root) or 3)
    is_removable(drives.Path("E:\\Models\\m.gguf"))
    is_removable(drives.Path("\\\\host\\share\\m.gguf"))
    assert asked == ["E:\\", "\\\\host\\share\\"]


def test_a_failing_check_counts_as_a_fixed_drive(tmp_path, monkeypatch):
    def broken(root):
        raise OSError("no")

    monkeypatch.setattr(drives, "_drive_type", broken)
    assert is_removable(tmp_path / "m.gguf") is False


@pytest.mark.skipif(sys.platform != "win32", reason="asks Windows")
def test_the_temp_folder_is_not_on_a_removable_drive(tmp_path):
    assert is_removable(tmp_path) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_drives.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tamra.drives'`

- [ ] **Step 3: Write the helper**

```python
"""Which kind of drive a path is on, so a model on a USB stick is copied rather than linked."""

import ctypes
import os
from pathlib import Path

_REMOVABLE, _CDROM = 2, 5  # GetDriveTypeW results


def _drive_type(root: str) -> int:
    return ctypes.windll.kernel32.GetDriveTypeW(root)  # AttributeError off Windows


def is_removable(path: Path) -> bool:
    """True for a path on a removable or CD-ROM drive, which may be gone the next time Tamra
    starts. False when Windows cannot say, or off Windows: the file is then used in place."""
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    try:
        return _drive_type(root) in (_REMOVABLE, _CDROM)
    except (AttributeError, OSError):
        return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_drives.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src/tamra/drives.py tests/test_drives.py
uv run ruff check src/tamra/drives.py tests/test_drives.py
git add src/tamra/drives.py tests/test_drives.py
git commit -m "feat: tell removable drives apart so their models are copied

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `model_unsupported` when llama.cpp does not know the model

**Files:**
- Modify: `src/tamra/llm/base.py:26`
- Modify: `src/tamra/llm/llama_server.py` (the error class, `start`, `gpu_offload`)
- Test: `tests/test_llama_server.py`

**Interfaces:**
- Produces:
  - `ErrorReason` gains `"model_unsupported"`.
  - `LlamaServerError(message, reason="other")` has a `.reason` attribute. `AnswerService` already reads it with `getattr(e, "reason", "other")` (`src/tamra/answer.py:372-374`), so the reason reaches the UI with no change there.

- [ ] **Step 1: Write the failing tests**

In `tests/test_llama_server.py`, add a mode to `_STUB_SERVER`, right after the `if mode == "fail":` branch:

```python
elif mode == "unknown_arch":
    print("llama_model_load: error loading model: error loading model architecture: "
          "unknown model architecture: 'qwen3vl'", flush=True)
    sys.exit(1)
```

Append these tests:

```python
def test_an_unknown_model_architecture_is_model_unsupported_and_skips_the_cpu_try(
    tmp_path, spawned
):
    stub_script = tmp_path / "stub.py"
    stub_script.write_text(_STUB_SERVER)
    srv = _StubLlamaServer(
        stub_script,
        Path("dummy.gguf"),
        tmp_path / "log.txt",
        modes={"gpu": "unknown_arch", "cpu": "ok"},
    )
    with pytest.raises(LlamaServerError) as e:
        srv.start(timeout=5)
    assert e.value.reason == "model_unsupported"
    assert "qwen3vl" in str(e.value)
    assert len(spawned) == 1  # the CPU would fail the same way: it is not tried


def test_any_other_start_failure_keeps_the_reason_other(tmp_path, spawned):
    stub_script = tmp_path / "stub.py"
    stub_script.write_text(_STUB_SERVER)
    srv = _StubLlamaServer(
        stub_script, Path("dummy.gguf"), tmp_path / "log.txt", modes={"gpu": "fail", "cpu": "fail"}
    )
    with pytest.raises(LlamaServerError) as e:
        srv.start(timeout=5)
    assert e.value.reason == "other"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_llama_server.py -v -k "unsupported or reason_other"`
Expected: FAIL with `AttributeError: 'LlamaServerError' object has no attribute 'reason'`

- [ ] **Step 3: Implement**

In `src/tamra/llm/base.py`:

```python
ErrorReason = Literal["offline", "auth", "quota", "model_missing", "model_unsupported", "other"]
```

In `src/tamra/llm/llama_server.py`, replace the error class:

```python
class LlamaServerError(Exception):
    """llama-server could not start. `reason` is a tamra.llm.base.ErrorReason."""

    def __init__(self, message: str, reason: str = "other"):
        super().__init__(message)
        self.reason = reason
```

Add it next to the other module patterns:

```python
_UNKNOWN_ARCH = re.compile(r"unknown model architecture: '([^']+)'")
```

Add this method to `LlamaServer`, and make `gpu_offload` use it. `parse_gpu_offload("")` returns None, which is what the old `except OSError` returned.

```python
    def _run_log(self) -> str:
        """This run's part of the log file; empty when it cannot be read."""
        try:
            with self.log_file.open("rb") as f:
                f.seek(self._log_start)
                return f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    @property
    def gpu_offload(self) -> bool | None:
        """True when layers were offloaded to the GPU, False when none were (CPU only), None when
        the log says nothing about it."""
        return parse_gpu_offload(self._run_log())
```

In `start`, right after the `self.stop()` that follows a failed `_wait_healthy`, stop retrying when the model type is unknown:

```python
                if self._wait_healthy(budget):
                    self.gpu_used = gpu
                    return self
                self.stop()
                unknown = _UNKNOWN_ARCH.search(self._run_log())
                if unknown:  # the CPU would fail the same way
                    raise LlamaServerError(
                        f"This version of llama.cpp cannot run models of type {unknown.group(1)}.",
                        "model_unsupported",
                    )
```

`self.stop()` closes the log handle before `_run_log` reads it. The `except BaseException: self.stop(); raise` wrapper calls `stop()` again, and `stop()` is safe to call twice.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_llama_server.py tests/test_runtime.py tests/test_answer.py -v`
Expected: PASS (tests marked `assets` are skipped).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src/tamra/llm/base.py src/tamra/llm/llama_server.py tests/test_llama_server.py
uv run ruff check src/tamra/llm tests/test_llama_server.py
git add src/tamra/llm/base.py src/tamra/llm/llama_server.py tests/test_llama_server.py
git commit -m "feat: report model_unsupported when llama.cpp does not know a model

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `ModelManager.import_file` can link instead of copy

**Files:**
- Modify: `src/tamra/models/manager.py:33-39` (`ImportResult`) and `:158-199` (`import_file`)
- Test: `tests/test_models_manager.py`

**Interfaces:**
- Produces:
  - `ImportResult` gains `linked: bool = False`, `sha256: str | None = None` and `size: int | None = None`.
  - `ModelManager.import_file(path: Path, *, link_llm: bool = False) -> ImportResult`.
    - With `link_llm=True`, an LLM file (a catalog match, or any GGUF) is validated and hashed but not copied.
    - The result then has `linked=True`, `path` is the given path, and `sha256` (lowercase) and `size` are set.
    - Its id is the catalog id, or `f"file:{path}"` for an uncatalogued file.
    - Embedding and OCR matches are always copied.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models_manager.py`:

```python
def test_a_linked_catalog_llm_is_hashed_but_not_copied(tmp_path):
    source = tmp_path / "elsewhere" / "anything.gguf"
    source.parent.mkdir()
    source.write_bytes(LLM)
    manager = ModelManager(tmp_path / "models", _catalog())
    result = manager.import_file(source, link_llm=True)
    assert (result.id, result.path, result.catalogued, result.linked) == ("tiny", source, True, True)
    assert (result.sha256, result.size, result.warning) == (_sha(LLM), len(LLM), None)
    assert not (tmp_path / "models").exists()


def test_a_linked_unknown_gguf_gets_a_file_id_and_the_warning(tmp_path):
    source = tmp_path / "My Model.gguf"
    source.write_bytes(b"GGUF-custom")
    manager = ModelManager(tmp_path / "models", _catalog())
    result = manager.import_file(source, link_llm=True)
    assert result.id == f"file:{source}"
    assert (result.catalogued, result.linked, result.warning) == (False, True, UNCATALOGUED_WARNING)
    assert (result.sha256, result.size) == (_sha(b"GGUF-custom"), len(b"GGUF-custom"))
    assert not (tmp_path / "models").exists()


def test_a_linked_gguf_may_reuse_a_catalog_file_name(tmp_path):
    # LM Studio's lmstudio-community build has the official file name with other content.
    source = tmp_path / "Tiny-Q4.gguf"
    source.write_bytes(b"GGUF-other-build")
    result = ModelManager(tmp_path / "models", _catalog()).import_file(source, link_llm=True)
    assert (result.id, result.linked) == (f"file:{source}", True)


def test_linking_still_refuses_non_gguf_files(tmp_path):
    manager = ModelManager(tmp_path / "models", _catalog())
    text = tmp_path / "notes.txt"
    text.write_text("hi")
    fake = tmp_path / "fake.gguf"
    fake.write_bytes(b"nope")
    for path in (text, fake):
        with pytest.raises(ImportRefused):
            manager.import_file(path, link_llm=True)


def test_an_embedding_file_is_copied_even_when_linking_is_asked(tmp_path):
    source = tmp_path / "model.onnx"
    source.write_bytes(EMB)
    result = ModelManager(tmp_path / "models", _catalog()).import_file(source, link_llm=True)
    assert (result.id, result.linked) == ("emb", False)
    assert (tmp_path / "models" / "emb" / "model.onnx").read_bytes() == EMB
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models_manager.py -v -k "link"`
Expected: FAIL with `TypeError: ModelManager.import_file() got an unexpected keyword argument 'link_llm'`

- [ ] **Step 3: Implement**

Replace `ImportResult`:

```python
@dataclass(frozen=True)
class ImportResult:
    # A catalog id, "import:<file name>" (copied) or "file:<path>" (linked): a valid local_model_id.
    id: str
    path: Path  # where the file now lives: inside the models directory, or where it was if linked
    catalogued: bool
    warning: str | None = None
    linked: bool = False  # used where it is, not copied
    sha256: str | None = None  # set when linked, so the link can be matched to the catalog later
    size: int | None = None  # set when linked
```

Replace `import_file` with the version below. The copy branches are unchanged. The new parts are the `link_llm` early returns, and reading `size` next to the hash.

```python
    def import_file(self, path: Path, *, link_llm: bool = False) -> ImportResult:
        """Copy a model file into the models directory, or, with `link_llm`, accept an LLM file
        where it is.

        A file whose sha256 matches a catalog file is stored under that file's catalog path. Any
        other GGUF is copied as it is, with a warning. Everything else is refused. The copy goes
        to a uniquely named temporary file first, so a half-copied file never looks installed
        and never mixes with a download's .part. Raises ImportRefused, also when the model is
        being downloaded right now.

        With `link_llm`, an LLM file (a catalog match, or any GGUF) is validated and hashed but
        not copied: the result is `linked`, with the file's sha256 and size. Embedding and OCR
        files are always copied: Tamra must own them.
        """
        path = Path(path)
        if not path.is_file():
            raise ImportRefused(f"{path} is not a file")
        with self._import_lock:
            try:
                size = path.stat().st_size
                digest = sha256_file(path)
            except OSError as exc:
                raise ImportRefused(_unreadable(path, exc)) from exc
            match = self._find_by_hash(digest)
            if match is not None:
                entry, f = match
                if link_llm and entry.role == "llm":
                    return ImportResult(
                        id=entry.id, path=path, catalogued=True, linked=True, sha256=digest, size=size
                    )
                self._refuse_if_downloading(entry)
                dest = self.models_dir / f.path
                if not self._already_there(dest, f.size, digest):
                    self._copy(path, dest, expected=digest)
                return ImportResult(id=entry.id, path=dest, catalogued=True)

            if path.suffix.lower() != ".gguf":
                raise ImportRefused("Only GGUF model files can be imported.")
            if link_llm:
                # No name-clash check: a linked file never enters the models directory.
                if _head(path, 4) != b"GGUF":
                    raise ImportRefused(f"{path.name} is not a valid GGUF model file.")
                return ImportResult(
                    id=f"file:{path}",
                    path=path,
                    catalogued=False,
                    warning=UNCATALOGUED_WARNING,
                    linked=True,
                    sha256=digest,
                    size=size,
                )
            clash = self._find_by_name(path.name)
            if clash is not None:
                entry, _ = clash
                raise ImportRefused(
                    f"{path.name} has the name of {entry.name} in Tamra's catalog but different "
                    "content, so it is probably incomplete or damaged."
                )
            if _head(path, 4) != b"GGUF":
                raise ImportRefused(f"{path.name} is not a valid GGUF model file.")
            dest = self.models_dir / path.name
            self._copy(path, dest, expected=None)
            return ImportResult(
                id=f"import:{path.name}", path=dest, catalogued=False, warning=UNCATALOGUED_WARNING
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_models_manager.py -v`
Expected: PASS. The existing import tests do not pass `link_llm`, so they still copy.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src/tamra/models/manager.py tests/test_models_manager.py
uv run ruff check src/tamra/models/manager.py tests/test_models_manager.py
git add src/tamra/models/manager.py tests/test_models_manager.py
git commit -m "feat: let a model import accept an LLM file where it is

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Core lists local models from every source

**Files:**
- Modify: `src/tamra/core.py` (imports, `__init__`, `resolve_local_model`, `_local_label`, new members)
- Modify: `tests/conftest.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes:
  - `ExternalModel` and `scan` (Task 1).
  - `Settings.linked_models` and `LinkedModel` (Task 2).
- Produces:
  - `LocalChoice(id: str, path: Path, label: str, source: str, catalogued: bool)` is a frozen dataclass in `tamra.core`. `source` is one of `"tamra"`, `"linked"`, `"ollama"`, `"lmstudio"`, `"huggingface"`.
  - `Core.local_choices() -> list[LocalChoice]` returns the choices in fallback order.
  - `Core.external_models() -> list[ExternalModel]` caches the scan for `EXTERNAL_TTL = 10.0` seconds. `Core._forget_scan()` clears that cache.
  - `Core(..., external_scan: Callable[[], list[ExternalModel]] | None = None)` lets tests inject the scan. The default calls `scan_external(Path.home(), os.environ)` through the module global `tamra.core.scan_external`, which the conftest patches.
  - `resolve_local_model() -> tuple[str, Path, str]` keeps its signature.

- [ ] **Step 1: Keep every test away from the real stores**

Append to `tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def _no_real_model_scan(monkeypatch):
    """Core scans Ollama, LM Studio and the Hugging Face cache; tests must not see this machine's."""
    monkeypatch.setattr("tamra.core.scan_external", lambda home, env: [])
```

This fixture fails until `tamra.core.scan_external` exists, because monkeypatch raises on a missing attribute. Steps 3–5 add it.

- [ ] **Step 2: Write the failing tests**

In `tests/test_core.py`, add `from tamra.models.external import ExternalModel`. Append to the "the local model" section:

```python
def gguf_file(path, data=b"GGUF-model"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def hashed_catalog(model_id, data):
    """A catalog of one LLM whose single file holds `data`."""
    sha = hashlib.sha256(data).hexdigest()
    return Catalog(
        [ModelEntry(model_id, "llm", "Qwen3-8B", (ModelFile(f"{model_id}.gguf", len(data), sha, "http://x"),))]
    )


def test_a_model_found_in_ollama_is_used_when_tamra_has_none(tmp_path):
    blob = gguf_file(tmp_path / "ollama" / "blobs" / "sha256-x")
    found = [ExternalModel("ollama", "qwen3:8b", blob, "f" * 64)]
    c = make_core(tmp_path, llm=None, catalog=catalog_of("a"), external_scan=lambda: found)
    try:
        assert c.resolve_local_model() == (f"file:{blob}", blob, "qwen3:8b")
        assert local_label(c) == "qwen3:8b"
        assert [(x.source, x.catalogued) for x in c.local_choices()] == [("ollama", False)]
    finally:
        c.shutdown()


def test_a_catalog_model_resolves_through_a_scanned_file_with_its_hash(tmp_path):
    data = b"GGUF-official"
    blob = gguf_file(tmp_path / "hf" / "blob", data)
    sha = hashlib.sha256(data).hexdigest()
    found = [ExternalModel("huggingface", "Qwen3-8B-Q4_K_M", blob, sha.upper())]
    c = make_core(tmp_path, llm=None, catalog=hashed_catalog("q8", data), external_scan=lambda: found)
    try:
        assert c.resolve_local_model() == ("q8", blob, "Qwen3-8B")
        assert [(x.id, x.source, x.catalogued) for x in c.local_choices()] == [("q8", "huggingface", True)]
    finally:
        c.shutdown()


def test_tamras_own_copy_wins_over_a_linked_or_scanned_copy(tmp_path):
    data = b"GGUF-official"
    sha = hashlib.sha256(data).hexdigest()
    own = gguf_file(tmp_path / "models" / "q8.gguf", data)
    linked = gguf_file(tmp_path / "elsewhere" / "q8.gguf", data)
    blob = gguf_file(tmp_path / "ollama" / "blob", data)
    found = [ExternalModel("ollama", "qwen3:8b", blob, sha)]
    c = make_core(tmp_path, llm=None, catalog=hashed_catalog("q8", data), external_scan=lambda: found)
    try:
        c.apply_settings({"linked_models": [{"path": str(linked), "size": len(data), "sha256": sha}]})
        assert [(x.id, x.path, x.source) for x in c.local_choices()] == [("q8", own, "tamra")]
    finally:
        c.shutdown()


def test_a_linked_file_is_listed_while_it_keeps_its_size(tmp_path):
    linked = gguf_file(tmp_path / "elsewhere" / "Mine.gguf")
    c = make_core(tmp_path, llm=None, catalog=catalog_of("a"))
    try:
        link = {"path": str(linked), "size": linked.stat().st_size, "sha256": "e" * 64}
        c.apply_settings({"linked_models": [link], "local_model_id": f"file:{linked}"})
        assert c.resolve_local_model() == (f"file:{linked}", linked, "Mine")
        assert c.local_choices()[0].source == "linked"
        linked.write_bytes(b"GGUF-replaced-with-something-longer")
        assert c.local_choices() == []
        with pytest.raises(ProviderError):
            c.resolve_local_model()
    finally:
        c.shutdown()


def test_a_file_id_that_is_neither_linked_nor_scanned_is_never_used(tmp_path):
    install(tmp_path / "models", "a.gguf")
    stray = gguf_file(tmp_path / "stray.gguf")
    c = make_core(tmp_path, llm=None, catalog=catalog_of("a"))
    try:
        c.apply_settings({"local_model_id": f"file:{stray}"})
        assert c.resolve_local_model()[0] == "a"
    finally:
        c.shutdown()


def test_the_fallback_order_is_catalog_then_tamra_then_linked_then_scanned(tmp_path):
    install(tmp_path / "models", "loose.gguf")
    linked = gguf_file(tmp_path / "elsewhere" / "linked.gguf")
    blob = gguf_file(tmp_path / "ollama" / "blob")
    found = [ExternalModel("ollama", "qwen3:8b", blob, None)]
    c = make_core(tmp_path, llm=None, catalog=catalog_of("a"), external_scan=lambda: found)
    try:
        link = {"path": str(linked), "size": linked.stat().st_size, "sha256": "e" * 64}
        c.apply_settings({"linked_models": [link]})
        assert [x.source for x in c.local_choices()] == ["tamra", "linked", "ollama"]
        install(tmp_path / "models", "a.gguf")
        assert [x.id for x in c.local_choices()][0] == "a"
    finally:
        c.shutdown()


def test_a_scanned_file_that_is_also_linked_is_listed_once_as_linked(tmp_path):
    model = gguf_file(tmp_path / "lmstudio" / "m.gguf")
    found = [ExternalModel("lmstudio", "m", model, None)]
    c = make_core(tmp_path, llm=None, catalog=catalog_of("a"), external_scan=lambda: found)
    try:
        link = {"path": str(model), "size": model.stat().st_size, "sha256": "e" * 64}
        c.apply_settings({"linked_models": [link]})
        assert [x.source for x in c.local_choices()] == ["linked"]
    finally:
        c.shutdown()


def test_the_scan_is_cached_and_a_failing_scan_lists_nothing(tmp_path, monkeypatch):
    calls = []
    c = make_core(tmp_path, llm=None, catalog=catalog_of("a"), external_scan=lambda: calls.append(1) or [])
    try:
        c.local_choices()
        c.local_choices()
        assert len(calls) == 1
        c._forget_scan()
        c.local_choices()
        assert len(calls) == 2
        monkeypatch.setattr("tamra.core.EXTERNAL_TTL", 0.0)
        c.local_choices()
        assert len(calls) == 3
    finally:
        c.shutdown()

    def broken():
        raise RuntimeError("store exploded")

    c = make_core(tmp_path / "2", llm=None, catalog=catalog_of("a"), external_scan=broken)
    try:
        assert c.local_choices() == []
    finally:
        c.shutdown()
```

Add `import hashlib` at the top of `tests/test_core.py` if it is not there yet.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_core.py -v -k "local or scan or linked or ollama or fallback or catalog_model"`
Expected: FAIL. The autouse fixture errors with `AttributeError: <module 'tamra.core'> has no attribute 'scan_external'`.

- [ ] **Step 4: Implement in `src/tamra/core.py`**

Imports. Add `import time` and `from dataclasses import dataclass` to the existing imports, and add:

```python
from tamra.models.external import ExternalModel
from tamra.models.external import scan as scan_external
from tamra.settings import LinkedModel, Settings, load_settings, save_settings
```

(The last line replaces the existing `from tamra.settings import ...` line.)

Below `DOCUMENT_CACHE_MAX_BYTES`:

```python
# shortcut: a model added in another tool shows up within this many seconds; a filesystem watch on
# the stores would make it instant if that ever matters.
EXTERNAL_TTL = 10.0
```

Below `_overlaps`:

```python
@dataclass(frozen=True)
class LocalChoice:
    """A local model Tamra can run: its `local_model_id`, file, label, and where the file is."""

    id: str
    path: Path
    label: str
    source: str  # "tamra" | "linked" | "ollama" | "lmstudio" | "huggingface"
    catalogued: bool


def _path_key(path: Path | str) -> str:
    return os.path.normcase(str(path))


def _same_id(a: str, b: str) -> bool:
    """Ids naming files compare as Windows paths do (case-insensitively); catalog ids exactly."""
    if a.startswith(("import:", "file:")):
        return _path_key(a) == _path_key(b)
    return a == b


def _intact(link: LinkedModel) -> bool:
    """A linked file is used while it is still there with the size it had when it was linked."""
    try:
        path = Path(link["path"])
        return path.is_file() and path.stat().st_size == link["size"]
    except OSError:
        return False
```

Add the parameter to `Core.__init__`'s signature, after `catalog`:

```python
        external_scan: Callable[[], list[ExternalModel]] | None = None,
```

Mention it in the docstring ("`external_scan` replaces the scan of other tools' model stores."). In the body, after `self.models = ModelManager(...)`:

```python
        # Looked up at call time, so tests can patch tamra.core.scan_external.
        self._external_scan = external_scan or (lambda: scan_external(Path.home(), os.environ))
        self._external: tuple[float, list[ExternalModel]] | None = None
        self._external_lock = threading.Lock()
        self._links_lock = threading.Lock()  # one read-modify-write of linked_models at a time
```

Replace `resolve_local_model` and `_local_label`, and add the new methods:

```python
    def external_models(self) -> list[ExternalModel]:
        """The models Ollama, LM Studio and the Hugging Face cache hold, scanned at most every
        EXTERNAL_TTL seconds (GET /api/models is polled every second). A failing scan lists none."""
        with self._external_lock:
            now = time.monotonic()
            if self._external is None or now - self._external[0] >= EXTERNAL_TTL:
                try:
                    found = self._external_scan()
                except Exception:  # a broken store must never break the model list
                    log.exception("scanning other tools' model folders failed")
                    found = []
                self._external = (now, found)
            return self._external[1]

    def _forget_scan(self) -> None:
        with self._external_lock:
            self._external = None

    def local_choices(self) -> list[LocalChoice]:
        """Every local model Tamra can run, in the order resolve_local_model falls back through:
        catalog LLMs (from Tamra's folder, else a linked file, else another tool, matched by
        sha256), then uncatalogued files in Tamra's folder, then linked files, then other tools'.

        Only sizes are checked here; nothing is hashed (linked files carry their sha256 from the
        import, and Ollama and Hugging Face name their files by it)."""
        llms = self._catalog.llms()
        installed = self._catalog.installed(self._models_dir)
        links = [link for link in self._settings.linked_models if _intact(link)]
        scanned = self.external_models()
        catalog_hashes = {e.main_file.sha256.lower() for e in llms}
        hashed: dict[str, tuple[Path, str]] = {}
        for link in links:
            hashed.setdefault(link["sha256"], (Path(link["path"]), "linked"))
        for m in scanned:
            if m.sha256 is not None:
                hashed.setdefault(m.sha256.lower(), (m.path, m.source))

        choices: list[LocalChoice] = []
        for entry in llms:
            sha = entry.main_file.sha256.lower()
            if entry.id in installed:
                path, source = installed[entry.id], "tamra"
            elif sha in hashed:
                path, source = hashed[sha]
            else:
                continue
            choices.append(LocalChoice(entry.id, path, entry.name, source, True))
        for path in self._catalog.uncatalogued(self._models_dir):
            choices.append(LocalChoice(f"import:{path.name}", path, path.stem, "tamra", False))
        offered: set[str] = set()
        for link in links:
            path = Path(link["path"])
            if link["sha256"] not in catalog_hashes and _path_key(path) not in offered:
                offered.add(_path_key(path))
                choices.append(LocalChoice(f"file:{path}", path, path.stem, "linked", False))
        for m in scanned:
            if (m.sha256 or "").lower() not in catalog_hashes and _path_key(m.path) not in offered:
                offered.add(_path_key(m.path))
                choices.append(LocalChoice(f"file:{m.path}", m.path, m.name, m.source, False))
        return choices

    def resolve_local_model(self) -> tuple[str, Path, str]:
        """The local model to serve, as (id, file, label): the selected one if it is available,
        else the first of local_choices(). The id is a valid `local_model_id`.

        Raises ProviderError (model_missing) when no model is available.
        """
        choices = self.local_choices()
        wanted = self._settings.local_model_id
        chosen = next((c for c in choices if wanted and _same_id(c.id, wanted)), None)
        if chosen is None and choices:
            chosen = choices[0]
        if chosen is None:
            raise ProviderError("No local model is installed.", "model_missing")
        return chosen.id, chosen.path, chosen.label

    def _local_model_path(self) -> Path:
        return self.resolve_local_model()[1]

    def _local_label(self, path: Path) -> str:
        """The name to show for the model file `path`: its catalog or Ollama name, else its stem."""
        for choice in self.local_choices():
            if choice.path == path:
                return choice.label
        return path.stem
```

- [ ] **Step 5: Run the whole Python suite**

Run: `uv run pytest -q`
Expected: PASS. The old resolution tests still hold: `import:LOOSE.gguf` resolves case-insensitively, and an unknown id falls back to the first catalog model.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff format src/tamra/core.py tests/test_core.py tests/conftest.py
uv run ruff check src/tamra/core.py tests/test_core.py tests/conftest.py
git add src/tamra/core.py tests/test_core.py tests/conftest.py
git commit -m "feat: resolve the local model from Tamra's folder, linked files and other tools

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Core imports by link and unlinks

**Files:**
- Modify: `src/tamra/core.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes:
  - `is_removable` (Task 3).
  - `ModelManager.import_file(path, *, link_llm)` and `ImportResult` (Task 5).
  - `local_choices` and `_forget_scan` (Task 6).
- Produces:
  - `Core.import_model(path: Path) -> ImportResult` links or copies. When it links, it adds or refreshes the `linked_models` entry and sets `local_model_id` to the result's id. It raises `ImportRefused`, and `OSError` when a copy fails.
  - `Core.unlink_model(path: str) -> None` removes the entry, matching paths case-insensitively. It raises `KeyError` when the path is not linked.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_core.py`:

```python
def test_a_file_on_a_fixed_drive_is_linked_and_selected(tmp_path, monkeypatch):
    monkeypatch.setattr("tamra.core.is_removable", lambda path: False)
    source = gguf_file(tmp_path / "elsewhere" / "Mine.gguf")
    c = make_core(tmp_path, llm=None, catalog=catalog_of("a"))
    try:
        result = c.import_model(source)
        assert (result.id, result.linked) == (f"file:{source}", True)
        assert c.settings.linked_models == (
            {"path": str(source), "size": source.stat().st_size, "sha256": result.sha256},
        )
        assert c.settings.local_model_id == result.id
        assert c.resolve_local_model() == (result.id, source, "Mine")
        assert not (tmp_path / "models" / "Mine.gguf").exists()
    finally:
        c.shutdown()


def test_a_file_on_a_removable_drive_is_copied_and_not_linked(tmp_path, monkeypatch):
    monkeypatch.setattr("tamra.core.is_removable", lambda path: True)
    source = gguf_file(tmp_path / "usb" / "Mine.gguf")
    c = make_core(tmp_path, llm=None, catalog=catalog_of("a"))
    try:
        result = c.import_model(source)
        assert (result.id, result.linked) == ("import:Mine.gguf", False)
        assert (tmp_path / "models" / "Mine.gguf").read_bytes() == source.read_bytes()
        assert c.settings.linked_models == ()
    finally:
        c.shutdown()


def test_importing_a_linked_path_again_refreshes_its_entry(tmp_path, monkeypatch):
    monkeypatch.setattr("tamra.core.is_removable", lambda path: False)
    source = gguf_file(tmp_path / "elsewhere" / "Mine.gguf")
    c = make_core(tmp_path, llm=None, catalog=catalog_of("a"))
    try:
        c.import_model(source)
        source.write_bytes(b"GGUF-a-newer-and-longer-build")
        again = c.import_model(source)
        assert len(c.settings.linked_models) == 1
        assert c.settings.linked_models[0]["size"] == source.stat().st_size
        assert c.settings.linked_models[0]["sha256"] == again.sha256
    finally:
        c.shutdown()


def test_unlinking_forgets_the_file_but_leaves_it_alone(tmp_path, monkeypatch):
    monkeypatch.setattr("tamra.core.is_removable", lambda path: False)
    source = gguf_file(tmp_path / "elsewhere" / "Mine.gguf")
    c = make_core(tmp_path, llm=None, catalog=catalog_of("a"))
    try:
        c.import_model(source)
        c.unlink_model(str(source).upper())  # Windows paths ignore case
        assert c.settings.linked_models == ()
        assert source.exists()
        with pytest.raises(KeyError):
            c.unlink_model(str(source))
    finally:
        c.shutdown()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_core.py -v -k "linked or removable or unlink"`
Expected: FAIL with `AttributeError: <module 'tamra.core'> has no attribute 'is_removable'`

- [ ] **Step 3: Implement**

Imports in `src/tamra/core.py`:

```python
from tamra.drives import is_removable
from tamra.models.manager import ImportResult, ModelManager
```

(The second line replaces `from tamra.models.manager import ModelManager`.)

Add to `Core`, after `local_choices`:

```python
    def import_model(self, path: Path) -> ImportResult:
        """Import a model file. An LLM file is used where it is (linked) and selected, unless it
        is on a removable drive, which may be gone next time: it is then copied in. Embedding and
        OCR files are always copied. Raises ImportRefused, and OSError when a copy fails."""
        result = self.models.import_file(path, link_llm=not is_removable(path))
        if result.linked:
            link: LinkedModel = {
                "path": str(result.path),
                "size": result.size,
                "sha256": result.sha256,
            }
            with self._links_lock:
                kept = [
                    old
                    for old in self._settings.linked_models
                    if _path_key(old["path"]) != _path_key(link["path"])
                ]
                self.apply_settings({"linked_models": [*kept, link], "local_model_id": result.id})
        self._forget_scan()
        return result

    def unlink_model(self, path: str) -> None:
        """Stop using a linked file; the file itself is left alone. KeyError if it is not linked."""
        with self._links_lock:
            links = self._settings.linked_models
            kept = [link for link in links if _path_key(link["path"]) != _path_key(path)]
            if len(kept) == len(links):
                raise KeyError(path)
            self.apply_settings({"linked_models": kept})
        self._forget_scan()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_core.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src/tamra/core.py tests/test_core.py
uv run ruff check src/tamra/core.py tests/test_core.py
git add src/tamra/core.py tests/test_core.py
git commit -m "feat: link imported LLM files in place and let them be unlinked

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Server routes: import, unlink, `/api/models`, settings

**Files:**
- Modify: `src/tamra/server.py` (`ImportBody` area at `:170`, `_settings_payload` at `:648`, `put_settings` at `:666`, `get_models` at `:734-792`, `import_model` at `:794-822`)
- Test: `tests/test_server_settings.py`

**Interfaces:**
- Consumes:
  - `Core.local_choices()`, `Core.import_model()` and `Core.unlink_model()` (Tasks 6 and 7).
- Produces (HTTP):
  - `POST /api/models/import` adds `linked` to its response.
  - `POST /api/models/unlink {path}` answers 204, or 404 when the path is not linked.
  - In `GET /api/models`, `local[i].source` is a source string or `null`, and each `uncatalogued[i]` has `{id, name, file, size, source, path}`.
  - `PUT /api/settings` answers 400 when the request has `linked_models`.
  - `GET /api/settings` leaves out `linked_models`.

- [ ] **Step 1: Update and add the failing tests**

In `tests/test_server_settings.py`:

1. Add `("POST", "/api/models/unlink", {"path": "C:/m.gguf"}),` to `ROUTES`.
2. In `test_get_models_lists_the_catalog_hardware_and_active_model`:
   - Add `"source": "tamra",` to the `"small"` dict.
   - Add `"source": None,` to the `"medium"` dict.
   - Replace the `uncatalogued` expectation with:

```python
        "uncatalogued": [
            {
                "id": "import:mine.gguf",
                "name": "mine",
                "file": "mine.gguf",
                "size": 8,
                "source": "tamra",
                "path": str(client.models_dir / "mine.gguf"),
            }
        ],
```

3. Replace `test_import_copies_a_gguf_and_returns_an_id_the_settings_accept` with the two tests below. Also add `monkeypatch` to `test_import_of_a_catalog_file_returns_the_catalog_id` and replace that test's body as shown.

```python
def test_import_links_a_gguf_where_it_is_and_selects_it(env):
    client, core, tmp_path = env
    source = tmp_path / "elsewhere" / "My Model.gguf"
    source.parent.mkdir()
    source.write_bytes(b"GGUF-custom")
    response = client.post("/api/models/import", headers=AUTH, json={"path": str(source)})
    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["linked"], body["catalogued"]) == (f"file:{source}", True, False)
    assert body["warning"]
    assert list(client.models_dir.iterdir()) == []
    listed = client.get("/api/models", headers=AUTH).json()
    assert listed["active"]["id"] == body["id"]
    assert [(m["id"], m["source"], m["path"]) for m in listed["uncatalogued"]] == [
        (body["id"], "linked", str(source))
    ]


def test_import_copies_a_gguf_from_a_removable_drive(env, monkeypatch):
    client, _, tmp_path = env
    monkeypatch.setattr("tamra.core.is_removable", lambda path: True)
    source = tmp_path / "usb" / "My Model.gguf"
    source.parent.mkdir()
    source.write_bytes(b"GGUF-custom")
    body = client.post("/api/models/import", headers=AUTH, json={"path": str(source)}).json()
    assert (body["id"], body["linked"]) == ("import:My Model.gguf", False)
    assert (client.models_dir / "My Model.gguf").read_bytes() == b"GGUF-custom"


def test_import_of_a_catalog_file_returns_the_catalog_id(env, monkeypatch):
    client, _, tmp_path = env
    source = tmp_path / "anything.bin"
    source.write_bytes(MEDIUM)
    body = client.post("/api/models/import", headers=AUTH, json={"path": str(source)}).json()
    assert (body["id"], body["catalogued"], body["linked"], body["warning"]) == (
        "medium", True, True, None
    )
    local = client.get("/api/models", headers=AUTH).json()["local"]
    assert [(m["id"], m["installed"], m["source"]) for m in local] == [
        ("small", False, None),
        ("medium", True, "linked"),
    ]
    monkeypatch.setattr("tamra.core.is_removable", lambda path: True)
    copied = client.post("/api/models/import", headers=AUTH, json={"path": str(source)}).json()
    assert copied["linked"] is False
    assert (client.models_dir / "Medium-Q4.gguf").read_bytes() == MEDIUM


def test_an_embedding_file_is_always_copied(env):
    client, _, tmp_path = env
    source = tmp_path / "model.onnx"
    source.write_bytes(EMB)
    body = client.post("/api/models/import", headers=AUTH, json={"path": str(source)}).json()
    assert (body["id"], body["linked"]) == ("emb", False)
    assert (client.models_dir / "emb" / "model.onnx").read_bytes() == EMB


def test_unlink_forgets_a_linked_file_and_404s_an_unknown_one(env):
    client, core, tmp_path = env
    source = tmp_path / "m.gguf"
    source.write_bytes(b"GGUF-custom")
    client.post("/api/models/import", headers=AUTH, json={"path": str(source)})
    gone = client.post("/api/models/unlink", headers=AUTH, json={"path": str(source)})
    assert gone.status_code == 204
    assert core.settings.linked_models == ()
    assert source.exists()
    again = client.post("/api/models/unlink", headers=AUTH, json={"path": str(source)})
    assert again.status_code == 404


def test_linked_models_are_not_a_client_setting(env):
    client, _, _ = env
    link = {"path": "C:\\m.gguf", "size": 1, "sha256": "a" * 64}
    response = client.put("/api/settings", headers=AUTH, json={"linked_models": [link]})
    assert response.status_code == 400
    assert "linked_models" not in client.get("/api/settings", headers=AUTH).json()
```

4. In `test_import_accepts_a_unc_path_shape`, change the monkeypatch target from `core.models, "import_file"` to `core, "import_model"`. Do the same in `test_a_destination_error_is_a_json_500_with_a_fixed_message`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_server_settings.py -v`
Expected: FAIL. `source` and `path` are missing, the unlink route answers 404 or 405, `linked` is missing, and `linked_models` is accepted by PUT.

- [ ] **Step 3: Implement in `src/tamra/server.py`**

Next to `ImportBody`:

```python
class UnlinkBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
```

In `_settings_payload`, leave the server-owned field out:

```python
    payload = dataclasses.asdict(settings)
    del payload["linked_models"]  # server-owned: changed by importing or unlinking a model
    return {
        **payload,
        "data_dir": str(core.data_dir),  # read-only: shown in Settings, never a setting
        "api_key_set": key is not None,
        "api_key_hint": key[-4:] if key is not None and len(key) > 8 else None,
    }
```

In `put_settings`, before `core.apply_settings(changes)`:

```python
        if "linked_models" in changes:
            raise HTTPException(
                status_code=400, detail="linked_models is changed by importing a model file."
            )
```

In `get_models`, replace the construction of `local` and `uncatalogued`:

```python
        choices = core.local_choices()
        sources = {c.id: c.source for c in choices if c.catalogued}
        local = [
            {
                **_entry_payload(m, m.id in sources, status[m.id]),
                "tier": m.tier,
                "recommended": m.tier == tier,
                "license": m.license,
                "languages": list(m.languages),
                "context_length": m.context_length,
                "thinking": m.thinking,
                "min_vram_gb": m.min_vram_gb,
                "source": sources.get(m.id),  # where the installed copy is; None if not installed
            }
            for m in catalog.llms()
        ]
        uncatalogued = []
        for choice in choices:
            if choice.catalogued:
                continue
            try:
                size = choice.path.stat().st_size
            except OSError:
                continue  # removed since the listing
            uncatalogued.append(
                {
                    "id": choice.id,
                    "name": choice.label,
                    "file": choice.path.name,
                    "size": size,
                    "source": choice.source,
                    "path": str(choice.path),
                }
            )
```

The `installed = catalog.installed(models_dir)` line stays, because the embedding and OCR payloads still use it.

In `import_model`, call the core and return `linked`:

```python
        try:
            result = core.import_model(path)
        except ImportRefused as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except OSError as e:  # the destination: a full disk, or the model file is in use
            ...  # unchanged
        return {
            "id": result.id,
            "path": str(result.path),
            "catalogued": result.catalogued,
            "linked": result.linked,
            "warning": result.warning,
        }
```

After the import route, add the unlink route:

```python
    @app.post("/api/models/unlink", status_code=204)
    def unlink_model(body: UnlinkBody) -> Response:
        """Stop using a linked model file. The file itself is never deleted."""
        try:
            core.unlink_model(body.path.strip())
        except KeyError as e:
            raise HTTPException(status_code=404, detail="This file is not linked.") from e
        return Response(status_code=204)
```

- [ ] **Step 4: Run the whole Python suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src/tamra/server.py tests/test_server_settings.py
uv run ruff check src/tamra/server.py tests/test_server_settings.py
git add src/tamra/server.py tests/test_server_settings.py
git commit -m "feat: serve linked and other tools' models, and unlink them

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: UI: source labels, "Remove from list" and the import notes

**Files:**
- Modify: `ui/src/types.ts` (`LocalModel`, `UncataloguedModel`, `ImportResult`, `ErrorReason`)
- Modify: `ui/src/models.ts`, `ui/src/test-models.ts`
- Modify: `ui/src/SettingsLocal.tsx`
- Modify: `ui/src/strings/en.ts`, `ui/src/strings/th.ts`
- Test: `ui/src/SettingsPage.test.tsx`

**Interfaces:**
- Consumes the HTTP shapes from Task 8.
- Produces:
  - `type ModelSource = "tamra" | "linked" | "ollama" | "lmstudio" | "huggingface"`.
  - `SOURCE_KEYS: Record<ModelSource, TranslationKey>` is exported from `ui/src/models.ts`. Task 10 uses it.

- [ ] **Step 1: Types and fixtures**

In `ui/src/types.ts`:

```ts
/** Why a model call failed (ProviderError.reason); errors that are not the model's carry none. */
export type ErrorReason =
  | "offline"
  | "auth"
  | "quota"
  | "model_missing"
  | "model_unsupported"
  | "embedding_missing"
  | "other";
```

```ts
/** Where a local model file is: Tamra's own folder, a file the user linked, or another tool. */
export type ModelSource = "tamra" | "linked" | "ollama" | "lmstudio" | "huggingface";
```

Add to `LocalModel`:

```ts
  /** Where the installed copy is; null while the model is not installed. */
  source: ModelSource | null;
```

Replace `UncataloguedModel` and `ImportResult`:

```ts
export type UncataloguedModel = {
  id: string;
  name: string;
  file: string;
  size: number;
  source: ModelSource;
  /** The full path of the file (shown as a tooltip; sent back to unlink a linked file). */
  path: string;
};
```

```ts
export type ImportResult = {
  id: string;
  path: string;
  catalogued: boolean;
  /** Used where it is rather than copied into Tamra's models folder. */
  linked: boolean;
  warning: string | null;
};
```

In `ui/src/test-models.ts`:
- Add `source: "tamra",` to `localModel`'s defaults.
- Add `source: null` to the `qwen3-8b` and `qwen3-14b` overrides, which are not installed.
- Replace the uncatalogued fixture:

```ts
    uncatalogued: [
      {
        id: "import:mine.gguf",
        name: "mine",
        file: "mine.gguf",
        size: 8,
        source: "tamra",
        path: "C:\\Users\\me\\AppData\\Local\\Tamra\\models\\mine.gguf",
      },
    ],
```

In `ui/src/models.ts`, add (import `TranslationKey` from `./i18n` and `ModelSource` from `./types`):

```ts
/** The words for where a model file is, as the model list and the model menu show them. */
export const SOURCE_KEYS: Record<ModelSource, TranslationKey> = {
  tamra: "model.imported",
  linked: "model.source.linked",
  ollama: "model.source.ollama",
  lmstudio: "model.source.lmstudio",
  huggingface: "model.source.huggingface",
};
```

- [ ] **Step 2: Strings**

In `ui/src/strings/en.ts`, after `"model.imported"`:

```ts
  "model.source.linked": "Linked file",
  "model.source.ollama": "From Ollama",
  "model.source.lmstudio": "From LM Studio",
  "model.source.huggingface": "From Hugging Face",
```

Replace `settings.importHint`, and add after `settings.importedOther`:

```ts
  "settings.importHint":
    "Tamra uses the file where it is. A file on a USB stick or other removable drive is copied in, and so are search and OCR model files.",
  "settings.importedLinked": "Using {file} where it is; it was not copied.",
  "settings.importedLinkedOther": "Using {file} where it is; it was not copied. {warning}",
  "settings.unlink": "Remove from list",
  "settings.unlinkHint": "Tamra stops using this file. The file itself is not deleted.",
```

After `chat.error.modelMissing`:

```ts
  "chat.error.modelUnsupported":
    "Tamra cannot run this type of model yet. Choose another model in Settings.",
```

In `ui/src/strings/th.ts`, at the matching places:

```ts
  "model.source.linked": "ไฟล์ที่เลือกไว้",
  "model.source.ollama": "จาก Ollama",
  "model.source.lmstudio": "จาก LM Studio",
  "model.source.huggingface": "จาก Hugging Face",
```

```ts
  "settings.importHint":
    "Tamra ใช้ไฟล์จากที่เดิม ถ้าไฟล์อยู่บน USB หรือไดรฟ์ถอดได้จะคัดลอกเข้ามา และไฟล์โมเดลค้นหากับ OCR จะคัดลอกเสมอ",
  "settings.importedLinked": "ใช้ไฟล์ {file} จากที่เดิม ไม่ได้คัดลอก",
  "settings.importedLinkedOther": "ใช้ไฟล์ {file} จากที่เดิม ไม่ได้คัดลอก {warning}",
  "settings.unlink": "เอาออกจากรายการ",
  "settings.unlinkHint": "Tamra จะเลิกใช้ไฟล์นี้ แต่ไม่ลบตัวไฟล์",
```

```ts
  "chat.error.modelUnsupported": "Tamra ยังรันโมเดลชนิดนี้ไม่ได้ เลือกโมเดลอื่นในการตั้งค่า",
```

- [ ] **Step 3: Write the failing UI tests**

In `ui/src/SettingsPage.test.tsx`, add `linked: false,` to the `imported` constant of the "AI model tab: import" block. Add to the "AI model tab: local" block:

```ts
  it("names where each model file is and removes a linked file from the list", async () => {
    const { calls } = await mountPage({
      tab: "model",
      models: modelsInfo({
        local: [localModel({ source: "ollama" })],
        uncatalogued: [
          {
            id: "file:D:\\Models\\mine.gguf",
            name: "mine",
            file: "mine.gguf",
            size: 5_000_000_000,
            source: "linked",
            path: "D:\\Models\\mine.gguf",
          },
          {
            id: "file:C:\\Users\\me\\.ollama\\models\\blobs\\sha256-ab",
            name: "llama3:8b",
            file: "sha256-ab",
            size: 4_700_000_000,
            source: "ollama",
            path: "C:\\Users\\me\\.ollama\\models\\blobs\\sha256-ab",
          },
        ],
      }),
      routes: { "POST /api/models/unlink": noContent },
    });
    const rows = [...view!.container.querySelectorAll(".model-row")];
    expect(rows[0].textContent).toContain("From Ollama");
    expect(rows[1].textContent).toContain("Linked file · 5.0 GB");
    expect(rows[1].querySelector(".model-name")?.getAttribute("title")).toBe("D:\\Models\\mine.gguf");
    expect(rows[2].textContent).toContain("From Ollama · 4.7 GB");
    expect(rows[2].textContent).not.toContain("Remove from list");
    await click(rows[1].querySelector('button[aria-label="Remove from list: mine"]'));
    expect(calls).toContainEqual({
      key: "POST /api/models/unlink",
      body: { path: "D:\\Models\\mine.gguf" },
    });
    const order = keys(calls);
    expect(order.lastIndexOf("GET /api/models")).toBeGreaterThan(order.indexOf("POST /api/models/unlink"));
  });

  it("does not name Tamra's own folder on a catalog model", async () => {
    await mountPage({ tab: "model" });
    const first = view!.container.querySelector(".model-row")!;
    expect(first.textContent).not.toContain("Imported model");
  });
```

Add to the "AI model tab: import" block:

```ts
  it("says a linked file is used where it is", async () => {
    await mountPage({
      tab: "model",
      routes: {
        "POST /api/pick-file": () => json({ file_path: "D:\\Models\\mine.gguf" }),
        "POST /api/models/import": () =>
          json({ ...imported, id: "file:D:\\Models\\mine.gguf", path: "D:\\Models\\mine.gguf", linked: true }),
      },
    });
    await click(buttonByText(view!.container, "Import model file…"));
    expect(find('[role="status"]').textContent).toBe(
      "Using mine.gguf where it is; it was not copied. Not in Tamra's catalog; answer quality is unknown.",
    );
  });

  it("says a linked catalog file is used where it is, without a warning", async () => {
    await mountPage({
      tab: "model",
      routes: {
        "POST /api/pick-file": () => json({ file_path: "D:\\Models\\q.gguf" }),
        "POST /api/models/import": () =>
          json({ id: "qwen3-4b", path: "D:\\Models\\q.gguf", catalogued: true, linked: true, warning: null }),
      },
    });
    await click(buttonByText(view!.container, "Import model file…"));
    expect(find('[role="status"]').textContent).toBe("Using q.gguf where it is; it was not copied.");
  });
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `npm --prefix ui test -- SettingsPage`
Expected: FAIL. "From Ollama", "Linked file", the title, the "Remove from list" button and the linked import notes are all missing.

- [ ] **Step 5: Implement in `ui/src/SettingsLocal.tsx`**

- Import `SOURCE_KEYS` from `./models`, next to `isDownloading`.
- Replace the success-note expression in `importPath`:

```tsx
      const warning = t("settings.uncatalogued");
      setNote({
        ok: true,
        text: result.linked
          ? result.catalogued
            ? t("settings.importedLinked", { file: name })
            : t("settings.importedLinkedOther", { file: name, warning })
          : result.catalogued
            ? t("settings.importedCatalogued", { file: name })
            : t("settings.importedOther", { file: name, warning }),
      });
```

- Add next to `cancelDownload`:

```tsx
  async function unlink(model: UncataloguedModel) {
    setError(null);
    try {
      await api("POST", "/api/models/unlink", { path: model.path });
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
```

- Pass `onUnlink={() => void unlink(model)}` to each `<ImportedRow>`.
- In `ModelRow`, after `if (model.thinking) parts.push(t("model.canThink"));`:

```tsx
  if (model.installed && model.source && model.source !== "tamra") parts.push(t(SOURCE_KEYS[model.source]));
```

- Replace `ImportedRow`:

```tsx
function ImportedRow({
  model,
  selected,
  onSelect,
  onUnlink,
}: {
  model: UncataloguedModel;
  selected: boolean;
  onSelect: () => void;
  onUnlink: () => void;
}) {
  const t = useT();
  return (
    <div className={selected ? "model-row selected" : "model-row"}>
      <button
        type="button"
        role="radio"
        aria-checked={selected}
        className="model-pick"
        onClick={onSelect}
      >
        <RadioMark on={selected} />
        <span className="model-text">
          <span className="model-name" title={model.path}>
            {model.name}
          </span>
          <span className="model-detail">
            {t(SOURCE_KEYS[model.source])} · {formatSize(model.size, t)}
          </span>
          <span className="model-warning">
            <AlertTriangle size={13} />
            {t("settings.uncatalogued")}
          </span>
        </span>
      </button>
      <div className="model-action">
        <span className="installed">
          <Check size={14} />
          {t("settings.installed")}
        </span>
        {model.source === "linked" && (
          <button
            type="button"
            className="link-button"
            aria-label={`${t("settings.unlink")}: ${model.name}`}
            title={t("settings.unlinkHint")}
            onClick={onUnlink}
          >
            {t("settings.unlink")}
          </button>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Run the tests and the type check**

Run: `npm --prefix ui test`
Expected: PASS

Run: `npm --prefix ui run build`
Expected: the build succeeds (`tsc` passes, `th.ts` has every key).

- [ ] **Step 7: Commit**

```bash
git add ui/src/types.ts ui/src/models.ts ui/src/test-models.ts ui/src/SettingsLocal.tsx ui/src/strings/en.ts ui/src/strings/th.ts ui/src/SettingsPage.test.tsx
git commit -m "feat(ui): show where each local model is and let linked files be removed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: UI: model-menu source notes and the `model_unsupported` notice

**Files:**
- Modify: `ui/src/ModelMenu.tsx:102-111`
- Modify: `ui/src/ChatView.tsx` (`noticeText` around `:601-617`, the settings button condition around `:498`)
- Test: `ui/src/Composer.test.tsx`

**Interfaces:**
- Consumes:
  - `SOURCE_KEYS` and `ModelSource` (Task 9).
  - The `chat.error.modelUnsupported` string (Task 9).

- [ ] **Step 1: Write the failing tests**

In `ui/src/Composer.test.tsx`, add to `describe("model menu", …)`:

```ts
  it("names the tool a model comes from", async () => {
    await mountChat({
      models: modelsInfo({
        uncatalogued: [
          {
            id: "file:C:\\o\\blob",
            name: "qwen3:8b",
            file: "blob",
            size: 5,
            source: "ollama",
            path: "C:\\o\\blob",
          },
        ],
      }),
    });
    await click(chip("Model"));
    expect(menu()?.textContent).toContain("qwen3:8bFrom Ollama");
  });
```

If `mountChat` does not take `models`, set the fixture the way the existing tests at `Composer.test.tsx:337` do. Check its options type first. If the menu entry renders its name and note in separate elements, assert `entry("qwen3:8b")?.textContent` contains `"From Ollama"` instead.

Add to `describe("model errors", …)`:

```ts
  it("explains a model type llama.cpp cannot run, with a button to the model settings", async () => {
    const onOpenSettings = vi.fn();
    await mountChat({
      onOpenSettings,
      routes: {
        "POST /api/chats/5/messages": () =>
          sse([
            { type: "sources", sources: [source] },
            {
              type: "error",
              message: "This version of llama.cpp cannot run models of type qwen3vl.",
              reason: "model_unsupported",
            },
          ]),
      },
    });
    await ask();
    const alert = view!.container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain(
      "Tamra cannot run this type of model yet. Choose another model in Settings.",
    );
    await click(buttonByText(alert!, "Open model settings"));
    expect(onOpenSettings).toHaveBeenCalledWith("model");
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix ui test -- Composer`
Expected: FAIL. The note says "Imported model", and the raw message shows with no settings button.

- [ ] **Step 3: Implement**

In `ui/src/ModelMenu.tsx`, import `SOURCE_KEYS` from `./models`, and in the `imported.map` entry:

```tsx
              note={t(SOURCE_KEYS[model.source])}
```

In `ui/src/ChatView.tsx` `noticeText`, after the `model_missing` case:

```tsx
    case "model_unsupported":
      return t("chat.error.modelUnsupported");
```

And the settings button condition:

```tsx
              {(notice.reason === "model_missing" ||
                notice.reason === "model_unsupported" ||
                notice.reason === "embedding_missing") && (
```

- [ ] **Step 4: Run all UI tests and the build**

Run: `npm --prefix ui test`
Expected: PASS

Run: `npm --prefix ui run build`
Expected: success

- [ ] **Step 5: Commit**

```bash
git add ui/src/ModelMenu.tsx ui/src/ChatView.tsx ui/src/Composer.test.tsx
git commit -m "feat(ui): name a model's source in the menu and explain unsupported models

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Docs and the end-to-end check

**Files:**
- Modify: `docs/superpowers/specs/2026-10-02-tamra-design.md:238-240` (the Import bullet)
- Modify: `docs/superpowers/specs/2026-10-23-external-models-design.md` (the import-note deviation and the status line)

- [ ] **Step 1: Rewrite the Import bullet in the main spec**

Replace the bullet that begins `- **Import:** for offline machines.` with:

```markdown
- **Import:** pick a model file, which is hashed and matched against the catalog. An LLM file
  on a fixed or network drive is used where it is (linked, not copied) and selected. One on
  a removable drive is copied into `models\`, and so is every embedding and OCR file. The
  embedding model must match the catalog exactly. Uncatalogued GGUF files are allowed for
  the LLM role, with a warning.
- **Other tools' models:** GGUF chat models that Ollama, LM Studio or the Hugging Face
  cache already hold are found and run in place. Tamra never writes to those stores. See
  `2026-10-23-external-models-design.md`.
```

- [ ] **Step 2: Record the implementation in the feature spec**

In `docs/superpowers/specs/2026-10-23-external-models-design.md`, change the status line to `Status: implemented (plan docs/superpowers/plans/2026-10-23-external-models.md). Date: 2026-10-23.` Under "Import notes", replace the "When it was copied from a removable drive" bullet with:

```markdown
  - When it was copied, the note keeps today's wording ("Imported {file}. …"). A distinct
    removable-drive message was dropped: the server would have to tell a removable-drive copy
    from an embedding copy, and nothing else needs that.
```

In the `linked_models` bullets of section 1, change "`path` is absolute and ends in `.gguf`." to "`path` is absolute. It may have any extension, because a file that matches the catalog by sha256 may have any name."

- [ ] **Step 3: Full checks**

Run: `uv run ruff check; uv run ruff format --check`
Expected: no findings

Run: `uv run pytest -q`
Expected: PASS

Run: `npm --prefix ui test`
Expected: PASS

- [ ] **Step 4: Manual check on the dev machine**

```powershell
uv run tamra --dev
```

In a second terminal:

```powershell
npm --prefix ui run dev
```

Open `http://localhost:5173/#token=dev`, go to Settings → AI model, choose Import, and type the path of a GGUF file outside `%LOCALAPPDATA%\Tamra\models`.

Check all of the following:

- The note says "Using … where it is; it was not copied".
- The row shows "Linked file", and hovering its name shows the full path.
- No new file appears in `%LOCALAPPDATA%\Tamra\models`.
- A question gets an answer from the linked model.
- After "Remove from list", the row is gone and the file still exists.

If `.models` is fetched, also run `uv run python scripts/exe_smoke.py` after `./scripts/build.ps1`. Expected: it passes as before.

This machine has neither Ollama nor LM Studio, so tell the user that the real-store checks are theirs to do:

- With Ollama installed and one model pulled, the model appears as "From Ollama" with no import step.
- With LM Studio installed, the same with "From LM Studio".

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-10-02-tamra-design.md docs/superpowers/specs/2026-10-23-external-models-design.md
git commit -m "docs: imports link LLM files in place; other tools' models are found

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
