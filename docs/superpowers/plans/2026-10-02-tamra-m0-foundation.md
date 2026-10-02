# Tamra M0 — Foundation & Packaging Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scaffold the Tamra repo (Python core + React UI + CI). Prove that a PyInstaller-packaged `Tamra.exe` can embed text with bge-m3 (ONNX), store and query vectors in SQLite (`sqlite-vec` + FTS5 trigram), and generate tokens through a managed `llama-server` (Vulkan, CPU fallback).

**Architecture:** One Python package `tamra` (src layout, managed by uv) serves a FastAPI app on `127.0.0.1`, protected by a per-launch token. A pywebview window shows the React UI that Vite builds into `ui/dist`. Local generation uses llama.cpp's prebuilt `llama-server.exe` as a child process, called through its OpenAI-compatible API. A `tamra selfcheck` command exercises every risky dependency, so the packaged exe can be verified in CI and on real machines.

**Tech Stack:** Python 3.12, uv, FastAPI, uvicorn, pywebview, onnxruntime, tokenizers, numpy, sqlite-vec, httpx, pytest, ruff, PyInstaller; React 18+ / TypeScript / Vite / Vitest; GitHub Actions (windows-latest).

**Spec:** `docs/superpowers/specs/2026-10-02-tamra-design.md` (M0 row of §13). Read it before starting.

## Global Constraints

