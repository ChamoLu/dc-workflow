"""Public submission through real asset generation with controlled model replies.

Python candidates run in the existing isolated process against the actual fixture
bytes. Markdown prompt validation does not execute the later platform model.
"""

import io
import json

import openpyxl
import pytest
from fastapi.testclient import TestClient

from dc_agent.application.service import AssetService
from dc_agent.config import Settings
from dc_agent.entrypoints.http import create_app
from dc_agent.generation import SkillRegistry
from dc_agent.infrastructure import LocalReferences
from dc_agent.infrastructure.execution import LocalProcessRunner
from dc_agent.integration.contracts import V4_AGENT_FIELDS
from dc_agent.integration.worker import run_once
from dc_agent.validation import HEADINGS

TOKEN = "v4-generation-route-test-token-without-real-credentials"
RULES = [{"rule_indicator_name": "指标甲", "rule_indicator_code": "1000000001"}]
MARKDOWN = "|指标|值|\n|---|---|\n|指标甲|0.0000|\n".encode("utf-8")

EXCEL_CODE = """import requests
import openpyxl
from io import BytesIO

def main(params):
    raw = requests.get("http://service-1.example.invalid/api/file/download/" + params["fileMd5"]).content
    book = openpyxl.load_workbook(BytesIO(raw), data_only=True)
    try:
        sheet = book["数据"]
        record = {"indicator_name": sheet["A2"].value,
                  "indicator_data": sheet["B2"].value,
                  "indicator_unit": "-", "end_date": ""}
    finally:
        book.close()
    return {"code": 0, "msg": "success", "ExcelData": [record]}
"""

HTML_CODE = """import requests

def main(params):
    raw = requests.get("http://service-1.example.invalid/api/file/download/" + params["markdownMD5"]).content
    line = raw.decode("utf-8").splitlines()[2]
    name, value = line.strip("|").split("|")
    return {"code": 0, "msg": "success", "ExcelData": [
        {"indicator_name": name, "indicator_data": value,
         "indicator_unit": "-", "end_date": ""}]}
"""


class RouteSource:
    def __init__(self, raw):
        self.raw = raw
        self.calls = []

    def read(self, task):
        assert task.source_path is None
        self.calls.append((task.kind, task.source_id))
        return self.raw


class RouteModel:
    """Only the model replies are scripted; execution and source inspection are real."""

    def __init__(self, kind):
        self.kind = kind
        self.calls = []

    def complete(self, operation, system, context, schema, timeout):
        self.calls.append(operation)
        assert timeout > 0
        if operation == "preflight":
            assert "GZKJG" not in context
            cells = context["inspection"]["sections"][0]["cells"]
            assert any("指标甲" in str(cell["value"]) for cell in cells)
            return {"verdict": "has_data", "reason": "Actual fixture contains 指标甲 and 0.0000"}
        assert "source_rules_v2" in system
        is_excel = self.kind == "python_excel"
        if operation == "design":
            return {
                "summary": "Current fixture only",
                "name_logic": "数据!A2" if is_excel else "Markdown L3 indicator cell",
                "unit_logic": "No unit supplied; use -",
                "unit_position": "not supplied in source",
                "date_logic": "No reliable statistical period; leave blank",
                "date_position": "not supplied in current source or title",
                "covered_sections": ["数据" if is_excel else "markdown"],
            }
        if operation == "generate_0":
            if self.kind == "markdown_prompt":
                prompt = "<!-- 来源：V4受控生成夹具；单位位置：源中未提供；日期位置：未提供 -->\n"
                prompt += "\n".join(
                    f"# {heading}\n从本次Markdown真实表格构造候选，按GZK.GZKJG.rule_indicator_name唯一精确匹配。"
                    for heading in HEADINGS
                )
                prompt += (
                    '\n{"out_data":[{"indicator_code":"rule_indicator_code",'
                    '"indicator_name":"", "indicator_data":"",'
                    '"indicator_unit":"-", "end_date":""}]}'
                )
                return {"content": prompt}
            assert context["parser_contract"]["download_parameter"] == (
                "fileMd5" if is_excel else "markdownMD5"
            )
            return {
                "content": EXCEL_CODE if is_excel else HTML_CODE,
                "evidence": [
                    {
                        "record_index": 0,
                        "name_refs": ["数据!A2" if is_excel else "L3"],
                        "data_ref": "数据!B2" if is_excel else "L3",
                        "unit_refs": [],
                        "date_refs": [],
                    }
                ],
            }
        assert operation == "review_0", operation
        assert context["review_contract"]["asset_kind"] == self.kind
        if self.kind != "markdown_prompt":
            assert context["runtime_report"]["records"][0]["indicator_data"] == "0.0000"
        return {
            "approved": True,
            "findings": [],
            "reviewed_refs": ["数据!A2", "数据!B2"] if is_excel else ["L1", "L3"],
        }


