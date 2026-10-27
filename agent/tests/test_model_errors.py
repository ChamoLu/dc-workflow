import ssl

import httpx
import pytest

from dc_agent.config import Settings
from dc_agent.domain import AgentError
from dc_agent.infrastructure import ChatCompletionsGateway, model_transport_error


@pytest.mark.parametrize(
    ("exception", "code"),
    [
        (httpx.ReadTimeout, "MODEL_READ_TIMEOUT"),
        (httpx.ConnectTimeout, "MODEL_CONNECT_TIMEOUT"),
        (httpx.WriteTimeout, "MODEL_TIMEOUT"),
        (httpx.RemoteProtocolError, "MODEL_PROTOCOL_ERROR"),
        (httpx.ProxyError, "MODEL_PROXY_ERROR"),
        (httpx.ConnectError, "MODEL_CONNECTION_FAILED"),
    ],
)
def test_network_error_classification_without_secret_leak(exception, code):
    settings = Settings(
        _env_file=None,
        model_base_url="https://models.example/v1",
        model_name="test",
        model_api_key="test-secret",
        model_retries=0,
    )

    def fail(request):
        raise exception("test-secret https://sensitive.example/path", request=request)

    gateway = ChatCompletionsGateway(settings, httpx.MockTransport(fail))
    with pytest.raises(AgentError) as result:
        gateway.complete("generate_0", "JSON", {}, {}, 600)
    assert result.value.code == code
    assert "test-secret" not in str(result.value)
    assert "sensitive.example" not in str(result.value)
    assert result.value.retryable


def test_certificate_error_identified_without_disabling_tls():
    exc = httpx.ConnectError("private connection details")
    exc.__cause__ = ssl.SSLCertVerificationError("certificate verification failed")
    error = model_transport_error(exc, "generate_0", 240)
    assert error.code == "MODEL_TLS_ERROR"
    assert not error.retryable


def test_timeout_retry_can_recover():
    settings = Settings(
        _env_file=None,
        model_base_url="https://models.example/v1",
        model_name="test",
        model_api_key="test-secret",
        model_retries=1,
    )
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("fixture", request=request)
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}]},
        )

    result = ChatCompletionsGateway(settings, httpx.MockTransport(handler)).complete(
        "generate_0", "JSON", {}, {}, 600
    )
    assert result == {"ok": True}
    assert len(attempts) == 2


@pytest.mark.parametrize("code", ["context_length_exceeded", 400])
def test_http_error_reports_safe_protocol_fields_without_body(code):
    settings = Settings(
        _env_file=None,
        model_base_url="https://models.example/v1",
        model_name="test",
        model_api_key="test-secret",
        model_retries=0,
    )
    gateway = ChatCompletionsGateway(
        settings,
        httpx.MockTransport(
            lambda _: httpx.Response(
                400,
                json={
                    "error": {
                        "code": code,
                        "type": "invalid_request_error",
                        "param": "max_output_tokens",
                        "message": "test-secret plus echoed source content",
                    }
                },
            )
        ),
    )
    with pytest.raises(AgentError) as result:
        gateway.complete("generate_0", "JSON", {}, {}, 600)
    assert result.value.code == "MODEL_HTTP_ERROR"
    assert f"code={code}" in str(result.value)
    assert "param=max_output_tokens" in str(result.value)
    assert gateway.last_call_metrics["provider_error_type"] == "invalid_request_error"
    assert "test-secret" not in str(result.value)
    assert "echoed source" not in str(gateway.last_call_metrics)


def test_oversized_http_error_is_not_parsed_or_echoed():
    settings = Settings(
        _env_file=None,
        model_base_url="https://models.example/v1",
        model_name="test",
        model_api_key="test-secret",
        model_retries=0,
    )
    gateway = ChatCompletionsGateway(
        settings,
        httpx.MockTransport(lambda _: httpx.Response(400, text="private-source" * 4000)),
    )
    with pytest.raises(AgentError) as result:
        gateway.complete("generate_0", "JSON", {}, {}, 600)
    assert result.value.code == "MODEL_HTTP_ERROR"
    assert "private-source" not in str(result.value)


@pytest.mark.parametrize("http_status", [400, 503])
def test_channel_failure_is_retryable_but_not_automatically_replayed(http_status):
    settings = Settings(
        _env_file=None,
        model_base_url="https://models.example/v1",
        model_name="test",
        model_api_key="test-secret",
        model_retries=2,
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            http_status,
            json={
                "error": {
                    "code": "get_channel_failed",
                    "type": "new_api_error",
                    "message": "private",
                }
            },
        )

    gateway = ChatCompletionsGateway(settings, httpx.MockTransport(handler))
    with pytest.raises(AgentError) as result:
        gateway.complete("generate_0", "JSON", {}, {}, 600)
    assert result.value.code == "MODEL_PROVIDER_CHANNEL_UNAVAILABLE"
    assert result.value.retryable
    assert len(calls) == 1


@pytest.mark.parametrize("outcome", ["ok", "channel_failure", "invalid_reply"])
def test_cli_model_check_needs_no_task_or_business_data(tmp_path, monkeypatch, capsys, outcome):
    import json

    from dc_agent.entrypoints import cli

    settings = Settings(_env_file=None, runtime_dir=tmp_path)

    class Probe:
        last_call_metrics = {"http_status": 400 if outcome == "channel_failure" else 200}

        def __init__(self, config):
            assert config.model_retries == 0
            assert config.model_max_tokens == 128

        def complete(self, operation, system, context, schema, timeout):
            assert operation == "diagnostic_probe"
            assert context == {"ping": True}
            if outcome == "channel_failure":
                raise AgentError(
                    "MODEL_PROVIDER_CHANNEL_UNAVAILABLE", "get_channel_failed", retryable=True
                )
            return {"ok": outcome == "ok"}

    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "ChatCompletionsGateway", Probe)
    assert cli.main(["check-model"]) == (0 if outcome == "ok" else 3)
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == ("model_available" if outcome == "ok" else "model_unavailable")
    assert list((tmp_path / "diagnostics").glob("model-check-*.json"))
