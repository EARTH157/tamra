import hashlib
import os
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
    src.write_bytes(b"GGUF" + b"some other weights")
    result = manager.import_file(src)
    assert result.catalogued is False
    assert result.id == "import:my-own-model.gguf"  # accepted as Settings.local_model_id
    assert result.warning == "This model is not in Tamra's catalog; answer quality is unknown."
    assert result.warning == UNCATALOGUED_WARNING
    assert result.path == models / "my-own-model.gguf"
    assert result.path.read_bytes() == b"GGUF" + b"some other weights"


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


def test_import_refuses_an_unknown_gguf_without_the_gguf_magic(tmp_path):
    manager, models = _manager(tmp_path)
    src = tmp_path / "fake.gguf"
    src.write_bytes(b"not a model at all")
    with pytest.raises(ImportRefused, match="not a valid GGUF"):
        manager.import_file(src)
    assert list(models.iterdir()) == []


def test_import_uses_its_own_temp_file_and_leaves_a_download_part_alone(tmp_path, monkeypatch):
    manager, models = _manager(tmp_path)
    part = models / "Tiny-Q4.gguf.part"
    part.write_bytes(b"half a download")
    src = tmp_path / "a.gguf"
    src.write_bytes(LLM)
    renamed = []
    real_replace = os.replace
    monkeypatch.setattr(
        manager_module.os, "replace", lambda a, b: (renamed.append(Path(a)), real_replace(a, b))
    )
    manager.import_file(src)
    assert len(renamed) == 1
    assert renamed[0].name != "Tiny-Q4.gguf.part"
    assert renamed[0].parent == models
    assert part.read_bytes() == b"half a download"
    assert (models / "Tiny-Q4.gguf").read_bytes() == LLM
    assert not list(models.glob("*.import"))


def test_import_of_the_model_being_downloaded_is_refused(tmp_path):
    gate = threading.Event()
    manager = ModelManager(tmp_path, _catalog(), transport=_transport(gate))
    manager.start_download("tiny")
    _wait_state(manager, "tiny", "downloading")
    src = tmp_path / "elsewhere.gguf"
    src.write_bytes(LLM)
    with pytest.raises(ImportRefused, match="being downloaded"):
        manager.import_file(src)
    gate.set()
    _wait_idle(manager, "tiny")
    assert manager.import_file(src).id == "tiny"  # fine once the download is over


def test_imports_are_serialised(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path)
    src = tmp_path / "a.gguf"
    src.write_bytes(LLM)
    inside = 0
    peak = 0
    guard = threading.Lock()
    real_copy = manager._copy

    def counting_copy(*args, **kwargs):
        nonlocal inside, peak
        with guard:
            inside += 1
            peak = max(peak, inside)
        time.sleep(0.05)
        try:
            return real_copy(*args, **kwargs)
        finally:
            with guard:
                inside -= 1

    monkeypatch.setattr(manager, "_copy", counting_copy)
    other = tmp_path / "b.gguf"
    other.write_bytes(b"GGUF" + b"second model")
    sources = [src, other, src, other]
    threads = [threading.Thread(target=manager.import_file, args=(s,)) for s in sources]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert peak == 1


def test_an_unreadable_source_is_refused_with_a_message(tmp_path, monkeypatch):
    manager, models = _manager(tmp_path)
    src = tmp_path / "locked.gguf"
    src.write_bytes(LLM)

    def deny(path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(manager_module, "sha256_file", deny)
    with pytest.raises(ImportRefused, match="Could not read locked.gguf"):
        manager.import_file(src)
    assert list(models.iterdir()) == []


def test_a_failed_thread_start_releases_the_download_slot(tmp_path, monkeypatch):
    manager = ModelManager(tmp_path, _catalog(), transport=_transport())

    def fail(self):
        raise RuntimeError("cannot start new thread")

    monkeypatch.setattr(threading.Thread, "start", fail)
    with pytest.raises(RuntimeError):
        manager.start_download("tiny")
    monkeypatch.undo()
    assert manager.status()["tiny"]["state"] == "idle"
    manager.start_download("tiny")  # the slot is free again
    _wait_idle(manager, "tiny")


def test_a_worker_killed_by_a_base_exception_releases_the_slot(tmp_path, monkeypatch):
    def die(*args, **kwargs):
        raise SystemExit

    monkeypatch.setattr(manager_module, "download_resumable", die)
    monkeypatch.setattr(threading, "excepthook", lambda args: None)  # the worker dies on purpose
    manager = ModelManager(tmp_path, _catalog(), transport=_transport())
    manager.start_download("tiny")
    st = _wait_idle(manager, "tiny")
    assert st["state"] == "error"
    monkeypatch.undo()
    manager.start_download("tiny")
    assert _wait_idle(manager, "tiny")["state"] == "idle"


def test_a_finished_worker_leaves_a_retry_of_the_same_model_alone(tmp_path):
    gate = threading.Event()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("offline")
        return _transport(gate).handle_request(request)

    manager = ModelManager(tmp_path, _catalog(), transport=httpx.MockTransport(handler))
    real_finish = manager._finish
    first_worker = []

    def finish_then_retry(model_id, **fields):
        real_finish(model_id, **fields)
        if not first_worker:  # retry inside the window before the old worker's cleanup
            first_worker.append(threading.current_thread())
            manager.start_download(model_id)

    manager._finish = finish_then_retry
    manager.start_download("tiny")
    deadline = time.monotonic() + 10
    while not first_worker and time.monotonic() < deadline:
        time.sleep(0.01)
    first_worker[0].join(10)

    assert manager.status()["tiny"]["state"] == "downloading"  # the retry was not clobbered
    with pytest.raises(DownloadBusy):
        manager.start_download("emb")  # and it still holds the slot
    gate.set()
    assert _wait_idle(manager, "tiny")["state"] == "idle"
