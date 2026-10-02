import json

import httpx
import pytest

from tamra.llm.openai_compat import LLMError, OpenAICompatibleLLM

SSE = (
    'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
    'data: {"choices":[],"usage":{"total_tokens":5}}\n\n'
    "data: [DONE]\n\n"
)


def test_streams_content_deltas_and_sends_expected_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text=SSE, headers={"content-type": "text/event-stream"})

    llm = OpenAICompatibleLLM(
        "http://llm.test", "m1", api_key="k", transport=httpx.MockTransport(handler)
    )
    tokens = list(llm.generate([{"role": "user", "content": "hi"}], max_tokens=8))

    assert tokens == ["Hel", "lo"]
    assert seen["url"] == "http://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"] == {
        "model": "m1",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 8,
        "stream": True,
    }


def test_no_auth_header_without_key():
    def handler(request):
        assert "authorization" not in request.headers
        return httpx.Response(200, text="data: [DONE]\n\n")

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    assert list(llm.generate([{"role": "user", "content": "hi"}])) == []


def test_http_error_raises_llm_error():
    llm = OpenAICompatibleLLM(
        "http://llm.test",
        "m1",
        transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad key")),
    )
    with pytest.raises(LLMError, match="401"):
        list(llm.generate([{"role": "user", "content": "hi"}]))


def test_mid_stream_error_event_raises_llm_error():
    """Mid-stream error event should raise LLMError."""
    sse_with_error = (
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        'data: {"error":{"message":"rate limited"}}\n\n'
    )

    def handler(request):
        return httpx.Response(
            200, text=sse_with_error, headers={"content-type": "text/event-stream"}
        )

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="rate limited"):
        list(llm.generate([{"role": "user", "content": "hi"}]))


def test_stream_ending_without_done_or_finish_reason_raises_llm_error():
    """Stream ending without [DONE] or finish_reason should raise LLMError."""
    sse_no_done = 'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'

    def handler(request):
        return httpx.Response(200, text=sse_no_done, headers={"content-type": "text/event-stream"})

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="Stream ended"):
        list(llm.generate([{"role": "user", "content": "hi"}]))


def test_finish_reason_without_done_returns_tokens():
    """finish_reason should mark stream as complete without requiring [DONE]."""
    sse_with_finish = (
        'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
        'data: {"choices":[{"finish_reason":"stop","delta":{}}]}\n\n'
    )

    def handler(request):
        return httpx.Response(
            200, text=sse_with_finish, headers={"content-type": "text/event-stream"}
        )

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    tokens = list(llm.generate([{"role": "user", "content": "hi"}]))
    assert tokens == ["Hello", " world"]


def test_transport_failure_raises_llm_error():
    """Transport errors should be wrapped as LLMError."""

    def handler(request):
        raise httpx.ConnectError("Connection refused")

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="Connection refused"):
        list(llm.generate([{"role": "user", "content": "hi"}]))


def test_invalid_json_data_raises_llm_error():
    """Non-empty invalid JSON data: line should raise LLMError."""
    sse_invalid_json = 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\ndata: {invalid json}\n\n'

    def handler(request):
        return httpx.Response(
            200, text=sse_invalid_json, headers={"content-type": "text/event-stream"}
        )

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="JSONDecodeError"):
        list(llm.generate([{"role": "user", "content": "hi"}]))


def test_raw_unicode_separators_preserved():
    """Raw U+2028, U+2029, U+0085 in JSON content preserved intact."""
    content = "Hello  \u0085World"
    payload = {"choices": [{"delta": {"content": content}}]}
    # Use ensure_ascii=False to send raw separator bytes
    body = f"data: {json.dumps(payload, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode()

    # Verify raw bytes are present
    assert " ".encode() in body
    assert " ".encode() in body
    assert "\u0085".encode("utf-8") in body

    def handler(request):
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    tokens = list(llm.generate([{"role": "user", "content": "hi"}]))
    assert tokens == [content]


def test_multibyte_utf8_split_across_chunks():
    """Multibyte UTF-8 character split across network chunks decoded intact."""
    content = "Hello♥World"  # ♥ is \xe2\x9d\xa5 in UTF-8
    payload = {"choices": [{"delta": {"content": content}}]}
    body = f"data: {json.dumps(payload, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode()

    # Find the heart character and split right after the first byte of the multibyte sequence
    heart_bytes = "♥".encode()  # 3 bytes
    heart_index = body.index(heart_bytes)
    cut = heart_index + 1  # Split after first byte of multibyte char

    def handler(request):
        # Return response with content split across chunks
        return httpx.Response(
            200,
            content=iter([body[:cut], body[cut:]]),
            headers={"content-type": "text/event-stream"},
        )

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    tokens = list(llm.generate([{"role": "user", "content": "hi"}]))
    assert tokens == [content]


def test_string_valued_error_raises_llm_error():
    """A string-valued error field should raise LLMError (not AttributeError)."""
    sse = (
        'data: {"choices":[{"delta":{"content":"token"}}]}\n\n'
        'data: {"error":"model not loaded"}\n\n'
    )

    def handler(request):
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="model not loaded"):
        list(llm.generate([{"role": "user", "content": "hi"}]))


def test_non_dict_json_raises_llm_error():
    """Valid JSON that is not a dict should raise LLMError."""
    sse = "data: [1]\n\n"

    def handler(request):
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError):
        list(llm.generate([{"role": "user", "content": "hi"}]))


def test_final_done_without_newline():
    """A final [DONE] without trailing newline returns tokens, no error."""
    sse = 'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\ndata: [DONE]'  # No \n\n

    def handler(request):
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    tokens = list(llm.generate([{"role": "user", "content": "hi"}]))
    assert tokens == ["Hello"]


def test_empty_finish_reason_continues_reading():
    """An empty finish_reason is not completion; stream continues until [DONE]."""
    sse = (
        'data: {"choices":[{"delta":{"content":"A"}}]}\n\n'
        'data: {"choices":[{"finish_reason":""}]}\n\n'
        'data: {"choices":[{"delta":{"content":"B"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request):
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    tokens = list(llm.generate([{"role": "user", "content": "hi"}]))
    assert tokens == ["A", "B"]


def test_empty_data_and_null_delta():
    """Empty data: lines and null delta values are skipped gracefully."""
    sse = (
        'data: {"choices":[{"delta":{"content":"A"}}]}\n\n'
        "data: \n\n"  # Empty payload
        'data: {"choices":[{"delta":null}]}\n\n'  # Null delta
        'data: {"choices":[{"delta":{"content":"B"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request):
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    tokens = list(llm.generate([{"role": "user", "content": "hi"}]))
    assert tokens == ["A", "B"]


def test_loopback_client_bypasses_proxy(monkeypatch):
    """Loopback client ignores proxy env vars and succeeds."""
    import http.server
    import threading

    # Set broken proxy env vars
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")

    # Start a background server on loopback
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(SSE.encode())

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    host, port = server.server_address
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        # Connect to the loopback server and generate
        llm = OpenAICompatibleLLM(f"http://{host}:{port}", "local")
        tokens = list(llm.generate([{"role": "user", "content": "hi"}]))
        llm.close()
        assert tokens == ["Hel", "lo"]
    finally:
        server.shutdown()
