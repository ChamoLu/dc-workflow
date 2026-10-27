"""Offline HTTP 422 diagnostics: useful field locations without rejected input."""

import copy
import importlib.util
import json
import re
import traceback
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dc_agent.config import Settings
from dc_agent.entrypoints.http import create_app

PROJECT = Path(__file__).resolve().parents[1]
TOKEN = "diagnostic-test-token-at-least-24-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PAYLOAD = {
    "fileMd5": "synthetic-file-md5",
    "pageTitle": "诊断测试\\统计",
    "fileType": "xlsx",
    "title": "测试标题",
    "publictime": "",
    "GZKJG": [{"rule_indicator_name": "真实候选指标", "rule_indicator_code": 123}],
}


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, runtime_dir=tmp_path / "runtime", api_token=TOKEN)


@pytest.fixture
def access():
    path = PROJECT / "workflow_agent_test_v3" / "工作流节点代码" / "外部框架接入.py"
    spec = importlib.util.spec_from_file_location("diagnostic_workflow_access", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.API_TOKEN = "diagnostic-node-token"
    return module


def assert_safe_report(settings, response, *, phase, field, error_type, input_type):
    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error_code", "phase", "message", "diagnostic_id", "issues"}
    assert body["error_code"] == "INVALID_INPUT"
    assert body["phase"] == phase
    assert isinstance(body["message"], str) and body["message"]
    assert re.fullmatch(r"[0-9a-f]{32}", body["diagnostic_id"])
    assert {
        "field": field,
        "error_type": error_type,
        "input_type": input_type,
    } in body["issues"]
    assert all(set(issue) == {"field", "error_type", "input_type"} for issue in body["issues"])
    report_dir = settings.resolved_runtime() / "diagnostics" / "http-input-errors"
    report = report_dir / f"{body['diagnostic_id']}.json"
    assert report.is_file()
    assert json.loads(report.read_text(encoding="utf-8")) == body
    return body, report


def test_request_schema_error_identifies_field_without_raw_values(settings, caplog):
    raw_value = "UNSUPPORTED_FILE_TYPE_SECRET"
    with TestClient(create_app(settings, start_worker=False)) as client:
        response = client.post(
            "/v3/excel/jobs", json={**PAYLOAD, "fileType": raw_value}, headers=AUTH
        )
    body, report = assert_safe_report(
        settings,
        response,
        phase="request_schema",
        field="fileType",
        error_type="literal_error",
        input_type="str",
    )
    combined = response.text + report.read_text(encoding="utf-8") + caplog.text
    assert raw_value not in combined
    assert TOKEN not in combined
    assert "input" not in body and "ctx" not in body and "detail" not in body


def test_task_contract_error_identifies_rule_code_without_value_or_context(settings, caplog):
    raw_name = "DO_NOT_ECHO_THIS_RULE_NAME"
    invalid = {
        **PAYLOAD,
        "GZKJG": [{"rule_indicator_name": raw_name, "rule_indicator_code": 987654321.125}],
    }
    with TestClient(create_app(settings, start_worker=False)) as client:
        response = client.post("/v3/excel/jobs", json=invalid, headers=AUTH)
    _, report = assert_safe_report(
        settings,
        response,
        phase="task_contract",
        field="GZKJG[0].rule_indicator_code",
        error_type="value_error",
        input_type="float",
    )
    combined = response.text + report.read_text(encoding="utf-8") + caplog.text
    for secret in (raw_name, "987654321.125", TOKEN, "ValueError(", "ctx"):
        assert secret not in combined


def test_unknown_field_name_and_value_are_not_exposed(settings, caplog):
    extra_key = "do_not_echo_secret_extra_field_name"
    extra_value = "do_not_echo_secret_extra_field_value"
    with TestClient(create_app(settings, start_worker=False)) as client:
        response = client.post(
            "/v3/excel/jobs", json={**PAYLOAD, extra_key: extra_value}, headers=AUTH
        )
    assert response.status_code == 422
    body = response.json()
    report = (
        settings.resolved_runtime()
        / "diagnostics"
        / "http-input-errors"
        / f"{body['diagnostic_id']}.json"
    )
    combined = response.text + report.read_text(encoding="utf-8") + caplog.text
    for secret in (extra_key, extra_value, TOKEN):
        assert secret not in combined


def test_same_error_has_distinct_diagnostic_ids_and_matching_reports(settings):
    invalid = {**PAYLOAD, "fileType": "invalid"}
    with TestClient(create_app(settings, start_worker=False)) as client:
        responses = [client.post("/v3/excel/jobs", json=invalid, headers=AUTH) for _ in range(2)]
    reports = [
        assert_safe_report(
            settings,
            response,
            phase="request_schema",
            field="fileType",
            error_type="literal_error",
            input_type="str",
        )[0]
        for response in responses
    ]
    assert reports[0]["diagnostic_id"] != reports[1]["diagnostic_id"]


def test_integer_rule_codes_are_accepted_and_normalized(settings):
    payload = copy.deepcopy(PAYLOAD)
    payload["GZKJG"][0]["rule_indicator_code"] = 1100202045
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        response = client.post("/v3/excel/jobs", json=payload, headers=AUTH)
    assert response.status_code == 202
    row = app.state.store.get(response.json()["job_id"])
    assert row["payload"]["GZKJG"][0]["rule_indicator_code"] == "1100202045"


@pytest.mark.parametrize("value", [None, False, "", " ", "md", "unknown-type"])
def test_node_rejects_missing_or_unsupported_type_before_network(access, monkeypatch, value):
    def network_forbidden():
        raise AssertionError("Invalid input must be rejected before opening a session")

    monkeypatch.setattr(access.requests, "Session", network_forbidden)
    with pytest.raises(ValueError, match="fileType"):
        access.main({**PAYLOAD, "fileType": value})


def test_node_rejects_absent_file_type_and_normalizes_explicit_excel_type(access):
    payload = {key: value for key, value in PAYLOAD.items() if key != "fileType"}
    with pytest.raises(ValueError, match="fileType"):
        access._payload(payload)
    assert access._payload({**PAYLOAD, "fileType": " .XLSX "})["fileType"] == "xlsx"


class Response:
    status_code = 422

    def __init__(self, body):
        self.body = body

    def json(self):
        return copy.deepcopy(self.body)


def test_node_displays_safe_error_fields_but_ignores_untrusted_response_details(access):
    identifier = "a" * 32
    response = Response(
        {
            "error_code": "INVALID_INPUT",
            "phase": "task_contract",
            "diagnostic_id": identifier,
            "message": "SECRET_UNTRUSTED_MESSAGE",
            "detail": {"raw_input": "SECRET_UNTRUSTED_DETAIL"},
            "issues": [
                {
                    "field": "GZKJG[0].rule_indicator_code",
                    "error_type": "value_error",
                    "input_type": "float",
                    "input": "SECRET_UNTRUSTED_INPUT",
                    "ctx": {"error": "SECRET_UNTRUSTED_CONTEXT"},
                }
            ],
        }
    )
    with pytest.raises(RuntimeError) as error:
        access._response_json(response)
    rendered = "".join(traceback.format_exception(error.value))
    for expected in ("422", "task_contract", identifier, "GZKJG[0].rule_indicator_code", "float"):
        assert expected in str(error.value)
    assert "SECRET_UNTRUSTED" not in rendered


def test_node_ignores_malicious_field_phase_type_and_diagnostic_identifier(access):
    response = Response(
        {
            "error_code": "INVALID_INPUT",
            "phase": "SECRET_UNTRUSTED_PHASE",
            "diagnostic_id": "SECRET_UNTRUSTED_IDENTIFIER",
            "issues": [
                {
                    "field": "SECRET_UNTRUSTED_FIELD",
                    "error_type": "SECRET_UNTRUSTED_ERROR_TYPE",
                    "input_type": "SECRET_UNTRUSTED_INPUT_TYPE",
                }
            ],
        }
    )
    with pytest.raises(RuntimeError, match="422") as error:
        access._response_json(response)
    assert "SECRET_UNTRUSTED" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize(
    "body",
    [
        {"detail": "SECRET_UNTRUSTED_DETAIL"},
        {"error_code": "INVALID_INPUT", "message": "SECRET_UNTRUSTED_DETAIL"},
    ],
)
def test_node_old_unstructured_422_response_remains_safe(access, body):
    response = Response(body)
    with pytest.raises(RuntimeError, match="422") as error:
        access._response_json(response)
    assert "SECRET_UNTRUSTED" not in "".join(traceback.format_exception(error.value))
    assert "更新并重启本机服务" in str(error.value)
