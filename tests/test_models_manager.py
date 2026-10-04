import hashlib
import threading
import time
from pathlib import Path

import httpx
import pytest

import tamra.models.manager as manager_module
from tamra.models.catalog import Catalog, ModelEntry, ModelFile
from tamra.models.manager import (
    UNCATALOGUED_WARNING,
    DownloadBusy,
    ImportRefused,
    ModelManager,
)

LLM = bytes(range(256)) * 200  # 51,200 bytes
EMB = b"onnx-bytes" * 500
TOK = b"tokenizer" * 50


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _catalog(sha_upper: bool = False) -> Catalog:
    llm_sha = _sha(LLM).upper() if sha_upper else _sha(LLM)
    return Catalog(
        [
            ModelEntry(
                id="tiny",
                role="llm",
                name="Tiny",
                files=(ModelFile("Tiny-Q4.gguf", len(LLM), llm_sha, "https://x/tiny.gguf"),),
                tier="small",
            ),
            ModelEntry(
                id="emb",
                role="embedding",
                name="Emb",
                files=(
                    ModelFile("emb/model.onnx", len(EMB), _sha(EMB), "https://x/model.onnx"),
                    ModelFile("emb/tokenizer.json", len(TOK), _sha(TOK), "https://x/tok.json"),
                ),
            ),
        ]
    )


BODIES = {"https://x/tiny.gguf": LLM, "https://x/model.onnx": EMB, "https://x/tok.json": TOK}


def _transport(gate: threading.Event | None = None) -> httpx.MockTransport:
    class Body(httpx.SyncByteStream):
        def __init__(self, data: bytes):
            self.data = data

        def __iter__(self):
            for i in range(0, len(self.data), 4096):
                if gate is not None and i > 0:
                    gate.wait(10)  # the first piece flows; the rest wait for the test
                yield self.data[i : i + 4096]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Body(BODIES[str(request.url)]))

    return httpx.MockTransport(handler)


