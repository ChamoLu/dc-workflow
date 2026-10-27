import json
from pathlib import Path

import openpyxl
import pytest
from test_application import CODE, ScriptedModel, make_service
from test_application import inputs as inputs

from dc_agent.domain import AgentError


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.mark.parametrize("verdict", ["coverage", "correctness"])
@pytest.mark.parametrize("breakage", ["execution", "source_value"])
def test_keeps_first_usable_candidate_when_later_repairs_fail(inputs, verdict, breakage):
    settings, task = inputs
    settings = settings.model_copy(update={"max_repairs": 4})
    full_reason = "真实源层级需核验：" + "逐条定位不能丢失；" * 200

    class Model(ScriptedModel):
        def complete(self, operation, *args):
            reply = super().complete(operation, *args)
            if operation.startswith("review"):
                reply.update(approved=False, rejection_kind=verdict, findings=[full_reason])
            if operation.startswith("generate") and operation != "generate_0":
                assert args[1]["retained_candidate"]["attempt"] == 0
                reply["content"] = (
                    CODE.replace('"ExcelData"', '"OUTDATA"')
                    if breakage == "execution"
                    else CODE.replace('str(sheet["B2"].value)', '"999"')
                )
            return reply

    model = Model()
    service = make_service(settings, model)
    try:
        result = service.run(task)
        assert result.status == "ready", result
        selection = result.artifact["selection"]
        assert selection["selected_attempt"] == 0
        assert selection["latest_attempt"] == 2
        assert selection["stop_reason"] == "no_progress"
        assert selection["quality_status"] == "usable_with_warnings"
        assert (
            Path(service.assets.content_path(result.artifact)).read_text(encoding="utf-8") == CODE
        )
        document = read(result.diagnostics["quality_report"])
        assert document["attempts"][0]["review"]["findings"] == [full_reason]
        assert full_reason in Path(result.diagnostics["quality_report_markdown"]).read_text(
            encoding="utf-8"
        )
        ledger = Path(result.diagnostics["issue_log"])
        assert full_reason in ledger.read_text(encoding="utf-8")
        # Original rejection remains available, while publication exposes the fallback.
        original = read(document["attempts"][0]["validation_path"])
        published = read(settings.resolved_runtime() / result.artifact["validation_path"])
        if verdict == "correctness":
            assert any(c["status"] == "fail" for c in original["checks"])
            assert selection["semantic_status"] == "issues_found"
            assert any(c["name"] == "usable_candidate_fallback" for c in published["checks"])
        assert not any(c["status"] == "fail" for c in published["checks"])
        cached = service.run(task.model_copy(update={"request_id": "cached"}))
        assert cached.cache_hit and cached.diagnostics == result.diagnostics
        assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1
    finally:
        service.close()


@pytest.mark.parametrize("operation", ["generate_1", "review_0"])
@pytest.mark.parametrize(
    "code", ["MODEL_CONNECTION_FAILED", "MODEL_SCHEMA_INVALID", "BUDGET_EXCEEDED"]
)
def test_model_or_budget_interruption_keeps_valid_data(inputs, operation, code):
    settings, task = inputs

    class Interrupted(ScriptedModel):
        def complete(self, name, *args):
            if name == operation:
                raise AgentError(code, "完整中断原因" * 200)
            reply = super().complete(name, *args)
            if name == "review_0":
                reply["quality_findings"] = ["另一个表块可能遗漏"]
            return reply

    service = make_service(settings, Interrupted())
    try:
        result = service.run(task)
        assert result.status == "ready", result
        assert result.artifact["selection"]["selected_attempt"] == 0
        report = read(result.diagnostics["quality_report"])
        assert report["repair_interruption"]["code"] == code
        assert len(report["repair_interruption"]["message"]) > 500
        if operation == "review_0":
            assert result.artifact["selection"]["semantic_status"] == "unverified"
            assert result.artifact["quality_status"] == "usable_with_warnings"
    finally:
        service.close()


