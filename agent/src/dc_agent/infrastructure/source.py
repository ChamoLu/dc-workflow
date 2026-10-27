"""Source downloads and explicitly bound references."""

import hashlib
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from ..config import Settings
from ..domain import AgentError, Task


class FileSourceProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.deadline: float | None = None

    def _timeout(self) -> float:
        if self.deadline is None:
            return self.settings.download_timeout
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise AgentError("BUDGET_EXCEEDED", "Task deadline exceeded during source download")
        return min(self.settings.download_timeout, remaining)

    def read(self, task: Task) -> bytes:
        timeout = self._timeout()
        limit = self.settings.max_source_bytes
        if task.source_path:
            path = Path(task.source_path)
            if not path.is_file() or path.stat().st_size > limit:
                raise AgentError("SOURCE_UNAVAILABLE", "Local source missing or exceeds byte limit")
            content = path.read_bytes()
        else:
            url = self.settings.download_base_url.rstrip("/") + "/" + quote(task.source_id, safe="")
            try:
                with httpx.stream(
                    "GET",
                    url,
                    timeout=timeout,
                    trust_env=False,
                    follow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        self._timeout()
                        size += len(chunk)
                        if size > limit:
                            raise AgentError("SOURCE_TOO_LARGE", "Download exceeds byte limit")
                        chunks.append(chunk)
                    content = b"".join(chunks)
            except httpx.HTTPError as exc:
                raise AgentError(
                    "DOWNLOAD_FAILED", "Source download failed", retryable=True
                ) from exc
        self._timeout()
        # Successful empty downloads are content, not network failures; the model gate classifies them.
        if len(content) > limit:
            raise AgentError("SOURCE_TOO_LARGE", "Source exceeds byte limit")
        return content


class LocalReferences:
    def load(self, task: Task) -> list[dict]:
        result = []
        for name in task.reference_paths:
            path = Path(name)
            if not path.is_file() or path.stat().st_size > 500_000:
                raise AgentError(
                    "REFERENCE_UNAVAILABLE", f"Reference missing or too large: {path.name}"
                )
            if path.suffix.lower() not in {".md", ".txt", ".json", ".csv"}:
                raise AgentError(
                    "REFERENCE_FORMAT", "Initial reference provider supports text files only"
                )
            raw = path.read_bytes()
            result.append(
                {
                    "name": path.name,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "content": raw.decode("utf-8-sig"),
                }
            )
        return result
