"""Individual repair acceptance and recovery must survive changed code and retries."""

import json
import sqlite3

from test_application import ScriptedModel, make_service
from test_http_integration import PAYLOAD
from test_repair_learning import date_code
from test_repair_learning import inputs as inputs

from dc_agent.domain import Candidate, Check, Evidence, Report, Review, Task
from dc_agent.repair.session import RepairSession, code_hash
from dc_agent.validation import Validator


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def task_and_inspection():
    task = Task.model_validate({**PAYLOAD, "title": "2026年3月统计"})
    inspection = {
        "sha256": "same-original-source",
        "format": "xlsx",
        "sections": [
            {
                "id": "Sheet",
                "cells": [
                    {"ref": "Sheet!A2", "value": "甲"},
                    {"ref": "Sheet!B2", "value": 0},
                    {"ref": "Sheet!A3", "value": "乙"},
                    {"ref": "Sheet!B3", "value": 1},
                ],
            }
        ],
    }
    return task, inspection


def valid_report(*, source_rows=(2,), reviewed=False):
    return Report(
        scope="repair-recovery",
        checks=[
            Check(name="runtime_contract", status="pass", message="Valid real-output contract"),
            Check(name="source_evidence", status="pass", message="Values match original cells"),
            *(
                [Check(name="model_semantic_review", status="pass", message="Reviewed source")]
                if reviewed
                else []
            ),
        ],
        records=[
            {
                "indicator_name": "甲" if row == 2 else "乙",
                "indicator_data": 0 if row == 2 else 1,
                "indicator_unit": "-",
                "end_date": "",
            }
            for row in source_rows
        ],
        evidence=[
            Evidence(record_index=index, name_refs=[f"Sheet!A{row}"], data_ref=f"Sheet!B{row}")
            for index, row in enumerate(source_rows)
        ],
    )


def case_documents(session):
    with sqlite3.connect(session.memory.path) as db:
        rows = db.execute("SELECT document FROM verified_cases").fetchall()
    return [json.loads(row[0]) for row in rows]


def test_dropping_wrong_source_cell_cannot_resolve_or_promote_its_issue(tmp_path):
    task, inspection = task_and_inspection()
    session = RepairSession(tmp_path, tmp_path, task, inspection, "recovery-policy")
    broken = {"content": "def main(params): return {'broken': True}"}
    report = valid_report(source_rows=(2, 3))
    report.records[0]["indicator_data"] = 999
    source_check = next(check for check in report.checks if check.name == "source_evidence")
    source_check.status = "fail"
    source_check.details = {"category": "source_value", "record_indices": [0]}
    session.validation(report, broken, 0)
    identifier = next(iter(session.issues))
    assert session.issues[identifier]["data_ref_counts"] == {"Sheet!B2": 1}

    dropped = {"content": "def main(params): return {'remaining_source_cell': 'B3'}"}
    remaining = valid_report(source_rows=(3,), reviewed=True)
    session.validation(remaining, dropped, 1)
    session.review(
        Review(approved=True, findings=[], resolved_issue_ids=[identifier]),
        remaining,
        dropped,
        1,
        approved=True,
    )
    assert session.issues[identifier]["status"] == "open"
    assert session.issues[identifier]["resolutions"] == []
    session.finish({"selection": {"semantic_status": "accepted"}, "candidate": dropped})
    assert case_documents(session) == []
    assert read(tmp_path / "repair_issues.json")["promoted_case_ids"] == []


def test_revalidated_issue_binds_new_hash_after_intermediate_review_rejection(tmp_path):
    task, inspection = task_and_inspection()
    session = RepairSession(tmp_path, tmp_path, task, inspection, "recovery-policy")
    broken = {"content": "def main(params): return {'ExcelData': []}"}
    empty = Report(
        scope="repair-recovery",
        checks=[
            Check(
                name="runtime_contract",
                status="fail",
                message="Successful download but no extracted records",
                details={"category": "empty_extraction", "parser_code": 0},
            )
        ],
    )
    session.validation(empty, broken, 0)
    identifier = next(iter(session.issues))
    first = {"content": "def main(params): return {'revision': 1}"}
    first_report = valid_report()
    session.validation(first_report, first, 1)
    assert session.issues[identifier]["validated_sha256"] == code_hash(first)
    session.review(
        Review(
            approved=False,
            findings=["A separate business name still needs repair"],
            issues=[
                {
                    "category": "name_semantics",
                    "message": "A separate business name still needs repair",
                    "record_indices": [0],
                    "source_refs": ["Sheet!A2"],
                }
            ],
        ),
        first_report,
        first,
        1,
        approved=False,
    )
    assert session.issues[identifier]["status"] == "validated"
    assert not session.issues[identifier]["resolutions"]

    final = {"content": "def main(params): return {'revision': 2}"}
    final_report = valid_report(reviewed=True)
    session.validation(final_report, final, 2)
    assert session.issues[identifier]["validated_sha256"] == code_hash(final)
    session.review(
        Review(approved=True, findings=[], resolved_issue_ids=list(session.issues)),
        final_report,
        final,
        2,
        approved=True,
    )
    issue = session.issues[identifier]
    assert issue["status"] == "resolved"
    assert issue["resolutions"][-1]["content_sha256"] == code_hash(final)
    session.finish({"selection": {"semantic_status": "accepted"}, "candidate": final})
    cases = case_documents(session)
    assert len(cases) == 1
    assert cases[0]["category"] == "empty_extraction"
    assert cases[0]["after_sha256"] == code_hash(final)
    assert cases[0]["after_sha256"] != code_hash(first)


