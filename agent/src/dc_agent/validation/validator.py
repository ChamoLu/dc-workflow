"""Mechanical checks backed by original source positions; semantic review is separate."""

import ast
import re
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation

from ..domain import Candidate, Check, Design, Evidence, Report, Task, normalize_name
from ..ports import ExecutionRunner
from .readiness import check_readiness, missing_date
from .source_values import positive_percentage_points, title_month_end

HEADINGS = [
    "角色定义",
    "工作目标",
    "输入说明",
    "总体解析流程",
    "表格结构识别规则",
    "数据列名称标准化规则",
    "候选指标生成规则",
    "分类节点与有效指标行识别规则",
    "父节点继承规则",
    "指标名称规范化规则",
    "GZK匹配规则",
    "indicator_code输出强制规则",
    "指标数据提取规则",
    "单位识别规则",
    "日期识别与转换规则",
    "输出格式",
    "输出限制",
]
ALLOWED_IMPORTS = {
    "requests",
    "urllib",
    "openpyxl",
    "xlrd",
    "csv",
    "io",
    "re",
    "json",
    "calendar",
    "datetime",
    "decimal",
    "unicodedata",
    "math",
    "gzip",
    "zipfile",
    "collections",
    "typing",
    "codecs",
    "html",
    "statistics",
}


def number(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value).replace(",", "").strip())
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def source_cells(inspection: dict, task: Task) -> dict:
    result = {
        cell["ref"]: cell["value"]
        for section in inspection["sections"]
        for cell in section["cells"]
    }
    if task.title:
        result["runtime:title"] = task.title
    return result


def check_design(design: Design, inspection: dict) -> list[Check]:
    expected = {section["id"] for section in inspection["sections"]}
    declared = set(design.covered_sections)
    if declared - expected:
        raise ValueError("Extraction design references unknown source sections")
    missing = sorted(expected - declared)
    return [
        Check(
            name="design_section_coverage",
            status="warning" if missing else "pass",
            message=f"Design does not list sections {missing}; coverage is advisory"
            if missing
            else "Design lists all inspected sections",
        )
    ]


