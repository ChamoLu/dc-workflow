"""Field-level input diagnostics without rejected values or exception context."""

import logging
from uuid import uuid4

from ..infrastructure.files import write_json

logger = logging.getLogger(__name__)
INPUT_FIELDS = {
    "body",
    "fileMd5",
    "markdownMD5",
    "pageTitle",
    "fileType",
    "title",
    "publictime",
    "GZKJG",
    "workflow_run_id",
    "rule_indicator_name",
    "rule_indicator_code",
    "wait_seconds",
}
ERROR_TYPES = {
    "missing",
    "literal_error",
    "string_type",
    "string_too_short",
    "string_too_long",
    "list_type",
    "dict_type",
    "too_short",
    "too_long",
    "extra_forbidden",
    "value_error",
    "json_invalid",
    "finite_number",
    "greater_than_equal",
    "less_than_equal",
    "float_parsing",
}


def _field(location):
    parts = []
    for part in location:
        if part in ("body", "query", "path"):
            continue
        if isinstance(part, int) and not isinstance(part, bool) and 0 <= part <= 100000:
            if not parts:
                parts.append("body")
            parts[-1] += f"[{part}]"
        elif isinstance(part, str):
            # Unknown keys can themselves be secrets. Only our protocol names leave the server.
            parts.append(part if part in INPUT_FIELDS else "unknown_field")
    return ".".join(parts) or "body"


def validation_diagnostic(settings, phase: str, errors: list[dict]) -> dict:
    without_rules = any(error.get("type") == "without_GZKJG" for error in errors)
    issues = []
    for error in errors[:20]:
        value = error.get("input")
        kind = (
            type(value).__name__
            if type(value) in (str, int, float, bool, list, dict, type(None))
            else "other"
        )
        error_type = error.get("type")
        issues.append(
            {
                "field": "GZKJG" if error_type == "without_GZKJG" else _field(error.get("loc", ())),
                "error_type": error_type if error_type in ERROR_TYPES else "validation_error",
                "input_type": "missing" if error_type == "missing" else kind,
            }
        )
    diagnostic = {
        "error_code": "without_GZKJG" if without_rules else "INVALID_INPUT",
        "phase": phase,
        "message": "GZKJG has no valid rules; generation was not started"
        if without_rules
        else "Input validation failed; inspect the listed fields",
        "diagnostic_id": uuid4().hex,
        "issues": issues,
    }
    try:
        write_json(
            settings.resolved_runtime()
            / "diagnostics"
            / "http-input-errors"
            / f"{diagnostic['diagnostic_id']}.json",
            diagnostic,
        )
    except OSError:
        logger.warning("Could not persist sanitized HTTP input diagnostics")
    logger.warning(
        "HTTP input validation rejected: phase=%s fields=%s diagnostic_id=%s",
        phase,
        ",".join(item["field"] for item in issues),
        diagnostic["diagnostic_id"],
    )
    return diagnostic
