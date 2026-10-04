"""HTTP API on 127.0.0.1 (spec §2-§3): token-gated JSON routes, SSE answers, and the built UI."""

import json
import secrets
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import tamra
from tamra.answer import source_payload
from tamra.core import Core
from tamra.store import Chat, Collection

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


def create_app(
    token: str,
    ui_dir: Path | None,
    core: Core | None = None,
    pick_folder: Callable[[], str | None] | None = None,
) -> FastAPI:
    if not token:
        raise ValueError("the API token must not be empty")  # an empty token would match ""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

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

    if core is not None:
        _add_routes(app, core)
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

    @app.delete("/api/chats/{chat_id}", status_code=204)
    def delete_chat(chat_id: int) -> Response:
        if not core.store.delete_chat(chat_id):
            raise HTTPException(status_code=404, detail="Chat not found.")
        return Response(status_code=204)

    @app.post("/api/chats/{chat_id}/messages")
    def ask(chat_id: int, body: QuestionBody) -> StreamingResponse:
        return StreamingResponse(
            _sse(core.answers.ask(chat_id, body.content)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/answer/cancel", status_code=204)
    def cancel() -> Response:
        core.answers.cancel()
        return Response(status_code=204)
