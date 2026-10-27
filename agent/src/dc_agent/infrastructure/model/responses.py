"""Responses wire protocol, isolated from business generation and Chat Completions."""

import json

import httpx

from ...domain import AgentError
from .chat_stream import ModelResponseReader


class ResponsesResponseReader(ModelResponseReader):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.response_status: str | None = None

    def diagnostics(self) -> dict:
        return {**super().diagnostics(), "response_status": self.response_status}

    def _response(self, value: dict) -> str:
        status = value.get("status")
        self.response_status = (
            status
            if status in {"completed", "incomplete", "failed", "in_progress", "queued"}
            else None
        )
        usage = value.get("usage") or {}
        if not isinstance(usage, dict):
            raise ValueError("Invalid Responses usage")
        self._usage(
            {
                "prompt_tokens": usage.get("input_tokens"),
                "completion_tokens": usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
        )
        self._provider_error(value.get("error"))
        if status == "failed":
            raise AgentError(
                "MODEL_STREAM_ERROR", "Provider returned response.failed", retryable=True
            )
        if status != "completed":
            reason = (value.get("incomplete_details") or {}).get("reason")
            if reason == "max_output_tokens":
                self._set_finish("length")
            elif reason == "content_filter":
                self._set_finish("content_filter")
            if self.finish_reason is not None:
                self._check_finish()
            raise AgentError(
                "MODEL_OUTPUT_INCOMPLETE",
                f"Responses status={self.response_status!r}; expected completed; partial output discarded",
                retryable=True,
            )
        pieces = []
        output = value.get("output") or []
        if not isinstance(output, list):
            raise ValueError("Invalid Responses output")
        for item in output:
            if not isinstance(item, dict):
                raise ValueError("Invalid Responses output item")
            kind = item.get("type")
            if kind == "reasoning":
                continue
            if kind != "message":
                raise AgentError(
                    "MODEL_UNEXPECTED_TOOL_CALL", "Responses output contains a non-message item"
                )
            content = item.get("content") or []
            if not isinstance(content, list):
                raise ValueError("Invalid Responses message content")
            for part in content:
                if not isinstance(part, dict):
                    raise ValueError("Invalid Responses content part")
                if part.get("type") == "refusal":
                    raise AgentError("MODEL_REFUSAL", "Model refused the requested output")
                if part.get("type") == "output_text":
                    text = part.get("text")
                    if not isinstance(text, str):
                        raise ValueError("Invalid Responses output text")
                    pieces.append(text)
        self._set_finish("stop")
        return "".join(pieces)

    def read(self, response: httpx.Response) -> dict:
        self.streaming = "text/event-stream" in response.headers.get("content-type", "").lower()
        if self.streaming:
            return self._sse(response)
        value = json.loads(b"".join(self.chunks(response)))
        if not isinstance(value, dict):
            raise ValueError("Expected Responses object")
        text = self._response(value)
        self.content_bytes = len(text.encode("utf-8"))
        return self._content(text)

    def _sse(self, response: httpx.Response) -> dict:
        parts: dict[tuple[int, int], list[str]] = {}
        for packet in self.events(response):
            kind = packet.get("type")
            if kind == "error":
                self._provider_error(packet)
            self._provider_error(packet.get("error"))
            if kind in {"response.failed", "response.incomplete", "response.completed"}:
                value = packet.get("response")
                if not isinstance(value, dict):
                    raise ValueError("Missing terminal Responses object")
                # The terminal event must agree with its object, never accept a completed body in a failure event.
                expected = kind.removeprefix("response.")
                if value.get("status") != expected:
                    raise ValueError("Conflicting Responses terminal status")
                final = self._response(value)
                if not final:
                    final = "".join("".join(parts[key]) for key in sorted(parts))
                self.content_bytes = len(final.encode("utf-8"))
                return self._content(final)
            if kind in {"response.refusal.delta", "response.refusal.done"}:
                raise AgentError("MODEL_REFUSAL", "Model refused the requested output")
            if kind == "response.output_text.delta":
                text = packet.get("delta")
                if not isinstance(text, str):
                    raise ValueError("Invalid Responses text delta")
                key = (packet.get("output_index", 0), packet.get("content_index", 0))
                if any(type(index) is not int or index < 0 for index in key):
                    raise ValueError("Invalid Responses text index")
                parts.setdefault(key, []).append(text)
                self.content_bytes += len(text.encode("utf-8"))
                if self.content_bytes > 4_000_000:
                    raise AgentError(
                        "MODEL_OUTPUT_TOO_LARGE", "Final model content exceeds byte limit"
                    )
            # created/in_progress/heartbeat/reasoning events carry no accepted final business output.
        raise AgentError(
            "MODEL_OUTPUT_INCOMPLETE",
            f"Responses stream has no response.completed; content_bytes={self.content_bytes}; partial output discarded",
            retryable=True,
        )
