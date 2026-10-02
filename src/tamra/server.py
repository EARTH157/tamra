import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import tamra


def create_app(token: str, ui_dir: Path | None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if request.url.path.startswith("/api/") and not secrets.compare_digest(
            request.headers.get("x-tamra-token", ""), token
        ):
            return JSONResponse({"detail": "invalid token"}, status_code=401)
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": tamra.__version__}

    if ui_dir is not None:
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
    return app
