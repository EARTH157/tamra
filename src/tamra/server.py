"""HTTP API on 127.0.0.1 (spec §2-§3): token-gated JSON routes, SSE answers, and the built UI."""

import dataclasses
import json
import logging
import secrets
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any, Literal

import anyio
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import tamra
from tamra import secrets as key_store
from tamra.answer import source_payload
from tamra.core import Core
from tamra.llm.base import LLMError, ProviderError
from tamra.models.catalog import ModelEntry
from tamra.models.hardware import _is_integrated, recommend
from tamra.models.manager import DownloadBusy, ImportRefused
from tamra.store import Chat, Collection

log = logging.getLogger(__name__)

API_PROVIDERS = ("anthropic", "openai")

ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'none'"
)


class TokenGate:
    """Reject /api requests, HTTP and WebSocket, that lack the per-launch token."""

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and _is_api(scope["path"]):
            supplied = dict(scope["headers"]).get(b"x-tamra-token", b"")
            if not secrets.compare_digest(supplied, self.token):
                if scope["type"] == "http":
                    response = JSONResponse({"detail": "invalid token"}, status_code=401)
                    await response(scope, receive, send)
                else:
                    await receive()  # websocket.connect
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)


def _is_api(path: str) -> bool:
    path = "/" + path.lstrip("/")
    return path == "/api" or path.startswith("/api/")


