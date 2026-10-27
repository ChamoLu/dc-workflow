import json
import time

import httpx
import pytest

from dc_agent.config import Settings
from dc_agent.domain import AgentError
from dc_agent.infrastructure import ChatCompletionsGateway
from dc_agent.infrastructure.model.chat_stream import ModelResponseReader


class Wire(httpx.SyncByteStream):
    def __init__(self, body, width=7, fail=False):
        self.body, self.width, self.fail = body, width, fail

    def __iter__(self):
        for offset in range(0, len(self.body), self.width):
            yield self.body[offset : offset + self.width]
        if self.fail:
            raise httpx.ReadTimeout("fixture secret must not appear")


def event(value):
    return ("data: " + json.dumps(value, ensure_ascii=False) + "\r\n\r\n").encode()


def delta(content=None, finish=None, **extra):
    return {
        "choices": [{"index": 0, "delta": {"content": content, **extra}, "finish_reason": finish}]
    }


def settings(**kwargs):
    return Settings(
        _env_file=None,
        model_base_url="https://models.example/v1",
        model_name="test",
        model_api_key="fixture-secret",
        model_retries=3,
        **kwargs,
    )


def test_sse_unicode_heartbeats_reasoning_and_usage():
    content = json.dumps({"content": "名称与中文\n完整代码", "evidence": []}, ensure_ascii=False)
    body = b": heartbeat\r\n\r\n" + event(delta(reasoning_content="private thinking"))
    body += event(delta(content[:11])) + event(delta(content[11:])) + event(delta(finish="stop"))
    body += event({"choices": [], "usage": {"total_tokens": 42, "private": "not saved"}})
    body += b"data: [DONE]\r\n\r\n"

    def handler(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            stream=Wire(body, width=1),
        )

    client = ChatCompletionsGateway(settings(), httpx.MockTransport(handler))
    assert client.complete("generate_0", "JSON", {}, {}, 600) == json.loads(content)
    assert client.last_call_metrics["received_stream"] is True
    assert client.last_call_metrics["usage"] == {"total_tokens": 42}
    assert "private thinking" not in json.dumps(client.last_call_metrics)


@pytest.mark.parametrize(
    ("finish", "code"),
    [
        (None, "MODEL_OUTPUT_INCOMPLETE"),
        ("length", "MODEL_OUTPUT_TRUNCATED"),
        ("content_filter", "MODEL_CONTENT_FILTER"),
        ("tool_calls", "MODEL_UNEXPECTED_TOOL_CALL"),
        ("error", "MODEL_OUTPUT_INCOMPLETE"),
    ],
)
def test_partial_or_nonstop_stream_never_accepted(finish, code):
    body = event(delta('{"ok":true}', finish=finish)) + b"data: [DONE]\n\n"
    reader = ModelResponseReader(time.monotonic() + 10)
    response = httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Wire(body))
    with pytest.raises(AgentError) as caught:
        reader.read(response)
    assert caught.value.code == code
    assert reader.diagnostics()["finish_reason"] == finish
    assert reader.diagnostics()["done_received"] is True
    assert reader.diagnostics()["content_bytes"] == len('{"ok":true}')


def test_empty_trailing_finish_does_not_overwrite_stop():
    body = event(delta('{"ok":true}', finish="stop")) + event(delta(finish=""))
    reader = ModelResponseReader(time.monotonic() + 10)
    response = httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Wire(body))
    assert reader.read(response) == {"ok": True}


def test_conflicting_finish_reasons_are_not_accepted():
    body = event(delta('{"ok":true}', finish="stop")) + event(delta(finish="length"))
    response = httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Wire(body))
    with pytest.raises(ValueError, match="Conflicting"):
        ModelResponseReader(time.monotonic() + 10).read(response)


