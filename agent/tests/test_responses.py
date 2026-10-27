import json
import time

import httpx
import pytest

from dc_agent.config import Settings
from dc_agent.domain import AgentError
from dc_agent.infrastructure import ChatCompletionsGateway
from dc_agent.infrastructure.model.responses import ResponsesResponseReader


class Wire(httpx.SyncByteStream):
    def __init__(self, body, fail=False):
        self.body, self.fail = body, fail

    def __iter__(self):
        for index in range(0, len(self.body), 3):
            yield self.body[index : index + 3]
        if self.fail:
            raise httpx.ReadTimeout("private provider details")


def event(kind, **payload):
    return (
        "event: "
        + kind
        + "\r\ndata: "
        + json.dumps({"type": kind, **payload}, ensure_ascii=False)
        + "\r\n\r\n"
    ).encode()


def completed(text='{"ok":true}'):
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": [{"text": "private reasoning"}]},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        ],
        "usage": {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60},
    }


def reader():
    return ResponsesResponseReader(time.monotonic() + 10)


def streamed(body, fail=False):
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, stream=Wire(body, fail)
    )


def test_native_request_completed_event_and_usage_without_done():
    def handler(request):
        assert request.url.path == "/v1/responses"
        body = json.loads(request.content)
        assert "messages" not in body and "response_format" not in body
        assert body["max_output_tokens"] == 4000
        assert body["reasoning"] == {"effort": "low"}
        assert body["text"]["format"] == {"type": "json_object"}
        assert body["store"] is False
        assert body["stream"] is True
        assert json.loads(body["input"][0]["content"][0]["text"])["operation"] == "design"
        wire = b": heartbeat\r\n\r\n" + event(
            "response.created", response={"status": "in_progress"}
        )
        wire += event("response.reasoning_text.delta", delta="private reasoning")
        wire += event("response.output_text.delta", output_index=1, content_index=0, delta='{"ok":')
        wire += event("response.output_text.delta", output_index=1, content_index=0, delta="true}")
        wire += event("response.completed", response=completed())
        return streamed(wire)

    config = Settings(
        _env_file=None,
        model_base_url="https://models.example/v1",
        model_name="test",
        model_api_key="secret",
        model_api_style="responses",
        model_reasoning_effort="low",
    )
    gateway = ChatCompletionsGateway(config, httpx.MockTransport(handler))
    assert gateway.complete("design", "JSON", {}, {}, 20) == {"ok": True}
    metrics = gateway.last_call_metrics
    assert metrics["response_status"] == "completed"
    assert metrics["done_received"] is False
    assert metrics["usage"] == {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}
    assert "private reasoning" not in json.dumps(metrics)


def test_full_terminal_json_is_authoritative_and_unicode_is_preserved():
    body = event("response.output_text.delta", delta='{"ok":')
    body += event("response.completed", response=completed('{"名称":"指标甲"}'))
    assert reader().read(streamed(body)) == {"名称": "指标甲"}


def test_completed_with_deltas_and_empty_terminal_output():
    body = event("response.output_text.delta", output_index=2, delta='{"ok":true}')
    body += event("response.completed", response={"status": "completed", "output": []})
    assert reader().read(streamed(body)) == {"ok": True}


@pytest.mark.parametrize("ending", [b"", b"data: [DONE]\n\n"])
def test_valid_json_text_without_response_completed_is_rejected(ending):
    body = event("response.output_text.delta", delta='{"ok":true}') + ending
    with pytest.raises(AgentError) as caught:
        reader().read(streamed(body))
    assert caught.value.code == "MODEL_OUTPUT_INCOMPLETE"


@pytest.mark.parametrize(
    ("kind", "value", "code"),
    [
        (
            "response.incomplete",
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
            "MODEL_OUTPUT_TRUNCATED",
        ),
        (
            "response.incomplete",
            {"status": "incomplete", "incomplete_details": {"reason": "content_filter"}},
            "MODEL_CONTENT_FILTER",
        ),
        (
            "response.failed",
            {"status": "failed", "error": {"code": "server_error", "message": "private details"}},
            "MODEL_STREAM_ERROR",
        ),
        ("response.failed", {"status": "failed"}, "MODEL_STREAM_ERROR"),
    ],
)
def test_native_failures_are_not_accepted(kind, value, code):
    current = reader()
    with pytest.raises(AgentError) as caught:
        current.read(streamed(event(kind, response=value)))
    assert caught.value.code == code
    assert "private details" not in str(caught.value)


def test_conflicting_terminal_type_and_status_are_rejected():
    with pytest.raises(ValueError, match="Conflicting"):
        reader().read(streamed(event("response.failed", response=completed())))


@pytest.mark.parametrize(
    "output",
    [
        [{"type": "function_call"}],
        [{"type": "message", "content": [{"type": "refusal", "refusal": "private details"}]}],
    ],
)
def test_tools_and_refusals_do_not_become_json(output):
    with pytest.raises(AgentError):
        reader().read(httpx.Response(200, json={"status": "completed", "output": output}))


def test_nonstream_responses_uses_same_terminal_contract():
    assert reader().read(httpx.Response(200, json=completed())) == {"ok": True}


def test_disconnect_after_native_events_is_not_replayed():
    calls = []

    def handler(request):
        calls.append(request)
        return streamed(event("response.created", response={"status": "in_progress"}), fail=True)

    config = Settings(
        _env_file=None,
        model_base_url="https://models.example/v1",
        model_name="test",
        model_api_key="secret",
        model_api_style="responses",
        model_retries=3,
    )
    gateway = ChatCompletionsGateway(config, httpx.MockTransport(handler))
    with pytest.raises(AgentError) as caught:
        gateway.complete("design", "JSON", {}, {}, 20)
    assert caught.value.code == "MODEL_STREAM_INTERRUPTED"
    assert len(calls) == 1
