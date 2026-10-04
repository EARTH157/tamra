import json
import threading
import time

import httpx
import pytest

from tamra.llm.base import Chunk, ProviderError
from tamra.llm.openai_compat import LLMError, OpenAICompatibleLLM, normalise_base_url


def texts(chunks):
    """The answer text of a stream (thinking chunks left out)."""
    return [c.text for c in chunks if c.kind == "text"]


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
    tokens = texts(llm.generate([{"role": "user", "content": "hi"}], max_tokens=8))

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
    tokens = texts(llm.generate([{"role": "user", "content": "hi"}]))
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
    content = "Hello\u2028\u2029\u0085World"
    payload = {"choices": [{"delta": {"content": content}}]}
    # Use ensure_ascii=False to send raw separator bytes
    body = f"data: {json.dumps(payload, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode()

    # Verify raw bytes are present
    assert "\u2028".encode() in body
    assert "\u2029".encode() in body
    assert "\u0085".encode("utf-8") in body

    def handler(request):
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    tokens = texts(llm.generate([{"role": "user", "content": "hi"}]))
    assert tokens == [content]


def test_multibyte_utf8_split_across_chunks():
    """Multibyte UTF-8 character split across network chunks decoded intact."""
    content = "Hello\u2665World"  # U+2665 is \xe2\x99\xa5 in UTF-8
    payload = {"choices": [{"delta": {"content": content}}]}
    body = f"data: {json.dumps(payload, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode()

    # Find the heart character and split right after the first byte of the multibyte sequence
    heart_bytes = "\u2665".encode()  # 3 bytes
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
    tokens = texts(llm.generate([{"role": "user", "content": "hi"}]))
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
    tokens = texts(llm.generate([{"role": "user", "content": "hi"}]))
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
    tokens = texts(llm.generate([{"role": "user", "content": "hi"}]))
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
    tokens = texts(llm.generate([{"role": "user", "content": "hi"}]))
    assert tokens == ["A", "B"]


def test_loopback_client_bypasses_proxy(monkeypatch):
    """Loopback client ignores proxy env vars and succeeds."""
    import http.server

    # Set broken proxy env vars
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    # Start a background server on loopback
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            # Read the request body to avoid socket abort on close
            content_length = int(self.headers["Content-Length"])
            self.rfile.read(content_length)

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
        tokens = texts(llm.generate([{"role": "user", "content": "hi"}]))
        llm.close()
        assert tokens == ["Hel", "lo"]
    finally:
        server.shutdown()
        server.server_close()


def test_tokens_arrive_before_the_stream_ends():
    gate = threading.Event()

    def body():
        yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
        gate.wait(5)
        yield (
            b'data: {"choices":[{"delta":{"content":"second"},"finish_reason":"stop"}]}\n\n'
            b"data: [DONE]\n\n"
        )

    llm = OpenAICompatibleLLM(
        "http://llm.test",
        "m",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body())),
    )
    stream = llm.generate([{"role": "user", "content": "hi"}])
    started = time.monotonic()
    assert next(stream) == Chunk("text", "first")
    assert time.monotonic() - started < 2  # did not wait for the rest of the body
    gate.set()
    assert texts(stream) == ["second"]


def test_closing_the_stream_closes_the_response():
    closed = threading.Event()

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'

        def close(self):
            closed.set()

    llm = OpenAICompatibleLLM(
        "http://llm.test",
        "m",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Body())),
    )
    stream = llm.generate([{"role": "user", "content": "hi"}])
    assert next(stream) == Chunk("text", "a")
    stream.close()
    assert closed.is_set()


def test_thai_and_chinese_tokens_stream_intact():
    body = (
        'data: {"choices":[{"delta":{"content":"สัญญาเช่า"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"三年"},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    ).encode()
    llm = OpenAICompatibleLLM(
        "http://llm.test",
        "m",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body)),
    )
    assert texts(llm.generate([{"role": "user", "content": "hi"}])) == ["สัญญาเช่า", "三年"]


def sse(*payloads: dict) -> bytes:
    lines = [f"data: {json.dumps(p)}\n\n" for p in payloads] + ["data: [DONE]\n\n"]
    return "".join(lines).encode()


def delta(**fields) -> dict:
    return {"choices": [{"delta": fields}]}


def make(handler, **kwargs) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(
        "http://llm.test", "m", transport=httpx.MockTransport(handler), **kwargs
    )


USER = [{"role": "user", "content": "hi"}]


def test_reasoning_deltas_become_thinking_chunks():
    body = sse(
        delta(reasoning_content="Let me "),
        delta(reasoning_content="think."),
        delta(content="Answer", reasoning_content=None),
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    )
    llm = make(lambda r: httpx.Response(200, content=body))
    assert list(llm.generate(USER, think=True)) == [
        Chunk("thinking", "Let me "),
        Chunk("thinking", "think."),
        Chunk("text", "Answer"),
    ]


@pytest.mark.parametrize("think", [True, False])
def test_local_requests_send_enable_thinking(think):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=sse())

    list(make(handler, kind="local").generate(USER, think=think))
    assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": think}


