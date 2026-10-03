import threading

import uvicorn
from fastapi.testclient import TestClient

import tamra
from tamra.net import free_port
from tamra.server import create_app


def test_health_requires_token():
    client = TestClient(create_app("secret", ui_dir=None))
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"X-Tamra-Token": "wrong"}).status_code == 401
    ok = client.get("/api/health", headers={"X-Tamra-Token": "secret"})
    assert ok.status_code == 200
    assert ok.json() == {"status": "ok", "version": tamra.__version__}


def test_serves_ui_without_token(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Tamra</h1>", encoding="utf-8")
    client = TestClient(create_app("secret", ui_dir=tmp_path))
    response = client.get("/")
    assert response.status_code == 200
    assert "Tamra" in response.text


def test_non_ascii_token_returns_401():
    client = TestClient(create_app("secret", ui_dir=None))
    assert client.get("/api/health", headers=[(b"x-tamra-token", b"\xe9")]).status_code == 401


def test_unknown_api_path_requires_token():
    client = TestClient(create_app("secret", ui_dir=None))
    assert client.get("/api/nope").status_code == 401
    assert client.get("/api/nope", headers={"X-Tamra-Token": "secret"}).status_code == 404


def test_api_not_shadowed_by_ui_mount(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Tamra</h1>", encoding="utf-8")
    client = TestClient(create_app("secret", ui_dir=tmp_path))
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"X-Tamra-Token": "secret"}).status_code == 200


def test_wait_until_up_bypasses_proxy(monkeypatch):
    from tamra.app import _wait_until_up

    port = free_port()
    token = "test-token"
    app = create_app(token, ui_dir=None)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
        _wait_until_up(port, token, timeout=5)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