class SecurityHeaders:
    """Add the Content-Security-Policy and related headers to every HTTP response."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [
                    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class CollectionBody(BaseModel):
    name: str = Field(default="", max_length=200)
    folder_path: str = Field(min_length=1, max_length=1000)


class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class QuestionBody(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    mode: Literal["answer", "search"] = "answer"
    think: bool = False


class ApiKeyBody(BaseModel):
    provider: str = Field(max_length=40)
    key: str


class ImportBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


def create_app(
    token: str,
    ui_dir: Path | None,
    core: Core | None = None,
    pick_folder: Callable[[], str | None] | None = None,
    pick_file: Callable[[], str | None] | None = None,
    open_data_folder: Callable[[], None] | None = None,
) -> FastAPI:
    if not token:
        raise ValueError("the API token must not be empty")  # an empty token would match ""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default body echoes each field's input, which here can be an API key.
        errors = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": tamra.__version__}

    @app.post("/api/pick-folder")
    def choose_folder() -> dict:
        if pick_folder is None:
            raise HTTPException(
                status_code=501, detail="The folder picker is only available in the Tamra window."
            )
        return {"folder_path": pick_folder()}

    @app.post("/api/pick-file")
    def choose_file() -> dict:
        if pick_file is None:
            raise HTTPException(
                status_code=501, detail="The file picker is only available in the Tamra window."
            )
        return {"file_path": pick_file()}

    @app.post("/api/open-data-folder", status_code=204)
    def show_data_folder() -> Response:
        if open_data_folder is None:
            raise HTTPException(
                status_code=501,
                detail="Opening the data folder is only available in the Tamra window.",
            )
        try:
            open_data_folder()
        except OSError as e:
            log.warning("cannot open the data folder: %s", e)
            raise HTTPException(status_code=500, detail="Could not open the data folder.") from e
        return Response(status_code=204)

    if core is not None:
        _add_routes(app, core)
        _add_settings_routes(app, core)
        _add_model_routes(app, core)
    if ui_dir is not None:
        if not (ui_dir / "index.html").is_file():
            raise RuntimeError(
                f"The user interface is missing ({ui_dir / 'index.html'}). "
                "Build it with: npm --prefix ui run build"
            )
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
    # The last one added runs first: response headers, then the host check, then the token.
    app.add_middleware(TokenGate, token=token)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    app.add_middleware(SecurityHeaders)
    return app


async def _sse(events: Iterator[dict]) -> AsyncIterator[str]:
    """Server-Sent Events from a blocking event iterator, advanced on worker threads.

    When the client goes away the response is cancelled; closing the iterator then lets the
    answer service keep the partial answer and stop generating.
    """
    try:
        while True:
            event = await anyio.to_thread.run_sync(next, events, None)
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    finally:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(events.close)


def _collection_payload(collection: Collection) -> dict:
    return {
        "id": collection.id,
        "name": collection.name,
        "folder_path": collection.folder_path,
        "created_at": collection.created_at,
    }


def _chat_payload(chat: Chat) -> dict:
    return {
        "id": chat.id,
        "title": chat.title,
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
    }


def _add_routes(app: FastAPI, core: Core) -> None:
    @app.get("/api/collection")
    def get_collection() -> dict:
        collection = core.store.get_collection()
        if collection is None:
            return {"collection": None, "index": None}
        return {"collection": _collection_payload(collection), "index": core.index_status()}

    @app.put("/api/collection")
    def put_collection(body: CollectionBody) -> dict:
        try:
            collection = core.set_collection(body.name, body.folder_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"collection": _collection_payload(collection), "index": core.index_status()}

    @app.post("/api/collection/rebuild", status_code=202)
    def rebuild() -> dict:
        if not core.rebuild():
            raise HTTPException(status_code=404, detail="No folder has been chosen yet.")
        return {"status": "rebuilding"}

    @app.get("/api/chats")
    def list_chats() -> dict:
        return {"chats": [_chat_payload(chat) for chat in core.store.list_chats()]}

    @app.post("/api/chats", status_code=201)
    def create_chat() -> dict:
        return _chat_payload(core.store.create_chat())

    @app.get("/api/chats/{chat_id}")
    def get_chat(chat_id: int) -> dict:
        chat = core.store.get_chat(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        messages = [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "provider": m.provider,
                "model": m.model,
                "created_at": m.created_at,
                "sources": [source_payload(s) for s in m.sources],
            }
            for m in core.store.list_messages(chat_id)
        ]
        return {**_chat_payload(chat), "messages": messages}

    @app.patch("/api/chats/{chat_id}")
    def rename_chat(chat_id: int, body: RenameBody) -> dict:
        try:
            renamed = core.store.rename_chat(chat_id, body.title)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        chat = core.store.get_chat(chat_id) if renamed else None
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        return _chat_payload(chat)

    @app.delete("/api/chats", status_code=204)
    def delete_all_chats() -> Response:
        if not core.answers.run_if_idle(core.store.delete_all_chats):
            raise HTTPException(
                status_code=409, detail="An answer is being written. Wait for it to finish."
            )
        return Response(status_code=204)

    @app.delete("/api/chats/{chat_id}", status_code=204)
    def delete_chat(chat_id: int) -> Response:
        if not core.store.delete_chat(chat_id):
            raise HTTPException(status_code=404, detail="Chat not found.")
        return Response(status_code=204)

    @app.post("/api/chats/{chat_id}/messages")
    def ask(chat_id: int, body: QuestionBody) -> StreamingResponse:
        return StreamingResponse(
            _sse(core.answers.ask(chat_id, body.content, mode=body.mode, think=body.think)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/answer/cancel", status_code=204)
    def cancel() -> Response:
        core.answers.cancel()
        return Response(status_code=204)


def _stored_key(provider: str) -> str | None:
    """The saved key, or None when there is none or the key store cannot be read."""
    try:
        return key_store.get_api_key(provider)
    except Exception as e:  # any keyring backend failure; the key is never in the message
        log.warning("cannot read the API key: %s", type(e).__name__)
        return None


def _settings_payload(core: Core) -> dict:
    """The settings, plus whether a key is saved for the selected provider and its last 4
    characters (only for a key longer than 8, so a short key is never nearly revealed)."""
    settings = core.settings
    key = _stored_key(settings.api_provider)
    return {
        **dataclasses.asdict(settings),
        "data_dir": str(core.data_dir),  # read-only: shown in Settings, never a setting
        "api_key_set": key is not None,
        "api_key_hint": key[-4:] if key is not None and len(key) > 8 else None,
    }


def _add_settings_routes(app: FastAPI, core: Core) -> None:
    @app.get("/api/settings")
    def get_settings() -> dict:
        return _settings_payload(core)

    @app.put("/api/settings")
    def put_settings(changes: dict[str, Any]) -> dict:
        try:
            core.apply_settings(changes)
        except ValueError as e:  # names the unknown or invalid field
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _settings_payload(core)

    @app.put("/api/settings/api-key", status_code=204)
    def put_api_key(body: ApiKeyBody) -> Response:
        if body.provider not in API_PROVIDERS:
            raise HTTPException(status_code=400, detail="Unknown API provider.")
        key = body.key.strip()
        try:
            if key:
                key_store.set_api_key(body.provider, key)
            else:
                key_store.delete_api_key(body.provider)
        except Exception as e:  # never put the key or the backend's message in the response
            log.warning("cannot save the API key: %s", type(e).__name__)
            raise HTTPException(
                status_code=500, detail="The API key could not be saved to the system."
            ) from e
        core.retire_api()  # the cached provider holds the old key
        return Response(status_code=204)

    @app.post("/api/settings/test-connection")
    def test_connection() -> dict:
        """Send a 1-token request with the saved API settings and the stored key.

        It tests the API settings whatever the current mode is, so a key can be tried before
        switching to API mode. It always answers 200 with {ok, reason, message}.
        """
        generator = None
        try:
            provider = core.open_api()
            generator = provider.generate([{"role": "user", "content": "ping"}], max_tokens=1)
            next(generator, None)  # the first chunk, or the end: either proves the call worked
        except ProviderError as e:
            return {"ok": False, "reason": e.reason, "message": str(e)}
        except LLMError as e:
            return {"ok": False, "reason": "other", "message": str(e)}
        except Exception as e:  # never a 500, and never the exception's text (it may name a URL)
            log.warning("connection test failed: %s", type(e).__name__)
            return {"ok": False, "reason": "other", "message": "The connection test failed."}
        finally:
            if generator is not None:
                try:
                    generator.close()
                except Exception as e:  # the route must never answer 500
                    log.warning("closing the connection test failed: %s", type(e).__name__)
        return {"ok": True, "reason": None, "message": "Connected."}


def _entry_payload(entry: ModelEntry, installed: bool, status: dict) -> dict:
    return {
        "id": entry.id,
        "name": entry.name,
        "size": sum(f.size for f in entry.files),
        "installed": installed,
        "state": status["state"],  # idle | downloading | verifying | error
        "done": status["done"],
        "total": status["total"],
        "error": status["error"],
    }


def _add_model_routes(app: FastAPI, core: Core) -> None:
    @app.get("/api/models")
    def get_models() -> dict:
        hardware = core.hardware()  # probed once (it runs llama-server), then remembered
        catalog = core.models.catalog
        models_dir = core.models.models_dir
        installed = catalog.installed(models_dir)  # file sizes only: nothing is hashed here
        status = core.models.status()
        tier = recommend(hardware, catalog)
        local = [
            {
                **_entry_payload(m, m.id in installed, status[m.id]),
                "tier": m.tier,
                "recommended": m.tier == tier,
                "license": m.license,
                "languages": list(m.languages),
                "context_length": m.context_length,
                "thinking": m.thinking,
                "min_vram_gb": m.min_vram_gb,
            }
            for m in catalog.llms()
        ]
        uncatalogued = []
        for path in catalog.uncatalogued(models_dir):
            try:
                size = path.stat().st_size
            except OSError:
                continue  # removed since the listing
            uncatalogued.append(
                {"id": f"import:{path.name}", "name": path.stem, "file": path.name, "size": size}
            )
        embedding = catalog.embedding()
        settings = core.settings
        # The local model that local mode would serve now; the same resolution LocalLLM uses.
        try:
            local_id: str | None
            local_id, _, local_name = core.resolve_local_model()
            local_label: str | None = local_name
        except ProviderError:  # no local model is installed
            local_id = local_label = None
        label = settings.api_model if settings.mode == "api" else local_label
        return {
            "hardware": {
                "ram_gb": round(hardware.ram_gb, 1),
                "gpus": [
                    {"name": g.name, "vram_mb": g.vram_mb, "integrated": _is_integrated(g)}
                    for g in hardware.gpus
                ],
            },
            "recommended_tier": tier,
            "active": {"mode": settings.mode, "label": label, "id": local_id},
            "gpu_offload": core.local.gpu_offload,
            "local": local,
            "uncatalogued": uncatalogued,
            "embedding": _entry_payload(embedding, embedding.id in installed, status[embedding.id]),
        }

    @app.post("/api/models/import")
    def import_model(body: ImportBody) -> dict:
        raw = body.path.strip()
        if raw.startswith(("\\\\?\\", "\\\\.\\")):  # device and extended-length paths
            raise HTTPException(status_code=400, detail=f"Choose an ordinary file path: {raw}")
        path = Path(raw)
        if not path.is_absolute():
            raise HTTPException(
                status_code=400, detail="Choose a full file path, for example C:\\Models\\m.gguf."
            )
        if not path.is_file():
            raise HTTPException(status_code=400, detail=f"File not found: {raw}")
        try:
            result = core.models.import_file(path)
        except ImportRefused as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except OSError as e:  # the destination: a full disk, or the model file is in use
            log.warning("model import failed: %s", e)
            raise HTTPException(
                status_code=500,
                detail="The model file could not be copied. "
                "Check free disk space and that the model is not in use.",
            ) from e
        return {
            "id": result.id,
            "path": str(result.path),
            "catalogued": result.catalogued,
            "warning": result.warning,
        }

    @app.post("/api/models/{model_id}/download", status_code=202)
    def download_model(model_id: str) -> dict:
        try:
            entry = core.models.catalog.get(model_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail="Model not found.") from e
        if entry.id in core.models.catalog.installed(core.models.models_dir):
            raise HTTPException(status_code=409, detail="Already installed.")
        try:
            core.models.start_download(model_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail="Model not found.") from e
        except DownloadBusy as e:
            raise HTTPException(status_code=409, detail="Another download is running.") from e
        return {"status": "downloading"}

    @app.delete("/api/models/{model_id}/download", status_code=204)
    def cancel_download(model_id: str) -> Response:
        try:
            core.models.catalog.get(model_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail="Model not found.") from e
        core.models.cancel_download(model_id)
        return Response(status_code=204)
