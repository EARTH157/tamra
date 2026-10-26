import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Test modules for Windows-only code that cannot even be imported elsewhere.
collect_ignore = [] if sys.platform == "win32" else ["test_winjob.py"]


def _require(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"missing {path}; run: uv run python scripts/fetch_assets.py")
    return path


@pytest.fixture(scope="session")
def bge_dir() -> Path:
    model_dir = ROOT / ".models" / "bge-m3"
    _require(model_dir / "tokenizer.json")
    return _require(model_dir / "model.onnx").parent


@pytest.fixture(scope="session")
def ocr_dir() -> Path:
    model_dir = ROOT / ".models" / "ppocr-v5"
    for name in (
        "ch_PP-OCRv5_det_mobile.onnx",
        "th_PP-OCRv5_rec_mobile.onnx",
        "ch_PP-OCRv5_rec_mobile.onnx",
    ):
        _require(model_dir / name)
    return model_dir


@pytest.fixture(scope="session")
def qwen_gguf() -> Path:
    return _require(ROOT / ".models" / "qwen2.5-0.5b-instruct-q4_k_m.gguf")


@pytest.fixture(scope="session")
def llama_exe() -> Path:
    from tamra.paths import llama_server

    return _require(llama_server())


@pytest.fixture(autouse=True)
def _no_real_key_store():
    """Tests never read or write this machine's credential store: it may hold the user's real
    keys, and a locked GNOME Keyring would wait for a password prompt."""
    import keyring
    from fakes import MemoryKeyring

    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    yield
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def _no_truststore_injection(monkeypatch):
    """app.run() injects the system trust store into ssl; tests must not patch ssl globally."""
    import truststore

    monkeypatch.setattr(truststore, "inject_into_ssl", lambda: None)


@pytest.fixture(autouse=True)
def _no_real_model_scan(monkeypatch):
    """Core scans Ollama, LM Studio and the HF cache; tests must not see this machine's stores."""
    monkeypatch.setattr("tamra.core.scan_external", lambda home, env: [])