def test_api_requests_do_not_send_server_specific_fields():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=sse())

    list(make(handler, kind="api").generate(USER, think=True))
    assert "chat_template_kwargs" not in seen["body"]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("http://llm.test", "http://llm.test"),
        ("http://llm.test/", "http://llm.test"),
        ("http://llm.test/v1", "http://llm.test"),
        ("http://llm.test/v1/", "http://llm.test"),
        ("https://api.example.com/openai/v1", "https://api.example.com/openai"),
        ("http://127.0.0.1:8080/v1", "http://127.0.0.1:8080"),
        ("http://llm.test/v10", "http://llm.test/v10"),
    ],
)
def test_base_urls_are_normalised(given, expected):
    assert normalise_base_url(given) == expected


def test_a_v1_base_url_does_not_double_the_path():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=sse())

    llm = OpenAICompatibleLLM("http://llm.test/v1/", "m", transport=httpx.MockTransport(handler))
    list(llm.generate(USER))
    assert seen["url"] == "http://llm.test/v1/chat/completions"


def test_max_tokens_is_retried_once_as_max_completion_tokens():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        if "max_tokens" in bodies[-1]:
            return httpx.Response(
                400,
                json={"error": {"message": "Unsupported parameter: 'max_tokens'"}},
            )
        return httpx.Response(200, content=sse(delta(content="ok")))

    chunks = list(make(handler).generate(USER, max_tokens=77))
    assert chunks == [Chunk("text", "ok")]
    assert len(bodies) == 2
    assert "max_tokens" in bodies[0] and bodies[0]["max_tokens"] == 77
    assert "max_tokens" not in bodies[1] and bodies[1]["max_completion_tokens"] == 77


def test_the_max_tokens_retry_happens_only_once():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, text="bad max_tokens, bad max_completion_tokens")

    with pytest.raises(ProviderError) as info:
        list(make(handler).generate(USER))
    assert len(calls) == 2
    assert info.value.reason == "other"


def test_other_400s_are_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, text="context too long")

    with pytest.raises(ProviderError, match="400"):
        list(make(handler).generate(USER))
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "auth"),
        (403, "auth"),
        (429, "quota"),
        (404, "model_missing"),
        (500, "other"),
        (400, "other"),
    ],
)
def test_http_statuses_map_to_error_reasons(status, reason):
    llm = make(lambda r: httpx.Response(status, text="nope"))
    with pytest.raises(ProviderError) as info:
        list(llm.generate(USER))
    assert info.value.reason == reason
    assert isinstance(info.value, LLMError)
    assert str(status) in str(info.value)


def test_connection_errors_map_to_offline():
    def handler(request):
        raise httpx.ConnectError("Connection refused")

    with pytest.raises(ProviderError) as info:
        list(make(handler).generate(USER))
    assert info.value.reason == "offline"


def test_a_read_timeout_is_not_offline():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ProviderError) as info:
        list(make(handler).generate(USER))
    assert info.value.reason == "other"


@pytest.mark.parametrize(("kind", "read"), [("local", 300.0), ("api", 120.0)])
def test_timeouts_depend_on_the_kind(kind, read):
    llm = make(lambda r: httpx.Response(200), kind=kind)
    timeout = llm._client.timeout
    assert (timeout.connect, timeout.read) == (10.0, read)


def test_the_label_defaults_to_the_model_name():
    assert make(lambda r: httpx.Response(200)).label == "m"
    assert make(lambda r: httpx.Response(200), label="Qwen").label == "Qwen"


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_do_not_echo_the_response_body(status):
    llm = make(lambda r: httpx.Response(status, text="Bad key: sk-proj-****abcd"))
    with pytest.raises(ProviderError) as info:
        list(llm.generate(USER))
    assert "sk-" not in str(info.value)
    assert str(status) in str(info.value)
    assert info.value.reason == "auth"


def test_key_shaped_tokens_are_redacted_from_other_error_bodies():
    llm = make(lambda r: httpx.Response(500, text="boom for sk-ant-api03-AbC_dEf-123 retry"))
    with pytest.raises(ProviderError) as info:
        list(llm.generate(USER))
    assert "sk-ant" not in str(info.value) and "AbC" not in str(info.value)
    assert "boom for [redacted] retry" in str(info.value)


@pytest.mark.parametrize(
    "error",
    [httpx.ReadError("reset"), httpx.RemoteProtocolError("closed early"), httpx.WriteError("x")],
)
def test_other_transport_errors_map_to_offline(error):
    def handler(request):
        raise error

    with pytest.raises(ProviderError) as info:
        list(make(handler).generate(USER))
    assert info.value.reason == "offline"


def test_a_connection_dropped_mid_stream_is_offline():
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
            raise httpx.ReadError("connection reset")

    llm = make(lambda r: httpx.Response(200, stream=Body()))
    stream = llm.generate(USER)
    assert next(stream) == Chunk("text", "a")
    with pytest.raises(ProviderError) as info:
        list(stream)
    assert info.value.reason == "offline"