def test_selects_source_backed_improvement_and_stops_when_clean(inputs):
    settings, task = inputs
    book = openpyxl.load_workbook(task.source_path)
    book.active.append(["指标乙", 12])
    book.save(task.source_path)
    book.close()
    two = (
        CODE.replace('    return {"code":', '    result = {"code":')
        + """
    result["ExcelData"].append({"indicator_name": sheet["A3"].value,
        "indicator_data": str(sheet["B3"].value), "indicator_unit": "-", "end_date": ""})
    return result
"""
    )

    class Improve(ScriptedModel):
        def complete(self, operation, *args):
            reply = super().complete(operation, *args)
            if operation == "review_0":
                reply["quality_findings"] = ["Sheet!A3:B3 有可补充的指标乙"]
            if operation == "generate_1":
                reply["content"] = two
                reply["evidence"].append(
                    {"record_index": 1, "name_refs": ["Sheet!A3"], "data_ref": "Sheet!B3"}
                )
            return reply

    model = Improve()
    service = make_service(settings, model)
    try:
        result = service.run(task)
        assert result.status == "ready", result
        assert result.artifact["selection"]["selected_attempt"] == 1
        assert result.artifact["selection"]["record_count"] == 2
        assert result.artifact["selection"]["semantic_status"] == "accepted"
        # The synthetic source has no statistical period. New downstream
        # diagnostics preserve this uncertainty even after coverage improves.
        assert result.artifact["quality_status"] == "usable_with_warnings"
        assert model.calls == ["design", "generate_0", "review_0", "generate_1", "review_1"]
    finally:
        service.close()


def test_quality_repairs_have_separate_small_budget(inputs):
    settings, task = inputs
    settings = settings.model_copy(
        update={"max_repairs": 4, "max_quality_repairs": 2, "repair_patience": 5}
    )

    class NeverComplete(ScriptedModel):
        def complete(self, operation, *args):
            reply = super().complete(operation, *args)
            if operation.startswith("review"):
                reply["quality_findings"] = ["可能仍有遗漏，但已有数据有效"]
            return reply

    model = NeverComplete()
    service = make_service(settings, model)
    try:
        result = service.run(task)
        assert result.status == "ready", result
        assert result.artifact["selection"]["stop_reason"] == "quality_budget_exhausted"
        assert result.artifact["selection"]["selected_attempt"] == 0
        assert model.calls[-1] == "review_2" and "generate_3" not in model.calls
    finally:
        service.close()


def test_resume_recovers_earlier_usable_candidate_and_revalidates_source(inputs):
    settings, task = inputs

    class FailsRepair(ScriptedModel):
        def complete(self, operation, *args):
            reply = super().complete(operation, *args)
            if operation == "review_0":
                reply["quality_findings"] = ["可能遗漏"]
            if operation == "generate_1":
                reply["content"] = CODE.replace('"ExcelData"', '"OUTDATA"')
            return reply

    service = make_service(settings, FailsRepair())
    try:
        first = service.run(task)
        assert first.status == "ready"
        service.repo.deactivate(task.source_key)
    finally:
        service.close()
    model = ScriptedModel()
    service = make_service(settings.model_copy(update={"max_repairs": 0}), model)
    try:
        result = service.run(task, retry=True, rebuild=True, resume_candidate=True)
        assert result.status == "ready", result
        assert result.artifact["selection"]["selected_attempt"] == 0
        assert result.artifact["selection"]["semantic_status"] == "accepted"
        assert model.calls == []
    finally:
        service.close()


def test_no_usable_candidate_still_returns_failure_and_full_report(inputs):
    settings, task = inputs
    service = make_service(settings.model_copy(update={"max_repairs": 0}), ScriptedModel(True))
    try:
        result = service.run(task)
        assert result.status == "review_required" and result.artifact is None
        assert result.error["quality_report"] == result.diagnostics["quality_report"]
        assert Path(result.diagnostics["quality_report_markdown"]).is_file()
        assert read(result.diagnostics["quality_report"])["attempts"][0]["issues"]
        assert service.repo.current(task.source_key) is None
    finally:
        service.close()
