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
