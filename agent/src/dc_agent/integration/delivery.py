"""Select a small wire result and verify the immutable code on every delivery."""

import hashlib
import json
import re
import time
import unicodedata
from pathlib import Path

from ..assets.layout import AssetLayout
from ..domain import AgentError, Task
from ..infrastructure.files import filesystem_path
from .contracts import AGENT_FIELDS, MARKDOWN_TYPES, V4_AGENT_FIELDS

REPORT_NAMES = {
    "quality_report.json",
    "quality_report.md",
    "validation.json",
    "selection.json",
    "repair_issues.json",
}


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _failure_message(settings, value) -> str:
    """Expose a bounded scalar summary, never a serialized error body or context."""
    if not isinstance(value, str) or not value.strip():
        return ""
    # Model readers already synthesize their messages from whitelisted protocol
    # fields. This additional boundary also protects unexpected upstream errors.
    value = "".join(
        " " if unicodedata.category(char) in {"Cc", "Cf", "Cs"} else char for char in value
    )
    value = re.sub(r"\s+", " ", value).strip()
    if (
        value.startswith(("{", "[", "<"))
        or re.search(r"[:=]\s*[\[{<]", value)
        or re.search(
            r"(?i)\b(?:request|response|raw)[_ -]?(?:body|headers|payload)\s*[:=]"
            r"|Traceback \(most recent call last\)",
            value,
        )
    ):
        return ""
    value = scrub(value, settings)
    value = re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s,;\"'<>]+", r"\1 [redacted]", value)
    value = re.sub(
        r"(?i)([\"']?(?:authorization|x[-_]api[-_]key|api[-_ ]?(?:key|token)"
        r"|(?:access|refresh)[-_ ]?token|token|secret|password)[\"']?\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)",
        r"\1[redacted]",
        value,
    )
    return value[:2000]


def scrub(text: str, settings) -> str:
    for name in ("api_token", "model_api_key"):
        value = getattr(settings, name, None)
        secret = value.get_secret_value() if hasattr(value, "get_secret_value") else ""
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _inside(root: Path, value: str | Path) -> Path:
    root = root.resolve()
    path = (root / value).resolve()
    if path == root or not path.is_relative_to(root):
        raise AgentError("ARTIFACT_CORRUPT", "Registered path is outside its root")
    return filesystem_path(path)


def read_code(settings, row: dict) -> tuple[dict, str]:
    """Read the registered immutable Python or prompt body, preserving UTF-8 bytes."""
    result = _mapping(row.get("result"))
    artifact = _mapping(result.get("artifact"))
    task = Task.model_validate({**row["payload"], "request_id": row["job_id"]})
    version = artifact.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", version):
        raise AgentError("ARTIFACT_CORRUPT", "Invalid asset version")
    layout = AssetLayout(task.pageTitle, task.fileType)
    expected = (
        layout.directory / ".versions" / task.source_key / version / layout.filename
    ).as_posix()
    if not (
        artifact.get("layout_version") == 2
        and artifact.get("content_root") == "code"
        and artifact.get("source_key") == task.source_key
        and artifact.get("pageTitle") == task.pageTitle
        and artifact.get("fileType") == task.fileType
        and artifact.get("kind") == task.kind
        and artifact.get("contract")
        == (
            "prompt-out_data-five-fields-v1"
            if task.kind == "markdown_prompt"
            else "main-ExcelData-v1"
        )
        and artifact.get("version_path") == expected
        and artifact.get("quality_status") in {"accepted", "usable_with_warnings"}
    ):
        raise AgentError("ARTIFACT_CORRUPT", "Asset identity or execution contract mismatch")
    path = _inside(settings.resolved_code(), expected)
    try:
        raw = path.read_bytes()
        expected_hash = artifact.get("content_hash", "")
        if (
            not re.fullmatch(r"[a-f0-9]{64}", expected_hash)
            or hashlib.sha256(raw).hexdigest() != expected_hash
        ):
            raise AgentError("ARTIFACT_CORRUPT", "Immutable code checksum mismatch")
        code = raw.decode("utf-8")
        if not code.strip():
            raise AgentError("ARTIFACT_CORRUPT", "Immutable code is empty")
        if scrub(code, settings) != code:
            raise AgentError("CREDENTIAL_IN_ARTIFACT", "Code contains a configured credential")
    except (OSError, UnicodeError) as exc:
        raise AgentError("ARTIFACT_CORRUPT", "Immutable code is unavailable or not UTF-8") from exc
    return artifact, code


