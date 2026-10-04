import threading

import pytest
import uvicorn
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import tamra
from tamra.net import free_port
from tamra.server import create_app


def make_client(ui_dir=None, **options):
    return TestClient(create_app("secret", ui_dir, **options), base_url="http://127.0.0.1")


def test_health_requires_token():
    client = make_client()
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"X-Tamra-Token": "wrong"}).status_code == 401
    ok = client.get("/api/health", headers={"X-Tamra-Token": "secret"})
    assert ok.status_code == 200
    assert ok.json() == {"status": "ok", "version": tamra.__version__}


def test_serves_ui_without_token(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Tamra</h1>", encoding="utf-8")
    client = make_client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assert "Tamra" in response.text


def test_non_ascii_token_returns_401():
    client = make_client()
    assert client.get("/api/health", headers=[(b"x-tamra-token", b"\xe9")]).status_code == 401


def test_unknown_api_path_requires_token():
    client = make_client()
    assert client.get("/api/nope").status_code == 401
    assert client.get("/api/nope", headers={"X-Tamra-Token": "secret"}).status_code == 404


def test_api_not_shadowed_by_ui_mount(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Tamra</h1>", encoding="utf-8")
    client = make_client(tmp_path)
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"X-Tamra-Token": "secret"}).status_code == 200


def test_wait_until_up_bypasses_proxy(monkeypatch):
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
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


AUTH = {"X-Tamra-Token": "secret"}


def test_foreign_host_headers_are_rejected():
    response = make_client().get("/api/health", headers={**AUTH, "Host": "evil.example"})
    assert response.status_code == 400


def test_localhost_is_an_allowed_host():
    client = TestClient(create_app("secret", ui_dir=None), base_url="http://localhost")
    assert client.get("/api/health", headers=AUTH).status_code == 200


def test_double_slash_and_bare_api_paths_are_gated():
    client = make_client()
    # An absolute URL keeps the double slash (httpx would merge "//api/..." into the base URL).
    assert client.get("http://127.0.0.1//api/health").status_code == 401
    assert client.get("/api").status_code == 401


def test_websockets_under_api_need_the_token():
    # websocket_connect ignores base_url, so the allowed Host is given explicitly.
    with pytest.raises(WebSocketDisconnect) as closed:
        with make_client().websocket_connect("/api/ws", headers={"host": "127.0.0.1"}):
            pass
    assert closed.value.code == 1008


def test_an_empty_token_is_refused():
    with pytest.raises(ValueError):
        create_app("", ui_dir=None)


def test_responses_carry_security_headers(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Tamra</h1>", encoding="utf-8")
    client = make_client(tmp_path)
    for response in (client.get("/"), client.get("/api/health")):
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_a_missing_ui_build_is_reported(tmp_path):
    with pytest.raises(RuntimeError, match="npm --prefix ui run build"):
        create_app("secret", ui_dir=tmp_path / "dist")


def test_the_folder_picker_needs_a_window():
    assert make_client().post("/api/pick-folder", headers=AUTH).status_code == 501


def test_the_folder_picker_returns_the_choice():
    client = make_client(pick_folder=lambda: "C:/docs")
    assert client.post("/api/pick-folder", headers=AUTH).json() == {"folder_path": "C:/docs"}
    cancelled = make_client(pick_folder=lambda: None)
    assert cancelled.post("/api/pick-folder", headers=AUTH).json() == {"folder_path": None}