def _wait_idle(manager: ModelManager, model_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = manager.status()[model_id]
        if st["state"] in ("idle", "error"):
            return st
        time.sleep(0.01)
    raise AssertionError("download did not finish")


def _wait_state(
    manager: ModelManager, model_id: str, state: str, timeout: float = 10.0, started: bool = True
) -> dict:
    """Wait for `state`; by default also for the first bytes to have arrived."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = manager.status()[model_id]
        if st["state"] == state and (st["done"] > 0 or not started):
            return st
            return st
        time.sleep(0.01)
    raise AssertionError(f"never reached {state}: {manager.status()[model_id]}")


# --- downloads ---------------------------------------------------------------------------


def test_status_starts_idle_for_every_catalog_entry(tmp_path):
    manager = ModelManager(tmp_path, _catalog())
    assert manager.status() == {
        "tiny": {"state": "idle", "done": 0, "total": 0, "error": None},
        "emb": {"state": "idle", "done": 0, "total": 0, "error": None},
    }


def test_downloads_a_single_file_model_in_the_background(tmp_path):
    catalog = _catalog(sha_upper=True)  # an upper-case pin must be accepted
    manager = ModelManager(tmp_path, catalog, transport=_transport())
    manager.start_download("tiny")
    st = _wait_idle(manager, "tiny")
    assert st == {"state": "idle", "done": len(LLM), "total": len(LLM), "error": None}
    assert (tmp_path / "Tiny-Q4.gguf").read_bytes() == LLM
    assert "tiny" in catalog.installed(tmp_path)


def test_downloads_every_file_of_a_multi_file_model(tmp_path):
    manager = ModelManager(tmp_path, _catalog(), transport=_transport())
    manager.start_download("emb")
    st = _wait_idle(manager, "emb")
    assert st["total"] == len(EMB) + len(TOK)
    assert st["done"] == st["total"]
    assert (tmp_path / "emb" / "model.onnx").read_bytes() == EMB
    assert (tmp_path / "emb" / "tokenizer.json").read_bytes() == TOK


def test_reports_downloading_progress_while_running(tmp_path):
    gate = threading.Event()
    manager = ModelManager(tmp_path, _catalog(), transport=_transport(gate))
    manager.start_download("tiny")
    st = _wait_state(manager, "tiny", "downloading")
    assert st["total"] == len(LLM)
    assert st["done"] < st["total"]
    gate.set()
    assert _wait_idle(manager, "tiny")["done"] == len(LLM)


def test_state_is_verifying_once_all_bytes_are_in(tmp_path, monkeypatch):
    release = threading.Event()
    real = manager_module.download_resumable

    def slow_verify(*args, on_progress=None, **kwargs):
        # Report all bytes, then stall as the hash check would.
        on_progress(len(LLM), len(LLM))
        release.wait(10)
        return real(*args, on_progress=on_progress, **kwargs)

    monkeypatch.setattr(manager_module, "download_resumable", slow_verify)
    manager = ModelManager(tmp_path, _catalog(), transport=_transport())
    manager.start_download("tiny")
    _wait_state(manager, "tiny", "verifying")
    release.set()
    assert _wait_idle(manager, "tiny")["state"] == "idle"


def test_only_one_download_runs_at_a_time(tmp_path):
    gate = threading.Event()
    manager = ModelManager(tmp_path, _catalog(), transport=_transport(gate))
    manager.start_download("tiny")
    _wait_state(manager, "tiny", "downloading")
    with pytest.raises(DownloadBusy):
        manager.start_download("emb")
    gate.set()
    _wait_idle(manager, "tiny")
    manager.start_download("emb")  # allowed again once the first one finished
    _wait_idle(manager, "emb")


def test_unknown_model_id_is_a_key_error(tmp_path):
    with pytest.raises(KeyError):
        ModelManager(tmp_path, _catalog()).start_download("nope")


def test_cancel_stops_the_download_and_keeps_the_part(tmp_path):
    gate = threading.Event()
    manager = ModelManager(tmp_path, _catalog(), transport=_transport(gate))
    manager.start_download("tiny")
    _wait_state(manager, "tiny", "downloading")
    manager.cancel_download("tiny")
    gate.set()
    st = _wait_idle(manager, "tiny")
    assert st["state"] == "idle"
    assert st["error"] is None
    assert not (tmp_path / "Tiny-Q4.gguf").exists()
    assert (tmp_path / "Tiny-Q4.gguf.part").exists()


def test_cancelling_an_idle_model_is_a_no_op(tmp_path):
    manager = ModelManager(tmp_path, _catalog())
    manager.cancel_download("tiny")
    assert manager.status()["tiny"]["state"] == "idle"


def test_a_checksum_failure_becomes_an_error_state(tmp_path):
    bad = Catalog(
        [
            ModelEntry(
                id="tiny",
                role="llm",
                name="Tiny",
                files=(ModelFile("Tiny-Q4.gguf", len(LLM), "0" * 64, "https://x/tiny.gguf"),),
                tier="small",
            )
        ]
    )
    manager = ModelManager(tmp_path, bad, transport=_transport())
    manager.start_download("tiny")
    st = _wait_idle(manager, "tiny")
    assert st["state"] == "error"
    assert "expected" in st["error"]
    assert list(tmp_path.iterdir()) == []


def test_a_network_failure_becomes_an_error_state_and_can_be_retried(tmp_path):
    def boom(request):
        raise httpx.ConnectError("offline")

    manager = ModelManager(tmp_path, _catalog(), transport=httpx.MockTransport(boom))
    manager.start_download("tiny")
    assert _wait_idle(manager, "tiny")["state"] == "error"

    manager = ModelManager(tmp_path, _catalog(), transport=_transport())
    manager.start_download("tiny")
    st = _wait_idle(manager, "tiny")
    assert st["state"] == "idle"
    assert st["error"] is None


# --- import ------------------------------------------------------------------------------


def _manager(tmp_path: Path) -> tuple[ModelManager, Path]:
    models = tmp_path / "models"
    models.mkdir()
    return ModelManager(models, _catalog()), models


def test_import_of_a_catalogued_file_uses_the_catalog_file_name(tmp_path):
    manager, models = _manager(tmp_path)
    src = tmp_path / "whatever-name.gguf"
    src.write_bytes(LLM)
    result = manager.import_file(src)
    assert result.id == "tiny"
    assert result.catalogued is True
    assert result.warning is None
    assert result.path == models / "Tiny-Q4.gguf"
    assert result.path.read_bytes() == LLM
    assert src.exists()  # the source is never moved
    assert [p.name for p in models.iterdir()] == ["Tiny-Q4.gguf"]


def test_import_of_a_catalogued_non_gguf_file_goes_to_its_catalog_path(tmp_path):
    manager, models = _manager(tmp_path)
    src = tmp_path / "downloaded-model.onnx"
    src.write_bytes(EMB)
    result = manager.import_file(src)
    assert (result.id, result.catalogued) == ("emb", True)
    assert result.path == models / "emb" / "model.onnx"
    assert result.path.read_bytes() == EMB


def test_import_of_an_unknown_gguf_is_copied_as_is_with_a_warning(tmp_path):
    manager, models = _manager(tmp_path)
    src = tmp_path / "my-own-model.gguf"
    src.write_bytes(b"some other weights")
    result = manager.import_file(src)
    assert result.catalogued is False
    assert result.warning == "This model is not in Tamra's catalog; answer quality is unknown."
    assert result.warning == UNCATALOGUED_WARNING
    assert result.path == models / "my-own-model.gguf"
    assert result.path.read_bytes() == b"some other weights"


def test_import_refuses_a_file_that_is_not_gguf(tmp_path):
    manager, models = _manager(tmp_path)
    src = tmp_path / "notes.txt"
    src.write_bytes(b"hello")
    with pytest.raises(ImportRefused, match="GGUF"):
        manager.import_file(src)
    assert list(models.iterdir()) == []


def test_import_refuses_an_embedding_file_that_does_not_match_exactly(tmp_path):
    manager, models = _manager(tmp_path)
    src = tmp_path / "model.onnx"
    src.write_bytes(b"a different onnx model")
    with pytest.raises(ImportRefused):
        manager.import_file(src)
    assert list(models.iterdir()) == []


def test_import_refuses_a_gguf_that_reuses_a_catalog_file_name_with_other_content(tmp_path):
    manager, models = _manager(tmp_path)
    src = tmp_path / "Tiny-Q4.gguf"
    src.write_bytes(b"truncated or tampered")
    with pytest.raises(ImportRefused, match="catalog"):
        manager.import_file(src)
    assert list(models.iterdir()) == []


def test_import_of_a_missing_file_is_refused(tmp_path):
    manager, _ = _manager(tmp_path)
    with pytest.raises(ImportRefused):
        manager.import_file(tmp_path / "nope.gguf")


def test_import_leaves_no_file_behind_when_the_copy_fails(tmp_path, monkeypatch):
    manager, models = _manager(tmp_path)
    src = tmp_path / "x.gguf"
    src.write_bytes(LLM)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(manager_module.os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        manager.import_file(src)
    assert list(models.rglob("*")) == []


def test_import_leaves_no_file_behind_when_the_copied_bytes_do_not_verify(tmp_path, monkeypatch):
    manager, models = _manager(tmp_path)
    src = tmp_path / "x.gguf"
    src.write_bytes(b"not the catalogued bytes")
    # The pre-copy check sees the catalogued hash (as if the source changed mid-copy);
    # the hash taken while copying sees the real bytes.
    monkeypatch.setattr(manager_module, "sha256_file", lambda path: _sha(LLM))
    with pytest.raises(ImportRefused, match="verif"):
        manager.import_file(src)
    assert list(models.rglob("*")) == []


def test_importing_the_same_catalogued_file_twice_is_harmless(tmp_path):
    manager, _ = _manager(tmp_path)
    src = tmp_path / "a.gguf"
    src.write_bytes(LLM)
    first = manager.import_file(src)
    second = manager.import_file(src)
    assert first.path == second.path
    assert second.path.read_bytes() == LLM