- Platform: Windows 10/11 x64 only. All commands are PowerShell unless marked otherwise.
- Python: `>=3.12,<3.13`, managed by uv (`.python-version` = `3.12`). Do **not** use the machine's system Python.
- License: Apache-2.0. Every bundled dependency must be Apache-2.0-compatible. **No AGPL or GPL libraries** (e.g. no PyMuPDF).
- Data directory: `%LOCALAPPDATA%\Tamra\`, overridable with the env var `TAMRA_DATA_DIR`.
- The embedding model always runs locally. Never call a remote embedding API.
- The API server binds `127.0.0.1` only. Every `/api/*` request must carry the header `X-Tamra-Token`.
- llama.cpp pin: release `b11344`, asset `llama-b11344-bin-win-vulkan-x64.zip`, sha256 `f561d5af233f802bd0605ff281fd204fba162dfaf09032a361e244ad292ba397`.
- Model weights and the llama.cpp binaries are never committed. They live in `.models/` and `vendor/` (both gitignored).
- Tests that need downloaded assets are marked `@pytest.mark.assets` and skipped by default. CI runs only unmarked tests.
- Prerequisites on the dev machine: `uv` (`winget install astral-sh.uv`), Node.js ≥ 22 with npm, git.

## File Structure

```
pyproject.toml              uv project, deps, ruff + pytest config
.python-version             3.12
README.md, LICENSE          project readme, Apache-2.0 text
src/tamra/__init__.py       __version__
src/tamra/__main__.py       CLI: `tamra` (run app), `tamra --dev`, `tamra selfcheck`
src/tamra/paths.py          data_dir(), resource_dir()
src/tamra/net.py            free_port()
src/tamra/store.py          connect() with sqlite-vec loaded, capabilities()
src/tamra/download.py       download_verified(), sha256_file()
src/tamra/embedder.py       Embedder (bge-m3 ONNX, CLS pooling, L2-normalised)
src/tamra/llm/__init__.py   (empty)
src/tamra/llm/openai_compat.py  OpenAICompatibleLLM, Message, LLMError
src/tamra/llm/llama_server.py   LlamaServer (child-process manager), LlamaServerError
src/tamra/server.py         create_app(token, ui_dir) — FastAPI, token middleware, static UI
src/tamra/app.py            run(dev) — uvicorn thread + pywebview window
src/tamra/selfcheck.py      run_selfcheck() — sqlite / embedding / llm checks
scripts/assets.json         pinned dev assets (llama zip, bge-m3, tiny GGUF)
scripts/fetch_assets.py     downloads + verifies + extracts assets
scripts/build.ps1           UI build → fetch llama → PyInstaller
packaging/entry.py          PyInstaller entry script
packaging/tamra.spec        PyInstaller spec (one-folder)
tests/conftest.py           asset fixtures (skip when missing)
tests/test_*.py             one test module per src module
ui/                         Vite + React + TS app (package.json, vite.config.ts, src/...)
.github/workflows/ci.yml    test job + package job
docs/spikes/2026-10-m0-results.md   measured spike results
```

---

### Task 1: Python project scaffold

**Files:**
- Create: `pyproject.toml`, `.python-version`, `README.md`, `LICENSE`, `src/tamra/__init__.py`, `src/tamra/paths.py`, `.github/workflows/ci.yml`
- Modify: `CLAUDE.md` (Commands section)
- Test: `tests/test_paths.py`

**Interfaces:**
- Produces: `tamra.__version__: str`; `tamra.paths.data_dir() -> Path` (creates the dir); `tamra.paths.resource_dir() -> Path` (repo root when running from source, `sys._MEIPASS` when frozen).

- [ ] **Step 1: Create project files**

`.python-version`:
```
3.12
```

`pyproject.toml`:
```toml
[project]
name = "tamra"
version = "0.1.0"
description = "Offline-first Windows app for asking questions about your documents, with verifiable citations."
readme = "README.md"
license = "Apache-2.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi>=0.115",
  "uvicorn>=0.30",
  "httpx>=0.27",
  "numpy>=2.0",
  "onnxruntime>=1.19",
  "tokenizers>=0.20",
  "sqlite-vec>=0.1.6",
  "pywebview>=5.3",
]

[project.scripts]
tamra = "tamra.__main__:main"

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.6", "pyinstaller>=6.10"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/tamra"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["assets: needs downloaded models/binaries (run scripts/fetch_assets.py)"]
addopts = "-m 'not assets'"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

`README.md`:
```markdown
# Tamra

Tamra (ตำรา) is an offline-first Windows app for asking questions about your own
documents — Thai, English, and Chinese — with answers that cite their sources and can be
checked passage by passage.

Status: early development (M0). Design: `docs/superpowers/specs/2026-10-02-tamra-design.md`.

License: Apache-2.0
```

`src/tamra/__init__.py`:
```python
__version__ = "0.1.0"
```

Fetch the license text:
```powershell
curl.exe -sSfL -o LICENSE https://www.apache.org/licenses/LICENSE-2.0.txt
```

- [ ] **Step 2: Write the failing test**

`tests/test_paths.py`:
```python
from pathlib import Path

from tamra import paths


def test_data_dir_uses_override_and_creates_it(monkeypatch, tmp_path):
    target = tmp_path / "custom"
    monkeypatch.setenv("TAMRA_DATA_DIR", str(target))
    assert paths.data_dir() == target
    assert target.is_dir()


def test_data_dir_defaults_to_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("TAMRA_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.data_dir() == tmp_path / "Tamra"


def test_resource_dir_is_repo_root_from_source():
    assert (paths.resource_dir() / "pyproject.toml").is_file()
    assert isinstance(paths.resource_dir(), Path)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv sync; uv run pytest tests/test_paths.py -v`
Expected: FAIL with `ImportError: cannot import name 'paths'`

- [ ] **Step 4: Write minimal implementation**

`src/tamra/paths.py`:
```python
import os
import sys
from pathlib import Path

APP_NAME = "Tamra"


def data_dir() -> Path:
    """Per-user data directory (%LOCALAPPDATA%\\Tamra), overridable via TAMRA_DATA_DIR."""
    override = os.environ.get("TAMRA_DATA_DIR")
    base = Path(override) if override else Path(os.environ["LOCALAPPDATA"]) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def resource_dir() -> Path:
    """Root for bundled resources (ui/dist, vendor/llama): _MEIPASS when frozen, else repo root."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]
```

- [ ] **Step 5: Run tests and lint to verify they pass**

Run: `uv run pytest -v; uv run ruff check; uv run ruff format --check`
Expected: 3 passed; ruff reports no errors. If `ruff format --check` fails, run `uv run ruff format` and re-check.

- [ ] **Step 6: Add CI workflow**

`.github/workflows/ci.yml`:
```yaml
name: CI
on:
  push:
  pull_request:

jobs:
  test:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --locked
      - run: uv run ruff check
      - run: uv run ruff format --check
      - run: uv run pytest
```

- [ ] **Step 7: Add commands to CLAUDE.md**

Replace the `## Status` section of `CLAUDE.md` with:
```markdown
## Commands

```powershell
uv sync                                   # install/refresh Python env (Python 3.12, managed by uv)
uv run pytest                             # unit tests (skips tests marked `assets`)
uv run pytest tests/test_paths.py::test_data_dir_uses_override_and_creates_it -v   # single test
uv run pytest -m assets                   # tests needing downloaded models/binaries
uv run ruff check; uv run ruff format     # lint, format
```
```

- [ ] **Step 8: Commit**

```powershell
git add pyproject.toml uv.lock .python-version README.md LICENSE src tests .github CLAUDE.md
git commit -m "chore: scaffold Python project, paths module, and CI"
```

---

### Task 2: SQLite store with sqlite-vec and FTS5 trigram

**Files:**
- Create: `src/tamra/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `tamra.store.connect(path: Path | str) -> sqlite3.Connection` (sqlite-vec loaded, WAL, foreign keys on, `check_same_thread=False`); `tamra.store.capabilities(conn) -> dict` with keys `sqlite_version: str`, `vec_version: str`, `fts5_trigram: bool`.

- [ ] **Step 1: Write the failing test**

`tests/test_store.py`:
```python
import sqlite_vec

from tamra import store


def test_connect_loads_sqlite_vec_and_trigram():
    conn = store.connect(":memory:")
    caps = store.capabilities(conn)
    assert caps["vec_version"].startswith("v")
    assert caps["fts5_trigram"] is True


def test_vec_knn_returns_nearest_first():
    conn = store.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE v USING vec0(embedding float[3])")
    for rowid, vec in enumerate([[1, 0, 0], [0, 1, 0], [0, 0, 1]], start=1):
        conn.execute(
            "INSERT INTO v(rowid, embedding) VALUES (?, ?)",
            (rowid, sqlite_vec.serialize_float32(vec)),
        )
    rows = conn.execute(
        "SELECT rowid FROM v WHERE embedding MATCH ? AND k = 2 ORDER BY distance",
        (sqlite_vec.serialize_float32([0.9, 0.1, 0.0]),),
    ).fetchall()
    assert [r[0] for r in rows] == [1, 2]


def test_trigram_fts_matches_thai_and_chinese_substrings():
    conn = store.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE f USING fts5(text, tokenize='trigram')")
    docs = ["สัญญาเช่าบ้านมีอายุสามปี", "The lease term is three years", "租赁期限为三年"]
    conn.executemany("INSERT INTO f(text) VALUES (?)", [(d,) for d in docs])

    def match(q: str) -> list[str]:
        return [r[0] for r in conn.execute("SELECT text FROM f WHERE f MATCH ?", (f'"{q}"',))]

    assert match("เช่าบ้าน") == [docs[0]]
    assert match("租赁期") == [docs[2]]
    assert match("lease term") == [docs[1]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL with `ImportError: cannot import name 'store'`

- [ ] **Step 3: Write minimal implementation**

`src/tamra/store.py`:
```python
import sqlite3
from pathlib import Path

import sqlite_vec


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the Tamra database with sqlite-vec loaded. The only module that touches SQL."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def capabilities(conn: sqlite3.Connection) -> dict[str, str | bool]:
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._trigram_probe USING fts5(t, tokenize='trigram')")
        conn.execute("DROP TABLE temp._trigram_probe")
        trigram = True
    except sqlite3.OperationalError:
        trigram = False
    return {
        "sqlite_version": conn.execute("SELECT sqlite_version()").fetchone()[0],
        "vec_version": conn.execute("SELECT vec_version()").fetchone()[0],
        "fts5_trigram": trigram,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store.py -v`
Expected: 3 passed.
If it fails with `AttributeError: ... enable_load_extension`, the uv-managed Python lacks extension loading. Stop and report this, because it changes the storage decision. Do not work around it silently.

- [ ] **Step 5: Commit**

```powershell
git add src/tamra/store.py tests/test_store.py
git commit -m "feat(store): SQLite connection with sqlite-vec and FTS5 trigram"
```

---

### Task 3: Verified downloads and dev asset fetcher

**Files:**
- Create: `src/tamra/download.py`, `scripts/assets.json`, `scripts/fetch_assets.py`
- Test: `tests/test_download.py`

**Interfaces:**
- Produces: `tamra.download.sha256_file(path: Path) -> str`; `tamra.download.download_verified(url: str, dest: Path, sha256: str, transport: httpx.BaseTransport | None = None) -> Path` (skips if `dest` already matches; writes `<dest>.part` then renames; raises `ChecksumError` and leaves no file on mismatch); `tamra.download.ChecksumError`.
- Produces (files on disk after `uv run python scripts/fetch_assets.py`): `vendor/llama/llama-server.exe` (+ DLLs), `.models/bge-m3/model.onnx`, `.models/bge-m3/tokenizer.json`, `.models/qwen2.5-0.5b-instruct-q4_k_m.gguf`.

- [ ] **Step 1: Write the failing test**

`tests/test_download.py`:
```python
import hashlib

import httpx
import pytest

from tamra.download import ChecksumError, download_verified, sha256_file

PAYLOAD = b"tamra" * 1000
GOOD = hashlib.sha256(PAYLOAD).hexdigest()


def serve(payload: bytes) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, content=payload))


def test_downloads_and_verifies(tmp_path):
    dest = tmp_path / "sub" / "file.bin"
    assert download_verified("https://x/file", dest, GOOD, transport=serve(PAYLOAD)) == dest
    assert dest.read_bytes() == PAYLOAD
    assert sha256_file(dest) == GOOD
    assert not dest.with_name("file.bin.part").exists()


def test_checksum_mismatch_raises_and_leaves_nothing(tmp_path):
    dest = tmp_path / "file.bin"
    with pytest.raises(ChecksumError):
        download_verified("https://x/file", dest, "0" * 64, transport=serve(PAYLOAD))
    assert list(tmp_path.iterdir()) == []


def test_existing_valid_file_is_not_downloaded_again(tmp_path):
    dest = tmp_path / "file.bin"
    dest.write_bytes(PAYLOAD)

    def fail(request):
        raise AssertionError("should not download")

    download_verified("https://x/file", dest, GOOD, transport=httpx.MockTransport(fail))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_download.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tamra.download'`

- [ ] **Step 3: Write minimal implementation**

`src/tamra/download.py`:
```python
import hashlib
from pathlib import Path

import httpx


class ChecksumError(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download_verified(
    url: str, dest: Path, sha256: str, transport: httpx.BaseTransport | None = None
) -> Path:
    """Download url to dest, verifying sha256. Never leaves an unverified file at dest."""
    if dest.exists() and sha256_file(dest) == sha256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    timeout = httpx.Timeout(30.0, read=300.0)
    with httpx.Client(follow_redirects=True, timeout=timeout, transport=transport) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with part.open("wb") as f:
                for chunk in response.iter_bytes(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
    if h.hexdigest() != sha256:
        part.unlink()
        raise ChecksumError(f"{url}: expected {sha256}, got {h.hexdigest()}")
    part.replace(dest)
    return dest
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_download.py -v`
Expected: 3 passed.

- [ ] **Step 5: Add the pinned asset list and fetcher**

`scripts/assets.json` (Hugging Face URLs are pinned to commit revisions; hashes are the published LFS sha256):
```json
{
  "llama": {
    "url": "https://github.com/ggml-org/llama.cpp/releases/download/b11344/llama-b11344-bin-win-vulkan-x64.zip",
    "sha256": "f561d5af233f802bd0605ff281fd204fba162dfaf09032a361e244ad292ba397",
    "dest": "vendor/downloads/llama-b11344-bin-win-vulkan-x64.zip",
    "extract_to": "vendor/llama"
  },
  "bge-m3-model": {
    "url": "https://huggingface.co/Xenova/bge-m3/resolve/4de13258303883538bd53b696b452bf8099f0858/onnx/model_int8.onnx",
    "sha256": "a206e10e995aa2a833924bcd725ba5dd6c3425cd34bac3cf2b5677cd2a1c51d6",
    "dest": ".models/bge-m3/model.onnx"
  },
  "bge-m3-tokenizer": {
    "url": "https://huggingface.co/Xenova/bge-m3/resolve/4de13258303883538bd53b696b452bf8099f0858/tokenizer.json",
    "sha256": "6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790",
    "dest": ".models/bge-m3/tokenizer.json"
  },
  "qwen-0.5b": {
    "url": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/9217f5db79a29953eb74d5343926648285ec7e67/qwen2.5-0.5b-instruct-q4_k_m.gguf",
    "sha256": "74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db",
    "dest": ".models/qwen2.5-0.5b-instruct-q4_k_m.gguf"
  }
}
```

`scripts/fetch_assets.py`:
```python
"""Download pinned dev/build assets. Usage: uv run python scripts/fetch_assets.py [name ...]"""

import json
import sys
import zipfile
from pathlib import Path

from tamra.download import download_verified

ROOT = Path(__file__).resolve().parents[1]


def main(names: list[str]) -> int:
    assets = json.loads((ROOT / "scripts" / "assets.json").read_text(encoding="utf-8"))
    unknown = set(names) - assets.keys()
    if unknown:
        print(f"unknown asset(s): {', '.join(sorted(unknown))}; known: {', '.join(assets)}")
        return 2
    for name in names or list(assets):
        spec = assets[name]
        dest = ROOT / spec["dest"]
        print(f"{name}: {dest}")
        download_verified(spec["url"], dest, spec["sha256"])
        if "extract_to" in spec:
            with zipfile.ZipFile(dest) as zf:
                zf.extractall(ROOT / spec["extract_to"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 6: Fetch the assets for real**

Run: `uv run python scripts/fetch_assets.py`
Expected: four lines of output, then `vendor/llama/llama-server.exe` exists. About 1.1 GB is downloaded in total.
Verify: `Test-Path vendor/llama/llama-server.exe, .models/bge-m3/model.onnx, .models/bge-m3/tokenizer.json, .models/qwen2.5-0.5b-instruct-q4_k_m.gguf` → four `True`.

- [ ] **Step 7: Commit**

```powershell
git add src/tamra/download.py tests/test_download.py scripts/assets.json scripts/fetch_assets.py
git commit -m "feat(download): sha256-verified downloads and pinned dev asset fetcher"
```

---

### Task 4: bge-m3 embedder

**Files:**
- Create: `src/tamra/embedder.py`, `tests/conftest.py`
- Test: `tests/test_embedder.py`

**Interfaces:**
- Consumes: assets from Task 3 (`.models/bge-m3/model.onnx`, `.models/bge-m3/tokenizer.json`).
- Produces: `tamra.embedder.Embedder(model_dir: Path, max_length: int = 512)`, with `.dim == 1024` and `.embed(texts: list[str], batch_size: int = 16) -> np.ndarray` (shape `(len(texts), 1024)`, float32, rows L2-normalised). Also pytest fixtures `bge_dir`, `qwen_gguf`, `llama_exe` in `tests/conftest.py`.

- [ ] **Step 1: Add asset fixtures**

`tests/conftest.py`:
```python
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _require(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"missing {path}; run: uv run python scripts/fetch_assets.py")
    return path


@pytest.fixture(scope="session")
def bge_dir() -> Path:
    return _require(ROOT / ".models" / "bge-m3" / "model.onnx").parent


@pytest.fixture(scope="session")
def qwen_gguf() -> Path:
    return _require(ROOT / ".models" / "qwen2.5-0.5b-instruct-q4_k_m.gguf")


@pytest.fixture(scope="session")
def llama_exe() -> Path:
    return _require(ROOT / "vendor" / "llama" / "llama-server.exe")
```

- [ ] **Step 2: Write the failing test**

`tests/test_embedder.py`:
```python
import numpy as np
import pytest

from tamra.embedder import Embedder

pytestmark = pytest.mark.assets


@pytest.fixture(scope="module")
def embedder(bge_dir):
    return Embedder(bge_dir)


def test_shape_dtype_and_normalisation(embedder):
    vecs = embedder.embed(["hello", "สวัสดี", "你好"])
    assert vecs.shape == (3, 1024)
    assert vecs.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-4)


def test_empty_input(embedder):
    assert embedder.embed([]).shape == (0, 1024)


def test_cross_lingual_similarity(embedder):
    th, en_same, zh_same, en_other = embedder.embed(
        [
            "แมวกำลังนอนหลับอยู่บนโซฟา",
            "A cat is sleeping on the sofa",
            "猫正在沙发上睡觉",
            "The stock market fell sharply today",
        ]
    )
    assert th @ en_same > th @ en_other + 0.1
    assert th @ zh_same > th @ en_other + 0.1


def test_padding_does_not_change_embedding(embedder):
    short = "Tamra answers questions about documents."
    alone = embedder.embed([short])[0]
    batched = embedder.embed([short, short + " " + "padding forces longer batch. " * 20])[0]
    np.testing.assert_allclose(alone, batched, atol=1e-3)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest -m assets tests/test_embedder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tamra.embedder'`

- [ ] **Step 4: Write minimal implementation**

`src/tamra/embedder.py`:
```python
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


class Embedder:
    """bge-m3 dense embeddings: CLS token of last_hidden_state, L2-normalised."""

    dim = 1024

    def __init__(self, model_dir: Path, max_length: int = 512):
        self._tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self._tok.enable_truncation(max_length)
        self._tok.enable_padding(pad_id=self._tok.token_to_id("<pad>"), pad_token="<pad>")
        self._session = ort.InferenceSession(
            str(model_dir / "model.onnx"), providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self._session.get_inputs()}

    def embed(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        out = []
        for start in range(0, len(texts), batch_size):
            encodings = self._tok.encode_batch(texts[start : start + batch_size])
            ids = np.array([e.ids for e in encodings], dtype=np.int64)
            feeds = {
                "input_ids": ids,
                "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
            }
            if "token_type_ids" in self._input_names:
                feeds["token_type_ids"] = np.zeros_like(ids)
            hidden = self._session.run(None, feeds)[0]
            cls = hidden[:, 0, :]
            out.append(cls / np.linalg.norm(cls, axis=1, keepdims=True))
        if not out:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack(out).astype(np.float32)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest -m assets tests/test_embedder.py -v`
Expected: 4 passed.
If `hidden` turns out to be 2-D (an already-pooled output), print `[o.name for o in self._session.get_outputs()]`, pick the `last_hidden_state` output by name instead of index 0, and re-run.

- [ ] **Step 6: Run the default suite to confirm asset tests are skipped**

Run: `uv run pytest -v`
Expected: all non-asset tests pass, and `tests/test_embedder.py` is deselected.

- [ ] **Step 7: Commit**

```powershell
git add src/tamra/embedder.py tests/conftest.py tests/test_embedder.py
git commit -m "feat(embedder): bge-m3 ONNX dense embeddings"
```

---

### Task 5: OpenAI-compatible streaming client

**Files:**
- Create: `src/tamra/llm/__init__.py` (empty), `src/tamra/llm/openai_compat.py`
- Test: `tests/test_openai_compat.py`

**Interfaces:**
- Produces: `tamra.llm.openai_compat.Message` (`TypedDict`: `role: str`, `content: str`); `OpenAICompatibleLLM(base_url: str, model: str, api_key: str | None = None, transport: httpx.BaseTransport | None = None)`, with `.generate(messages: list[Message], max_tokens: int = 1024) -> Iterator[str]` and `.close()`; `LLMError`. `base_url` excludes `/v1` (e.g. `http://127.0.0.1:8080`, `http://localhost:11434`).

- [ ] **Step 1: Write the failing test**

`tests/test_openai_compat.py`:
```python
import json

import httpx
import pytest

from tamra.llm.openai_compat import LLMError, OpenAICompatibleLLM

SSE = (
    'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
    'data: {"choices":[],"usage":{"total_tokens":5}}\n\n'
    "data: [DONE]\n\n"
)


def test_streams_content_deltas_and_sends_expected_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text=SSE, headers={"content-type": "text/event-stream"})

    llm = OpenAICompatibleLLM(
        "http://llm.test", "m1", api_key="k", transport=httpx.MockTransport(handler)
    )
    tokens = list(llm.generate([{"role": "user", "content": "hi"}], max_tokens=8))

    assert tokens == ["Hel", "lo"]
    assert seen["url"] == "http://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"] == {
        "model": "m1",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 8,
        "stream": True,
    }


def test_no_auth_header_without_key():
    def handler(request):
        assert "authorization" not in request.headers
        return httpx.Response(200, text="data: [DONE]\n\n")

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    assert list(llm.generate([{"role": "user", "content": "hi"}])) == []


def test_http_error_raises_llm_error():
    llm = OpenAICompatibleLLM(
        "http://llm.test",
        "m1",
        transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad key")),
    )
    with pytest.raises(LLMError, match="401"):
        list(llm.generate([{"role": "user", "content": "hi"}]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_openai_compat.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tamra.llm'`

- [ ] **Step 3: Write minimal implementation**

`src/tamra/llm/__init__.py`: empty file.

`src/tamra/llm/openai_compat.py`:
```python
import json
from collections.abc import Iterator
from typing import TypedDict

import httpx


class Message(TypedDict):
    role: str  # "system" | "user" | "assistant"
    content: str


class LLMError(Exception):
    pass


class OpenAICompatibleLLM:
    """Streaming chat client for any OpenAI-compatible server (llama-server, Ollama, LM Studio)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self._model = model
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=httpx.Timeout(10.0, read=120.0),
            transport=transport,
        )

    def generate(self, messages: list[Message], max_tokens: int = 1024) -> Iterator[str]:
        body = {"model": self._model, "messages": messages, "max_tokens": max_tokens, "stream": True}
        with self._client.stream("POST", "/v1/chat/completions", json=body) as response:
            if response.status_code != 200:
                response.read()
                raise LLMError(f"HTTP {response.status_code}: {response.text[:500]}")
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    return
                choices = json.loads(data).get("choices") or []
                content = choices[0].get("delta", {}).get("content") if choices else None
                if content:
                    yield content

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_openai_compat.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```powershell
git add src/tamra/llm tests/test_openai_compat.py
git commit -m "feat(llm): OpenAI-compatible streaming client"
```

---

### Task 6: llama-server process manager

**Files:**
- Create: `src/tamra/net.py`, `src/tamra/llm/llama_server.py`
- Test: `tests/test_llama_server.py`

**Interfaces:**
- Consumes: `OpenAICompatibleLLM` (Task 5); asset fixtures `llama_exe`, `qwen_gguf` (Task 4).
- Produces: `tamra.net.free_port() -> int`. `tamra.llm.llama_server.LlamaServer(exe: Path, model: Path, log_file: Path, ctx_size: int = 4096, gpu: bool = True, device: str | None = None)`:
  - Attributes: `.port: int`, `.base_url: str`, `.gpu_used: bool | None`.
  - Methods: `.start(timeout: float = 120.0) -> LlamaServer`, `.stop()`, `.args(gpu: bool) -> list[str]`.
  - It is a context manager (`__enter__` calls `start`, `__exit__` calls `stop`).
  - On failure it raises `LlamaServerError`.
  - With `gpu=True` it tries the GPU first, then retries on CPU (`--device none`).

- [ ] **Step 1: Write the failing test**

`tests/test_llama_server.py`:
```python
from pathlib import Path

import pytest

from tamra.llm.llama_server import LlamaServer, LlamaServerError
from tamra.llm.openai_compat import OpenAICompatibleLLM
from tamra.net import free_port


def test_free_port_is_usable_int():
    port = free_port()
    assert 1024 < port < 65536


def test_args_gpu_and_cpu(tmp_path):
    srv = LlamaServer(Path("llama-server.exe"), Path("m.gguf"), tmp_path / "log.txt", ctx_size=2048)
    base = ["llama-server.exe", "-m", "m.gguf", "-c", "2048", "--host", "127.0.0.1"]
    assert srv.args(gpu=True)[:7] == base
    assert srv.args(gpu=True)[-2:] == ["-ngl", "99"]
    assert srv.args(gpu=False)[-2:] == ["--device", "none"]


def test_args_with_explicit_device(tmp_path):
    srv = LlamaServer(Path("x.exe"), Path("m.gguf"), tmp_path / "l.txt", device="Vulkan1")
    assert srv.args(gpu=True)[-4:] == ["--device", "Vulkan1", "-ngl", "99"]


@pytest.mark.assets
@pytest.mark.parametrize("gpu", [True, False])
def test_generates_tokens(llama_exe, qwen_gguf, tmp_path, gpu):
    with LlamaServer(llama_exe, qwen_gguf, tmp_path / "llama.log", gpu=gpu) as srv:
        llm = OpenAICompatibleLLM(srv.base_url, "local")
        text = "".join(
            llm.generate([{"role": "user", "content": "Reply with one word: OK"}], max_tokens=8)
        )
        llm.close()
    assert text.strip()
    assert srv.gpu_used is gpu or (gpu and srv.gpu_used is False)


@pytest.mark.assets
def test_bad_model_raises(llama_exe, tmp_path):
    bogus = tmp_path / "not-a-model.gguf"
    bogus.write_bytes(b"nope")
    with pytest.raises(LlamaServerError):
        LlamaServer(llama_exe, bogus, tmp_path / "llama.log").start(timeout=30)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llama_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tamra.llm.llama_server'`

- [ ] **Step 3: Write minimal implementation**

`src/tamra/net.py`:
```python
import socket


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
```

`src/tamra/llm/llama_server.py`:
```python
import subprocess
import time
from pathlib import Path
from typing import IO

import httpx

from tamra.net import free_port


class LlamaServerError(Exception):
    pass


class LlamaServer:
    """Runs llama.cpp's llama-server as a child process bound to 127.0.0.1."""

    def __init__(
        self,
        exe: Path,
        model: Path,
        log_file: Path,
        ctx_size: int = 4096,
        gpu: bool = True,
        device: str | None = None,
    ):
        self.exe, self.model, self.log_file = exe, model, log_file
        self.ctx_size, self.gpu, self.device = ctx_size, gpu, device
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.gpu_used: bool | None = None
        self._proc: subprocess.Popen | None = None
        self._log: IO[bytes] | None = None

    def args(self, gpu: bool) -> list[str]:
        args = [str(self.exe), "-m", str(self.model), "-c", str(self.ctx_size)]
        args += ["--host", "127.0.0.1", "--port", str(self.port)]
        if not gpu:
            return args + ["--device", "none"]
        if self.device:
            args += ["--device", self.device]
        return args + ["-ngl", "99"]

    def start(self, timeout: float = 120.0) -> "LlamaServer":
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        for gpu in [True, False] if self.gpu else [False]:
            self._log = self.log_file.open("ab")
            self._proc = subprocess.Popen(
                self.args(gpu),
                stdout=self._log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if self._wait_healthy(timeout):
                self.gpu_used = gpu
                return self
            self.stop()
        raise LlamaServerError(f"llama-server failed to start; see {self.log_file}")

    def _wait_healthy(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc is None or self._proc.poll() is not None:
                return False
            try:
                if httpx.get(f"{self.base_url}/health", timeout=2.0).status_code == 200:
                    return True
            except httpx.TransportError:
                pass
            time.sleep(0.25)
        return False

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        self._proc = None
        if self._log is not None:
            self._log.close()
            self._log = None

    def __enter__(self) -> "LlamaServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
```

- [ ] **Step 4: Run unit tests, then asset tests**

Run: `uv run pytest tests/test_llama_server.py -v`
Expected: 3 passed (asset tests deselected).
Run: `uv run pytest -m assets tests/test_llama_server.py -v`
Expected: 3 passed. Check the log file to confirm that the GPU run used a Vulkan device. If the GPU run fell back to CPU, note it for Task 10's spike report; it is not a test failure.

- [ ] **Step 5: Commit**

```powershell
git add src/tamra/net.py src/tamra/llm/llama_server.py tests/test_llama_server.py
git commit -m "feat(llm): llama-server child process with GPU→CPU fallback"
```

---

### Task 7: API server, desktop window, and CLI entry

**Files:**
- Create: `src/tamra/server.py`, `src/tamra/app.py`, `src/tamra/__main__.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `tamra.__version__`, `paths.resource_dir()`, `net.free_port()`.
- Produces:
  - `tamra.server.create_app(token: str, ui_dir: Path | None) -> FastAPI`. `GET /api/health` → `{"status": "ok", "version": str}`. Any `/api/*` request without a correct `X-Tamra-Token` gets 401. When `ui_dir` is given, it is served at `/` (with `index.html`).
  - `tamra.app.run(dev: bool = False) -> None`. In dev mode the API is on port 8765 with token `dev`, and no window opens.
  - `tamra.__main__.main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write the failing test**

`tests/test_server.py`:
```python
from fastapi.testclient import TestClient

import tamra
from tamra.server import create_app


def test_health_requires_token():
    client = TestClient(create_app("secret", ui_dir=None))
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"X-Tamra-Token": "wrong"}).status_code == 401
    ok = client.get("/api/health", headers={"X-Tamra-Token": "secret"})
    assert ok.status_code == 200
    assert ok.json() == {"status": "ok", "version": tamra.__version__}


def test_serves_ui_without_token(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Tamra</h1>", encoding="utf-8")
    client = TestClient(create_app("secret", ui_dir=tmp_path))
    response = client.get("/")
    assert response.status_code == 200
    assert "Tamra" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tamra.server'`

- [ ] **Step 3: Write minimal implementation**

`src/tamra/server.py`:
```python
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import tamra


def create_app(token: str, ui_dir: Path | None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if request.url.path.startswith("/api/") and not secrets.compare_digest(
            request.headers.get("x-tamra-token", ""), token
        ):
            return JSONResponse({"detail": "invalid token"}, status_code=401)
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": tamra.__version__}

    if ui_dir is not None:
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
    return app
```

`src/tamra/app.py`:
```python
import secrets
import threading
import time

import httpx
import uvicorn

from tamra.net import free_port
from tamra.paths import resource_dir
from tamra.server import create_app

DEV_PORT = 8765
DEV_TOKEN = "dev"


def _wait_until_up(port: int, token: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(
                f"http://127.0.0.1:{port}/api/health", headers={"X-Tamra-Token": token}
            )
            if r.status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Tamra core did not start")


def run(dev: bool = False) -> None:
    port, token = (DEV_PORT, DEV_TOKEN) if dev else (free_port(), secrets.token_urlsafe(32))
    ui_dir = None if dev else resource_dir() / "ui" / "dist"
    config = uvicorn.Config(
        create_app(token, ui_dir), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_until_up(port, token)

    if dev:
        print(f"Tamra core on http://127.0.0.1:{port} (token: {token})")
        print("Run `npm run dev` in ui/ and open http://localhost:5173/#token=dev")
        while thread.is_alive():
            thread.join(0.5)
        return

    import webview  # imported lazily: heavy, and not needed for dev mode or selfcheck

    webview.create_window("Tamra", f"http://127.0.0.1:{port}/#token={token}", width=1200, height=800)
    webview.start()
    server.should_exit = True
    thread.join(timeout=5)
```

`src/tamra/__main__.py`:
```python
import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tamra")
    parser.add_argument("--dev", action="store_true", help="API only on :8765, token 'dev'")
    args = parser.parse_args(argv)

    from tamra.app import run

    run(dev=args.dev)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_server.py -v`
Expected: 2 passed.

- [ ] **Step 5: Smoke-test dev mode by hand**

Run (terminal 1): `uv run tamra --dev`
Run (terminal 2): `curl.exe -s -H "X-Tamra-Token: dev" http://127.0.0.1:8765/api/health`
Expected: `{"status":"ok","version":"0.1.0"}`. Stop terminal 1 with Ctrl+C.

- [ ] **Step 6: Commit**

```powershell
git add src/tamra/server.py src/tamra/app.py src/tamra/__main__.py tests/test_server.py
git commit -m "feat(app): token-protected FastAPI core, pywebview window, CLI entry"
```

---

### Task 8: React UI scaffold

**Files:**
- Create: `ui/package.json` (via npm), `ui/tsconfig.json`, `ui/vite.config.ts`, `ui/index.html`, `ui/src/main.tsx`, `ui/src/App.tsx`, `ui/src/api.ts`
- Modify: `.github/workflows/ci.yml`, `CLAUDE.md`
- Test: `ui/src/api.test.ts`

**Interfaces:**
- Consumes: `GET /api/health` with the `X-Tamra-Token` header (Task 7). The token arrives in the URL fragment `#token=...`.
- Produces: `tokenFromHash(hash: string): string | null`; `apiGet<T>(path: string, fetchImpl?: typeof fetch): Promise<T>` (throws `Error("<path>: HTTP <status>")` on a non-2xx response). Built output goes to `ui/dist/` (consumed by Task 10).

- [ ] **Step 1: Create the package and install deps**

```powershell
New-Item -ItemType Directory -Force ui | Out-Null
Set-Location ui
npm init -y
npm pkg set name=tamra-ui private=true type=module
npm pkg set scripts.dev="vite" scripts.build="tsc && vite build" scripts.test="vitest run"
npm pkg delete main
npm install react react-dom
npm install -D vite @vitejs/plugin-react typescript @types/react @types/react-dom vitest jsdom
Set-Location ..
```

- [ ] **Step 2: Add config and entry files**

`ui/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true
  },
  "include": ["src", "vite.config.ts"]
}
```

`ui/vite.config.ts`:
```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
  test: { environment: "jsdom" },
});
```

`ui/index.html`:
```html
<!doctype html>
<html lang="th">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Tamra</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`ui/src/main.tsx`:
```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 3: Write the failing test**

`ui/src/api.test.ts`:
```ts
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet, tokenFromHash } from "./api";

describe("tokenFromHash", () => {
  it("reads the token from the fragment", () => {
    expect(tokenFromHash("#token=abc")).toBe("abc");
    expect(tokenFromHash("#x=1&token=a%2Bb")).toBe("a+b");
    expect(tokenFromHash("")).toBeNull();
  });
});

describe("apiGet", () => {
  beforeEach(() => {
    window.location.hash = "#token=t0k";
  });

  it("sends the token header and returns JSON", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ status: "ok" })));
    await expect(apiGet("/api/health", fetchImpl)).resolves.toEqual({ status: "ok" });
    expect(fetchImpl).toHaveBeenCalledWith("/api/health", {
      headers: { "X-Tamra-Token": "t0k" },
    });
  });

  it("throws on HTTP errors", async () => {
    const fetchImpl = vi.fn(async () => new Response("no", { status: 401 }));
    await expect(apiGet("/api/health", fetchImpl)).rejects.toThrow("/api/health: HTTP 401");
  });
});
```

- [ ] **Step 4: Run test to verify it fails**

Run: `npm --prefix ui test`
Expected: FAIL with `Failed to resolve import "./api"`

- [ ] **Step 5: Write minimal implementation**

`ui/src/api.ts`:
```ts
export function tokenFromHash(hash: string): string | null {
  const match = /(?:^#|&)token=([^&]+)/.exec(hash);
  return match ? decodeURIComponent(match[1]) : null;
}

export async function apiGet<T>(path: string, fetchImpl: typeof fetch = fetch): Promise<T> {
  const token = tokenFromHash(window.location.hash) ?? "";
  const response = await fetchImpl(path, { headers: { "X-Tamra-Token": token } });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return (await response.json()) as T;
}
```

`ui/src/App.tsx`:
```tsx
import { useEffect, useState } from "react";
import { apiGet } from "./api";

type Health = { status: string; version: string };

export default function App() {
  const [message, setMessage] = useState("Connecting to Tamra core…");

  useEffect(() => {
    apiGet<Health>("/api/health")
      .then((h) => setMessage(`Tamra core: ${h.status} (v${h.version})`))
      .catch((e: Error) => setMessage(`Tamra core unreachable: ${e.message}`));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: 24 }}>
      <h1>Tamra</h1>
      <p>{message}</p>
    </main>
  );
}
```

- [ ] **Step 6: Run tests and build**

Run: `npm --prefix ui test; npm --prefix ui run build`
Expected: 3 tests pass; the build writes `ui/dist/index.html`.

- [ ] **Step 7: Smoke-test against the core by hand**

Run `uv run tamra --dev` in one terminal and `npm --prefix ui run dev` in another. Open `http://localhost:5173/#token=dev`.
Expected: the page shows `Tamra core: ok (v0.1.0)`.

- [ ] **Step 8: Add UI steps to CI and commands to CLAUDE.md**

Append these steps to the `test` job in `.github/workflows/ci.yml`, after the pytest step:
```yaml
      - uses: actions/setup-node@v5
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: ui/package-lock.json
      - run: npm ci
        working-directory: ui
      - run: npm test
        working-directory: ui
      - run: npm run build
        working-directory: ui
```

Append to the PowerShell block under `## Commands` in `CLAUDE.md`:
```powershell
uv run tamra --dev                        # core API only on :8765, token "dev"
npm --prefix ui run dev                   # UI with hot reload → http://localhost:5173/#token=dev
npm --prefix ui test                      # UI unit tests (Vitest)
npm --prefix ui run build                 # build UI into ui/dist (bundled by PyInstaller)
```

- [ ] **Step 9: Commit**

```powershell
git add ui/package.json ui/package-lock.json ui/tsconfig.json ui/vite.config.ts ui/index.html ui/src .github/workflows/ci.yml CLAUDE.md
git commit -m "feat(ui): React + Vite scaffold with token-aware API client"
```

---

### Task 9: `tamra selfcheck`

**Files:**
- Create: `src/tamra/selfcheck.py`
- Modify: `src/tamra/__main__.py`
- Test: `tests/test_selfcheck.py`

**Interfaces:**
- Consumes: `store.connect/capabilities` (Task 2), `Embedder` (Task 4), `LlamaServer` (Task 6), `OpenAICompatibleLLM` (Task 5), `paths.resource_dir()` / `paths.data_dir()` (Task 1).
- Produces: `tamra.selfcheck.run_selfcheck(embed_model_dir: Path | None, llm_model: Path | None, llama_exe: Path, log_dir: Path) -> dict`. The result is `{"ok": bool, "checks": {"sqlite": {...}, "embedding"?: {...}, "llm"?: {...}}}`. Each check is a dict with `"ok": bool` plus measurements, or `"error": str` on failure. A check runs only when its input path is given. CLI: `tamra selfcheck [--embed-model-dir DIR] [--llm-model FILE] [--report FILE]` exits 0 when ok, 1 otherwise.

- [ ] **Step 1: Write the failing test**

`tests/test_selfcheck.py`:
```python
import json

import pytest

from tamra.__main__ import main
from tamra.selfcheck import run_selfcheck


def test_sqlite_only_selfcheck(tmp_path):
    report = run_selfcheck(None, None, tmp_path / "missing.exe", tmp_path)
    assert report["ok"] is True
    assert set(report["checks"]) == {"sqlite"}
    assert report["checks"]["sqlite"]["fts5_trigram"] is True


def test_cli_writes_report_and_exit_code(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path / "data"))
    out = tmp_path / "report.json"
    assert main(["selfcheck", "--report", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["ok"] is True


def test_failed_check_is_reported_not_raised(tmp_path):
    report = run_selfcheck(tmp_path / "no-model-here", None, tmp_path / "x.exe", tmp_path)
    assert report["ok"] is False
    assert report["checks"]["embedding"]["ok"] is False
    assert report["checks"]["embedding"]["error"]


@pytest.mark.assets
def test_full_selfcheck(bge_dir, qwen_gguf, llama_exe, tmp_path):
    report = run_selfcheck(bge_dir, qwen_gguf, llama_exe, tmp_path)
    assert report["ok"] is True, json.dumps(report, ensure_ascii=False, indent=2)
    assert report["checks"]["embedding"]["passages_per_sec"] > 0
    assert report["checks"]["llm"]["tokens"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_selfcheck.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tamra.selfcheck'`

- [ ] **Step 3: Write minimal implementation**

`src/tamra/selfcheck.py`:
```python
"""Exercises every risky dependency; runs from source and from the packaged exe."""

import time
from collections.abc import Callable
from pathlib import Path

import sqlite_vec

from tamra import store

PASSAGE = "Tamra ตอบคำถามจากเอกสารพร้อมอ้างอิงแหล่งที่มา 它会引用来源。 " * 12


def _guard(check: Callable[[], dict]) -> dict:
    try:
        return check()
    except Exception as e:  # report, never crash: this runs on unknown machines
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _check_sqlite() -> dict:
    conn = store.connect(":memory:")
    caps = store.capabilities(conn)
    conn.execute("CREATE VIRTUAL TABLE v USING vec0(embedding float[2])")
    conn.execute("INSERT INTO v(rowid, embedding) VALUES (1, ?)", (sqlite_vec.serialize_float32([1, 0]),))
    hit = conn.execute(
        "SELECT rowid FROM v WHERE embedding MATCH ? AND k = 1",
        (sqlite_vec.serialize_float32([1, 0]),),
    ).fetchone()
    return {"ok": bool(caps["fts5_trigram"]) and hit == (1,), **caps}


def _check_embedding(model_dir: Path) -> dict:
    from tamra.embedder import Embedder

    t0 = time.perf_counter()
    embedder = Embedder(model_dir)
    load_s = time.perf_counter() - t0

    docs = ["แมวกำลังนอนหลับอยู่บนโซฟา", "汽车停在路边"]
    conn = store.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE v USING vec0(embedding float[1024])")
    for rowid, vec in enumerate(embedder.embed(docs), start=1):
        conn.execute("INSERT INTO v(rowid, embedding) VALUES (?, ?)", (rowid, vec.tobytes()))
    query = embedder.embed(["A cat sleeping on a couch"])[0]
    nearest = conn.execute(
        "SELECT rowid FROM v WHERE embedding MATCH ? AND k = 1", (query.tobytes(),)
    ).fetchone()[0]

    t0 = time.perf_counter()
    embedder.embed([PASSAGE] * 32)
    rate = 32 / (time.perf_counter() - t0)
    return {"ok": nearest == 1, "load_s": round(load_s, 2), "passages_per_sec": round(rate, 2)}


def _check_llm(llama_exe: Path, model: Path, log_dir: Path) -> dict:
    from tamra.llm.llama_server import LlamaServer
    from tamra.llm.openai_compat import OpenAICompatibleLLM

    t0 = time.perf_counter()
    with LlamaServer(llama_exe, model, log_dir / "llama-server.log") as srv:
        start_s = time.perf_counter() - t0
        llm = OpenAICompatibleLLM(srv.base_url, "local")
        t1 = time.perf_counter()
        first_token_s, tokens = None, 0
        for _ in llm.generate([{"role": "user", "content": "Count from 1 to 20."}], max_tokens=64):
            first_token_s = first_token_s or time.perf_counter() - t1
            tokens += 1
        gen_s = time.perf_counter() - t1
        llm.close()
        gpu_used = srv.gpu_used
    return {
        "ok": tokens > 0,
        "gpu_used": gpu_used,
        "server_start_s": round(start_s, 2),
        "first_token_s": round(first_token_s or 0, 2),
        "tokens": tokens,
        "tokens_per_sec": round(tokens / gen_s, 2) if gen_s else 0,
    }


def run_selfcheck(
    embed_model_dir: Path | None, llm_model: Path | None, llama_exe: Path, log_dir: Path
) -> dict:
    checks = {"sqlite": _guard(_check_sqlite)}
    if embed_model_dir is not None:
        checks["embedding"] = _guard(lambda: _check_embedding(embed_model_dir))
    if llm_model is not None:
        checks["llm"] = _guard(lambda: _check_llm(llama_exe, llm_model, log_dir))
    return {"ok": all(c["ok"] for c in checks.values()), "checks": checks}
```

Replace `src/tamra/__main__.py` with:
```python
import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tamra")
    parser.add_argument("--dev", action="store_true", help="API only on :8765, token 'dev'")
    sub = parser.add_subparsers(dest="command")
    sc = sub.add_parser("selfcheck", help="verify bundled dependencies on this machine")
    sc.add_argument("--embed-model-dir", type=Path)
    sc.add_argument("--llm-model", type=Path)
    sc.add_argument("--report", type=Path, help="also write the JSON report here")
    args = parser.parse_args(argv)

    if args.command == "selfcheck":
        from tamra.paths import data_dir, resource_dir
        from tamra.selfcheck import run_selfcheck

        report = run_selfcheck(
            args.embed_model_dir,
            args.llm_model,
            resource_dir() / "vendor" / "llama" / "llama-server.exe",
            data_dir() / "logs",
        )
        text = json.dumps(report, ensure_ascii=False, indent=2)
        print(text)  # no-op in the windowed exe (stdout is None); use --report there
        if args.report:
            args.report.write_text(text, encoding="utf-8")
        return 0 if report["ok"] else 1

    from tamra.app import run

    run(dev=args.dev)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_selfcheck.py -v; uv run pytest -m assets tests/test_selfcheck.py -v`
Expected: 3 passed, then 1 passed.

- [ ] **Step 5: Commit**

```powershell
git add src/tamra/selfcheck.py src/tamra/__main__.py tests/test_selfcheck.py
git commit -m "feat: tamra selfcheck for sqlite, embedding, and llama-server"
```

---

### Task 10: PyInstaller packaging, CI package job, spike report

**Files:**
- Create: `packaging/entry.py`, `packaging/tamra.spec`, `scripts/build.ps1`, `docs/spikes/2026-10-m0-results.md`
- Modify: `.github/workflows/ci.yml`, `CLAUDE.md`

**Interfaces:**
- Consumes: everything above. `ui/dist` (Task 8), `vendor/llama` (Task 3), and `tamra.__main__.main` (Task 9).
- Produces: `dist/Tamra/Tamra.exe` (one-folder, windowed). Running `Tamra.exe selfcheck --report r.json` passes on CI (sqlite only) and on the dev machine (all checks).

- [ ] **Step 1: Add the entry script and spec**

`packaging/entry.py`:
```python
import sys

from tamra.__main__ import main

sys.exit(main())
```

`packaging/tamra.spec`:
```python
# PyInstaller spec — build with: uv run pyinstaller packaging/tamra.spec --noconfirm
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=collect_dynamic_libs("sqlite_vec"),
    datas=[
        (str(ROOT / "ui" / "dist"), "ui/dist"),
        (str(ROOT / "vendor" / "llama"), "vendor/llama"),
    ],
    hiddenimports=collect_submodules("uvicorn") + ["tamra.app", "tamra.selfcheck", "tamra.embedder"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Tamra", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Tamra")
```

`scripts/build.ps1`:
```powershell
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
npm --prefix ui ci
npm --prefix ui run build
uv run python scripts/fetch_assets.py llama
uv run pyinstaller packaging/tamra.spec --noconfirm --distpath dist --workpath build
```

- [ ] **Step 2: Build**

Run: `./scripts/build.ps1`
Expected: `dist/Tamra/Tamra.exe` exists.

- [ ] **Step 3: Verify the packaged exe (sqlite only, then full)**

The exe is windowed, so use `Start-Process -Wait` to get its exit code:
```powershell
$p = Start-Process dist\Tamra\Tamra.exe -ArgumentList "selfcheck","--report","selfcheck-min.json" -Wait -PassThru
Get-Content selfcheck-min.json; "exit=$($p.ExitCode)"
$p = Start-Process dist\Tamra\Tamra.exe -ArgumentList "selfcheck","--embed-model-dir",".models\bge-m3","--llm-model",".models\qwen2.5-0.5b-instruct-q4_k_m.gguf","--report","selfcheck-full.json" -Wait -PassThru
Get-Content selfcheck-full.json; "exit=$($p.ExitCode)"
```
Expected: both reports show `"ok": true` and `exit=0`. If a check fails, the report's `error` says which import or DLL is missing. Fix the spec (`hiddenimports`, `collect_dynamic_libs`, `datas`), rebuild, and re-run. Then double-click `dist\Tamra\Tamra.exe`: a window titled Tamra must show `Tamra core: ok (v0.1.0)`.

- [ ] **Step 4: Record spike results**

Create `docs/spikes/2026-10-m0-results.md` and fill it with the real values from `selfcheck-full.json` and the llama-server log. Use this structure:
```markdown
# M0 spike results — <date>

Machine: <CPU>, <RAM>, GPUs: <from `vendor\llama\llama-server.exe --list-devices`>

| Check | Result |
|---|---|
| sqlite | sqlite <version>, sqlite-vec <version>, trigram <true/false> |
| embedding (bge-m3 int8, CPU) | load <s>, <passages/s> passages/s (~450-token passages) |
| llm (qwen2.5-0.5b q4_k_m) | gpu_used <bool>, start <s>, first token <s>, <tok/s> tok/s |
| llm, CPU only (`gpu=False` test) | <tok/s> tok/s |
| packaged exe size | <MB> (`dist/Tamra`) |

## Findings
- Which Vulkan device(s) llama-server used by default (log lines `using device ...`),
  and whether pinning the discrete GPU via `LlamaServer(device=...)` was faster.
- Any packaging fixes that were needed (hidden imports, DLLs).
- Indexing estimate: hours to embed 1,000 pages at the measured rate.
- Decisions or risks to carry into the M1 plan.
```
Delete `selfcheck-min.json` and `selfcheck-full.json` afterwards (they are scratch files).

- [ ] **Step 5: Add the package job to CI**

Append to `.github/workflows/ci.yml` under `jobs:`:
```yaml
  package:
    needs: test
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
      - uses: actions/setup-node@v5
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: ui/package-lock.json
      - run: uv sync --locked
      - run: ./scripts/build.ps1
        shell: pwsh
      - name: Selfcheck packaged exe
        shell: pwsh
        run: |
          $p = Start-Process dist\Tamra\Tamra.exe -ArgumentList "selfcheck","--report","selfcheck.json" -Wait -PassThru
          Get-Content selfcheck.json
          if ($p.ExitCode -ne 0) { exit 1 }
      - uses: actions/upload-artifact@v4
        with:
          name: Tamra-windows
          path: dist/Tamra
```

- [ ] **Step 6: Add build commands to CLAUDE.md**

Append to the PowerShell block under `## Commands`:
```powershell
uv run python scripts/fetch_assets.py     # download pinned llama.cpp + dev models (~1.1 GB, gitignored)
./scripts/build.ps1                       # UI build + PyInstaller → dist/Tamra/Tamra.exe
# windowed exe: get output via --report and Start-Process -Wait
Start-Process dist\Tamra\Tamra.exe -ArgumentList "selfcheck","--report","r.json" -Wait
```

- [ ] **Step 7: Run the full local suite one last time**

Run: `uv run ruff check; uv run ruff format --check; uv run pytest; uv run pytest -m assets; npm --prefix ui test`
Expected: everything passes.

- [ ] **Step 8: Commit**

```powershell
git add packaging scripts/build.ps1 .github/workflows/ci.yml CLAUDE.md docs/spikes
git commit -m "build: PyInstaller packaging, CI package job, M0 spike results"
```

---

## After M0

M0 is done when Task 10 Step 3 passes on the dev machine and the CI `package` job is green
(once the repo is pushed to GitHub). Next, write the M1 plan (core loop: ingestion → index
→ hybrid retrieval → cited answers) using the findings in `docs/spikes/2026-10-m0-results.md`.