@pytest.mark.parametrize(
    "route,file_type,kind,source_field,skill_name",
    [
        ("excel", "xlsx", "python_excel", "fileMd5", "excel-rule-parser-codegen"),
        ("html", "shtml", "python_html", "markdownMD5", "html-rule-parser-codegen"),
        (
            "markdown",
            "png",
            "markdown_prompt",
            "markdownMD5",
            "markdown-table-prompt-extraction",
        ),
    ],
)
def test_public_routes_generate_validate_publish_and_reuse_actual_source(
    tmp_path, write_catalog, write_reference, route, file_type, kind, source_field, skill_name
):
    settings = Settings(
        _env_file=None,
        runtime_dir=tmp_path / "runtime",
        code_dir=tmp_path / "code",
        rule_dir=tmp_path / "rules",
        skill_root=tmp_path / "skills",
        api_token=TOKEN,
        max_repairs=0,
        max_quality_repairs=0,
    )
    skill = settings.skill_root / skill_name / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "Controlled fixture skill; current source facts take priority.", encoding="utf-8"
    )
    page_title = "V4生成验证\\" + route
    write_catalog(
        settings.resolved_rules(),
        [["fixture", "受控样本", "fixture-code", "指标甲", page_title]],
    )
    write_reference(settings.resolved_rules() / "IndicatorName_Excel_Md.xlsx", code="other-code")
    write_reference(settings.resolved_rules() / "rule_html.xlsx", code="other-code")
    raw = MARKDOWN
    if kind == "python_excel":
        book = openpyxl.Workbook()
        book.active.title = "数据"
        book.active.append(["指标", "值"])
        book.active.append(["指标甲", "0.0000"])
        stream = io.BytesIO()
        book.save(stream)
        book.close()
        raw = stream.getvalue()
    source = RouteSource(raw)
    model = RouteModel(kind)
    runner = LocalProcessRunner(allow_unsafe=True)
    executions = []
    real_run = runner.run

    def execute(*args, **kwargs):
        assert kind != "markdown_prompt", "Prompt generation must not execute the platform model"
        executions.append(args[3])
        return real_run(*args, **kwargs)

    runner.run = execute

    def factory(current_settings):
        return AssetService(
            current_settings,
            model,
            source,
            LocalReferences(),
            runner,
            SkillRegistry(current_settings.skill_root),
        )

    application = create_app(settings, start_worker=False)
    body = {
        "pageTitle": page_title,
        "fileType": file_type,
        source_field: "current-source",
        "title": "本次真实表格",
        "publictime": "",
        "GZKJG": RULES,
    }
    headers = {"Authorization": "Bearer " + TOKEN}
    with TestClient(application) as client:
        accepted = client.post(f"/v4/{route}/jobs", json=body, headers=headers)
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]
        assert run_once(settings, application.state.store, service_factory=factory)
        delivered = client.get(f"/v4/jobs/{job_id}", headers=headers)
        assert delivered.status_code == 200
        item = delivered.json()["agent"][0]
        assert item["status"] == "ready", item
        assert set(item) == set(V4_AGENT_FIELDS)
        assert len(item) == 16
        assert "artifact_kind" not in item
        assert item["cache_hit"] == "false"
        assert source.calls == [(kind, "current-source")]
        assert model.calls == ["preflight", "design", "generate_0", "review_0"]
        row = application.state.store.get(job_id)
        artifact = row["result"]["artifact"]
        validation = json.loads(
            (settings.resolved_runtime() / artifact["validation_path"]).read_text(encoding="utf-8")
        )
        if kind == "markdown_prompt":
            assert executions == []
            assert item["PythonFile"] == item["PythonCode"] == ""
            assert item["PromptCode"].startswith("<!--")
            assert artifact["contract"] == "prompt-out_data-five-fields-v1"
            assert any(
                check["name"] == "target_model_execution" and check["status"] == "not_run"
                for check in validation["checks"]
            )
        else:
            assert executions == ["current-source"]
            assert item["PromptFile"] == item["PromptCode"] == ""
            assert artifact["contract"] == "main-ExcelData-v1"
            assert validation["records"] == [
                {
                    "indicator_name": "指标甲",
                    "indicator_data": "0.0000",
                    "indicator_unit": "-",
                    "end_date": "",
                }
            ]
            assert any(
                check["name"] == "source_evidence" and check["status"] == "pass"
                for check in validation["checks"]
            )
        duplicate = client.post(f"/v4/{route}/jobs", json=body, headers=headers)
        assert duplicate.json()["job_id"] == job_id
        changed = client.post(
            f"/v4/{route}/jobs", json={**body, source_field: "next-source"}, headers=headers
        )
        next_job_id = changed.json()["job_id"]
        assert next_job_id != job_id
        assert run_once(settings, application.state.store, service_factory=factory)
        reused = client.get(f"/v4/jobs/{next_job_id}", headers=headers).json()["agent"][0]
        assert reused["status"] == "ready" and reused["cache_hit"] == "true"
        assert reused["artifact_version"] == item["artifact_version"]
        assert source.calls == [(kind, "current-source")]
        assert len(model.calls) == 4
