"""Bounded Chat Completions response parsing; transport streaming is not API streaming."""

import codecs
import json
import re
import time

import httpx

from ...domain import AgentError, dumps


class ModelResponseReader:
    def __init__(self, deadline: float, max_bytes: int = 32_000_000):
        self.deadline = deadline
        self.max_bytes = max_bytes
        self.received_bytes = 0
        self.streaming = False
        self.first_byte_seconds: float | None = None
        self.started = time.monotonic()
        self.usage: dict = {}
        self.finish_reason: str | None = None
        self.done_received = False
        self.event_count = 0
        self.content_bytes = 0
        self.provider_error_code: str | None = None
        self.provider_error_type: str | None = None
        self.provider_error_param: str | None = None

    def diagnostics(self) -> dict:
        return {
            "finish_reason": self.finish_reason,
            "done_received": self.done_received,
            "event_count": self.event_count,
            "content_bytes": self.content_bytes,
            "provider_error_code": self.provider_error_code,
            "provider_error_type": self.provider_error_type,
            "provider_error_param": self.provider_error_param,
        }

    def _error_fields(self, value: object) -> str:
        # Only protocol fields, never provider message/body (which may echo source or credentials).
        if not isinstance(value, dict):
            return ""
        code = value.get("code") if isinstance(value, dict) else None
        if type(code) is int:
            code = str(code)
        if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code):
            self.provider_error_code = code
        for field in ("type", "param"):
            item = value.get(field)
            if isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,80}", item):
                setattr(self, f"provider_error_{field}", item)
        return "; ".join(
            f"{field}={getattr(self, f'provider_error_{field}')}"
            for field in ("code", "type", "param")
            if getattr(self, f"provider_error_{field}") is not None
        )

    def http_error_details(self, response: httpx.Response) -> str:
        raw = bytearray()
        try:
            for chunk in response.iter_bytes():
                if time.monotonic() > self.deadline or len(raw) + len(chunk) > 32_768:
                    return ""
                raw.extend(chunk)
            value = json.loads(raw)
            return self._error_fields(value.get("error")) if isinstance(value, dict) else ""
        except (ValueError, httpx.HTTPError):
            return ""

    def _provider_error(self, value: object) -> None:
        if not value:
            return
        details = self._error_fields(value)
        if self.provider_error_code == "get_channel_failed":
            raise AgentError(
                "MODEL_PROVIDER_CHANNEL_UNAVAILABLE",
                "Provider reported get_channel_failed: no available channel for the configured model. Check provider model routing and token group, then resume the saved task.",
                retryable=True,
            )
        raise AgentError(
            "MODEL_STREAM_ERROR",
            "Provider reported an error in completion response"
            + (f" ({details})" if details else ""),
            retryable=True,
        )

    def _check_finish(self) -> None:
        reason = self.finish_reason
        if reason == "stop":
            return
        if reason == "length":
            raise AgentError(
                "MODEL_OUTPUT_TRUNCATED",
                "Provider ended with finish_reason=length: output or reasoning token limit reached; "
                f"received {self.content_bytes} final-content bytes. Check provider model limits and "
                "token-parameter compatibility; partial output discarded.",
            )
        if reason == "content_filter":
            raise AgentError(
                "MODEL_CONTENT_FILTER", "Provider ended with finish_reason=content_filter"
            )
        if reason in {"tool_calls", "function_call"}:
            raise AgentError(
                "MODEL_UNEXPECTED_TOOL_CALL", "Expected final JSON; provider requested a tool"
            )
        raise AgentError(
            "MODEL_OUTPUT_INCOMPLETE",
            f"Response ended with finish_reason={reason!r}, done_received={self.done_received}, "
            f"content_bytes={self.content_bytes}; partial output discarded",
            retryable=True,
        )

    def _set_finish(self, value: object) -> None:
        if value is None or value == "":
            return
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", value):
            raise ValueError("Invalid completion finish reason")
        if self.finish_reason is not None and self.finish_reason != value:
            raise ValueError("Conflicting completion finish reasons")
        self.finish_reason = value

    def _usage(self, value: object) -> None:
        if isinstance(value, dict):
            self.usage = {
                name: count
                for name, count in value.items()
                if name in {"prompt_tokens", "completion_tokens", "total_tokens"}
                and type(count) is int
                and count >= 0
            }

    def chunks(self, response: httpx.Response):
        for chunk in response.iter_bytes():
            if time.monotonic() >= self.deadline:
                raise AgentError(
                    "MODEL_TIMEOUT", "Model response exceeded total task budget", retryable=True
                )
            if chunk and self.first_byte_seconds is None:
                self.first_byte_seconds = round(time.monotonic() - self.started, 3)
            self.received_bytes += len(chunk)
            if self.received_bytes > self.max_bytes:
                raise AgentError("MODEL_OUTPUT_TOO_LARGE", "Model response exceeds byte limit")
            yield chunk

    def read(self, response: httpx.Response) -> dict:
        self.streaming = "text/event-stream" in response.headers.get("content-type", "").lower()
        if self.streaming:
            return self._sse(response)
        payload = json.loads(b"".join(self.chunks(response)))
        if not isinstance(payload, dict):
            raise ValueError("Expected completion object")
        self._usage(payload.get("usage"))
        self._provider_error(payload.get("error"))
        choice = payload["choices"][0]
        message = choice["message"]
        if message.get("refusal"):
            raise AgentError("MODEL_REFUSAL", "Model refused the requested output")
        content = message.get("content")
        self.content_bytes = len(content.encode("utf-8")) if isinstance(content, str) else 0
        self._set_finish(choice.get("finish_reason"))
        self._check_finish()
        return self._content(content)

    @staticmethod
    def _content(content: object) -> dict:
        if not isinstance(content, str) or not content.strip():
            raise AgentError("MODEL_OUTPUT_INVALID", "Model returned no final content")
        if len(content.encode("utf-8")) > 4_000_000:
            raise AgentError("MODEL_OUTPUT_TOO_LARGE", "Final model content exceeds byte limit")
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("Expected JSON object")
        dumps(result)
        return result

    def _lines(self, response: httpx.Response):
        decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        buffer = ""
        for chunk in self.chunks(response):
            buffer += decoder.decode(chunk)
            while "\n" in buffer:
                value, buffer = buffer.split("\n", 1)
                yield value.removesuffix("\r")
        buffer += decoder.decode(b"", final=True)
        if buffer:
            yield buffer.removesuffix("\r")
        yield ""

    def events(self, response: httpx.Response):
        data_lines = []
        for line in self._lines(response):
            if line.startswith("data:"):
                data_lines.append(line[5:].removeprefix(" "))
                continue
            if line or not data_lines:
                continue
            text = "\n".join(data_lines)
            data_lines = []
            if text == "[DONE]":
                self.done_received = True
                return
            self.event_count += 1
            packet = json.loads(text)
            if not isinstance(packet, dict):
                raise ValueError("Invalid stream event")
            yield packet

    def _sse(self, response: httpx.Response) -> dict:
        parts = []
        for packet in self.events(response):
            self._provider_error(packet.get("error"))
            if packet.get("usage"):
                self._usage(packet["usage"])
            for choice in packet.get("choices", []):
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta") or {}
                if delta.get("refusal"):
                    raise AgentError("MODEL_REFUSAL", "Model refused the requested output")
                content = delta.get("content")
                if content is not None:
                    if not isinstance(content, str):
                        raise ValueError("Unsupported stream content type")
                    if self.finish_reason is not None and content:
                        raise ValueError("Content after completion")
                    self.content_bytes += len(content.encode("utf-8"))
                    if self.content_bytes > 4_000_000:
                        raise AgentError(
                            "MODEL_OUTPUT_TOO_LARGE", "Final model content exceeds byte limit"
                        )
                    parts.append(content)
                # reasoning_content/thinking are not business output and are never journaled.
                self._set_finish(choice.get("finish_reason"))

        self._check_finish()
        return self._content("".join(parts))