def report_paths(settings, row: dict) -> dict[str, Path]:
    result = _mapping(row.get("result"))
    artifact = _mapping(result.get("artifact"))
    diagnostics = _mapping(result.get("diagnostics")) or _mapping(artifact.get("diagnostics"))
    candidates = {
        "quality_report.json": diagnostics.get("quality_report"),
        "quality_report.md": diagnostics.get("quality_report_markdown"),
        "validation.json": artifact.get("validation_path"),
        "repair_issues.json": diagnostics.get("repair_issues"),
    }
    internal_id = result.get("task_id", "")
    if isinstance(internal_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", internal_id):
        candidates["selection.json"] = str(Path("tasks") / internal_id / "selection.json")
    found = {}
    for name, value in candidates.items():
        if not isinstance(value, str) or not value:
            continue
        try:
            path = _inside(settings.resolved_runtime(), value)
        except (AgentError, OSError):
            continue
        if path.is_file() and path.name == name:
            found[name] = path
    return found


def _warning(settings, row: dict) -> str:
    paths = report_paths(settings, row)
    details = []
    report = paths.get("validation.json")
    if report:
        try:
            content = json.loads(report.read_bytes().decode("utf-8"))
            details = [
                str(c.get("message", ""))
                for c in content.get("checks", [])
                if c.get("status") in {"warning", "fail"}
            ]
        except (OSError, UnicodeError, ValueError, AttributeError, TypeError):
            pass
    artifact = _mapping(_mapping(row.get("result")).get("artifact"))
    if not details and artifact.get("quality_status") == "usable_with_warnings":
        details = ["Selected code has unresolved quality findings; consult the quality report"]
    return scrub("; ".join(details), settings)[:2000]


def public_result(settings, row: dict, *, protocol_version: int = 3) -> dict:
    item = dict.fromkeys(V4_AGENT_FIELDS if protocol_version == 4 else AGENT_FIELDS, "")
    status = row["status"]
    result = _mapping(row.get("result"))
    item.update(
        status="processing" if status == "queued" else status,
        stage=row["stage"],
        job_id=row["job_id"],
        elapsed_seconds=f"{max(0, (row.get('finished_at') or time.time()) - row['created_at']):.2f}",
    )
    reports = report_paths(settings, row)
    preferred = next(
        (name for name in ("quality_report.json", "validation.json") if name in reports), None
    )
    if preferred:
        prefix = "/v4/jobs" if protocol_version == 4 else "/v3/excel/jobs"
        item["report_ref"] = f"{prefix}/{row['job_id']}/reports/{preferred}"
    if status == "ready":
        artifact, code = read_code(settings, row)
        filename = AssetLayout(row["payload"]["pageTitle"], row["payload"]["fileType"]).filename
        # read_code has already validated Task/fileType and the internal asset
        # identity/contract.  The wire result needs only its matching body pair.
        is_prompt = protocol_version == 4 and row["payload"]["fileType"] in MARKDOWN_TYPES
        if is_prompt:
            item.update(PromptFile=filename, PromptCode=code)
        else:
            item.update(PythonFile=filename, PythonCode=code)
        item.update(
            code_sha256=artifact["content_hash"],
            artifact_version=artifact["version"],
            cache_hit=str(bool(result.get("cache_hit"))).lower(),
            quality_status=artifact["quality_status"],
            warning_summary=_warning(settings, row),
            message="Reused registered code"
            if result.get("cache_hit")
            else "Generated and registered code",
        )
        if is_prompt:
            item["message"] = (
                "Reused registered prompt"
                if result.get("cache_hit")
                else "Generated and registered prompt"
            )
    elif status in {"failed", "review_required", "invalid_input"}:
        error = _mapping(result.get("error"))
        raw_code = scrub(
            str(row.get("error_code") or error.get("code") or "GENERATION_FAILED"),
            settings,
        )
        item["error_code"] = (
            raw_code
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,100}", raw_code)
            else "GENERATION_FAILED"
        )
        stage = error.get("stage")
        if (
            isinstance(stage, str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,100}", stage)
            and scrub(stage, settings) == stage
        ):
            item["stage"] = stage
        item["message"] = _failure_message(settings, error.get("message")) or {
            "BUDGET_EXCEEDED": "The task exhausted its original time budget",
            "WORKER_INTERRUPTED": "The worker was interrupted; submit a new workflow run",
            "SERVICE_RESTARTED": "The service restarted during generation; submit a new workflow run",
            "SERVICE_STOPPED": "The service stopped during generation; submit a new workflow run",
            "ARTIFACT_CORRUPT": "The registered immutable code failed delivery checks",
            "FilenoData": "No extractable source data was found",
            "without_GZKJG": "No valid GZKJG rules were supplied; generation was not started",
            "EMPTY_EXTRACTION": "The parser still extracted no data after bounded repair; inspect the source and local report",
            "END_DATE_EMPTY": "Source-backed date extraction remains unresolved after bounded repair",
            "todoFile_no_pageTitle": "The source pageTitle has no rule registration",
            "pageTitle_redundancy": "The source pageTitle has multiple rule registrations",
        }.get(
            item["error_code"],
            "Generation did not produce deliverable code; inspect the quality report",
        )
    else:
        item["message"] = "Waiting for the persistent task to complete"
    return {"agent": [item]}
