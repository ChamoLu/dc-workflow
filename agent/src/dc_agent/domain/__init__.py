"""Provider-independent contracts. Source facts and GZK are kept separate."""

import hashlib
import json
import unicodedata
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def normalize_name(value: str) -> str:
    # Preserve semantic English inter-word spaces; no fuzzy matching.
    text = unicodedata.normalize("NFKC", value).replace("\n", "").strip()
    return text.translate(str.maketrans("", "", "、,'"))


class AgentError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        stage: str | None = None,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.stage = stage
        self.details = details or {}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Rule(BaseModel):
    model_config = ConfigDict(extra="allow")
    rule_indicator_name: str
    rule_indicator_code: str = ""

    @field_validator("rule_indicator_code", mode="before")
    @classmethod
    def code_text(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError("rule_indicator_code must be text or an integer")
        return str(value)

    @field_validator("rule_indicator_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not value.strip() or value.strip().lower() in {"empty", "null", "none"}:
            raise ValueError("rule_indicator_name is not valid")
        return value


TYPES = {
    "xls",
    "xlsx",
    "csv",
    "html",
    "htm",
    "shtml",
    "md",
    "markdown",
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "doc",
    "docx",
}


class Task(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=200)
    pageTitle: str = Field(min_length=1)
    fileType: str
    fileMd5: str | None = None
    markdownMD5: str | None = None
    GZKJG: list[Rule] = Field(min_length=1)
    title: str = ""
    publictime: str = ""
    source_metadata: dict[str, str] = Field(default_factory=dict)
    # Development-only input adapter; generated parsers still download by MD5.
    source_path: str | None = None
    reference_paths: list[str] = Field(default_factory=list)
    invalid_rule_rows: int = 0

    @model_validator(mode="before")
    @classmethod
    def filter_invalid_names(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("GZKJG") is None:
            raise PydanticCustomError("without_GZKJG", "GZKJG has no valid rules")
        if not isinstance(data, dict) or not isinstance(data.get("GZKJG"), list):
            return data
        values = []
        invalid = 0
        for row in data["GZKJG"]:
            name = (
                row.get("rule_indicator_name")
                if isinstance(row, dict)
                else getattr(row, "rule_indicator_name", None)
            )
            if (
                not isinstance(name, str)
                or not name.strip()
                or name.strip().lower() in {"empty", "null", "none"}
            ):
                invalid += 1
            else:
                values.append(row)
        if not values:
            raise PydanticCustomError(
                "without_GZKJG", "GZKJG contains no valid rule_indicator_name"
            )
        return {
            **data,
            "GZKJG": values,
            "invalid_rule_rows": int(data.get("invalid_rule_rows", 0)) + invalid,
        }

    @field_validator("fileType")
    @classmethod
    def file_type(cls, value: str) -> str:
        result = value.strip().lower().removeprefix(".")
        if result not in TYPES:
            raise ValueError(f"Unsupported fileType: {result}")
        return result

    @model_validator(mode="after")
    def validate_context(self) -> "Task":
        if not self.pageTitle.strip():
            raise ValueError("pageTitle cannot be blank")
        identifier = self.fileMd5 if self.kind == "python_excel" else self.markdownMD5
        if not identifier or not identifier.strip():
            raise ValueError("fileMd5 or markdownMD5 required for the selected route")
        return self

    @property
    def kind(self) -> str:
        if self.fileType in {"xls", "xlsx", "csv"}:
            return "python_excel"
        if self.fileType in {"html", "htm", "shtml"}:
            return "python_html"
        return "markdown_prompt"

    @property
    def source_id(self) -> str:
        return str(self.fileMd5 if self.kind == "python_excel" else self.markdownMD5)

    @property
    def source_key(self) -> str:
        return digest([self.pageTitle, self.fileType])

    @property
    def input_hash(self) -> str:
        return digest(self.model_dump(exclude={"request_id"}))

    def parser_params(self) -> dict:
        return {
            "fileMd5": self.fileMd5,
            "markdownMD5": self.markdownMD5,
            "title": self.title,
            "publictime": self.publictime,
        }


class Design(Contract):
    summary: str = Field(min_length=1)
    name_logic: str = Field(min_length=1)
    unit_logic: str = Field(min_length=1)
    unit_position: str = Field(min_length=1)
    date_logic: str = Field(min_length=1)
    date_position: str = Field(min_length=1)
    covered_sections: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    reference_conflicts: list[str] = Field(default_factory=list)


class Evidence(Contract):
    record_index: int = Field(ge=0)
    name_refs: list[str] = Field(min_length=1)
    data_ref: str
    unit_refs: list[str] = Field(default_factory=list)
    date_refs: list[str] = Field(default_factory=list)


class Candidate(Contract):
    content: str = Field(min_length=1)
    evidence_mode: Literal["sidecar", "runtime"] = "sidecar"
    evidence: list[Evidence] = Field(default_factory=list)


class Review(Contract):
    approved: bool
    findings: list[str]
    reviewed_refs: list[str] = Field(default_factory=list)
    excluded_source_refs: dict[str, str] = Field(default_factory=dict)
    # Legacy negative reviews remain correctness rejections. New reviews can
    # explicitly classify omissions as nonblocking, without parsing free text.
    rejection_kind: Literal["correctness", "coverage"] = Field(
        default="correctness",
        description="Use coverage only when a negative verdict concerns omissions alone; wrong output or mixed issues use correctness",
    )
    quality_findings: list[str] = Field(default_factory=list)
    reference_conflicts: list[str] = Field(default_factory=list)
    issues: list["ReviewIssue"] = Field(default_factory=list)
    resolved_issue_ids: list[str] = Field(default_factory=list)
    repair_lessons: list["RepairLesson"] = Field(default_factory=list, max_length=6)


class RepairLesson(Contract):
    issue_id: str = Field(min_length=1, max_length=100)
    root_cause: str = Field(min_length=1, max_length=300)
    fix_summary: str = Field(min_length=1, max_length=500)
    applicability: str = Field(min_length=1, max_length=300)


class ReviewIssue(Contract):
    category: Literal[
        "empty_extraction",
        "end_date_empty",
        "name_semantics",
        "source_value",
        "source_unit",
        "unit_conversion",
        "coverage",
        "runtime",
        "other",
    ]
    message: str = Field(min_length=1)
    source_refs: list[str] = Field(default_factory=list)
    record_indices: list[int] = Field(default_factory=list)
    code_symbols: list[str] = Field(default_factory=list)


class Check(Contract):
    name: str
    status: Literal["pass", "fail", "warning", "not_run"]
    message: str
    details: dict = Field(default_factory=dict)


class Report(Contract):
    scope: str
    checks: list[Check] = Field(default_factory=list)
    records: list[dict] = Field(default_factory=list)
    matching: dict = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and not any(c.status == "fail" for c in self.checks)


class Result(Contract):
    status: Literal["ready", "processing", "failed", "review_required", "invalid_input"]
    task_id: str
    source_key: str
    cache_hit: bool = False
    artifact: dict | None = None
    error: dict | None = None
    preflight: dict | None = None
    diagnostics: dict | None = None


class ContentDecision(Contract):
    """A deliberately coarse model gate; uncertain content is allowed to proceed."""

    verdict: Literal["has_data", "no_data", "uncertain"]
    reason: str = Field(min_length=1)
