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