def test_resume_replays_rejected_date_repair_baseline_against_real_source(inputs):
    settings, task = inputs
    task = task.model_copy(update={"title": "2026年3月统计"})

    class ChangesNameWithDate(ScriptedModel):
        def complete(self, operation, *args):
            reply = super().complete(operation, *args)
            if operation == "generate_1":
                reply["content"] = date_code().replace('sheet["A2"].value', '"unrelated name"')
                reply["evidence"][0]["date_refs"] = ["runtime:title"]
            return reply

    first_model = ChangesNameWithDate()
    service = make_service(settings, first_model)
    try:
        result = service.run(task)
        assert result.status == "review_required", result
        task_id = result.task_id
        workspace = settings.resolved_runtime() / "tasks" / task_id
        baseline = workspace / "repair-baseline-1.json"
        assert baseline.is_file()
        checks = read(workspace / "validation-1.json")["checks"]
        assert any(c["name"] == "repair_regression" and c["status"] == "fail" for c in checks)
        assert "review_1" not in first_model.calls
        assert service.repo.current(task.source_key) is None
    finally:
        service.close()

    resumed_model = ScriptedModel()
    service = make_service(settings.model_copy(update={"max_repairs": 0}), resumed_model)
    try:
        resumed = service.run(task, retry=True, rebuild=True, resume_candidate=True)
        assert resumed.task_id == task_id
        assert resumed.status == "review_required", resumed
        checks = read(workspace / "validation-1.json")["checks"]
        assert any(c["name"] == "repair_regression" and c["status"] == "fail" for c in checks)
        assert not any(operation.startswith("generate") for operation in resumed_model.calls)
        assert "review_1" not in resumed_model.calls
        assert service.repo.current(task.source_key) is None
    finally:
        service.close()


def test_date_only_scope_rejects_global_factor_change_even_for_zero_sample(tmp_path):
    task, inspection = task_and_inspection()
    session = RepairSession(tmp_path, tmp_path, task, inspection, "recovery-policy")
    original = (
        "FACTOR = 1\n"
        "def extract(x): return x * FACTOR\n"
        "def _date(title): return ''\n"
        "def main(params): return {}\n"
    )
    changed = original.replace("FACTOR = 1", "FACTOR = 2").replace(
        "def _date(title): return ''", "def _date(title): return '2026-03-31'"
    )
    old = valid_report()
    fixed = old.model_copy(deep=True)
    fixed.records[0]["end_date"] = "2026-03-31"
    baseline = {
        "candidate": {"content": original},
        "report": old.model_dump(),
        "plan": {
            "targets": [
                {
                    "issue_id": "date",
                    "category": "end_date_empty",
                    "data_refs": ["Sheet!B2"],
                    "code_symbols": ["_date"],
                }
            ]
        },
    }
    session.guard(baseline, fixed, {"content": changed}, 1)
    assert next(c for c in fixed.checks if c.name == "repair_regression").status == "pass"
    scope = next(c for c in fixed.checks if c.name == "repair_code_scope")
    assert scope.status == "fail"
    assert any(name.startswith("global:") for name in scope.details["outside_symbols"])


def test_boolean_false_is_not_numeric_success_code():
    class BooleanSuccess:
        def run(self, *args, **kwargs):
            return {
                "code": False,
                "ExcelData": [
                    {
                        "indicator_name": "甲",
                        "indicator_data": 0,
                        "indicator_unit": "-",
                        "end_date": "",
                    }
                ],
            }

    task, inspection = task_and_inspection()
    candidate = Candidate(content='def main(params): return {"ExcelData": []}')
    report = Validator(BooleanSuccess()).validate(task, candidate, inspection, b"local")
    assert not report.passed
    check = next(check for check in report.checks if check.name == "runtime_contract")
    assert check.status == "fail"
    assert check.details["parser_code"] is False
