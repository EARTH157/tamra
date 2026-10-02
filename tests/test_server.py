from fastapi.testclient import TestClient

import tamra
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
