"""Per-issue evidence, focused context, behavioral regression and explicit closure."""

import ast
import hashlib
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from ..domain import AgentError, Check, Report, Task, digest
from ..infrastructure.files import write_json
from ..validation.readiness import missing_date
from .memory import POLICY, RepairMemory

FIELDS = {"indicator_name", "indicator_data", "indicator_unit", "end_date"}
FIELD_SCOPE = {
    "end_date_empty": {"end_date"},
    "name_semantics": {"indicator_name"},
    "source_value": {"indicator_data"},
    "source_unit": {"indicator_unit"},
    "unit_conversion": set(),
    "rule_coverage": set(),
    "regression": set(),
}
PRIORITY = {"empty_extraction": 0, "runtime": 1, "end_date_empty": 2}


def code_hash(candidate: dict) -> str:
    return hashlib.sha256(candidate.get("content", "").encode()).hexdigest()


class RepairSession:
    def __init__(self, root, workspace, task: Task, inspection: dict, policy_key: str):
        self.workspace, self.task, self.inspection = workspace, task, inspection
        self.policy_key = policy_key
        self.memory = RepairMemory(root)
        self.issues: dict[str, dict] = {}
        self.rounds = []
        path = workspace / "repair_issues.json"
        if path.is_file():
            previous = json.loads(path.read_text(encoding="utf-8"))
            if (
                previous.get("policy_key") == policy_key
                and previous.get("source_sha256") == inspection["sha256"]
                and previous.get("task_input_hash") == task.input_hash
            ):
                self.issues = {issue["issue_id"]: issue for issue in previous["issues"]}
                self.rounds = previous["rounds"]

    def _observe(
        self,
        category,
        message,
        report,
        indices,
        refs,
        attempt,
        candidate,
        *,
        check_name=None,
        advisory=False,
        symbols=(),
    ):
        evidence = report.evidence
        located = [e for e in evidence if not indices or e.record_index in indices]
        data_refs = sorted({e.data_ref for e in located})
        source_refs = sorted(
            set(refs)
            | {
                ref
                for e in located
                for ref in e.name_refs + [e.data_ref] + e.unit_refs + e.date_refs
            }
        )
        identifier = digest([category, check_name, data_refs, message if not check_name else ""])[
            :16
        ]
        issue = self.issues.setdefault(
            identifier,
            {
                "issue_id": identifier,
                "category": category,
                "check_name": check_name,
                "first_attempt": attempt,
                "observations": [],
                "resolutions": [],
            },
        )
        issue.update(
            message=message,
            source_refs=source_refs,
            data_refs=data_refs,
            data_ref_counts=dict(Counter(e.data_ref for e in located)),
            record_indices=indices,
            code_symbols=list(symbols),
            last_attempt=attempt,
            status="advisory" if advisory else "open",
        )
        issue["observations"].append(
            {"attempt": attempt, "content_sha256": code_hash(candidate), "status": issue["status"]}
        )
        return issue

    def validation(self, report: Report, candidate: dict, attempt: int):
        for issue in self.issues.values():
            if (
                issue["check_name"]
                and issue["status"] in {"validated", "resolved"}
                and issue.get("validated_sha256") != code_hash(candidate)
            ):
                issue["status"] = "open"
        for check in report.checks:
            if check.status != "fail" and not (
                check.name == "end_date_presence" and check.status == "warning"
            ):
                continue
            category = check.details.get("category") or (
                "source_value" if check.name == "source_evidence" else "runtime"
            )
            self._observe(
                category,
                check.message,
                report,
                check.details.get("unknown_record_indices", check.details.get("record_indices", []))
                if check.status == "warning"
                else check.details.get("record_indices", []),
                check.details.get("source_refs", []),
                attempt,
                candidate,
                check_name=check.name,
                advisory=check.status == "warning",
            )
        passed = {c.name for c in report.checks if c.status == "pass"}
        if report.passed and {"runtime_contract", "source_evidence"} <= passed:
            for issue in self.issues.values():
                if issue["status"] != "open" or not issue["check_name"]:
                    continue
                local_date_warning = issue["check_name"] == "end_date_presence" and any(
                    check.name == "end_date_presence" and check.status == "warning"
                    for check in report.checks
                )
                if issue["check_name"] not in passed and not local_date_warning:
                    continue
                required_counts = Counter(issue.get("data_ref_counts", issue["data_refs"]))
                if required_counts - Counter(e.data_ref for e in report.evidence):
                    continue
                if issue["category"] == "end_date_empty":
                    relevant = [
                        report.records[e.record_index]
                        for e in report.evidence
                        if not issue["data_refs"] or e.data_ref in issue["data_refs"]
                    ]
                    if not relevant or any(missing_date(r.get("end_date")) for r in relevant):
                        continue
                issue["status"] = "validated"
                issue["validated_sha256"] = code_hash(candidate)
                issue["verified_checks"] = sorted(passed)
        self.save()

    def review(self, review, report: Report, candidate: dict, attempt: int, *, approved: bool):
        for item in review.issues:
            self._observe(
                item.category,
                item.message,
                report,
                item.record_indices,
                item.source_refs,
                attempt,
                candidate,
                advisory=item.category in {"coverage", "unit_conversion"},
                symbols=item.code_symbols,
            )
        if not review.issues and not approved and review.rejection_kind != "coverage":
            for message in review.findings:
                self._observe("other", message, report, [], [], attempt, candidate)
        if approved and report.passed:
            for issue in self.issues.values():
                mechanically_verified = issue["status"] == "validated" and issue.get(
                    "validated_sha256"
                ) == code_hash(candidate)
                explicitly_reviewed = (
                    issue["issue_id"] in review.resolved_issue_ids
                    and not issue["check_name"]
                    and issue["status"] == "open"
                )
                if mechanically_verified or explicitly_reviewed:
                    issue["status"] = "resolved"
                    issue["resolutions"].append(
                        {
                            "attempt": attempt,
                            "content_sha256": code_hash(candidate),
                            "method": "execution_and_semantic_review"
                            if mechanically_verified
                            else "semantic_review",
                            "verified_checks": sorted(
                                c.name for c in report.checks if c.status == "pass"
                            )
                            if mechanically_verified
                            else [],
                        }
                    )
                    lesson = next(
                        (
                            item
                            for item in review.repair_lessons
                            if item.issue_id == issue["issue_id"]
                        ),
                        None,
                    )
                    if lesson:
                        issue["resolutions"][-1]["lesson"] = lesson.model_dump(exclude={"issue_id"})
        unresolved = [
            issue["issue_id"]
            for issue in self.issues.values()
            if issue["status"] in {"open", "validated"}
        ]
        if unresolved:
            report.checks.append(
                Check(
                    name="repair_issue_status",
                    status="warning",
                    message=f"Issues remain without complete individual acceptance: {unresolved}; do not treat a disappeared record as a successful fix",
                    details={"unresolved_issue_ids": unresolved},
                )
            )
        self.save()

    def _excerpt(self, refs):
        direct = set(refs)
        result = []
        for section in self.inspection.get("sections", []):
            cells = section["cells"]
            rows = {
                int(m[1])
                for ref in direct
                if (m := re.search(r"!(?:[A-Z]+)(\d+)$", ref))
                and ref.startswith(section["id"] + "!")
            }
            chosen = [c for c in cells if c["ref"] in direct]
            chosen += [
                c
                for c in cells
                if c["ref"] not in direct
                and (
                    (m := re.search(r"![A-Z]+(\d+)$", c["ref"]))
                    and (int(m[1]) <= 5 or any(abs(int(m[1]) - row) <= 2 for row in rows))
                )
            ]
            result.extend({"section": section["id"], **c} for c in chosen[:80])
        if "runtime:title" in direct:
            result.insert(0, {"ref": "runtime:title", "value": self.task.title})
        return result[:160]

    def plan(self, report: Report, candidate: dict) -> dict:
        targets = sorted(
            [deepcopy(i) for i in self.issues.values() if i["status"] in {"open", "validated"}],
            key=lambda i: (PRIORITY.get(i["category"], 3), i["first_attempt"]),
        )[:6]
        indices = {index for issue in targets for index in issue["record_indices"]}
        refs = {ref for issue in targets for ref in issue["source_refs"]}
        evidence = [e for e in report.evidence if not indices or e.record_index in indices][:12]
        return {
            "policy": POLICY,
            "baseline_content_sha256": code_hash(candidate),
            "targets": targets,
            "record_fragments": [
                {
                    "record_index": e.record_index,
                    "record": report.records[e.record_index],
                    "evidence": e.model_dump(),
                }
                for e in evidence
                if e.record_index < len(report.records)
            ],
            "source_fragments": self._excerpt(refs),
            "code_fragments": self._code_excerpt(candidate.get("content", ""), targets),
            "memory": self.memory.advice(
                self.task,
                self.policy_key,
                [i["category"] for i in targets] or ["empty_extraction", "end_date_empty"],
            ),
            "instruction": "Repair only the identified source-backed problems. Keep unrelated output fields and logic unchanged. Evidence and findings can be wrong: confirm against this source. Group shared causes; do not solve an absent GZK match by fabricating names. Close each issue using its own execution/source acceptance checks; list resolved_issue_ids in review. Unit conversion is deferred to DC.",
        }

    @staticmethod
    def _code_excerpt(content, targets):
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return [
                {"line": index + 1, "text": line}
                for index, line in enumerate(content.splitlines()[:40])
            ]
        wanted = {symbol for issue in targets for symbol in issue["code_symbols"]}
        if not wanted:
            wanted = {"main", "_dc_validate"}
            if any(i["category"] == "end_date_empty" for i in targets):
                wanted.update(
                    node.name
                    for node in tree.body
                    if isinstance(node, ast.FunctionDef)
                    and re.search(r"date|period|title|time", node.name, re.I)
                )
        lines = content.splitlines()
        numbers = {
            index
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
            for index in range(node.lineno - 1, node.end_lineno)
        }
        return [{"line": index + 1, "text": lines[index]} for index in sorted(numbers)[:140]]

    @staticmethod
    def prompt_plan(plan: dict) -> dict:
        bounded = deepcopy(plan)
        for issue in bounded["targets"]:
            for field, limit in (
                ("source_refs", 40),
                ("data_refs", 16),
                ("record_indices", 12),
                ("observations", 2),
                ("resolutions", 2),
            ):
                issue[field + "_count"] = len(issue[field])
                issue[field] = issue[field][:limit]
            counts = issue.get("data_ref_counts", {})
            issue["data_ref_counts_count"] = len(counts)
            issue["data_ref_counts"] = dict(list(counts.items())[:16])
        return bounded

    def save_baseline(self, attempt, baseline, context_key):
        if not baseline.get("candidate", {}).get("content") or not baseline.get("plan", {}).get(
            "targets"
        ):
            return
        write_json(
            self.workspace / f"repair-baseline-{attempt}.json",
            {
                "context_key": context_key,
                "source_sha256": self.inspection["sha256"],
                "task_input_hash": self.task.input_hash,
                "policy_key": self.policy_key,
                "baseline": baseline,
            },
        )

    def load_baseline(self, attempt, context_key):
        path = self.workspace / f"repair-baseline-{attempt}.json"
        if not path.is_file():
            diff = self.workspace / f"repair-diff-{attempt}.json"
            if (
                diff.is_file()
                and json.loads(diff.read_text(encoding="utf-8")).get("status") == "rejected"
            ):
                raise AgentError(
                    "VALIDATION_FAILED",
                    "Rejected repair lost its acceptance baseline; rebuild without resume",
                )
            return {}
        saved = json.loads(path.read_text(encoding="utf-8"))
        if (
            saved.get("context_key") != context_key
            or saved.get("policy_key") != self.policy_key
            or saved.get("source_sha256") != self.inspection["sha256"]
            or saved.get("task_input_hash") != self.task.input_hash
        ):
            return {}
        baseline = saved["baseline"]
        if baseline["plan"].get("baseline_content_sha256") != code_hash(baseline["candidate"]):
            raise AgentError("VALIDATION_FAILED", "Saved repair baseline checksum mismatch")
        Report.model_validate(baseline["report"])
        return baseline

    def guard(self, baseline: dict | None, report: Report, candidate: dict, attempt: int):
        if not baseline or not baseline.get("report"):
            return
        old = Report.model_validate(baseline["report"])
        if not old.records or not old.evidence or not report.records or not report.evidence:
            return
        targets = baseline.get("plan", {}).get("targets", [])
        if not targets:
            return
        allowed = defaultdict(set)
        global_fields = set()
        for issue in targets:
            fields = FIELD_SCOPE.get(issue["category"], FIELDS)
            if issue["data_refs"]:
                for ref in issue["data_refs"]:
                    allowed[ref].update(fields)
            else:
                global_fields.update(fields)

        def grouped(current):
            result = defaultdict(list)
            for entry in current.evidence:
                if entry.record_index < len(current.records):
                    result[entry.data_ref].append(current.records[entry.record_index])
            return result

        before, after = grouped(old), grouped(report)
        changed = []
        for ref in before.keys() & after.keys():
            protected = sorted(FIELDS - global_fields - allowed[ref])
            if not protected:
                continue

            def signatures(records):
                def value(field, raw):
                    if field == "indicator_data":
                        try:
                            parsed = Decimal(str(raw).replace(",", ""))
                            if parsed.is_finite():
                                return parsed
                        except InvalidOperation:
                            pass
                    return str(raw)

                return Counter(
                    tuple((field, value(field, r.get(field, ""))) for field in protected)
                    for r in records
                )

            if signatures(before[ref]) != signatures(after[ref]):
                changed.append({"data_ref": ref, "protected_fields": protected})
        report.checks.append(
            Check(
                name="repair_regression",
                status="fail" if changed else "pass",
                message=f"Protected output changed outside the repair plan: {changed[:10]}"
                if changed
                else "Unrelated output fields at retained source cells were preserved; missing source refs remain coverage findings",
                details={
                    "category": "regression",
                    "changes": changed,
                    "baseline_sha256": code_hash(baseline["candidate"]),
                },
            )
        )

        def symbols(content):
            try:
                tree = ast.parse(content)
            except SyntaxError:
                return {}
            result = {}
            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    key = "import:" + ast.dump(node, include_attributes=False)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    values = node.targets if isinstance(node, ast.Assign) else [node.target]
                    key = "global:" + ",".join(ast.unparse(value) for value in values)
                elif (
                    isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    continue
                else:
                    key = getattr(node, "name", f"statement:{len(result)}")
                result[key] = ast.dump(node, include_attributes=False)
            return result

        before_symbols, after_symbols = (
            symbols(baseline["candidate"]["content"]),
            symbols(candidate["content"]),
        )
        edits = [
            name
            for name in before_symbols.keys() | after_symbols.keys()
            if before_symbols.get(name) != after_symbols.get(name)
        ]
        declared = {symbol for issue in targets for symbol in issue["code_symbols"]}
        declared |= {"global:" + symbol for symbol in declared if not symbol.startswith("global:")}
        if declared and all(
            issue["code_symbols"] for issue in targets if issue["category"] != "regression"
        ):
            outside = sorted(
                name for name in edits if name in before_symbols and name not in declared
            )
            report.checks.append(
                Check(
                    name="repair_code_scope",
                    status="fail" if outside else "pass",
                    message=f"Existing functions changed outside the declared repair scope: {outside}"
                    if outside
                    else "Changed existing functions remain within the declared repair scope",
                    details={"category": "regression", "outside_symbols": outside},
                )
            )
        round_info = {
            "attempt": attempt,
            "baseline_sha256": code_hash(baseline["candidate"]),
            "candidate_sha256": code_hash(candidate),
            "target_issue_ids": [i["issue_id"] for i in targets],
            "changed_symbols": sorted(edits),
            "protected_output_changes": changed,
            "status": "rejected"
            if any(
                c.status == "fail" and c.name in {"repair_regression", "repair_code_scope"}
                for c in report.checks
            )
            else "passed",
        }
        self.rounds.append(round_info)
        write_json(self.workspace / f"repair-diff-{attempt}.json", round_info)
        self.save()

    def finish(self, output: dict):
        selection = output["selection"]
        promoted = []
        if selection["semantic_status"] == "accepted":
            selected_hash = code_hash(output["candidate"])
            for issue in self.issues.values():
                resolution = next(
                    (
                        r
                        for r in issue["resolutions"]
                        if r["content_sha256"] == selected_hash
                        and r["method"] == "execution_and_semantic_review"
                    ),
                    None,
                )
                if resolution and issue["category"] in {
                    "empty_extraction",
                    "end_date_empty",
                    "runtime",
                    "source_value",
                }:
                    if issue["observations"][0]["content_sha256"] == selected_hash:
                        continue
                    case_id = self.memory.remember(
                        self.task,
                        self.policy_key,
                        {
                            "category": issue["category"],
                            "issue_id": issue["issue_id"],
                            "task_id": self.workspace.name,
                            "source_sha256": self.inspection["sha256"],
                            "before_sha256": issue["observations"][0]["content_sha256"],
                            "after_sha256": selected_hash,
                            "verified_checks": resolution["verified_checks"],
                            "lesson": resolution.get("lesson"),
                        },
                    )
                    if case_id:
                        promoted.append(case_id)
        self.save(selection=selection, promoted_case_ids=promoted)

    def save(self, **extra):
        write_json(
            self.workspace / "repair_issues.json",
            {
                "policy": POLICY,
                "policy_key": self.policy_key,
                "source_sha256": self.inspection["sha256"],
                "issues": list(self.issues.values()),
                "task_input_hash": self.task.input_hash,
                "rounds": self.rounds,
                "memory_warning": self.memory.warning,
                **extra,
            },
        )
