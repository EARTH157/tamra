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