def test_nonstream_length_uses_same_classification():
    response = httpx.Response(
        200, json={"choices": [{"finish_reason": "length", "message": {"content": "{"}}]}
    )
    with pytest.raises(AgentError) as caught:
        ModelResponseReader(time.monotonic() + 10).read(response)
    assert caught.value.code == "MODEL_OUTPUT_TRUNCATED"


def test_failed_stream_metrics_include_finish_reason_without_final_text():
    def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Wire(event(delta('"private output"', finish="length")) + b"data: [DONE]\n\n"),
        )

    client = ChatCompletionsGateway(settings(), httpx.MockTransport(handler))
    with pytest.raises(AgentError):
        client.complete("design", "JSON", {}, {}, 20)
    assert client.last_call_metrics["finish_reason"] == "length"
    assert client.last_call_metrics["done_received"] is True
    assert "private output" not in json.dumps(client.last_call_metrics)


def test_tiny_early_incomplete_response_recovers_once_and_keeps_both_attempts():
    calls = []

    def handler(request):
        calls.append(request)
        finish = None if len(calls) == 1 else "stop"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Wire(event(delta('{"ok":true}', finish=finish)) + b"data: [DONE]\n\n"),
        )

    client = ChatCompletionsGateway(settings(), httpx.MockTransport(handler))
    assert client.complete("design", "JSON", {}, {}, 20) == {"ok": True}
    assert len(calls) == 2
    assert client.last_call_metrics["attempts"][0]["error_code"] == "MODEL_OUTPUT_INCOMPLETE"
    assert client.last_call_metrics["attempts"][1]["finish_reason"] == "stop"


@pytest.mark.parametrize(
    ("finish", "content", "enabled", "expected_calls"),
    [
        (None, "{}", True, 2),
        (None, "{}", False, 1),
        (None, "x" * 1025, True, 1),
        ("length", "{}", True, 1),
        ("content_filter", "{}", True, 1),
    ],
)
def test_incomplete_recovery_is_bounded_and_does_not_replay_other_failures(
    finish, content, enabled, expected_calls
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Wire(event(delta(content, finish=finish)) + b"data: [DONE]\n\n"),
        )

    client = ChatCompletionsGateway(
        settings(model_retry_short_incomplete=enabled), httpx.MockTransport(handler)
    )
    with pytest.raises(AgentError):
        client.complete("design", "JSON", {}, {}, 20)
    assert len(calls) == expected_calls


def test_incomplete_with_large_reported_usage_is_not_replayed():
    calls = []

    def handler(request):
        calls.append(request)
        body = event(delta("{}"))
        body += event({"choices": [], "usage": {"completion_tokens": 1000}})
        body += b"data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Wire(body))

    client = ChatCompletionsGateway(settings(), httpx.MockTransport(handler))
    with pytest.raises(AgentError, match="design:"):
        client.complete("design", "JSON", {}, {}, 20)
    assert len(calls) == 1
    assert client.last_call_metrics["requested_max_tokens"] == 4000


@pytest.mark.parametrize(
    ("operation", "limit"), [("design", 4000), ("generate_0", 16000), ("review_1", 4000)]
)
def test_stage_budgets_and_reasoning_compatibility_are_explicit(operation, limit):
    def handler(request):
        body = json.loads(request.content)
        assert "max_tokens" not in body
        assert body["max_completion_tokens"] == limit
        assert body["reasoning_effort"] == "low"
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]},
        )

    config = settings(
        model_max_tokens=120000,
        model_token_parameter="max_completion_tokens",
        model_reasoning_effort="low",
    )
    client = ChatCompletionsGateway(config, httpx.MockTransport(handler))
    assert client.complete(operation, "JSON", {}, {}, 20) == {}
    assert client.last_call_metrics["requested_max_tokens"] == limit


def test_global_budget_remains_ceiling_and_legacy_parameters_are_unchanged():
    def handler(request):
        body = json.loads(request.content)
        assert body["max_tokens"] == 100
        assert "max_completion_tokens" not in body
        assert "reasoning_effort" not in body
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]},
        )

    assert (
        ChatCompletionsGateway(
            settings(model_max_tokens=100), httpx.MockTransport(handler)
        ).complete("generate_0", "JSON", {}, {}, 20)
        == {}
    )