class Validator:
    def __init__(self, runner: ExecutionRunner):
        self.runner = runner

    def validate(
        self,
        task: Task,
        candidate: Candidate,
        inspection: dict,
        raw: bytes,
        design: Design | None = None,
    ) -> Report:
        scope = (
            "prompt_static_and_semantic_review"
            if task.kind == "markdown_prompt"
            else "code_runtime_and_evidence_review"
        )
        report = Report(scope=scope)
        if task.kind == "markdown_prompt":
            self._prompt(candidate.content, report)
            return report
        try:
            self._static(candidate.content)
            if candidate.evidence_mode == "runtime":
                helpers = [
                    node
                    for node in ast.parse(candidate.content).body
                    if isinstance(node, ast.FunctionDef) and node.name == "_dc_validate"
                ]
                if len(helpers) != 1 or len(helpers[0].args.args) != 1:
                    raise ValueError("Runtime evidence requires exactly one _dc_validate(params)")
            report.checks.append(
                Check(
                    name="python_static",
                    status="pass",
                    message="Compiled main(params), imports and interface checked; not a sandbox",
                )
            )
        except (ValueError, SyntaxError) as exc:
            report.checks.append(Check(name="python_static", status="fail", message=str(exc)))
            return report
        result = self.runner.run(candidate.content, task.parser_params(), raw, task.source_id)
        records = result.get("ExcelData")
        if (
            isinstance(result.get("code"), bool)
            or result.get("code") != 0
            or not isinstance(records, list)
            or not records
        ):
            report.checks.append(
                Check(
                    name="runtime_contract",
                    status="fail",
                    message=(
                        "Expected code=0 and nonempty ExcelData; do not fabricate empty source data. "
                        f"Parser returned code={result.get('code')!r}, "
                        f"msg={str(result.get('msg', ''))[:1500]!r}"
                    ),
                    details={
                        "category": "empty_extraction"
                        if isinstance(records, list) and not records
                        else "runtime",
                        "parser_code": result.get("code"),
                        "record_count": len(records) if isinstance(records, list) else None,
                    },
                )
            )
            return report
        report.records = records
        if candidate.evidence_mode == "runtime":
            traced = self.runner.run(
                candidate.content,
                task.parser_params(),
                raw,
                task.source_id,
                entrypoint="_dc_validate",
            )
            values = traced.pop("_dc_evidence", None)
            if traced != result or not isinstance(values, list):
                report.checks.append(
                    Check(
                        name="runtime_evidence",
                        status="fail",
                        message="Validation entrypoint must return the same production result plus _dc_evidence",
                    )
                )
                return report
            try:
                evidence = [Evidence.model_validate(value) for value in values]
            except ValueError:
                report.checks.append(
                    Check(
                        name="runtime_evidence",
                        status="fail",
                        message="Runtime evidence entries do not satisfy the evidence contract",
                    )
                )
                return report
            candidate = candidate.model_copy(update={"evidence": evidence})
            report.checks.append(
                Check(
                    name="runtime_evidence",
                    status="pass",
                    message="Independent production and trace executions agree; source refs still require validation",
                )
            )
        report.evidence = candidate.evidence
        errors = []
        invalid_dates = []
        data_refs = {entry.record_index: entry.data_ref for entry in candidate.evidence}
        for index, item in enumerate(records):
            if not isinstance(item, dict) or not all(
                k in item
                for k in ("indicator_name", "indicator_data", "indicator_unit", "end_date")
            ):
                errors.append(f"record {index}: missing four-field contract")
                continue
            if not isinstance(item["indicator_name"], str) or not item["indicator_name"].strip():
                errors.append(f"record {index}: empty name")
            if number(item["indicator_data"]) is None:
                explicit = positive_percentage_points(item["indicator_data"])
                hint = (
                    f"; explicit source change must be numeric {explicit[0]!r} with unit {explicit[1]!r}"
                    if explicit
                    else ""
                )
                errors.append(
                    f"record {index}: invalid numeric data {str(item['indicator_data'])[:160]!r}; "
                    f"source_ref={data_refs.get(index, 'not provided')}{hint}"
                )
            if not isinstance(item["indicator_unit"], str) or not item["indicator_unit"].strip():
                errors.append(f"record {index}: missing unit (use - if unsupported by source)")
            if not isinstance(item["end_date"], str):
                errors.append(
                    f"record {index}: invalid end_date type; use an ISO string or empty string"
                )
                invalid_dates.append(index)
            elif item["end_date"]:
                try:
                    date.fromisoformat(item["end_date"])
                except (ValueError, TypeError):
                    errors.append(f"record {index}: invalid end_date")
                    invalid_dates.append(index)
        report.checks.append(
            Check(
                name="runtime_contract",
                status="fail" if errors else "pass",
                message="; ".join(errors)
                or f"{len(records)} valid records; strict JSON checked by runner",
                details={
                    "category": "end_date_empty"
                    if invalid_dates
                    and len(errors) == len(invalid_dates)
                    and all(missing_date(records[index].get("end_date")) for index in invalid_dates)
                    else "runtime",
                    "record_indices": invalid_dates,
                },
            )
        )
        if design and design.date_position.strip().startswith("runtime:title"):
            expected = title_month_end(task.title)
            if expected:
                mismatches = [
                    index
                    for index, record in enumerate(records)
                    if isinstance(record, dict)
                    and record.get("end_date") != expected
                    and not any(
                        e.record_index == index
                        and e.date_refs
                        and "runtime:title" not in e.date_refs
                        for e in report.evidence
                    )
                ]
                observed = [
                    {"record_index": index, "actual_end_date": records[index].get("end_date")}
                    for index in mismatches[:3]
                ]
                report.checks.append(
                    Check(
                        name="runtime_title_date",
                        status="fail" if mismatches else "pass",
                        message=f"Design binds dates to runtime:title; unique source month ends {expected}; mismatched records={mismatches[:10]}; observed={observed}. Read runtime title and calculate month end; empty dates may indicate overescaped regex. Do not hardcode the sample date.",
                        details={
                            "category": "end_date_empty",
                            "record_indices": mismatches,
                            "source_refs": ["runtime:title"],
                            "expected": expected,
                            "affected_fields": ["end_date"],
                        },
                    )
                )
        if not errors:
            self._evidence(task, candidate, inspection, records, report)
            check_readiness(task, design, report, inspection)
            rules = Counter(normalize_name(rule.rule_indicator_name) for rule in task.GZKJG)
            names = Counter(normalize_name(item["indicator_name"]) for item in records)
            report.matching = {
                "source_records": len(records),
                "matched_records": sum(count for name, count in names.items() if rules[name] == 1),
                "uncovered_names": [name for name in names if not rules[name]],
                "ambiguous_rule_names": [name for name, count in rules.items() if count > 1],
                "rules_absent_from_source": [name for name in rules if not names[name]],
            }
            status = "pass" if report.matching["matched_records"] else "warning"
            report.checks.append(
                Check(
                    name="gzk_diagnostic",
                    status=status,
                    message="Exact diagnostic only; valid uncovered source records remain in ExcelData; zero matches do not establish usable downstream output",
                )
            )
        return report

    def _static(self, content: str) -> None:
        tree = ast.parse(content)
        compile(tree, "candidate.py", "exec")
        mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
        if len(mains) != 1 or len(mains[0].args.args) != 1:
            raise ValueError("Exactly one synchronous main(params) required")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                roots = (
                    [n.name.split(".")[0] for n in node.names]
                    if isinstance(node, ast.Import)
                    else [(node.module or "").split(".")[0]]
                )
                if any(root not in ALLOWED_IMPORTS for root in roots) or getattr(node, "level", 0):
                    raise ValueError("Import outside the parser dependency allowlist")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id
                in {"input", "open", "exec", "eval", "__import__", "compile", "breakpoint"}
            ):
                raise ValueError(f"Forbidden direct call: {node.func.id}")
        if "ExcelData" not in content:
            raise ValueError("Parser return key must be ExcelData")

    def _evidence(
        self, task: Task, candidate: Candidate, inspection: dict, records: list, report: Report
    ) -> None:
        cells, errors = source_cells(inspection, task), []
        categories, affected = set(), set()
        indices = [e.record_index for e in candidate.evidence]
        if sorted(indices) != list(range(len(records))):
            errors.append("Every record requires exactly one evidence entry")
            categories.add("runtime")
        for evidence in candidate.evidence:
            if evidence.record_index >= len(records):
                errors.append("Evidence index outside output")
                categories.add("runtime")
                continue
            refs = (
                evidence.name_refs + [evidence.data_ref] + evidence.unit_refs + evidence.date_refs
            )
            if any(ref not in cells for ref in refs):
                errors.append(f"record {evidence.record_index}: unknown source ref")
                categories.add("runtime")
                affected.add(evidence.record_index)
                continue
            record = records[evidence.record_index]
            # Spreadsheet cells compare numerically; Markdown lines need review for value attribution.
            original = cells[evidence.data_ref]
            source_number = number(original)
            explicit = positive_percentage_points(original)
            if explicit and record["indicator_unit"] == explicit[1]:
                source_number = number(explicit[0])
            if inspection["format"] != "markdown" and source_number != number(
                record["indicator_data"]
            ):
                errors.append(
                    f"record {evidence.record_index}: value differs from actual source cell"
                )
                categories.add("source_value")
                affected.add(evidence.record_index)
            if not evidence.date_refs and record["end_date"]:
                errors.append(f"record {evidence.record_index}: date has no source")
                categories.add("end_date_empty")
                affected.add(evidence.record_index)
            if not evidence.unit_refs and record["indicator_unit"] != "-":
                errors.append(f"record {evidence.record_index}: unit has no source")
                categories.add("source_unit")
                affected.add(evidence.record_index)
        report.checks.append(
            Check(
                name="source_evidence",
                status="fail" if errors else "pass",
                message="; ".join(errors[:10])
                or "All output records have real source references; semantic attribution requires review",
                details={
                    "category": next(iter(categories)) if len(categories) == 1 else "runtime",
                    "record_indices": sorted(affected),
                },
            )
        )

    def _prompt(self, content: str, report: Report) -> None:
        headings = re.findall(r"^# (.+?)\s*$", content, re.MULTILINE)
        missing = [h for h in HEADINGS if headings.count(h) != 1]
        ordered = not missing and [h for h in headings if h in HEADINGS] == HEADINGS
        required = [
            "GZK.GZKJG",
            "rule_indicator_name",
            "rule_indicator_code",
            "indicator_name",
            "indicator_code",
            "indicator_data",
            "indicator_unit",
            "end_date",
            "out_data",
        ]
        valid = (
            ordered
            and all(token in content for token in required)
            and content.lstrip().startswith("<!--")
        )
        report.checks.append(
            Check(
                name="prompt_structure",
                status="pass" if valid else "fail",
                message="Audit header, 17 ordered headings and five-field interface"
                if valid
                else "Missing audit header, required headings/order or output fields",
            )
        )
        report.checks.append(
            Check(
                name="target_model_execution",
                status="not_run",
                message="Prompt is executed later by the workflow platform",
            )
        )
