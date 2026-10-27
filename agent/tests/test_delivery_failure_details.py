"""Public terminal diagnostics retain useful reasons without returning error context."""

import json

import pytest

from dc_agent.config import Settings
from dc_agent.integration import delivery
from dc_agent.integration.contracts import AGENT_FIELDS

API_TOKEN = "delivery-api-token-for-isolated-tests"
MODEL_KEY = "delivery-model-key-for-isolated-tests"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        runtime_dir=tmp_path / "runtime",
        api_token=API_TOKEN,
        model_api_key=MODEL_KEY,
    )


def failure_row(**updates):
    return {
        "status": "failed",
        "stage": "failed",
        "job_id": "job-diagnostic",
        "created_at": 10.0,
        "finished_at": 20.0,
        "error_code": "MODEL_STREAM_ERROR",
        "result": {
            "error": {
                "code": "MODEL_STREAM_ERROR",
                "stage": "build_design",
                "message": "design: Provider reported an error in completion response (type=error)",
                "details": {"request_body": "THIS MUST NEVER BE RETURNED"},
            }
        },
        **updates,
    }


@pytest.mark.parametrize("status", ["failed", "review_required", "invalid_input"])
def test_terminal_failure_preserves_actual_stage_and_scalar_reason(settings, status):
    row = failure_row(status=status)
    item = delivery.public_result(settings, row)["agent"][0]
    assert item["status"] == status
    assert item["stage"] == "build_design"
    assert item["error_code"] == "MODEL_STREAM_ERROR"
    assert item["message"] == row["result"]["error"]["message"]
    assert item["PythonCode"] == ""
    assert item["report_ref"] == ""
    assert "THIS MUST NEVER" not in json.dumps(item)
    assert set(item) == set(AGENT_FIELDS)
    assert all(isinstance(value, str) for value in item.values())


def test_budget_expiry_without_result_keeps_mapped_reason(settings):
    item = delivery.public_result(
        settings,
        failure_row(stage="expired", error_code="BUDGET_EXCEEDED", result=None),
    )["agent"][0]
    assert item["stage"] == "expired"
    assert item["message"] == "The task exhausted its original time budget"
    assert item["error_code"] == "BUDGET_EXCEEDED"


@pytest.mark.parametrize("error", [None, [], ["not a mapping"], "raw secret", 0, True])
def test_malformed_error_shape_uses_fallback_without_echoing_it(settings, error):
    item = delivery.public_result(settings, failure_row(result={"error": error}))["agent"][0]
    assert item["stage"] == "failed"
    assert item["message"] == (
        "Generation did not produce deliverable code; inspect the quality report"
    )


@pytest.mark.parametrize("result", [[], "not a mapping", 7, True])
def test_malformed_result_shape_does_not_crash_failure_delivery(settings, result):
    item = delivery.public_result(settings, failure_row(result=result))["agent"][0]
    assert item["error_code"] == "MODEL_STREAM_ERROR"
    assert item["stage"] == "failed"


@pytest.mark.parametrize("stage", [None, 99, {}, "bad stage", "../source", "a" * 102])
def test_invalid_actual_stage_keeps_queue_stage(settings, stage):
    row = failure_row()
    row["result"]["error"]["stage"] = stage
    item = delivery.public_result(settings, row)["agent"][0]
    assert item["stage"] == "failed"


def test_message_redacts_configured_and_labeled_credentials_and_controls(settings):
    row = failure_row()
    row["result"]["error"]["message"] = (
        f"\x00design:\r\n{API_TOKEN} {MODEL_KEY}; "
        "Authorization: Bearer unknown-bearer; Basic dXNlcjpwYXNz; "
        "api_key='unconfigured-key'; access_token=unknown-access; "
        'password="unknown-password"; token=unknown-query; \u202e useful reason'
    )
    item = delivery.public_result(settings, row)["agent"][0]
    for secret in (
        API_TOKEN,
        MODEL_KEY,
        "unknown-bearer",
        "dXNlcjpwYXNz",
        "unconfigured-key",
        "unknown-access",
        "unknown-password",
        "unknown-query",
    ):
        assert secret not in item["message"]
    assert "[redacted]" in item["message"]
    assert "useful reason" in item["message"]
    assert not any(char in item["message"] for char in ("\x00", "\r", "\n", "\u202e"))


@pytest.mark.parametrize(
    "message",
    [
        {"input": "private input"},
        ["private input"],
        '{"error": "private input"}',
        'Failed: {"request": "private input"}',
        "<html>private input</html>",
        "response_body=private input",
        "Traceback (most recent call last): private input",
    ],
)
def test_structured_or_raw_body_message_is_not_a_public_summary(settings, message):
    row = failure_row()
    row["result"]["error"]["message"] = message
    item = delivery.public_result(settings, row)["agent"][0]
    assert item["message"] == (
        "Generation did not produce deliverable code; inspect the quality report"
    )
    assert "private input" not in json.dumps(item)


def test_message_is_bounded_and_only_existing_report_is_linked(settings):
    row = failure_row()
    row["result"]["error"]["message"] = "useful failure " * 500
    row["result"].update(
        task_id=["not-a-valid-task-id"],
        artifact=["not-a-mapping"],
        diagnostics={"quality_report": "tasks/diagnostic/quality_report.json"},
    )
    assert delivery.public_result(settings, row)["agent"][0]["report_ref"] == ""
    report = settings.resolved_runtime() / "tasks/diagnostic/quality_report.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}", encoding="utf-8")
    item = delivery.public_result(settings, row)["agent"][0]
    assert len(item["message"]) == 2000
    assert item["report_ref"] == "/v3/excel/jobs/job-diagnostic/reports/quality_report.json"


def test_ready_delivery_keeps_success_fields_and_ignores_failure_detail(settings, monkeypatch):
    row = failure_row(
        status="ready",
        stage="complete",
        payload={"pageTitle": "站点\\统计", "fileType": "xlsx"},
    )
    row["result"]["cache_hit"] = True
    artifact = {
        "content_hash": "a" * 64,
        "version": "version-1",
        "quality_status": "accepted",
    }
    monkeypatch.setattr(delivery, "read_code", lambda _settings, _row: (artifact, "CODE\r\n"))
    monkeypatch.setattr(delivery, "_warning", lambda _settings, _row: "")
    item = delivery.public_result(settings, row)["agent"][0]
    assert item["status"] == "ready"
    assert item["stage"] == "complete"
    assert item["PythonFile"] == "站点_统计.py"
    assert item["PythonCode"] == "CODE\r\n"
    assert item["code_sha256"] == "a" * 64
    assert item["artifact_version"] == "version-1"
    assert item["cache_hit"] == "true"
    assert item["quality_status"] == "accepted"
    assert item["error_code"] == ""
    assert item["message"] == "Reused registered code"
    assert set(item) == set(AGENT_FIELDS)
