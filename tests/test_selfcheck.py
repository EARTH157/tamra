import io
import json
import sys

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
