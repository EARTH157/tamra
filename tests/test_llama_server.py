import subprocess
import sys
import time
from pathlib import Path

import pytest

from tamra.llm.llama_server import LlamaServer, LlamaServerError, parse_gpu_offload
from tamra.llm.openai_compat import OpenAICompatibleLLM
from tamra.net import free_port

# Stub server script for lifecycle tests
_STUB_SERVER = """
import http.server
import sys
import time

port = int(sys.argv[1])
mode = sys.argv[2]

if mode == "fail":
    sys.exit(1)
elif mode == "hang":
    time.sleep(3600)
elif mode in ("ok", "offload"):
    if mode == "offload":
        print("load_tensors: offloaded 33/33 layers to GPU", flush=True)
        print("load_tensors:   Vulkan0 model buffer size =   100.00 MiB", flush=True)
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.handle_request()
    server.handle_request()
    server.server_close()
"""


def test_free_port_is_usable_int():
    port = free_port()
    assert 1024 < port < 65536


def test_args_gpu_and_cpu(tmp_path):
    srv = LlamaServer(Path("llama-server.exe"), Path("m.gguf"), tmp_path / "log.txt", ctx_size=2048)
    base = ["llama-server.exe", "-m", "m.gguf", "-c", "2048", "--host", "127.0.0.1"]
    assert srv.args(gpu=True)[:7] == base
    assert srv.args(gpu=True)[7:9] == ["--port", str(srv.port)]
    assert srv.args(gpu=True)[9:11] == ["-lv", "4"]  # makes llama-server report layer offload
    assert srv.args(gpu=True)[-2:] == ["-ngl", "99"]
    assert srv.args(gpu=False)[-2:] == ["--device", "none"]


# Real output of the pinned llama-server (build 11344) at -lv 4, trimmed to the relevant lines.
GPU_LOG = """0.02.019.047 I llama_prepare_model_devices: using device Vulkan1 (NVIDIA RTX 5060)
0.02.235.994 I load_tensors: offloading 23 repeating layers to GPU
0.02.235.995 I load_tensors: offloaded 25/25 layers to GPU
0.02.236.003 I load_tensors:   CPU_Mapped model buffer size =    89.26 MiB
0.02.236.004 I load_tensors:      Vulkan1 model buffer size =   373.71 MiB
"""
# With `--device none` the same build still says "offloaded 25/25 layers to GPU".
CPU_ONLY_LOG = """0.01.496.863 I load_tensors: offloading 23 repeating layers to GPU
0.01.496.864 I load_tensors: offloaded 25/25 layers to GPU
0.01.496.867 I load_tensors:   CPU_Mapped model buffer size =   462.96 MiB
0.01.496.868 I load_tensors:   CPU_REPACK model buffer size =    28.05 MiB
"""


@pytest.mark.parametrize(
    ("log", "expected"),
    [
        (GPU_LOG, True),
        (CPU_ONLY_LOG, False),
        ("load_tensors: offloaded 25/25 layers to GPU\n", True),  # no buffer lines to cross-check
        ("llm_load_tensors: offloaded 12/29 layers to GPU", True),
        ("load_tensors: offloaded 0/25 layers to GPU\n", False),
        ("load_tensors:  CPU model buffer size = 300 MiB\nmain: server is listening\n", None),
        ("", None),
        # a log that holds two launches: the last "offloaded" line wins
        ("offloaded 25/25 layers to GPU\nerror\noffloaded 0/25 layers to GPU\n", False),
    ],
)
def test_parse_gpu_offload(log, expected):
    assert parse_gpu_offload(log) is expected


def test_gpu_offload_is_read_from_this_runs_log_only(tmp_path, spawned):
    stub_script = tmp_path / "stub.py"
    stub_script.write_text(_STUB_SERVER)
    log = tmp_path / "log.txt"
    log.write_text("load_tensors: offloaded 0/33 layers to GPU\n")  # an earlier run's output

    quiet = _StubLlamaServer(stub_script, Path("dummy.gguf"), log, modes={"gpu": "ok"})
    quiet.start(timeout=5)
    assert quiet.gpu_offload is None
    quiet.stop()

    loud = _StubLlamaServer(stub_script, Path("dummy.gguf"), log, modes={"gpu": "offload"})
    loud.start(timeout=5)
    assert loud.gpu_offload is True
    loud.stop()


def test_gpu_offload_is_unknown_without_a_log(tmp_path):
    srv = LlamaServer(Path("x.exe"), Path("m.gguf"), tmp_path / "missing.txt")
    assert srv.gpu_offload is None


def test_args_with_explicit_device(tmp_path):
    srv = LlamaServer(Path("x.exe"), Path("m.gguf"), tmp_path / "l.txt", device="Vulkan1")
    assert srv.args(gpu=True)[-4:] == ["--device", "Vulkan1", "-ngl", "99"]


@pytest.mark.assets
@pytest.mark.parametrize("gpu", [True, False])
def test_generates_tokens(llama_exe, qwen_gguf, tmp_path, gpu):
    with LlamaServer(llama_exe, qwen_gguf, tmp_path / "llama.log", gpu=gpu) as srv:
        llm = OpenAICompatibleLLM(srv.base_url, "local", kind="local")
        text = "".join(
            c.text
            for c in llm.generate(
                [{"role": "user", "content": "Reply with one word: OK"}], max_tokens=8
            )
        )
        llm.close()
    assert text.strip()
    if gpu:
        assert isinstance(srv.gpu_used, bool)
    else:
        assert srv.gpu_used is False


