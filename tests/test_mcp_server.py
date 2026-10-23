"""`Tamra.exe mcp` (docs/superpowers/specs/2026-10-22-tamra-mcp-design.md §6), through the
SDK's in-process client."""

import json
import subprocess
import sys

import anyio
import httpx
import pytest
from mcp import Client

from tamra import mcp_server
from tamra.discovery import write_discovery

PROJECTS = {"projects": [{"name": "Contracts", "indexed_files": 3, "stale": False}]}
FOUND = {
    "passages": [
        {"project": "Contracts", "file": "lease.pdf", "location": "p. 2", "text": "Rent is 10,000."}
    ],
    "notices": [],
}


@pytest.fixture
def core(tmp_path, monkeypatch):
    """A fake core behind the discovery file. `replies` maps a path to a function of the
    request; `seen` records every request."""
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path))
    write_discovery(tmp_path, 5123, "mcp-token")
    seen: list[httpx.Request] = []
    replies = {
        "/api/mcp/projects": lambda request: httpx.Response(200, json=PROJECTS),
        "/api/mcp/search": lambda request: httpx.Response(200, json=FOUND),
    }

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return replies[request.url.path](request)

    monkeypatch.setattr(mcp_server, "_transport", httpx.MockTransport(handle))
    return seen, replies


def call(name, arguments=None):
    async def go():
        async with Client(mcp_server.server) as client:
            return await client.call_tool(name, arguments or {})

    return anyio.run(go)


def error_text(result) -> str:
    assert result.is_error
    return result.content[0].text


def test_the_server_offers_two_read_only_tools():
    async def go():
        async with Client(mcp_server.server) as client:
            return (await client.list_tools()).tools

    tools = {tool.name: tool for tool in anyio.run(go)}
    assert sorted(tools) == ["list_projects", "search"]
    assert all(tool.annotations.read_only_hint for tool in tools.values())
    schema = tools["search"].input_schema
    assert schema["required"] == ["query"]
    assert (schema["properties"]["k"]["minimum"], schema["properties"]["k"]["maximum"]) == (1, 20)


def test_list_projects_returns_the_core_s_list_and_sends_the_mcp_token(core):
    seen, _ = core
    result = call("list_projects")
    assert not result.is_error
    assert result.structured_content == PROJECTS
    assert seen[0].url == httpx.URL("http://127.0.0.1:5123/api/mcp/projects")
    assert seen[0].headers["X-Tamra-Token"] == "mcp-token"


def test_search_sends_the_query_projects_and_k(core):
    seen, _ = core
    result = call("search", {"query": "ค่าเช่าเท่าไร", "projects": ["Contracts"], "k": 3})
    assert result.structured_content == FOUND
    assert json.loads(seen[0].content) == {
        "query": "ค่าเช่าเท่าไร",
        "projects": ["Contracts"],
        "k": 3,
    }


def test_search_leaves_projects_out_when_none_are_named(core):
    seen, _ = core
    call("search", {"query": "rent"})
    assert json.loads(seen[0].content) == {"query": "rent", "k": 8}


def test_a_k_out_of_range_is_refused_before_the_core_is_asked(core):
    seen, _ = core
    assert call("search", {"query": "rent", "k": 50}).is_error
    assert seen == []


def test_without_the_discovery_file_tamra_is_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path))
    assert mcp_server.NOT_RUNNING in error_text(call("list_projects"))


def test_a_refused_connection_or_an_old_token_means_tamra_is_not_running(core):
    _, replies = core

    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    replies["/api/mcp/projects"] = refuse
    assert mcp_server.NOT_RUNNING in error_text(call("list_projects"))
    replies["/api/mcp/projects"] = lambda request: httpx.Response(401, json={"detail": "x"})
    assert mcp_server.NOT_RUNNING in error_text(call("list_projects"))


def test_the_core_s_reason_reaches_the_ai(core):
    _, replies = core
    off = "AI access is off. Turn it on in Tamra: Settings > General > AI apps."
    replies["/api/mcp/search"] = lambda request: httpx.Response(403, json={"detail": off})
    assert off in error_text(call("search", {"query": "rent"}))


def test_a_timeout_or_an_unreadable_reply_says_tamra_did_not_answer(core):
    _, replies = core

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    replies["/api/mcp/search"] = slow
    assert mcp_server.NO_ANSWER in error_text(call("search", {"query": "rent"}))
    replies["/api/mcp/search"] = lambda request: httpx.Response(500, text="oops")
    assert mcp_server.NO_ANSWER in error_text(call("search", {"query": "rent"}))
    replies["/api/mcp/search"] = lambda request: httpx.Response(200, text="not json")
    assert mcp_server.NO_ANSWER in error_text(call("search", {"query": "rent"}))


def test_the_bridge_loads_neither_the_core_nor_the_window():
    code = (
        "import sys, tamra.mcp_server; "
        "print([m for m in ('tamra.core', 'onnxruntime', 'webview') if m in sys.modules])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_the_mcp_command_runs_the_server_and_not_the_app(monkeypatch):
    from tamra import __main__ as entry

    ran = []
    monkeypatch.setattr(mcp_server, "main", lambda: ran.append("mcp") or 0)
    monkeypatch.setattr("tamra.app.run", lambda **kw: pytest.fail("the app must not start"))
    assert entry.main(["mcp"]) == 0
    assert ran == ["mcp"]
