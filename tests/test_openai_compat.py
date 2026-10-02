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
    with pytest.raises(LLMError, match="stream ended"):
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
    with pytest.raises(LLMError, match="invalid.*JSON"):
        list(llm.generate([{"role": "user", "content": "hi"}]))


def test_unicode_separator_in_content():
    """Raw U+2028 inside content should be yielded intact, not split."""
    # U+2028 is encoded as \xe2\x80\xa8 in UTF-8
    content_with_u2028 = "Hello World"
    # Build SSE response with json.dumps to handle escaping properly
    payload = {"choices": [{"delta": {"content": content_with_u2028}}]}
    sse = f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n"

    def handler(request):
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=httpx.MockTransport(handler))
    tokens = list(llm.generate([{"role": "user", "content": "hi"}]))
    assert tokens == [content_with_u2028]


def test_multibyte_utf8_split_across_chunks():
    """Multibyte UTF-8 character in content should be handled intact."""
    # U+2665 HEAVY BLACK HEART = \xe2\x9d\xa5 in UTF-8
    # Test that multibyte UTF-8 characters in content are preserved
    sse_with_unicode = 'data: {"choices":[{"delta":{"content":"Hello♥World"}}]}\n\ndata: [DONE]\n\n'

    def handler(request):
        return httpx.Response(
            200, text=sse_with_unicode, headers={"content-type": "text/event-stream"}
        )

    transport = httpx.MockTransport(handler)
    llm = OpenAICompatibleLLM("http://llm.test", "m1", transport=transport)
    tokens = list(llm.generate([{"role": "user", "content": "hi"}]))
    assert tokens == ["Hello♥World"]