@pytest.mark.assets
def test_bad_model_raises(llama_exe, tmp_path):
    bogus = tmp_path / "not-a-model.gguf"
    bogus.write_bytes(b"nope")
    with pytest.raises(LlamaServerError):
        LlamaServer(llama_exe, bogus, tmp_path / "llama.log").start(timeout=30)


class _StubLlamaServer(LlamaServer):
    """LlamaServer subclass that runs a stub server instead of llama-server."""

    def __init__(self, *args, modes=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.modes = modes or {}

    def args(self, gpu: bool):
        mode_key = "gpu" if gpu else "cpu"
        mode = self.modes.get(mode_key, "ok")
        return [sys.executable, str(self.exe), str(self.port), mode]


@pytest.fixture
def spawned(monkeypatch):
    """Fixture that records subprocess.Popen calls and cleans up on teardown."""
    procs = []
    original_popen = subprocess.Popen

    def tracked_popen(*args, **kwargs):
        proc = original_popen(*args, **kwargs)
        procs.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", tracked_popen)
    yield procs

    # Cleanup: terminate any still-running processes
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
            proc.wait()


def test_stub_server_gpu_fail_cpu_ok(tmp_path, spawned):
    """GPU fails, CPU succeeds."""
    stub_script = tmp_path / "stub.py"
    stub_script.write_text(_STUB_SERVER)

    srv = _StubLlamaServer(
        stub_script,
        Path("dummy.gguf"),
        tmp_path / "log.txt",
        modes={"gpu": "fail", "cpu": "ok"},
    )
    srv.start(timeout=5)
    assert srv.gpu_used is False
    assert srv.alive() is True
    assert srv._job is not None  # the child is in a kill-on-close job
    srv.stop()
    assert srv.alive() is False
    assert srv._job is None
    for proc in spawned:
        assert proc.poll() is not None


def test_stub_server_both_hang(tmp_path, spawned):
    """Both GPU and CPU attempts hang; timeout and error."""
    stub_script = tmp_path / "stub.py"
    stub_script.write_text(_STUB_SERVER)

    srv = _StubLlamaServer(
        stub_script,
        Path("dummy.gguf"),
        tmp_path / "log.txt",
        modes={"gpu": "hang", "cpu": "hang"},
    )
    with pytest.raises(LlamaServerError):
        srv.start(timeout=1)
    assert len(spawned) == 2
    for proc in spawned:
        assert proc.poll() is not None


def test_both_attempts_share_one_time_budget(tmp_path, spawned):
    """GPU and CPU attempts together stay within the single timeout."""
    stub_script = tmp_path / "stub.py"
    stub_script.write_text(_STUB_SERVER)

    srv = _StubLlamaServer(
        stub_script,
        Path("dummy.gguf"),
        tmp_path / "log.txt",
        modes={"gpu": "hang", "cpu": "hang"},
    )
    started = time.monotonic()
    with pytest.raises(LlamaServerError):
        srv.start(timeout=2)
    assert time.monotonic() - started < 3.5
    assert len(spawned) == 2


def test_missing_exe_raises(tmp_path):
    """Missing exe raises LlamaServerError and closes log handle."""
    try:
        srv = LlamaServer(
            tmp_path / "missing.exe",
            Path("dummy.gguf"),
            tmp_path / "log.txt",
        )
        with pytest.raises(LlamaServerError):
            srv.start()
        assert srv._log is None
    finally:
        srv.stop()


def test_proxy_bypass(tmp_path, monkeypatch, spawned):
    """GPU startup succeeds even with broken proxy env."""
    stub_script = tmp_path / "stub.py"
    stub_script.write_text(_STUB_SERVER)

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    srv = _StubLlamaServer(
        stub_script,
        Path("dummy.gguf"),
        tmp_path / "log.txt",
        modes={"gpu": "ok"},
    )
    srv.start(timeout=10)
    assert srv.gpu_used is True
    srv.stop()
    for proc in spawned:
        assert proc.poll() is not None


def test_keyboard_interrupt_during_start(tmp_path, spawned):
    """KeyboardInterrupt during _wait_healthy closes log handle and stops child."""
    stub_script = tmp_path / "stub.py"
    stub_script.write_text(_STUB_SERVER)

    srv = _StubLlamaServer(
        stub_script,
        Path("dummy.gguf"),
        tmp_path / "log.txt",
        modes={"gpu": "ok"},
    )

    # Make _wait_healthy raise KeyboardInterrupt
    def raise_on_wait(timeout):
        raise KeyboardInterrupt()

    srv._wait_healthy = raise_on_wait

    with pytest.raises(KeyboardInterrupt):
        srv.start(timeout=10)

    assert srv._log is None
    for proc in spawned:
        assert proc.poll() is not None


def test_the_api_key_is_passed_when_given(tmp_path):
    keyed = LlamaServer(Path("x.exe"), Path("m.gguf"), tmp_path / "l.txt", api_key="k1")
    args = keyed.args(gpu=True)
    assert args[args.index("--api-key") + 1] == "k1"
    assert args[-2:] == ["-ngl", "99"]
    plain = LlamaServer(Path("x.exe"), Path("m.gguf"), tmp_path / "l.txt")
    assert "--api-key" not in plain.args(gpu=True)


def test_an_unwritable_log_raises_llama_server_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    srv = LlamaServer(Path("x.exe"), Path("m.gguf"), blocker / "logs" / "l.txt")
    with pytest.raises(LlamaServerError, match="cannot write"):
        srv.start(timeout=1)
