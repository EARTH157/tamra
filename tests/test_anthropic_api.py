from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from tamra.llm.anthropic_api import THINKING_HEADROOM, AnthropicLLM
from tamra.llm.base import Chunk, LLMError, ProviderError

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def delta_event(kind: str, text: str):
    field = "text" if kind == "text_delta" else "thinking"
    return SimpleNamespace(
        type="content_block_delta", delta=SimpleNamespace(type=kind, **{field: text})
    )


def stop_event(reason: str):
    return SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason=reason))


class StubStream:
    def __init__(self, events, error=None):
        self.events, self.error = events, error
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.exited = True
        return False

    def __iter__(self):
        yield from self.events
        if self.error:
            raise self.error


class StubClient:
    """Stands in for anthropic.Anthropic: records the stream() arguments."""

    def __init__(self, events=(), error=None):
        self.closed = False
        self.calls: list[dict] = []
        self.stream_obj = StubStream(list(events), error)
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        return self.stream_obj

    def close(self):
        self.closed = True


def status_error(cls, status: int, message: str = "nope"):
    return cls(message, response=httpx2.Response(status, request=REQUEST), body=None)


MESSAGES = [
    {"role": "system", "content": "Be brief."},
    {"role": "user", "content": "Hi"},
    {"role": "assistant", "content": "Hello"},
    {"role": "user", "content": "Again"},
]


def llm_for(client) -> AnthropicLLM:
    return AnthropicLLM("claude-sonnet-5-5", "test-key", client=client)


def test_text_deltas_stream_as_text_chunks_and_the_system_prompt_is_separate():
    client = StubClient([delta_event("text_delta", "Hel"), delta_event("text_delta", "lo")])
    chunks = list(llm_for(client).generate(MESSAGES, max_tokens=500))
    assert chunks == [Chunk("text", "Hel"), Chunk("text", "lo")]
    [call] = client.calls
    assert call["model"] == "claude-sonnet-5-5"
    assert call["system"] == "Be brief."
    assert call["max_tokens"] == 500 + THINKING_HEADROOM  # Claude 5 thinks even when not asked
    assert call["messages"] == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
        {"role": "user", "content": "Again"},
    ]
    assert "thinking" not in call
    assert client.stream_obj.exited


def test_no_system_message_means_no_system_parameter():
    client = StubClient([])
    list(llm_for(client).generate(MESSAGES[1:2]))
    assert "system" not in client.calls[0]


def test_thinking_deltas_become_thinking_chunks_and_thinking_is_requested():
    client = StubClient(
        [
            delta_event("thinking_delta", "Hmm. "),
            delta_event("text_delta", "Answer"),
            SimpleNamespace(
                type="content_block_delta", delta=SimpleNamespace(type="signature_delta")
            ),
            SimpleNamespace(type="content_block_start"),
        ]
    )
    chunks = list(llm_for(client).generate(MESSAGES, max_tokens=500, think=True))
    assert chunks == [Chunk("thinking", "Hmm. "), Chunk("text", "Answer")]
    [call] = client.calls
    assert call["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert call["max_tokens"] == 500 + THINKING_HEADROOM


def test_thinking_is_dropped_when_it_was_not_asked_for():
    client = StubClient([delta_event("thinking_delta", "x"), delta_event("text_delta", "y")])
    assert list(llm_for(client).generate(MESSAGES)) == [Chunk("text", "y")]


def test_a_refusal_is_an_error():
    client = StubClient([delta_event("text_delta", "I"), stop_event("refusal")])
    stream = llm_for(client).generate(MESSAGES)
    assert next(stream) == Chunk("text", "I")
    with pytest.raises(ProviderError) as info:
        list(stream)
    assert info.value.reason == "other"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (status_error(anthropic.AuthenticationError, 401), "auth"),
        (status_error(anthropic.PermissionDeniedError, 403), "auth"),
        (status_error(anthropic.RateLimitError, 429), "quota"),
        (status_error(anthropic.APIStatusError, 402), "quota"),
        (status_error(anthropic.BadRequestError, 400, "Your credit balance is too low"), "quota"),
        (status_error(anthropic.BadRequestError, 400, "bad field"), "other"),
        (status_error(anthropic.NotFoundError, 404), "model_missing"),
        (status_error(anthropic.InternalServerError, 500), "other"),
        (anthropic.APIConnectionError(request=REQUEST), "offline"),
        (anthropic.APITimeoutError(request=REQUEST), "offline"),
        (anthropic.AnthropicError("something"), "other"),
    ],
)
def test_sdk_errors_map_to_provider_error_reasons(error, reason):
    client = StubClient([delta_event("text_delta", "partial")], error=error)
    stream = llm_for(client).generate(MESSAGES)
    assert next(stream) == Chunk("text", "partial")
    with pytest.raises(ProviderError) as info:
        list(stream)
    assert info.value.reason == reason
    assert isinstance(info.value, LLMError)
    assert info.value.__cause__ is error


