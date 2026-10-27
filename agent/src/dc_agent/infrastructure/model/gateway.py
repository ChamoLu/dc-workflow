"""Configurable model transport with bounded streaming and diagnostics."""

import ssl
import time
from urllib.parse import urlsplit

import httpx

from ...config import Settings
from ...domain import AgentError, dumps
from .chat_stream import ModelResponseReader
from .responses import ResponsesResponseReader


def model_transport_error(exc: httpx.HTTPError, operation: str, seconds: float) -> AgentError:
    """Preserve failure category without echoing URLs, headers, or provider bodies."""
    if isinstance(exc, httpx.ReadTimeout):
        return AgentError(
            "MODEL_READ_TIMEOUT",
            f"ReadTimeout during {operation}: no response data within {seconds:g}s. "
            "The endpoint may still be generating; increase DC_MODEL_TIMEOUT or reduce output size.",
            retryable=True,
        )
    if isinstance(exc, httpx.ConnectTimeout):
        return AgentError(
            "MODEL_CONNECT_TIMEOUT",
            f"ConnectTimeout during {operation}: cannot establish connection within {seconds:g}s",
            retryable=True,
        )
    if isinstance(exc, httpx.TimeoutException):
        return AgentError(
            "MODEL_TIMEOUT",
            f"{type(exc).__name__} during {operation}: request I/O timed out",
            retryable=True,
        )
    if isinstance(exc, httpx.ProxyError):
        return AgentError(
            "MODEL_PROXY_ERROR",
            "ProxyError: model request could not traverse the proxy",
            retryable=True,
        )
    if isinstance(exc, httpx.RemoteProtocolError):
        return AgentError(
            "MODEL_PROTOCOL_ERROR",
            "RemoteProtocolError: model server closed the connection or returned an invalid HTTP response",
            retryable=True,
        )
    cause: BaseException | None = exc
    for _ in range(6):
        if isinstance(cause, ssl.SSLCertVerificationError):
            return AgentError(
                "MODEL_TLS_ERROR",
                "TLS certificate verification failed; configure the trusted certificate chain",
            )
        cause = cause.__cause__ if cause else None
    return AgentError(
        "MODEL_CONNECTION_FAILED",
        f"{type(exc).__name__} during {operation}: model transport failed; check endpoint/network",
        retryable=True,
    )


