"""Small public wire contract; local development inputs are deliberately absent."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain import Task

AGENT_FIELDS = (
    "status",
    "stage",
    "job_id",
    "PythonFile",
    "PythonCode",
    "code_sha256",
    "artifact_version",
    "cache_hit",
    "quality_status",
    "warning_summary",
    "error_code",
    "message",
    "report_ref",
    "elapsed_seconds",
)
# V3 keeps its exact fourteen-field response.  V4 adds prompt delivery without
# overloading PythonCode, so a platform executor can reject the wrong route.
V4_AGENT_FIELDS = (*AGENT_FIELDS, "PromptFile", "PromptCode")
EXCEL_TYPES = ("xls", "xlsx", "csv")
HTML_TYPES = ("html", "htm", "shtml")
MARKDOWN_TYPES = ("md", "markdown", "pdf", "png", "jpg", "jpeg", "doc", "docx")
TERMINAL = {"ready", "failed", "review_required", "invalid_input"}


class _JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pageTitle: str = Field(min_length=1, max_length=1000)
    title: str = Field(default="", max_length=10000)
    publictime: str = Field(default="", max_length=1000)
    GZKJG: list[Any] = Field(default_factory=list, max_length=100000)
    workflow_run_id: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("GZKJG", mode="before")
    @classmethod
    def decode_rules(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
                return [] if decoded is None else decoded
            except ValueError:
                raise ValueError("GZKJG must be an array or a JSON array string") from None
        return value

    @field_validator("fileType", mode="before", check_fields=False)
    @classmethod
    def normalize_type(cls, value):
        return value.strip().lower().removeprefix(".") if isinstance(value, str) else value

    @field_validator("fileMd5", "markdownMD5", "pageTitle", "workflow_run_id", check_fields=False)
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("A nonblank value is required")
        return value

    def task_payload(self) -> dict:
        # Reuse Task's checked GZK handling before putting a request on the queue.
        payload = self.model_dump(exclude={"workflow_run_id"})
        task = Task.model_validate(payload)
        return task.model_dump(exclude={"request_id"})


class ExcelJobRequest(_JobRequest):
    """Shared by the V3 Excel endpoint and the V4 Excel endpoint."""

    fileMd5: str = Field(min_length=1, max_length=200)
    fileType: Literal["xls", "xlsx", "csv"]


class HtmlJobRequest(_JobRequest):
    """HTML parsers consume the current converted Markdown, never raw HTML."""

    markdownMD5: str = Field(min_length=1, max_length=200)
    fileType: Literal["html", "htm", "shtml"]


class MarkdownJobRequest(_JobRequest):
    """Upstream extensions are audit/asset identity; input is always Markdown."""

    markdownMD5: str = Field(min_length=1, max_length=200)
    fileType: Literal["md", "markdown", "pdf", "png", "jpg", "jpeg", "doc", "docx"]
