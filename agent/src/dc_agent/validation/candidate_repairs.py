"""Small, evidence-backed repair proposals; adoption still requires real execution."""

import ast
import re
from collections import Counter
from dataclasses import dataclass

from ..domain import Candidate, Design, Report, Task, dumps
from .source_values import positive_percentage_points, title_month_end

# Only these bounded, known grammars are eligible. Do not rewrite arbitrary regexes,
# paths, replacement strings, prompts, or every backslash in generated source.
_DATE_PATTERNS = {
    r"(?<!\d)(20\d{2})年\s*(1[0-2]|[1-9])月(?!\d)",
    r"(20\d{2})年\s*(1[0-2]|[1-9])月",
    r"(20\d{2})年\s*(\d{1,2})月",
}
_POINT_PATTERNS = {
    r"(?:提高|上升|增加)\s*(\d+(?:\.\d+)?)\s*(个百分点|百分点)",
}


@dataclass(frozen=True)
class RepairProposal:
    candidate: Candidate
    edits: list[dict]


def propose_regex_escape_repair(
    candidate: Candidate, task: Task, design: Design, inspection: dict
) -> RepairProposal | None:
    if task.kind == "markdown_prompt":
        return None
    try:
        tree = ast.parse(candidate.content)
    except SyntaxError:
        return None
    aliases = {
        alias.asname or "re"
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "re"
    }
    lines = candidate.content.encode("utf-8").splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    replacements = []
    edits = []
    for call in ast.walk(tree):
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id in aliases
            and call.func.attr in {"compile", "findall", "search", "match", "fullmatch", "finditer"}
        ):
            continue
        pattern = (
            call.args[0]
            if call.args
            else next((item.value for item in call.keywords if item.arg == "pattern"), None)
        )
        if not isinstance(pattern, ast.Constant) or not isinstance(pattern.value, str):
            continue
        original = pattern.value
        fixed = original.replace("\\\\", "\\")
        if fixed == original:
            continue
        refs = []
        if (
            fixed in _DATE_PATTERNS
            and design.date_position.strip().startswith("runtime:title")
            and title_month_end(task.title)
            and not re.search(original, task.title)
            and re.search(fixed, task.title)
        ):
            refs = ["runtime:title"]
        elif fixed in _POINT_PATTERNS:
            refs = [
                cell["ref"]
                for section in inspection["sections"]
                for cell in section["cells"]
                if isinstance(cell["value"], str)
                and positive_percentage_points(cell["value"])
                and not re.fullmatch(original, cell["value"])
                and re.fullmatch(fixed, cell["value"])
            ]
        if not refs:
            continue
        start = offsets[pattern.lineno - 1] + pattern.col_offset
        end = offsets[pattern.end_lineno - 1] + pattern.end_col_offset
        replacements.append((start, end, repr(fixed).encode("utf-8")))
        edits.append(
            {"line": pattern.lineno, "original": original, "corrected": fixed, "source_refs": refs}
        )
    if not replacements:
        return None
    content = candidate.content.encode("utf-8")
    for start, end, value in sorted(replacements, reverse=True):
        content = content[:start] + value + content[end:]
    return RepairProposal(candidate.model_copy(update={"content": content.decode("utf-8")}), edits)


def preserves_source_records(before: Report, after: Report) -> bool:
    """Existing source records must survive; only dates may change in-place."""

    def signatures(report: Report) -> Counter:
        return Counter(
            (
                entry.data_ref,
                dumps(
                    {
                        key: value
                        for key, value in report.records[entry.record_index].items()
                        if key != "end_date"
                    }
                ),
            )
            for entry in report.evidence
            if entry.record_index < len(report.records)
        )

    return not (signatures(before) - signatures(after))