class ChatCompletionsGateway:
    """Configurable OpenAI-compatible wire protocols; name retained for existing imports."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport
        self.last_call_metrics: dict = {}

    def complete(
        self, operation: str, system: str, context: dict, schema: dict, timeout: float
    ) -> dict:
        config = self.settings
        if (
            not config.model_base_url
            or not config.model_name
            or not config.model_api_key.get_secret_value()
        ):
            raise AgentError(
                "MODEL_NOT_CONFIGURED",
                "Set DC_MODEL_BASE_URL, DC_MODEL_NAME and DC_MODEL_API_KEY in .env",
            )
        parsed = urlsplit(config.model_base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.query
        ):
            raise AgentError(
                "MODEL_CONFIGURATION_INVALID",
                "Model base URL must not contain credentials or query parameters",
            )
        request = {
            "model": config.model_name,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": dumps(
                        {"operation": operation, "context": context, "required_json_schema": schema}
                    ),
                },
            ],
            config.model_token_parameter: config.output_budget(operation),
            "stream": config.model_stream,
        }
        if config.model_reasoning_effort:
            request["reasoning_effort"] = config.model_reasoning_effort
        if config.model_json_mode:
            request["response_format"] = {"type": "json_object"}
        native = config.model_api_style == "responses"
        if native:
            request = {
                "model": config.model_name,
                "instructions": system,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": request["messages"][1]["content"]}
                        ],
                    }
                ],
                "max_output_tokens": config.output_budget(operation),
                "stream": config.model_stream,
                "store": False,
            }
            if config.model_reasoning_effort:
                request["reasoning"] = {"effort": config.model_reasoning_effort}
            if config.model_json_mode:
                request["text"] = {"format": {"type": "json_object"}}
        if len(dumps(request)) > config.max_context_chars:
            raise AgentError(
                "CONTEXT_TOO_LARGE",
                "Complete source context exceeds configured limit; no silent truncation",
            )
        deadline = time.monotonic() + timeout
        headers = {"Authorization": f"Bearer {config.model_api_key.get_secret_value()}"}
        url = config.model_base_url.rstrip("/") + ("/responses" if native else "/chat/completions")
        attempt_metrics = []
        for attempt in range(config.model_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AgentError(
                    "MODEL_TIMEOUT", "Model call exhausted its time budget", retryable=True
                )
            reader = ResponsesResponseReader(deadline) if native else ModelResponseReader(deadline)
            self.last_call_metrics = {
                "operation": operation,
                "attempt": attempt + 1,
                "requested_stream": config.model_stream,
                "requested_model": config.model_name,
                "requested_max_tokens": config.output_budget(operation),
                "api_style": config.model_api_style,
                "token_parameter": "max_output_tokens" if native else config.model_token_parameter,
                "reasoning_effort": config.model_reasoning_effort or None,
            }
            try:
                with httpx.Client(
                    timeout=min(config.model_timeout, remaining),
                    trust_env=False,
                    transport=self.transport,
                ) as client:
                    with client.stream("POST", url, headers=headers, json=request) as response:
                        self.last_call_metrics["http_status"] = response.status_code
                        if response.status_code == 524:
                            raise AgentError(
                                "MODEL_GATEWAY_TIMEOUT",
                                f"HTTP 524: remote gateway timed out during {operation}. "
                                f"Requested stream={config.model_stream}; increasing local timeout cannot change the gateway limit. "
                                "Check provider streaming/latency; this request was not automatically replayed.",
                                retryable=True,
                            )
                        if response.status_code >= 400:
                            details = reader.http_error_details(response)
                            if reader.provider_error_code == "get_channel_failed":
                                raise AgentError(
                                    "MODEL_PROVIDER_CHANNEL_UNAVAILABLE",
                                    f"HTTP {response.status_code}: provider reported get_channel_failed; no available channel for the configured model. Check provider model routing and token group, then resume the saved task.",
                                    retryable=True,
                                )
                            if response.status_code == 429 or response.status_code >= 500:
                                if attempt < config.model_retries:
                                    time.sleep(
                                        min(
                                            0.5 * (attempt + 1), max(0, deadline - time.monotonic())
                                        )
                                    )
                                    continue
                            raise AgentError(
                                "MODEL_HTTP_ERROR",
                                f"Model endpoint returned HTTP {response.status_code}"
                                + (f" ({details})" if details else ""),
                                retryable=response.status_code == 429
                                or response.status_code >= 500,
                            )
                        result = reader.read(response)
                return result
            except AgentError as exc:
                self.last_call_metrics["error_code"] = exc.code
                # A tiny early response with no useful result may be a transient gateway failure.
                # Never replay a long generation, token truncation, filter or tool response here.
                if (
                    config.model_retry_short_incomplete
                    and attempt == 0
                    and attempt < config.model_retries
                    and exc.code == "MODEL_OUTPUT_INCOMPLETE"
                    and reader.finish_reason in {None, "error"}
                    and reader.streaming
                    and reader.event_count > 0
                    and reader.content_bytes <= 1024
                    and reader.usage.get("completion_tokens", 0) <= 32
                    and time.monotonic() - reader.started <= 15
                ):
                    time.sleep(min(0.5, max(0, deadline - time.monotonic())))
                    continue
                raise AgentError(exc.code, f"{operation}: {exc}", retryable=exc.retryable) from exc
            except httpx.HTTPError as exc:
                if reader.streaming and reader.received_bytes:
                    raise AgentError(
                        "MODEL_STREAM_INTERRUPTED",
                        f"{type(exc).__name__} after receiving stream data during {operation}; "
                        "partial output discarded and not automatically replayed.",
                        retryable=True,
                    ) from exc
                if attempt == config.model_retries:
                    raise model_transport_error(
                        exc, operation, min(config.model_timeout, remaining)
                    ) from exc
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise AgentError(
                    "MODEL_OUTPUT_INVALID", "Model returned incomplete or invalid JSON"
                ) from exc
            finally:
                self.last_call_metrics.update(
                    {
                        "received_stream": reader.streaming,
                        "response_bytes": reader.received_bytes,
                        "first_byte_seconds": reader.first_byte_seconds,
                        "elapsed_seconds": round(time.monotonic() - reader.started, 3),
                        "usage": reader.usage,
                        **reader.diagnostics(),
                    }
                )
                attempt_metrics.append(dict(self.last_call_metrics))
                self.last_call_metrics["attempts"] = list(attempt_metrics)
        raise AgentError("MODEL_CONNECTION_FAILED", "Model retries exhausted", retryable=True)