def test_errors_raised_when_opening_the_stream_are_mapped_too():
    class Failing:
        def __init__(self):
            self.messages = SimpleNamespace(stream=self._stream)

        def _stream(self, **kwargs):
            raise status_error(anthropic.AuthenticationError, 401, "invalid x-api-key")

    with pytest.raises(ProviderError) as info:
        list(llm_for(Failing()).generate(MESSAGES))
    assert info.value.reason == "auth"
    assert "invalid x-api-key" in str(info.value)


def test_the_key_is_not_shown_in_repr():
    secret = "test-secret-value"
    assert secret not in repr(AnthropicLLM("claude-sonnet-5-5", secret, client=StubClient()))
    assert secret not in repr(AnthropicLLM("claude-sonnet-5-5", secret))  # the real SDK client


def test_provider_attributes():
    llm = llm_for(StubClient())
    assert (llm.kind, llm.label) == ("api", "claude-sonnet-5-5")


def test_close_closes_the_sdk_client():
    client = StubClient()
    llm = llm_for(client)
    llm.close()
    assert client.closed


def test_thinking_headroom_is_reserved_without_thinking_and_summaries_only_with_it():
    plain, thinking = StubClient(), StubClient()
    list(llm_for(plain).generate(MESSAGES, max_tokens=1024))
    list(llm_for(thinking).generate(MESSAGES, max_tokens=1024, think=True))
    assert plain.calls[0]["max_tokens"] == 1024 + THINKING_HEADROOM
    assert "thinking" not in plain.calls[0]
    assert thinking.calls[0]["max_tokens"] == 1024 + THINKING_HEADROOM
    assert thinking.calls[0]["thinking"] == {"type": "adaptive", "display": "summarized"}


def test_running_out_of_room_before_any_text_is_an_error():
    client = StubClient([delta_event("thinking_delta", "hmm"), stop_event("max_tokens")])
    with pytest.raises(ProviderError, match="ran out of room") as info:
        list(llm_for(client).generate(MESSAGES, think=True))
    assert info.value.reason == "other"


def test_a_partial_answer_cut_off_at_max_tokens_stands_with_a_warning(caplog):
    client = StubClient([delta_event("text_delta", "Three years"), stop_event("max_tokens")])
    with caplog.at_level("WARNING", logger="tamra.llm.anthropic_api"):
        chunks = list(llm_for(client).generate(MESSAGES))
    assert chunks == [Chunk("text", "Three years")]
    assert "max_tokens" in caplog.text


def test_a_normal_stop_is_not_an_error():
    client = StubClient([delta_event("text_delta", "ok"), stop_event("end_turn")])
    assert list(llm_for(client).generate(MESSAGES)) == [Chunk("text", "ok")]


@pytest.mark.parametrize("key", ["", None])
def test_a_missing_key_is_refused_before_the_sdk_can_look_elsewhere(key, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-environment")
    with pytest.raises(ProviderError) as info:
        AnthropicLLM("claude-sonnet-5-5", key)
    assert info.value.reason == "auth"
