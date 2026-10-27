"""Bounded generation, validation and semantic-review graph; source preflight is upstream."""

import hashlib
import json
import re
import time
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from ..domain import AgentError, Candidate, Check, Design, Report, Task, digest
from ..generation import Generator
from ..infrastructure.files import write_json
from ..references.evidence import reference_ref_exists
from ..repair import RepairSession
from ..validation import Validator, check_design, source_cells
from ..validation.candidate_repairs import preserves_source_records, propose_regex_escape_repair
from ..validation.source_values import title_month_end
from .candidates import CandidatePool


class FlowState(TypedDict, total=False):
    inspection: dict
    task_info: dict
    references: list[dict]
    design: dict
    candidate: dict
    report: dict
    attempt: int
    repairs: int
    approved: bool
    semantic_findings: list[str]
    covered_data_refs: list[str]
    missing_data_refs: list[str]
    quality_pending: bool
    quality_repairs: int
    stop_reason: str
    selection: dict
    repair_baseline: dict


def load_saved_candidate(workspace, repo, task_id):
    """Read legacy/current candidates without changing the original source evidence."""
    candidates = [
        (int(match[1]), path)
        for path in workspace.glob("candidate-*.json")
        if (match := re.fullmatch(r"candidate-(\d+)\.json", path.name))
    ]
    if not candidates or not all(
        (workspace / name).is_file() for name in ("design.json", "inspection.json")
    ):
        raise AgentError("RESUME_UNAVAILABLE", "No saved candidate and design to resume")
    attempt, path = max(candidates)
    candidate = Candidate.model_validate_json(path.read_text(encoding="utf-8"))
    design = Design.model_validate_json((workspace / "design.json").read_text(encoding="utf-8"))
    previous = json.loads((workspace / "inspection.json").read_text(encoding="utf-8"))
    saved = (attempt, candidate, design, previous["sha256"])
    repo.event(
        task_id,
        "resume_candidate",
        {"filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
    )
    return saved


class GenerationWorkflow:
    def __init__(self, settings, model, runner, skills, repo, assets):
        self.settings, self.model = settings, model
        self.runner, self.skills, self.repo, self.assets = runner, skills, repo, assets

    def run(
        self, task: Task, task_id: str, workspace: Path, prepared: dict, saved, deadline: float
    ):
        skill = self.skills.load(task)
        generator = Generator(self.model, self.settings, workspace / "model_journal", deadline)
        validator = Validator(self.runner)
        pool = CandidatePool(
            workspace,
            task.kind,
            digest(
                {
                    "task": task.model_dump(exclude={"request_id", "source_path"}),
                    "inspection": prepared["inspection"],
                    "task_info": prepared["task_info"],
                    "references": prepared["references"],
                    "skill": [skill["sha256"], skill["profile_sha256"]],
                    "policy": "best_usable_candidate_v1",
                }
            ),
        )
        previous_pool = workspace / "retained_candidates.json"
        retained = (
            json.loads(previous_pool.read_text(encoding="utf-8"))
            if saved and previous_pool.is_file()
            else {}
        )
        progress = {"score": None, "stagnant": 0, "latest_attempt": saved[0] if saved else 0}
        repair = RepairSession(
            self.settings.resolved_runtime(),
            workspace,
            task,
            prepared["inspection"],
            digest(["evidence_repair_v1", task.kind, skill["sha256"], skill["profile_sha256"]]),
        )

        def stage(name: str) -> None:
            if time.monotonic() > deadline:
                raise AgentError("BUDGET_EXCEEDED", "Task deadline exceeded")
            self.repo.event(task_id, name)

        def context(state: FlowState) -> dict:
            return {
                "task": task.model_dump(exclude={"request_id", "source_path", "reference_paths"}),
                "asset_contract": {
                    "asset_kind": task.kind,
                    "output": "out_data with five fields including indicator_code"
                    if task.kind == "markdown_prompt"
                    else "main(params) returns code/msg/ExcelData; records use indicator_name/indicator_data/indicator_unit/end_date",
                    "gzk_usage": "Build source candidates, then exact unique GZK filtering; code/name come from the same GZK rule"
                    if task.kind == "markdown_prompt"
                    else "GZK and reference names are design/review diagnostics only. Do not filter source records by GZK, emit indicator_code, hardcode a GZK whitelist, or require GZK at parser runtime. Keep usable source-supported records even when GZK has no matching name.",
                    "source_authority": "Actual source facts override task descriptions and optional references; document conflicts and resolve them from source locations",
                    "coverage_policy": "Try to extract more valid data; omissions alone are advisory and do not block usable output",
                    "repair_policy": "Make focused source-backed fixes, preserve verified logic and valid records, and avoid broad rewrites. Usable earlier candidates are retained if later repairs fail; report unresolved issues honestly rather than claiming complete correctness.",
                    "unit_conversion_policy": "Source-vs-GZK/KB unit differences are allowed and must not trigger parser rewrites. DC nodes 4551/7882 own conversion. Keep source values and source units. Incorrect extraction of the source unit remains a source-fact defect.",
                },
                "inspection": state["inspection"],
                "source_preflight": {
                    key: value
                    for key, value in prepared.get("preflight", {}).items()
                    if key in {"policy", "source_sha256", "verdict", "reason", "allowed"}
                },
                "task_info": state["task_info"],
                "references": state["references"],
            }

        def inspect_node(state: FlowState) -> dict:
            stage("load_source_context")
            inspection = prepared["inspection"]
            covered = set()
            baseline_path = workspace / "coverage_baseline.json"
            if baseline_path.is_file():
                baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
                if baseline["source_sha256"] == inspection["sha256"]:
                    covered.update(baseline["data_refs"])
            elif saved:
                # Migrate prior same-source reports when first using candidate recovery.
                for path in workspace.glob("validation-*.json"):
                    if not re.fullmatch(r"validation-\d+\.json", path.name):
                        continue
                    previous_report = Report.model_validate_json(path.read_text(encoding="utf-8"))
                    passed = {c.name for c in previous_report.checks if c.status == "pass"}
                    if {"source_evidence", "runtime_contract"} <= passed:
                        covered.update(e.data_ref for e in previous_report.evidence)
            return {
                "inspection": inspection,
                "task_info": prepared["task_info"],
                "references": prepared["references"],
                "attempt": saved[0] if saved else 0,
                "repairs": 0,
                "candidate": saved[1].model_dump() if saved else {},
                "report": {},
                "approved": False,
                "semantic_findings": [],
                "covered_data_refs": sorted(covered),
                "missing_data_refs": [],
                "quality_pending": False,
                "quality_repairs": 0,
                "stop_reason": "",
                "repair_baseline": {},
            }

        def design_node(state: FlowState) -> dict:
            stage("build_design")
            fingerprint = digest(
                {
                    "context": context(state),
                    "skill_sha256": skill["sha256"],
                    "profile_sha256": skill["profile_sha256"],
                    "schema": Design.model_json_schema(),
                }
            )
            marker = workspace / "design_context.json"
            previous = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
            reuse = saved and previous.get("sha256") == fingerprint
            design = saved[2] if reuse else generator.design(context(state), skill)
            check_design(design, state["inspection"])
            write_json(workspace / "design.json", design.model_dump())
            write_json(marker, {"sha256": fingerprint})
            if saved:
                # Re-run earlier candidates against the current original bytes.
                # Old approvals are reusable only with the same complete context.
                reusable = (
                    {e["attempt"]: e for e in retained.get("candidates", [])}
                    if retained.get("fingerprint") == pool.fingerprint
                    else {}
                )
                raw = (workspace / "source.bin").read_bytes()
                for path in sorted(workspace.glob("candidate-*.json")):
                    match = re.fullmatch(r"candidate-(\d+)\.json", path.name)
                    if not match or int(match[1]) == saved[0]:
                        continue
                    if time.monotonic() >= deadline:
                        break
                    candidate = Candidate.model_validate_json(path.read_text(encoding="utf-8"))
                    try:
                        report = validator.validate(
                            task, candidate, state["inspection"], raw, design
                        )
                    except AgentError as exc:
                        if exc.code.lower() in {
                            "execution_error",
                            "execution_timeout",
                            "invalid_result",
                        }:
                            continue
                        raise
                    old = reusable.get(int(match[1]))
                    repair.guard(
                        repair.load_baseline(int(match[1]), pool.fingerprint),
                        report,
                        candidate.model_dump(),
                        int(match[1]),
                    )
                    if old and old["candidate"] == candidate.model_dump():
                        report.checks.extend(
                            c
                            for c in Report.model_validate(old["report"]).checks
                            if c.name
                            in {
                                "model_semantic_review",
                                "extraction_quality",
                                "reference_conflicts",
                                "review_section_coverage",
                            }
                        )
                    pool.observe(
                        {
                            **state,
                            "design": design.model_dump(),
                            "attempt": int(match[1]),
                            "candidate": candidate.model_dump(),
                            "report": report.model_dump(),
                            "approved": bool(old and old["semantic_status"] == "accepted"),
                            "quality_pending": bool(old and old["quality_pending"]),
                        },
                        reviewed=bool(old),
                        known=bool(old and old["semantic_status"] != "unverified"),
                    )
            return {"design": design.model_dump()}

        def generate_node(state: FlowState) -> dict:
            stage("generate_candidate")
            progress["latest_attempt"] = state["attempt"]
            baseline = {"candidate": state.get("candidate", {}), "report": state.get("report", {})}
            if (
                pool.best
                and state.get("report")
                and not Report.model_validate(state["report"]).passed
            ):
                baseline = {"candidate": pool.best["candidate"], "report": pool.best["report"]}
            elif (
                state.get("report")
                and not Report.model_validate(state["report"]).passed
                and state.get("repair_baseline", {}).get("candidate", {}).get("content")
            ):
                baseline = state["repair_baseline"]
            plan = repair.plan(
                Report.model_validate(state["report"] or {"scope": "not_run"}),
                baseline["candidate"],
            )
            baseline["plan"] = plan
            payload = {
                **context(state),
                "design": state["design"],
                "previous_candidate": baseline["candidate"],
                "repair_plan": repair.prompt_plan(plan),
                "previous_semantic_findings": state.get("semantic_findings", []),
                "coverage_comparison": {"missing_previous_data_refs": state["missing_data_refs"]},
                "validation_feedback": {
                    key: value
                    for key, value in state.get("report", {}).items()
                    if key not in {"records", "evidence"}
                },
                "retained_candidate": {
                    "attempt": pool.best["attempt"],
                    "candidate": pool.best["candidate"]
                    if pool.best["attempt"] != state["attempt"]
                    else None,
                    "semantic_status": pool.best["semantic_status"],
                    "instruction": "When candidate is null, this retained baseline is the previous_candidate above. Prefer repairing the usable baseline when the most recent candidate failed execution or lost correct logic. Make minimal source-backed changes; do not reintroduce earlier issues.",
                }
                if pool.best
                else None,
            }
            if task.kind != "markdown_prompt":
                payload["parser_contract"] = {
                    "entrypoint": "main(params)",
                    "download_base_url": self.settings.download_base_url,
                    "download_parameter": "fileMd5"
                    if task.kind == "python_excel"
                    else "markdownMD5",
                    "available_parameters": list(task.parser_params()),
                    "result_key": "ExcelData",
                    "evidence_protocol": {
                        "mode": "runtime",
                        "candidate": "Set evidence_mode=runtime and evidence=[] in the Candidate JSON. Do not enumerate output records or evidence in the model response.",
                        "validation_entrypoint": "Define _dc_validate(params). Share the same extraction core with main(params), and return the exact same result dictionary plus _dc_evidence, a list of record_index/name_refs/data_ref/unit_refs/date_refs entries built dynamically as records are extracted.",
                        "production_entrypoint": "Keep main(params) and its code/msg/ExcelData contract. main must not return _dc_evidence. Both entrypoints download the runtime file through the provided service and produce identical data.",
                    },
                    "record_fields": {
                        "indicator_name": "Nonempty string grounded in source business labels",
                        "indicator_data": "Finite scalar number or numeric string; not boolean, prose, composite ratio, NaN or Infinity",
                        "indicator_unit": "Source-supported unit string, otherwise -",
                        "end_date": "Source-supported ISO date YYYY-MM-DD, otherwise empty string",
                    },
                    "source_value_policy": "Compare numeric output with the source cell, allowing numeric thousands separators. Narrow supported annotation: a whole cell exactly matching 提高/上升/增加 + a nonnegative decimal + 个百分点/百分点 has the explicit positive numeric change and that exact unit; output its numeric token preserving precision and cite the original cell. This policy supersedes the draft design's instruction to retain the whole annotation as indicator_data. Do not convert percentage points to percent. Composite ratios, decreases or multi-number prose have no such normalization; document unsupported cases, never arbitrarily take a number or silently discard source information.",
                    "unit_binding_policy": "Bind units per data cell within its actual table block. An explicit supported annotation in that data cell supplies its unit. A specific growth/ratio column header with % supplies % even when the row label contains a physical unit or a table-wide declaration says 万元. For an absolute/value column, an explicit unit in the row label such as （吨标准煤） or （%） overrides a table-wide default. A generic 累计 header is not a unit declaration. Only use a table-wide unit when no more specific applicable source unit exists; never inherit from another table. Numeric strings without an annotation must retain their resolved unit, not an empty annotation unit. Cite the actual local unit/header/row ref, not a semantically identical header from another block. Include the applicable column header and all actual parent labels in name_refs; preserve source-supported parent/child hierarchy and footnote-defined statistical scope.",
                    "runtime_date_check": {
                        "source": "runtime:title when designated by design",
                        "expected_sample_month_end": title_month_end(task.title),
                        "instruction": "Calculate dates from the actual runtime title, never hardcode the sample. In Python raw regex use a single backslash for digit classes; do not emit overescaped literal backslashes.",
                    },
                    "production_record_fields": [
                        "indicator_name",
                        "indicator_data",
                        "indicator_unit",
                        "end_date",
                    ],
                    "audit_metadata": {
                        "pageTitle": task.pageTitle,
                        "task_info": state["task_info"]["fields"],
                        "target_table": state["task_info"]["fields"].get("数据目标表")
                        or "Not provided; do not invent it from the worksheet name",
                    },
                    "instruction": "Use the provided download_base_url in parser code. Do not require new parameters such as fileServiceUrl. The four fields must come from the downloaded file. The runtime evidence_protocol in this task overrides the generic sidecar-output wording in the skill: generate compact readable code and build evidence dynamically during validation, never enumerate sample output records in your response.",
                }
            candidate = generator.generate(payload, skill, state["attempt"])
            self.assets.save_candidate(task, task_id, state["attempt"], candidate.content)
            write_json(workspace / f"candidate-{state['attempt']}.json", candidate.model_dump())
            repair.save_baseline(state["attempt"], baseline, pool.fingerprint)
            return {
                "candidate": candidate.model_dump(),
                "repair_baseline": baseline,
                "approved": False,
                "quality_pending": False,
            }

        def validate_node(state: FlowState) -> dict:
            stage("validate_candidate")
            candidate = Candidate.model_validate(state["candidate"])
            design = Design.model_validate(state["design"])
            raw = (workspace / "source.bin").read_bytes()
            attempt = state["attempt"]
            baseline = state.get("repair_baseline") or repair.load_baseline(
                attempt, pool.fingerprint
            )
            try:
                report = validator.validate(
                    task,
                    candidate,
                    state["inspection"],
                    raw,
                    design,
                )
            except Exception as exc:
                if getattr(exc, "code", "").lower() in {
                    "execution_error",
                    "invalid_result",
                    "execution_timeout",
                }:
                    report = Report(
                        scope="code_runtime_and_evidence_review",
                        checks=[Check(name="parser_execution", status="fail", message=str(exc))],
                    )
                else:
                    raise
            if not report.passed and any(
                c.name == "runtime_title_date" and c.status == "fail" for c in report.checks
            ):
                proposal = propose_regex_escape_repair(candidate, task, design, state["inspection"])
                if proposal:
                    repaired = validator.validate(
                        task, proposal.candidate, state["inspection"], raw, design
                    )
                    if repaired.passed and preserves_source_records(report, repaired):
                        repair.validation(report, candidate.model_dump(), attempt)
                        if not baseline.get("candidate", {}).get("content"):
                            baseline = {
                                "candidate": candidate.model_dump(),
                                "report": report.model_dump(),
                                "plan": repair.plan(report, candidate.model_dump()),
                            }
                        write_json(workspace / f"validation-{attempt}.json", report.model_dump())
                        attempt = (
                            max(
                                [attempt]
                                + [
                                    int(match[1])
                                    for path in workspace.glob("candidate-*.json")
                                    if (match := re.fullmatch(r"candidate-(\d+)\.json", path.name))
                                ]
                            )
                            + 1
                        )
                        self.repo.event(
                            task_id,
                            "repair_regex_escaping",
                            {"revision": attempt, "edits": proposal.edits},
                        )
                        write_json(
                            workspace / f"local-repair-{attempt}.json",
                            {
                                "kind": "source_verified_regex_escaping",
                                "base_revision": state["attempt"],
                                "base_content_sha256": hashlib.sha256(
                                    candidate.content.encode()
                                ).hexdigest(),
                                "source_sha256": state["inspection"]["sha256"],
                                "edits": proposal.edits,
                                "verification": "Actual production/trace execution and all mechanical checks passed; previous source records preserved; semantic review still required",
                            },
                        )
                        candidate, report = proposal.candidate, repaired
                        repair.save_baseline(attempt, baseline, pool.fingerprint)
                        self.assets.save_candidate(task, task_id, attempt, candidate.content)
                        write_json(workspace / f"candidate-{attempt}.json", candidate.model_dump())
            report.checks[:0] = check_design(design, state["inspection"])
            repair.guard(baseline, report, candidate.model_dump(), attempt)
            repair.validation(report, candidate.model_dump(), attempt)
            covered = set(state["covered_data_refs"])
            missing = []
            passed = {c.name for c in report.checks if c.status == "pass"}
            if {"source_evidence", "runtime_contract"} <= passed:
                current = {entry.data_ref for entry in report.evidence}
                missing = sorted(covered - current)
                covered.update(current)
                write_json(
                    workspace / "coverage_baseline.json",
                    {"source_sha256": state["inspection"]["sha256"], "data_refs": sorted(covered)},
                )
                if missing:
                    report.checks.append(
                        Check(
                            name="source_coverage_comparison",
                            status="warning",
                            message=f"Previously source-validated data refs disappeared: {missing}; consider recovering them, but incomplete coverage does not block usable output",
                        )
                    )
            write_json(workspace / f"validation-{attempt}.json", report.model_dump())
            progress["latest_attempt"] = attempt
            pool.observe(
                {
                    **state,
                    "attempt": attempt,
                    "candidate": candidate.model_dump(),
                    "report": report.model_dump(),
                }
            )
            return {
                "attempt": attempt,
                "candidate": candidate.model_dump(),
                "report": report.model_dump(),
                "repair_baseline": baseline,
                "covered_data_refs": sorted(covered),
                "missing_data_refs": missing,
            }

        def review_node(state: FlowState) -> dict:
            stage("semantic_review")
            review = generator.review(
                {
                    **context(state),
                    "design": state["design"],
                    "candidate": state["candidate"],
                    "runtime_report": state["report"],
                    "repair_issues": repair.prompt_plan(
                        repair.plan(Report.model_validate(state["report"]), state["candidate"])
                    ),
                    "coverage_comparison": {
                        "missing_previous_data_refs": state["missing_data_refs"],
                        "instruction": "Coverage is advisory. Identify likely omissions in quality_findings, including disappeared prior data refs, but do not reject otherwise correct usable output for incomplete extraction, missing section review or missing exclusion explanations. If a negative verdict concerns omissions only, set rejection_kind=coverage; actual incorrect output, fabricated facts or mixed correctness/coverage issues must use rejection_kind=correctness. Never manufacture or rewrite source facts to increase coverage or GZK matching.",
                    },
                    "review_contract": {
                        "asset_kind": task.kind,
                        "gzk_policy": "Markdown prompts require exact unique GZK filtering and five fields. Python parsers retain all source-supported records and use GZK only for diagnostics, never require indicator_code or exact rule coverage in production output.",
                        "execution_evidence": "For Python, the passing runtime_report comes from actual local execution with download mocked by the actual source bytes. It takes precedence over draft comments saying generation had not yet run. This is not a claim of platform execution. Runtime evidence is in runtime_report.evidence.",
                        "duplicate_policy": "Preserve duplicate source records. Do not invent business-name fragments just to make names unique or increase GZK matching. Reject actual semantic errors with precise source refs; diagnostic matching counts alone are not correctness evidence.",
                        "numeric_policy": "Finite scalar numbers and numeric strings are both valid. Do not require converting a numeric string to float: preserving source precision is intentional. A whole-cell supported positive percentage-point annotation can supply its numeric token as a string with its exact original unit and data-cell evidence. Reject incorrect source values or units, not a permitted numeric representation.",
                        "reference_policy": "task_info is the program-selected todo row. references are optional design-time evidence, not source values or output whitelists. Verify any conflict against this actual source, record the source location and decision in reference_conflicts, and use the source facts. A wrong reference or a conflict resolved in favor of source is not a correctness failure.",
                        "issue_policy": "Provide issues with category, message, exact source_refs, affected record_indices and code_symbols when known. Categorize only source-vs-KB unit differences as unit_conversion: advisory, no code fix. For prior issues actually rechecked and fixed, return resolved_issue_ids. Optionally provide repair_lessons tied to those issue IDs: explain root cause, fix mechanism and applicability abstractly, without source-specific values, titles, dates, names or code to copy. A declaration alone cannot replace mechanical acceptance/regression. Check empty end_date against actual source periods and permitted runtime title fallback; unknown dates must remain unresolved, never fabricated.",
                    },
                },
                skill,
                state["attempt"],
            )
            cells = source_cells(state["inspection"], task)
            refs = set(review.reviewed_refs) | {
                ref for issue in review.issues for ref in issue.source_refs
            }
            unknown = sorted(
                ref
                for ref in refs
                if ref not in cells and not reference_ref_exists(ref, state["references"])
            )
            invalid_indices = [
                index
                for issue in review.issues
                for index in issue.record_indices
                if index < 0 or index >= len(state["report"].get("records", []))
            ]
            known = not unknown and not invalid_indices
            sections = [s for s in state["inspection"]["sections"] if s["cells"]]
            covered = all(any(c["ref"] in refs for c in section["cells"]) for section in sections)
            unexplained = [
                ref
                for ref in state["missing_data_refs"]
                if ref not in refs or not review.excluded_source_refs.get(ref, "").strip()
            ]
            # A structured coverage-only rejection cannot block usable output.
            # Negative legacy reviews default to correctness; never infer the
            # rejection reason by searching free-text findings.
            unit_only = (
                bool(review.issues)
                and all(i.category == "unit_conversion" for i in review.issues)
                and all(
                    finding in {i.message for i in review.issues} for finding in review.findings
                )
            )
            approved = (
                review.approved or review.rejection_kind == "coverage" or unit_only
            ) and known
            if any(i.category not in {"coverage", "unit_conversion"} for i in review.issues):
                approved = False
            report = Report.model_validate(state["report"])
            if not covered:
                report.checks.append(
                    Check(
                        name="review_section_coverage",
                        status="warning",
                        message="Some source sections were not cited in review; coverage is advisory",
                    )
                )
            if state["missing_data_refs"]:
                report.checks.append(
                    Check(
                        name="source_coverage_regression",
                        status="warning" if unexplained else "pass",
                        message=f"Unexplained missing data refs: {unexplained}; coverage is advisory"
                        if unexplained
                        else "Each removed source ref has an explicit semantic-review reason",
                    )
                )
            quality = [
                finding
                for finding in review.quality_findings
                if finding
                not in {i.message for i in review.issues if i.category == "unit_conversion"}
            ]
            if not review.approved and review.rejection_kind == "coverage":
                quality.extend(
                    finding
                    for finding in review.findings
                    if finding
                    not in {i.message for i in review.issues if i.category == "unit_conversion"}
                )
            if quality:
                report.checks.append(
                    Check(
                        name="extraction_quality",
                        status="warning",
                        message="; ".join(quality),
                    )
                )
            if review.reference_conflicts:
                report.checks.append(
                    Check(
                        name="reference_conflicts",
                        status="warning",
                        message="; ".join(review.reference_conflicts),
                    )
                )
            report.checks.append(
                Check(
                    name="model_semantic_review",
                    status="pass" if approved else "fail",
                    message=f"Unknown review refs: {unknown}; invalid record indices: {invalid_indices}"
                    if not known
                    else (
                        "Output correctness accepted; coverage-only findings are advisory"
                        if not review.approved and review.rejection_kind == "coverage" and known
                        else "; ".join(review.findings)
                    )
                    or (
                        "Model review backed by valid source/selected-reference refs"
                        if approved
                        else "Review rejected output correctness or cited invalid source refs"
                    ),
                )
            )
            repair.review(review, report, state["candidate"], state["attempt"], approved=approved)
            write_json(workspace / f"review-{state['attempt']}.json", review.model_dump())
            write_json(workspace / f"validation-{state['attempt']}.json", report.model_dump())
            update = {
                "approved": approved,
                "report": report.model_dump(),
                "semantic_findings": review.findings,
                "quality_pending": bool(quality or unexplained),
            }
            pool.observe({**state, **update}, reviewed=True, known=known)
            return update

        def after_validate(state: FlowState) -> str:
            return "review" if Report.model_validate(state["report"]).passed else "repair"

        def repair_node(state: FlowState) -> dict:
            stage("repair_or_stop")
            score = pool.score(pool.best) if pool.best else None
            if score is not None:
                if progress["score"] is None or score > progress["score"]:
                    progress["score"], progress["stagnant"] = score, 0
                else:
                    progress["stagnant"] += 1
            reason = (
                "repair_budget_exhausted"
                if state["repairs"] >= self.settings.max_repairs
                else "no_progress"
                if score is not None and progress["stagnant"] >= self.settings.repair_patience
                else "quality_budget_exhausted"
                if state["approved"]
                and state["quality_pending"]
                and state["quality_repairs"] >= self.settings.max_quality_repairs
                else ""
            )
            if reason:
                if pool.best:
                    return {"stop_reason": reason}
                failures = [
                    f"{check.name}: {check.message}"[:500]
                    for check in Report.model_validate(state["report"]).checks
                    if check.status == "fail"
                ]
                report = Report.model_validate(state["report"])
                categories = {
                    c.details.get("category") for c in report.checks if c.status == "fail"
                }
                failure_code = (
                    "END_DATE_EMPTY" if categories == {"end_date_empty"} else "VALIDATION_FAILED"
                )
                if any(
                    c.details.get("category") == "empty_extraction"
                    and c.details.get("parser_code") == 0
                    for c in report.checks
                    if c.status == "fail"
                ):
                    failure_code = "EMPTY_EXTRACTION"
                raise AgentError(
                    failure_code,
                    "Candidate did not pass: "
                    + "; ".join(failures[:3])
                    + "; inspect saved validation report",
                )
            return {
                "attempt": state["attempt"] + 1,
                "repairs": state["repairs"] + 1,
                "quality_repairs": state["quality_repairs"] + int(state["approved"]),
            }

        def finalize_node(state: FlowState) -> dict:
            self.repo.event(task_id, "select_usable_candidate")
            output = pool.select(state.get("stop_reason") or "review_accepted", state["attempt"])
            repair.finish(output)
            return output

        graph = StateGraph(FlowState)
        for name, node in [
            ("inspect", inspect_node),
            ("design", design_node),
            ("generate", generate_node),
            ("validate", validate_node),
            ("review", review_node),
            ("repair", repair_node),
            ("finalize", finalize_node),
        ]:
            graph.add_node(name, node)
        graph.add_edge(START, "inspect")
        graph.add_edge("inspect", "design")
        graph.add_edge("design", "validate" if saved else "generate")
        graph.add_edge("generate", "validate")
        graph.add_conditional_edges(
            "validate", after_validate, {"review": "review", "repair": "repair"}
        )
        graph.add_conditional_edges(
            "review",
            lambda s: "done" if s["approved"] and not s["quality_pending"] else "repair",
            {"done": "finalize", "repair": "repair"},
        )
        graph.add_conditional_edges(
            "repair",
            lambda s: "stop" if s["stop_reason"] else "generate",
            {"stop": "finalize", "generate": "generate"},
        )
        graph.add_edge("finalize", END)
        # A retry starts at input inspection and reuses successful operation journals.
        # Checkpoints are retained for diagnostics, not advertised as exactly-once execution.
        with SqliteSaver.from_conn_string(str(workspace / "checkpoints.sqlite")) as saver:
            try:
                output = graph.compile(checkpointer=saver).invoke(
                    {}, {"configurable": {"thread_id": task_id}, "recursion_limit": 80}
                )
            except AgentError as exc:
                if not pool.best or not (
                    exc.code.startswith("MODEL_") or exc.code == "BUDGET_EXCEEDED"
                ):
                    raise
                write_json(
                    workspace / "repair_interruption.json",
                    {
                        "code": exc.code,
                        "message": str(exc),
                        "stage": self.repo.last_stage(task_id),
                        "attempt": progress["latest_attempt"],
                    },
                )
                self.repo.event(task_id, "select_usable_candidate", {"reason": exc.code})
                output = pool.select(exc.code, progress["latest_attempt"])
                repair.finish(output)
        return output, skill
