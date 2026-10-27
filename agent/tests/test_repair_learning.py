"""Offline models supply replies; actual child processes parse original workbook bytes."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_application import CODE, ScriptedModel, make_service
from test_application import inputs as inputs
from test_http_integration import AUTH, PAYLOAD
from test_workflow_nodes import inputs as node_inputs
from test_workflow_nodes import load_node

from dc_agent.config import Settings
from dc_agent.domain import Check, Report, Task
from dc_agent.entrypoints.http import create_app
from dc_agent.repair.memory import RepairMemory
from dc_agent.repair.session import RepairSession


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "rules", [[], "[]", None, [{"rule_indicator_name": "empty"}], [{"rule_indicator_name": " "}]]
)
def test_empty_rules_have_exact_error_and_never_enter_queue(tmp_path, rules):
    settings = Settings(
        _env_file=None,
        runtime_dir=tmp_path / "runtime",
        api_token="integration-test-token-at-least-24-chars",
    )
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        response = client.post("/v3/excel/jobs", json={**PAYLOAD, "GZKJG": rules}, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["error_code"] == "without_GZKJG"
    assert response.json()["issues"][0]["field"] == "GZKJG"
    with app.state.store._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert not (settings.resolved_runtime() / "repair_memory").exists()


def test_domain_and_node_reject_all_blank_rules_before_network(monkeypatch):
    payload = {**PAYLOAD, "GZKJG": [{"rule_indicator_name": "null"}]}
    with pytest.raises(ValidationError) as exc:
        Task.model_validate(payload)
    assert exc.value.errors()[0]["type"] == "without_GZKJG"
    node = load_node("外部框架接入.py", "empty_rules_node")
    monkeypatch.setattr(node.requests, "Session", lambda: pytest.fail("No network for empty rules"))
    record = node.main(node_inputs(GZKJG=[]))["agent"][0]
    assert record["status"] == "invalid_input"
    assert record["error_code"] == "without_GZKJG"
    assert set(record) == set(node.AGENT_FIELDS)
    assert all(record[field] == "" for field in node._CODE_FIELDS)


def test_empty_extraction_repair_is_verified_remembered_and_retrieved(inputs):
    settings, task = inputs

    class EmptyFirst(ScriptedModel):
        def complete(self, operation, system, context, schema, timeout):
            reply = super().complete(operation, system, context, schema, timeout)
            if operation == "generate_0":
                reply["content"] = (
                    'def main(params):\n return {"code":0,"msg":"no rows","ExcelData":[]}'
                )
            if operation == "generate_1":
                plan = context["repair_plan"]
                assert plan["targets"][0]["category"] == "empty_extraction"
                assert any(cell["value"] == 0 for cell in plan["source_fragments"])
                assert (
                    plan["memory"]["strategies"][0]["strategy_id"]
                    == "inspect_original_then_fix_selection_v1"
                )
                assert plan["code_fragments"]
            if operation == "review_1":
                issue = next(
                    i
                    for i in context["repair_issues"]["targets"]
                    if i["category"] == "empty_extraction"
                )
                reply["resolved_issue_ids"] = [issue["issue_id"]]
                reply["repair_lessons"] = [
                    {
                        "issue_id": issue["issue_id"],
                        "root_cause": "Extraction branch returned before reading actual rows",
                        "fix_summary": "Read original worksheet rows before constructing the result",
                        "applicability": "A successful download with data but an empty parser result",
                    }
                ]
            return reply

    service = make_service(settings, EmptyFirst())
    try:
        result = service.run(task)
        assert result.status == "ready", result
        workspace = settings.resolved_runtime() / "tasks" / result.task_id
        issues = read(workspace / "repair_issues.json")
        issue = next(i for i in issues["issues"] if i["category"] == "empty_extraction")
        assert issue["status"] == "resolved"
        assert issue["resolutions"][0]["method"] == "execution_and_semantic_review"
        assert len(issues["promoted_case_ids"]) == 1
    finally:
        service.close()

    class ReuseExperience(ScriptedModel):
        def complete(self, operation, system, context, schema, timeout):
            if operation == "generate_0":
                cases = context["repair_plan"]["memory"]["verified_cases"]
                assert cases and cases[0]["category"] == "empty_extraction"
                assert cases[0]["has_auditable_lesson"] is True
                assert "lesson" not in cases[0]
                assert "source_fragments" not in cases[0] and "content" not in cases[0]
            return super().complete(operation, system, context, schema, timeout)

    service = make_service(settings, ReuseExperience())
    try:
        assert (
            service.run(
                task.model_copy(update={"request_id": "new-generation"}), rebuild=True
            ).status
            == "ready"
        )
    finally:
        service.close()


def date_code():
    return (
        CODE.replace('"end_date": ""', '"end_date": _date(params["title"])')
        + r"""
