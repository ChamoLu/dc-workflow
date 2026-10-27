"""Local downstream estimates; no platform callback and no unit conversion."""

import re
import unicodedata
from datetime import date

from ..domain import Check, Report, Task
from .source_values import title_month_end


def missing_date(value) -> bool:
    return value is None or (
        isinstance(value, str) and value.strip().lower() in {"", "empty", "null", "none"}
    )


def _has_period_hint(value) -> bool:
    """Only block implicit title fallback; these hints never supply an output date."""
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))
    return bool(
        re.search(r"(?<!\d)(?:19|20)\d{2}(?:年|[-/]\d{1,2}(?:[-/]\d{1,2})?)(?!\d)", text)
        or re.fullmatch(r"(?:19|20)\d{2}(?:\.0)?", text)
        or re.search(r"(?<!\d)(?:1[0-2]|0?[1-9])(?:\s*[-~～至—–]\s*(?:1[0-2]|0?[1-9]))?月", text)
        or re.search(r"(?:第?[一二三四1-4]季度|[上下]半年|全年)", text)
        or re.search(r"(?:19|20)\d{2}[QqHh][1-4]", text)
    )


def check_readiness(task: Task, design, report: Report, inspection: dict) -> None:
    """Keep unknown dates honest, while sending source-repairable blanks to repair."""
    sections = inspection.get("sections", [])
    cells = {cell["ref"] for section in sections for cell in section["cells"]}
    position = design.date_position if design else ""
    source_date_refs = {
        ref for evidence in report.evidence for ref in evidence.date_refs if ref in cells
    }
    # Design references are global inspection hints, not per-record bindings. Even
    # a matching Sheet may contain several tables with different or unknown dates.
    bound = {ref for ref in cells if ref in position}
    title_hint = title_month_end(task.title)
    missing = [i for i, record in enumerate(report.records) if missing_date(record.get("end_date"))]
    period_refs = {
        cell["ref"]
        for section in sections
        for cell in section["cells"]
        if _has_period_hint(cell.get("value"))
        or cell.get("python_type") in {"date", "datetime"}
        or cell.get("ctype") == 3
    }
    source_period_hint = bool(
        period_refs
        or any(
            _has_period_hint(section.get("title", section.get("id", ""))) for section in sections
        )
    )
    nonempty_sections = [section for section in sections if section.get("cells")]
    title_fallback = bool(
        title_hint
        and len(nonempty_sections) == 1
        and not source_period_hint
        and not source_date_refs
        and not bound
    )
    bindings = {}
    evidence_by_record = {}
    for evidence in report.evidence:
        if evidence.data_ref in cells:
            evidence_by_record.setdefault(evidence.record_index, []).append(evidence)
    for index in missing:
        own_evidence = evidence_by_record.get(index, [])
        own_refs = {
            ref
            for evidence in own_evidence
            for ref in evidence.date_refs
            if ref in cells or (ref == "runtime:title" and title_hint)
        }
        if not own_refs and own_evidence and title_fallback:
            own_refs = {"runtime:title"}
        if own_refs:
            bindings[index] = sorted(own_refs)
    known = sorted(bindings)
    unknown = [index for index in missing if index not in bindings]
    repairable = bool(known)
    unknown_message = (
        f" Unknown-date records={unknown[:20]} remain a warning: no reliable date source was "
        "established for their own source scope; leave them unresolved and never borrow another "
        "record, Sheet or table's period."
        if unknown
        else ""
    )
    report.checks.append(
        Check(
            name="end_date_presence",
            status="fail" if missing and repairable else "warning" if missing else "pass",
            message=(
                f"Records with source-bound repairable missing end_date={known[:20]}. "
                "Repair only these records against their own source/date bindings; never hardcode "
                "a date or use publictime." + unknown_message
                if repairable
                else f"Records with missing end_date={missing[:20]}; no reliable date source was "
                "established for these records. Downstream may discard them; report unresolved, "
                "never fabricate a date." + unknown_message
                if missing
                else "Every extracted record has a nonempty statistical end_date"
            ),
            details={
                "category": "end_date_empty",
                "record_indices": known,
                "unknown_record_indices": unknown,
                "source_refs": sorted({ref for refs in bindings.values() for ref in refs}),
                "source_refs_by_record": {str(index): refs for index, refs in bindings.items()},
                "design_source_refs": sorted(bound),
                "source_period_refs": sorted(period_refs),
                "title_period_hint": title_hint
                if title_fallback or any("runtime:title" in refs for refs in bindings.values())
                else None,
                "title_fallback_allowed": title_fallback,
                "repairable": repairable,
                "affected_fields": ["end_date"],
            },
        )
    )
    title_mismatches = [
        evidence.record_index
        for evidence in report.evidence
        if title_hint
        and "runtime:title" in evidence.date_refs
        and evidence.record_index < len(report.records)
        and report.records[evidence.record_index].get("end_date") != title_hint
    ]
    if title_hint and any("runtime:title" in e.date_refs for e in report.evidence):
        report.checks.append(
            Check(
                name="runtime_evidence_date",
                status="fail" if title_mismatches else "pass",
                message=f"Runtime title-bound evidence must resolve to {title_hint}; mismatches={title_mismatches[:20]}",
                details={
                    "category": "end_date_empty",
                    "record_indices": title_mismatches,
                    "source_refs": ["runtime:title"],
                    "expected": title_hint,
                },
            )
        )
    # Mirror the workflow's trimmed-name comparison for an estimate only. Do not
    # filter production records, infer missing facts or convert their values.
    rules = {}
    for rule in task.GZKJG:
        rules.setdefault(rule.rule_indicator_name.strip(), rule)
    matched = coded = dated = 0
    for record in report.records:
        rule = rules.get(str(record.get("indicator_name", "")).strip())
        if rule is None:
            continue
        matched += 1
        code = rule.rule_indicator_code.strip()
        if not code or code.lower() in {"empty", "null", "none"}:
            continue
        coded += 1
        try:
            date.fromisoformat(record.get("end_date", ""))
        except (ValueError, TypeError):
            continue
        dated += 1
    cause = (
        "NO_NAME_MATCH"
        if not matched
        else "MISSING_RULE_CODE"
        if not coded
        else "END_DATE_EMPTY"
        if not dated
        else "READY"
    )
    report.checks.append(
        Check(
            name="downstream_readiness",
            status="pass" if dated else "warning",
            message=f"Local estimate only: name matches={matched}, coded={coded}, dated={dated}; cause={cause}. "
            "This is not a DC execution result. Unit differences are handled by 4551/7882. "
            "A missing GZK name/code is not proof of a parser defect.",
            details={
                "name_matches": matched,
                "coded_records": coded,
                "dated_records": dated,
                "cause": cause,
            },
        )
    )
