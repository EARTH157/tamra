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