import calendar
import re
def _date(title):
    match = re.search(r"(20\d{2})年(\d{1,2})月", title)
    if not match: return ""
    year, month = map(int, match.groups())
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
"""
    )


@pytest.mark.parametrize("bad_name", [False, True])
def test_date_repair_runs_and_rejects_changes_to_protected_names(inputs, bad_name):
    settings, task = inputs
    task = task.model_copy(update={"title": "2026年3月经济指标"})

    class FixDate(ScriptedModel):
        def complete(self, operation, system, context, schema, timeout):
            reply = super().complete(operation, system, context, schema, timeout)
            if operation.startswith("generate") and operation != "generate_0":
                assert any(
                    i["category"] == "end_date_empty" for i in context["repair_plan"]["targets"]
                )
                assert (
                    context["repair_plan"]["record_fragments"][0]["evidence"]["data_ref"]
                    == "Sheet!B2"
                )
                reply["content"] = date_code()
                if bad_name:
                    reply["content"] = reply["content"].replace(
                        'sheet["A2"].value', '"wrong unrelated name"'
                    )
                reply["evidence"][0]["date_refs"] = ["runtime:title"]
            return reply

    model = FixDate()
    service = make_service(settings, model)
    try:
        result = service.run(task)
        workspace = settings.resolved_runtime() / "tasks" / result.task_id
        report = read(workspace / "validation-1.json")
        if bad_name:
            assert result.status == "review_required", result
            assert any(
                c["name"] == "repair_regression" and c["status"] == "fail" for c in report["checks"]
            )
            assert "review_1" not in model.calls
            with sqlite3.connect(
                settings.resolved_runtime() / "repair_memory" / "verified_cases.sqlite"
            ) as db:
                assert db.execute("SELECT COUNT(*) FROM verified_cases").fetchone()[0] == 0
        else:
            assert result.status == "ready", result
            assert report["records"][0]["end_date"] == "2026-03-31"
            assert report["records"][0]["indicator_name"] == "指标甲"
            assert any(
                c["name"] == "repair_regression" and c["status"] == "pass" for c in report["checks"]
            )
            assert read(workspace / "repair_issues.json")["promoted_case_ids"]
    finally:
        service.close()


def test_unknown_dates_are_reported_without_fabrication(inputs):
    settings, task = inputs
    service = make_service(settings, ScriptedModel())
    try:
        result = service.run(task)
        assert result.status == "ready", result
        report = read(settings.resolved_runtime() / result.artifact["validation_path"])
        assert report["records"][0]["end_date"] == ""
        check = next(c for c in report["checks"] if c["name"] == "end_date_presence")
        assert check["status"] == "warning" and check["details"]["repairable"] is False
        assert (
            next(c for c in report["checks"] if c["name"] == "downstream_readiness")["details"][
                "dated_records"
            ]
            == 0
        )
    finally:
        service.close()


def test_unit_conversion_only_review_does_not_trigger_repair(inputs):
    settings, task = inputs

    class UnitOnly(ScriptedModel):
        def complete(self, operation, *args):
            reply = super().complete(operation, *args)
            if operation.startswith("review"):
                reply.update(
                    approved=False,
                    findings=["source unit differs from KB"],
                    issues=[
                        {
                            "category": "unit_conversion",
                            "message": "source unit differs from KB",
                            "record_indices": [0],
                            "source_refs": ["Sheet!B2"],
                        }
                    ],
                )
            return reply

    model = UnitOnly()
    service = make_service(settings, model)
    try:
        result = service.run(task)
        assert result.status == "ready", result
        assert model.calls == ["design", "generate_0", "review_0"]
        assert result.artifact["selection"]["semantic_status"] == "accepted"
        workspace = settings.resolved_runtime() / "tasks" / result.task_id
        issue = next(
            i
            for i in read(workspace / "repair_issues.json")["issues"]
            if i["category"] == "unit_conversion"
        )
        assert issue["status"] == "advisory"
    finally:
        service.close()


def test_failed_empty_extraction_has_specific_error_without_success_memory(inputs):
    settings, task = inputs

    class AlwaysEmpty(ScriptedModel):
        def complete(self, operation, *args):
            reply = super().complete(operation, *args)
            if operation.startswith("generate"):
                reply["content"] = 'def main(params):\n return {"code":0,"ExcelData":[]}'
            return reply

    service = make_service(settings, AlwaysEmpty())
    try:
        result = service.run(task)
        assert result.status == "review_required"
        assert result.error["code"] == "EMPTY_EXTRACTION"
        with sqlite3.connect(
            settings.resolved_runtime() / "repair_memory" / "verified_cases.sqlite"
        ) as db:
            assert db.execute("SELECT COUNT(*) FROM verified_cases").fetchone()[0] == 0
    finally:
        service.close()


def test_memory_is_scoped_by_source_type_policy_and_has_no_old_values(tmp_path):
    task = Task.model_validate(PAYLOAD)
    memory = RepairMemory(tmp_path)
    memory.remember(
        task,
        "policy-a",
        {
            "category": "end_date_empty",
            "verified_checks": ["runtime_contract", "source_evidence", "model_semantic_review"],
            "source_sha256": "original",
            "before_sha256": "broken",
            "after_sha256": "fixed",
        },
    )
    assert memory.advice(task, "policy-a", ["end_date_empty"])["verified_cases"]
    assert not memory.advice(task, "policy-b", ["end_date_empty"])["verified_cases"]
    assert not memory.advice(
        task.model_copy(update={"pageTitle": "another"}), "policy-a", ["end_date_empty"]
    )["verified_cases"]
    assert not memory.advice(
        task.model_copy(update={"fileType": "xls"}), "policy-a", ["end_date_empty"]
    )["verified_cases"]


def test_issue_claim_cannot_override_failed_acceptance(tmp_path):
    from dc_agent.domain import Review

    task = Task.model_validate(PAYLOAD)
    session = RepairSession(
        tmp_path, tmp_path, task, {"sha256": "source", "sections": []}, "policy"
    )
    candidate = {"content": "def main(params): return {}"}
    report = Report(
        scope="test",
        checks=[
            Check(
                name="runtime_contract",
                status="fail",
                message="empty",
                details={"category": "empty_extraction"},
            )
        ],
    )
    session.validation(report, candidate, 0)
    issue_id = next(iter(session.issues))
    session.review(
        Review(approved=True, findings=[], resolved_issue_ids=[issue_id]),
        report,
        candidate,
        1,
        approved=True,
    )
    assert session.issues[issue_id]["status"] == "open"


def test_no_gzk_name_match_is_diagnostic_and_does_not_rewrite_source(inputs):
    settings, task = inputs
    task = task.model_copy(
        update={
            "GZKJG": [task.GZKJG[0].model_copy(update={"rule_indicator_name": "not in source"})]
        }
    )
    model = ScriptedModel()
    service = make_service(settings, model)
    try:
        result = service.run(task)
        assert result.status == "ready", result
        assert model.calls == ["design", "generate_0", "review_0"]
        report = read(settings.resolved_runtime() / result.artifact["validation_path"])
        assert report["records"][0]["indicator_name"] == "指标甲"
        assert report["matching"]["matched_records"] == 0
        assert (
            next(c for c in report["checks"] if c["name"] == "downstream_readiness")["details"][
                "cause"
            ]
            == "NO_NAME_MATCH"
        )
    finally:
        service.close()


def test_unverified_cases_cannot_be_written_and_corrupt_memory_is_optional(tmp_path):
    memory = RepairMemory(tmp_path)
    task = Task.model_validate(PAYLOAD)
    with pytest.raises(ValueError):
        memory.remember(
            task, "policy", {"category": "end_date_empty", "verified_checks": ["source_evidence"]}
        )
    memory.path.write_bytes(b"not a database")
    advice = RepairMemory(tmp_path).advice(task, "policy", ["end_date_empty"])
    assert advice["warning"] and not advice["verified_cases"]
    assert advice["strategies"][0]["category"] == "end_date_empty"


def test_date_guard_preserves_other_rows_and_compares_numeric_meaning(tmp_path):
    from dc_agent.domain import Evidence

    task = Task.model_validate(PAYLOAD)
    evidence = [
        Evidence(record_index=i, data_ref=f"Sheet!B{i + 2}", name_refs=[f"Sheet!A{i + 2}"])
        for i in range(2)
    ]
    old = Report(
        scope="test",
        records=[
            {"indicator_name": "甲", "indicator_data": "0", "indicator_unit": "-", "end_date": ""},
            {
                "indicator_name": "乙",
                "indicator_data": "1",
                "indicator_unit": "-",
                "end_date": "2026-03-31",
            },
        ],
        evidence=evidence,
    )
    new = old.model_copy(deep=True)
    new.records[0].update(indicator_data=0, end_date="2026-03-31")
    session = RepairSession(
        tmp_path, tmp_path, task, {"sha256": "source", "sections": []}, "policy"
    )
    baseline = {
        "candidate": {"content": "def main(params): return {}"},
        "report": old.model_dump(),
        "plan": {
            "targets": [
                {
                    "issue_id": "date",
                    "category": "end_date_empty",
                    "data_refs": ["Sheet!B2"],
                    "code_symbols": [],
                }
            ]
        },
    }
    candidate = {"content": "def main(params): return {'fixed': True}"}
    session.guard(baseline, new, candidate, 1)
    assert new.checks[-1].status == "pass"
    broken = new.model_copy(deep=True)
    broken.records[1]["indicator_name"] = "unrelated change"
    session.guard(baseline, broken, candidate, 2)
    assert broken.checks[-1].status == "fail"


def test_declared_function_scope_rejects_unrelated_function_change(tmp_path):
    from dc_agent.domain import Evidence

    task = Task.model_validate(PAYLOAD)
    report = Report(
        scope="test",
        records=[
            {"indicator_name": "甲", "indicator_data": "1", "indicator_unit": "-", "end_date": ""}
        ],
        evidence=[Evidence(record_index=0, data_ref="Sheet!B2", name_refs=["Sheet!A2"])],
    )
    original = "def main(params): return {}\ndef unrelated(): return 1"
    baseline = {
        "candidate": {"content": original},
        "report": report.model_dump(),
        "plan": {
            "targets": [
                {
                    "issue_id": "date",
                    "category": "end_date_empty",
                    "data_refs": ["Sheet!B2"],
                    "code_symbols": ["main"],
                }
            ]
        },
    }
    session = RepairSession(
        tmp_path, tmp_path, task, {"sha256": "source", "sections": []}, "policy"
    )
    session.guard(baseline, report, {"content": original.replace("return 1", "return 2")}, 1)
    assert next(c for c in report.checks if c.name == "repair_code_scope").status == "fail"
