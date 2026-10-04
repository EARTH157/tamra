import pytest

from tamra.llm.llama_server import LlamaServerError
from tamra.llm.runtime import LocalLLM


class FakeServer:
    instances: list["FakeServer"] = []

    def __init__(self, exe, model, log_file, ctx_size, api_key):
        self.model = model
        self.api_key = api_key
        self.gpu_offload = True
        self.base_url = "http://127.0.0.1:9"
        self.started = self.stopped = False
        self.dead = False
        FakeServer.instances.append(self)

    def start(self):
        self.started = True
        return self

    def alive(self):
        return self.started and not self.stopped and not self.dead

    def stop(self):
        self.stopped = True


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"gguf")
    FakeServer.instances = []
    return path


def test_the_server_starts_on_first_use_with_a_random_key(model, tmp_path):
    llm = LocalLLM(tmp_path / "x.exe", lambda: model, tmp_path / "l.log", server_factory=FakeServer)
    assert FakeServer.instances == [] and llm.base_url is None
    client = llm.client()
    assert llm.client() is client
    [server] = FakeServer.instances
    assert server.started and len(server.api_key) >= 32
    assert llm.base_url == server.base_url
    assert llm.label == "m"
    llm.close()
    assert server.stopped and llm.base_url is None


def test_a_dead_server_is_replaced_with_a_new_key(model, tmp_path):
    llm = LocalLLM(tmp_path / "x.exe", lambda: model, tmp_path / "l.log", server_factory=FakeServer)
    llm.client()
    FakeServer.instances[0].dead = True
    llm.client()
    first, second = FakeServer.instances
    assert first.stopped and second.started
    assert first.api_key != second.api_key
    llm.close()


def test_a_missing_model_is_reported(tmp_path):
    llm = LocalLLM(
        tmp_path / "x.exe",
        lambda: tmp_path / "none.gguf",
        tmp_path / "l.log",
        server_factory=FakeServer,
    )
    with pytest.raises(LlamaServerError, match="Local model not found"):
        llm.client()


def test_a_changed_model_restarts_the_server_on_the_next_question(tmp_path):
    FakeServer.instances = []
    first, second = tmp_path / "a.gguf", tmp_path / "b.gguf"
    first.write_bytes(b"gguf")
    second.write_bytes(b"gguf")
    chosen = [first]
    llm = LocalLLM(
        tmp_path / "x.exe", lambda: chosen[0], tmp_path / "l.log", server_factory=FakeServer
    )
    client = llm.client()
    assert llm.label == "a" and client.label == "a" and client.kind == "local"
    assert llm.client() is client  # unchanged model: same server
    chosen[0] = second
    assert llm.label == "b"
    new_client = llm.client()
    old, new = FakeServer.instances
    assert old.stopped and new.started and new.model == second
    assert new_client is not client and new_client.label == "b"
    assert old.api_key != new.api_key
    llm.close()


def test_gpu_offload_comes_from_the_running_server(model, tmp_path):
    llm = LocalLLM(tmp_path / "x.exe", lambda: model, tmp_path / "l.log", server_factory=FakeServer)
    assert llm.gpu_offload is None  # nothing running
    llm.client()
    assert llm.gpu_offload is True
    FakeServer.instances[0].gpu_offload = False
    assert llm.gpu_offload is False
    llm.close()
    assert llm.gpu_offload is None


@pytest.mark.assets
def test_the_real_server_requires_the_key(llama_exe, qwen_gguf, tmp_path):
    import httpx

    llm = LocalLLM(llama_exe, lambda: qwen_gguf, tmp_path / "llama.log")
    try:
        text = "".join(
            c.text
            for c in llm.client().generate(
                [{"role": "user", "content": "Reply with one word: OK"}], 8
            )
        )
        assert text.strip()
        anonymous = httpx.get(f"{llm.base_url}/v1/models", trust_env=False, timeout=5)
        assert anonymous.status_code == 401
    finally:
        llm.close()
