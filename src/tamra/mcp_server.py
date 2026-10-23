"""`Tamra.exe mcp`: an MCP server over stdio for AI apps such as Claude Desktop and Claude Code
(docs/superpowers/specs/2026-10-22-tamra-mcp-design.md §6).

It holds no documents and no models. Each tool call finds the running Tamra through the
discovery file and asks its core, which checks the token and the user's switch. Nothing but the
protocol may be written to stdout, and the query never goes to a log.
"""

from typing import Annotated, Any

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

import tamra
from tamra.discovery import read_discovery
from tamra.paths import data_dir

NOT_RUNNING = "Tamra is not running. Open Tamra and try again."
NO_ANSWER = "Tamra did not answer. Try again."
TIMEOUT = 60.0  # the first search after launch can wait for the embedding model to load
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

_transport: httpx.BaseTransport | None = None  # tests put an httpx.MockTransport here

server = MCPServer(
    "Tamra",
    instructions=(
        "Searches the user's own documents indexed in Tamra (Thai, English and Chinese). "
        "Answer only from the passages it returns, and cite each one by its file and location."
    ),
    version=tamra.__version__,
    log_level="WARNING",
)


def _call(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask the running core. Every failure becomes a ToolError the AI can read."""
    found = read_discovery(data_dir())
    if found is None:
        raise ToolError(NOT_RUNNING)
    port, token = found
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"X-Tamra-Token": token},
            trust_env=False,  # a proxy must never see the token or the passages
            timeout=TIMEOUT,
            transport=_transport,
        ) as client:
            response = client.get(path) if body is None else client.post(path, json=body)
    except httpx.ConnectError as e:
        raise ToolError(NOT_RUNNING) from e
    except httpx.HTTPError as e:
        raise ToolError(NO_ANSWER) from e
    if response.status_code == 401:  # a file left by an earlier run: its token is gone
        raise ToolError(NOT_RUNNING)
    try:
        data = response.json()
    except ValueError as e:
        raise ToolError(NO_ANSWER) from e
    if response.status_code != 200:
        detail = data.get("detail") if isinstance(data, dict) else None
        raise ToolError(detail if isinstance(detail, str) else NO_ANSWER)
    if not isinstance(data, dict):
        raise ToolError(NO_ANSWER)
    return data


@server.tool(annotations=READ_ONLY)
def list_projects() -> dict[str, Any]:
    """List the user's Tamra projects (folders of documents): each project's name, how many of
    its files are indexed, and whether its index is stale (built with another embedding model,
    so it is not searched until the user rebuilds it in Tamra)."""
    return _call("/api/mcp/projects")


@server.tool(annotations=READ_ONLY)
def search(
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=4000,
            description="What to look for, in Thai, English or Chinese. A query in one "
            "language also finds passages written in another.",
        ),
    ],
    projects: Annotated[
        list[str] | None,
        Field(description="Project names from list_projects. Leave out to search every project."),
    ] = None,
    k: Annotated[int, Field(ge=1, le=20, description="How many passages to return.")] = 8,
) -> dict[str, Any]:
    """Search the user's own documents in Tamra. Returns the best-matching passages, best
    first, each with its project, file, location (page, heading or lines) and text. Answer only
    from these passages and cite each one by its file and location. No passages means nothing
    in the documents matches; `notices` names projects that were not searched."""
    body: dict[str, Any] = {"query": query, "k": k}
    if projects is not None:
        body["projects"] = projects
    return _call("/api/mcp/search", body)


def main() -> int:
    server.run()  # stdio, until the client closes it
    return 0
