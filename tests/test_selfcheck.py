import io
import json
import sys

import pytest

from tamra.__main__ import main
from tamra.llm.base import Chunk
from tamra.selfcheck import run_selfcheck


def test_selfcheck_without_models(tmp_path):
    report = run_selfcheck(None, None, tmp_path / "missing.exe", tmp_path)
    assert report["ok"] is True
    assert set(report["checks"]) == {"sqlite", "documents", "providers"}
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


def test_report_survives_non_cp1252_stdout(tmp_path, monkeypatch):
    # Regression for Important 1: report is not lost when stdout encoding can't handle Thai text.
    # Deterministic: monkeypatch run_selfcheck to return a failing report with Thai error text.
    import tamra.selfcheck

    # Thai characters: chr(0x0e40) = THAI CHARACTER SARA E, etc.
    thai_chars = chr(0x0E40) + chr(0x0E41) + chr(0x0E42)
    fake_report = {
        "ok": False,
        "checks": {"embedding": {"ok": False, "error": f"NoSuchFile: {thai_chars}/model.onnx"}},
    }
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(tamra.selfcheck, "run_selfcheck", lambda *a, **k: fake_report)
    buf = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buf, encoding="cp1252"))
    out = tmp_path / "r.json"
    assert main(["selfcheck", "--report", str(out)]) == 1
    sys.stdout.flush()
    assert json.loads(out.read_text(encoding="utf-8")) == fake_report
    assert buf.getvalue().isascii()
    assert json.loads(buf.getvalue().decode("ascii")) == fake_report


def test_windowed_exe_with_none_stdout(tmp_path, monkeypatch):
    # Test that selfcheck works when stdout is None (windowed exe case)
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path / "data"))
    out = tmp_path / "report.json"

    # Monkeypatch stdout to None (simulates windowed exe)
    old_stdout = sys.stdout
    try:
        sys.stdout = None  # type: ignore
        result = main(["selfcheck", "--report", str(out)])
    finally:
        sys.stdout = old_stdout

    assert result == 0
    assert out.exists()
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["ok"] is True


def test_report_parent_directory_created(tmp_path, monkeypatch):
    # Test that parent directories are created for --report path
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path / "data"))
    report_path = tmp_path / "nested" / "deep" / "report.json"
    assert not report_path.parent.exists()

    result = main(["selfcheck", "--report", str(report_path)])

    assert result == 0
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is True


def test_llm_failure_is_reported(tmp_path):
    # Test that LLM failures are reported, not raised
    report = run_selfcheck(None, tmp_path / "m.gguf", tmp_path / "missing.exe", tmp_path)
    assert report["ok"] is False
    assert report["checks"]["llm"]["ok"] is False
    assert "error" in report["checks"]["llm"]
    assert "LlamaServerError" in report["checks"]["llm"]["error"]


def test_selfcheck_cli_does_not_create_the_data_folder(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("TAMRA_DATA_DIR", str(data))
    assert main(["selfcheck", "--report", str(tmp_path / "r.json")]) == 0
    assert not data.exists()


def test_llm_check_reports_timings_from_a_stub_server(tmp_path, monkeypatch):
    import tamra.llm.llama_server as llama_server
    import tamra.llm.openai_compat as openai_compat

    class StubServer:
        base_url = "http://127.0.0.1:9"
        gpu_used = True

        def __init__(self, exe, model, log_file):
            self.log_file = log_file

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class StubClient:
        closed = False

        def __init__(self, base_url, model, **kwargs):
            pass

        def generate(self, messages, max_tokens=1024, *, think=False):
            yield from [
                Chunk("text", "1"),
                Chunk("thinking", "hm"),
                Chunk("text", ","),
                Chunk("text", " 2"),
            ]

        def close(self):
            StubClient.closed = True

    monkeypatch.setattr(llama_server, "LlamaServer", StubServer)
    monkeypatch.setattr(openai_compat, "OpenAICompatibleLLM", StubClient)
    report = run_selfcheck(None, tmp_path / "m.gguf", tmp_path / "x.exe", tmp_path)
    llm = report["checks"]["llm"]
    assert report["ok"] is True
    assert (llm["tokens"], llm["gpu_used"]) == (3, True)
    assert llm["first_token_s"] >= 0
    assert llm["tokens_per_sec"] > 0
    assert StubClient.closed


def test_document_libraries_are_checked(tmp_path):
    report = run_selfcheck(None, None, tmp_path / "x.exe", tmp_path)
    assert report["checks"]["documents"] == {"ok": True}


def test_providers_probe_loads_keyring_anthropic_and_the_catalog(tmp_path):
    providers = run_selfcheck(None, None, tmp_path / "x.exe", tmp_path)["checks"]["providers"]
    assert providers["ok"] is True
    assert providers["keyring_backend"]
    assert providers["catalog_models"] >= 4


def test_providers_probe_fails_without_a_credential_store(monkeypatch, tmp_path):
    import keyring
    from keyring.backends.fail import Keyring as FailKeyring

    monkeypatch.setattr(keyring, "get_keyring", lambda: FailKeyring())
    providers = run_selfcheck(None, None, tmp_path / "x.exe", tmp_path)["checks"]["providers"]
    assert providers["ok"] is False


def test_a_startup_failure_returns_exit_code_1_without_raising(monkeypatch):
    import tamra.app

    def boom(dev):
        raise RuntimeError("boom")

    monkeypatch.setattr(tamra.app, "run", boom)
    assert main([]) == 1
    with pytest.raises(RuntimeError, match="boom"):
        main(["--dev"])