def test_sse_multiline_event_and_no_done_after_clean_stop():
    body = b'data: {"choices":\ndata: [{"index":0,"delta":{"content":"{\\"ok\\":true}"},"finish_reason":"stop"}]}\n\n'
    reader = ModelResponseReader(time.monotonic() + 10)
    assert reader.read(
        httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Wire(body))
    ) == {"ok": True}


def test_stream_disconnect_is_not_replayed():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Wire(event(delta('{"partial":')), fail=True),
        )

    client = ChatCompletionsGateway(settings(), httpx.MockTransport(handler))
    with pytest.raises(AgentError) as caught:
        client.complete("generate_0", "JSON", {}, {}, 600)
    assert caught.value.code == "MODEL_STREAM_INTERRUPTED"
    assert len(calls) == 1
    assert "fixture secret" not in str(caught.value)


def test_524_does_not_blindly_repeat_same_request():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(524, text="sensitive provider error body")

    client = ChatCompletionsGateway(settings(), httpx.MockTransport(handler))
    with pytest.raises(AgentError) as caught:
        client.complete("generate_0", "JSON", {}, {}, 600)
    assert caught.value.code == "MODEL_GATEWAY_TIMEOUT"
    assert len(calls) == 1
    assert "sensitive" not in str(caught.value)
    assert client.last_call_metrics["http_status"] == 524


def test_nonstream_compatibility_can_be_configured():
    def handler(request):
        assert json.loads(request.content)["stream"] is False
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]},
        )

    client = ChatCompletionsGateway(settings(model_stream=False), httpx.MockTransport(handler))
    assert client.complete("test", "JSON", {}, {}, 20) == {"ok": True}
    assert client.last_call_metrics["received_stream"] is False


def test_provider_error_inside_sse_not_accepted():
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=Wire(event({"error": {"message": "secret"}})),
    )
    with pytest.raises(AgentError) as caught:
        ModelResponseReader(time.monotonic() + 10).read(response)
    assert caught.value.code == "MODEL_STREAM_ERROR"
    assert "secret" not in str(caught.value)


def test_wire_byte_limit_applies_to_reasoning_and_heartbeats():
    response = httpx.Response(
        200, headers={"content-type": "text/event-stream"}, stream=Wire(b":" + b"x" * 100)
    )
    with pytest.raises(AgentError) as caught:
        ModelResponseReader(time.monotonic() + 10, max_bytes=10).read(response)
    assert caught.value.code == "MODEL_OUTPUT_TOO_LARGE"


def test_overall_deadline_is_checked_even_with_heartbeats():
    response = httpx.Response(
        200, headers={"content-type": "text/event-stream"}, stream=Wire(b": heartbeat\n\n")
    )
    with pytest.raises(AgentError) as caught:
        ModelResponseReader(time.monotonic() - 1).read(response)
    assert caught.value.code == "MODEL_TIMEOUT"


def test_interrupted_generation_saves_metrics_but_not_partial_candidate(tmp_path):
    from dc_agent.domain import Candidate
    from dc_agent.generation import Generator

    def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Wire(event(delta('{"content":"partial')), fail=True),
        )

    config = settings()
    gateway = ChatCompletionsGateway(config, httpx.MockTransport(handler))
    generator = Generator(gateway, config, tmp_path, time.monotonic() + 60)
    skill = {"sha256": "original", "profile_sha256": "profile", "adaptation": "", "content": ""}
    with pytest.raises(AgentError) as caught:
        generator.call("generate_0", {}, Candidate, skill)
    assert caught.value.code == "MODEL_STREAM_INTERRUPTED"
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert files[0].name.endswith(".metrics.json")
    assert json.loads(files[0].read_text(encoding="utf-8"))["received_stream"] is True
