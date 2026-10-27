"""Offline contract tests for the copyable DC loop access node; no real transport."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import requests

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "workflow_agent_test_v3/工作流节点代码/循环接入方案/02_外部框架接入_循环版.py"
)
JOB = "job-test-123"


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Response:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


@pytest.fixture
def node(monkeypatch):
    spec = importlib.util.spec_from_file_location("dc_loop_access_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "API_TOKEN", "secret-for-test-do-not-output")
    monkeypatch.setattr(module, "API_BASE_URL", "http://example.invalid:8765")
    clock = Clock()
    monkeypatch.setattr(module, "time", clock)
    return module, clock


def business(**updates):
    return {
        "fileMd5": "C02BBD1DEB3A82CCA96B66D873969E18",
        "pageTitle": "数据源/统计数据",
        "fileType": "xlsx",
        "title": "2026年2月统计表",
        "publictime": "2026-03-01",
        "GZKJG": [{"rule_indicator_name": "真实指标", "rule_indicator_code": "1"}],
        **updates,
    }


def record(module, status="processing", **updates):
    value = dict.fromkeys(module.AGENT_FIELDS, "")
    value.update(
        status=status,
        stage="build_design",
        job_id=JOB,
        elapsed_seconds="10.00",
        message="任务处理中",
    )
    if status == "ready":
        code = "def main(params):\n    return {'code': 0, 'ExcelData': []}\n"
        value.update(
            PythonFile="source.py",
            PythonCode=code,
            code_sha256=hashlib.sha256(code.encode("utf-8")).hexdigest(),
            artifact_version="v-test-123",
            quality_status="accepted",
            cache_hit="false",
        )
    value.update(updates)
    return value


def attach_transport(module, clock, monkeypatch, events):
    """Each event is (elapsed, response/exception); all unexpected requests fail."""
    calls = []

    class Session:
        def __init__(self):
            self.headers = {}
            self.trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs, dict(self.headers), self.trust_env))
            assert events, "Unexpected request; no live network is permitted"
            elapsed, result = events.pop(0)
            clock.now += elapsed
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(module.requests, "Session", Session)
    return calls


def output_agent(result, module):
    assert isinstance(result, dict) and set(result) == {"agent"}
    assert isinstance(result["agent"], list) and len(result["agent"]) == 1
    value = result["agent"][0]
    assert set(value) == set(module.AGENT_FIELDS)
    assert all(isinstance(value[key], str) for key in module.AGENT_FIELDS)
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    return value


def test_two_rounds_continue_same_job_without_resubmission(node, monkeypatch):
    module, clock = node
    events = [
        (1, Response({"job_id": JOB, "status": "processing"}, 202)),
        (103, Response({"agent": [record(module)]})),
        (1, Response({"agent": [record(module, "ready")]})),
    ]
    calls = attach_transport(module, clock, monkeypatch, events)
    first = module.main(business(count=0, workflow_run_id="run-fixed"))
    assert output_agent(first, module)["status"] == "processing"
    second = module.main(business(agent=first["agent"], count=1, workflow_run_id="run-fixed"))
    assert output_agent(second, module)["status"] == "ready"
    assert [call[0] for call in calls] == ["POST", "GET", "GET"]
    posted = calls[0][2]["json"]
    assert posted["workflow_run_id"] == "run-fixed"
    assert set(posted) == {
        "fileMd5",
        "pageTitle",
        "fileType",
        "title",
        "publictime",
        "GZKJG",
        "workflow_run_id",
    }
    assert all(call[1].endswith("/" + JOB) for call in calls[1:])
    assert all(call[2]["allow_redirects"] is False and call[4] is False for call in calls)
    assert all(call[2].get("params", {}).get("wait_seconds", 0) <= 20 for call in calls)


def test_processing_normal_return_clears_all_code(node, monkeypatch):
    module, clock = node
    stale = record(
        module, PythonFile="stale.py", PythonCode="stale", code_sha256="bad", artifact_version="old"
    )
    calls = attach_transport(module, clock, monkeypatch, [(104, Response({"agent": [stale]}))])
    value = output_agent(module.main(business(agent=[record(module)])), module)
    assert value["status"] == "processing"
    assert value["error_code"] == ""
    assert all(value[key] == "" for key in module._CODE_FIELDS)
    assert [call[0] for call in calls] == ["GET"]


def test_ready_already_received_wins_over_deadline(node, monkeypatch):
    module, clock = node
    attach_transport(
        module, clock, monkeypatch, [(110, Response({"agent": [record(module, "ready")]}))]
    )
    value = output_agent(module.main(business(agent=[record(module)])), module)
    assert value["status"] == "ready"
    assert value["PythonCode"]


@pytest.mark.parametrize("initial", ["missing", None, [], "blank"])
def test_supported_initial_values_submit_once(node, monkeypatch, initial):
    module, clock = node
    values = business()
    if initial != "missing":
        values["agent"] = (
            [dict.fromkeys(module.AGENT_FIELDS, "")] if initial == "blank" else initial
        )
    calls = attach_transport(
        module,
        clock,
        monkeypatch,
        [
            (0, Response({"job_id": JOB, "status": "ready"})),
            (0, Response({"agent": [record(module, "ready")]})),
        ],
    )
    assert output_agent(module.main(values), module)["status"] == "ready"
    assert [call[0] for call in calls] == ["POST", "GET"]


@pytest.mark.parametrize("status", ["failed", "review_required", "invalid_input", "ready"])
def test_existing_terminal_never_submits_or_queries(node, monkeypatch, status):
    module, clock = node
    current = record(module, status, error_code="MODEL_STREAM_ERROR" if status != "ready" else "")
    if status != "ready":
        current.update(
            PythonCode="must-not-return",
            PythonFile="stale.py",
            artifact_version="old",
            code_sha256="bad",
        )
    calls = attach_transport(module, clock, monkeypatch, [])
    value = output_agent(module.main(business(agent=[current])), module)
    assert value["status"] == status
    assert not calls
    if status != "ready":
        assert all(value[key] == "" for key in module._CODE_FIELDS)


@pytest.mark.parametrize(
    "rules",
    [None, [], "null", "[]", [{"rule_indicator_name": "empty"}], [{"rule_indicator_name": 1}]],
)
def test_missing_rules_return_exact_code_without_configuration_or_network(node, monkeypatch, rules):
    module, clock = node
    module.API_TOKEN = "REPLACE_WITH_DC_AGENT_API_TOKEN"
    calls = attach_transport(module, clock, monkeypatch, [])
    value = output_agent(module.main(business(GZKJG=rules)), module)
    assert (value["status"], value["stage"], value["error_code"]) == (
        "invalid_input",
        "preflight_rules",
        "without_GZKJG",
    )
    assert not calls


@pytest.mark.parametrize("bad", [{}, [{}], [1], [{"status": "processing"}], [{}, {}], "[]"])
def test_malformed_agent_rejected_without_new_job(node, monkeypatch, bad):
    module, clock = node
    calls = attach_transport(module, clock, monkeypatch, [])
    value = output_agent(module.main(business(agent=bad)), module)
    assert value["status"] == "invalid_input"
    assert value["stage"] == "client_input"
    assert not calls


def test_invalid_existing_agent_retains_known_job_id(node, monkeypatch):
    module, clock = node
    bad = record(module, status="unknown")
    attach_transport(module, clock, monkeypatch, [])
    value = output_agent(module.main(business(agent=[bad])), module)
    assert value["status"] == "invalid_input" and value["job_id"] == JOB


def test_processing_with_error_code_does_not_reenter_wait(node, monkeypatch):
    module, clock = node
    calls = attach_transport(
        module,
        clock,
        monkeypatch,
        [
            (0, Response({"job_id": JOB, "status": "processing"})),
            (0, Response({"agent": [record(module, error_code="MODEL_STREAM_ERROR")]})),
        ],
    )
    value = output_agent(module.main(business()), module)
    assert value["status"] == "failed"
    assert value["error_code"] == "DC_AGENT_PROTOCOL_ERROR"
    assert len(calls) == 2


@pytest.mark.parametrize(
    "updates",
    [
        {"fileType": ""},
        {"fileMd5": 1},
        {"pageTitle": " "},
        {"GZKJG": "bad-json"},
        {"GZKJG": {}},
        {"GZKJG": [{"rule_indicator_name": "valid", "extra": float("nan")}]},
        {"workflow_run_id": ""},
    ],
)
def test_bad_business_fields_return_safe_input_diagnostic(node, monkeypatch, updates):
    module, clock = node
    calls = attach_transport(module, clock, monkeypatch, [])
    value = output_agent(module.main(business(**updates)), module)
    assert value["status"] == "invalid_input" and value["error_code"] == "DC_AGENT_INPUT_ERROR"
    assert not calls


def test_network_failure_not_fake_processing_or_raw_exception(node, monkeypatch):
    module, clock = node
    secret = module.API_TOKEN
    calls = attach_transport(
        module, clock, monkeypatch, [(1, requests.ConnectionError(secret + " raw-network-body"))]
    )
    result = module.main(business(agent=[record(module)]))
    value = output_agent(result, module)
    assert value["status"] == "failed" and value["stage"] == "client_transport"
    assert value["error_code"] == "DC_AGENT_NETWORK_ERROR" and value["job_id"] == JOB
    assert secret not in json.dumps(result) and "raw-network-body" not in json.dumps(result)
    assert [call[0] for call in calls] == ["GET"]


def test_lost_submit_response_uses_stable_payload_on_manual_retry(node, monkeypatch):
    module, clock = node
    events = [
        (1, requests.ReadTimeout("unknown acceptance")),
        (1, Response({"job_id": JOB, "status": "processing"}, 202)),
        (1, Response({"agent": [record(module, "ready")]})),
    ]
    calls = attach_transport(module, clock, monkeypatch, events)
    params = business(workflow_run_id="stable-run")
    assert output_agent(module.main(params), module)["error_code"] == "DC_AGENT_NETWORK_ERROR"
    assert output_agent(module.main(params), module)["status"] == "ready"
    assert calls[0][2]["json"] == calls[1][2]["json"]


@pytest.mark.parametrize(
    "status,code,stage",
    [
        (401, "DC_AGENT_AUTH_ERROR", "client_configuration"),
        (403, "DC_AGENT_AUTH_ERROR", "client_configuration"),
        (404, "DC_AGENT_JOB_NOT_FOUND", "client_transport"),
        (500, "DC_AGENT_HTTP_ERROR", "client_transport"),
        (422, "DC_AGENT_HTTP_422", "client_input"),
    ],
)
def test_http_errors_do_not_echo_response_body(node, monkeypatch, status, code, stage):
    module, clock = node
    secret = module.API_TOKEN
    attach_transport(
        module, clock, monkeypatch, [(1, Response({"message": secret + " raw-body"}, status))]
    )
    result = module.main(business(agent=[record(module)]))
    value = output_agent(result, module)
    assert value["error_code"] == code and value["stage"] == stage
    assert secret not in json.dumps(result) and "raw-body" not in json.dumps(result)


def test_http422_only_exposes_whitelisted_locations_and_diagnostic_id(node, monkeypatch):
    module, clock = node
    body = {
        "error_code": "INVALID_INPUT",
        "diagnostic_id": "a" * 32,
        "message": "do-not-echo",
        "issues": [
            {
                "field": "GZKJG[0].rule_indicator_code",
                "error_type": "string_type",
                "input": "do-not-echo",
            },
            {"field": module.API_TOKEN, "error_type": "missing"},
        ],
    }
    attach_transport(module, clock, monkeypatch, [(1, Response(body, 422))])
    result = module.main(business(agent=[record(module)]))
    value = output_agent(result, module)
    assert "GZKJG[0].rule_indicator_code=string_type" in value["message"]
    assert "diagnostic_id=" + "a" * 32 in value["message"]
    assert module.API_TOKEN not in json.dumps(result) and "do-not-echo" not in json.dumps(result)


def test_idempotency_conflict_preserves_only_whitelisted_error_code(node, monkeypatch):
    module, clock = node
    body = {
        "detail": {
            "error_code": "IDEMPOTENCY_CONFLICT",
            "message": module.API_TOKEN + " do-not-echo-conflict-body",
        }
    }
    attach_transport(module, clock, monkeypatch, [(1, Response(body, 409))])
    result = module.main(business(workflow_run_id="stable-run"))
    value = output_agent(result, module)
    assert (value["status"], value["stage"], value["error_code"]) == (
        "invalid_input",
        "client_input",
        "IDEMPOTENCY_CONFLICT",
    )
    assert value["message"] == "同一workflow_run_id绑定了不同输入，请检查运行身份与输入。"
    assert module.API_TOKEN not in json.dumps(result)
    assert "do-not-echo-conflict-body" not in json.dumps(result)


@pytest.mark.parametrize(
    "body",
    [
        {"detail": {"error_code": "OTHER_CONFLICT", "message": "untrusted-body"}},
        {"detail": "untrusted-body"},
        {"error_code": "IDEMPOTENCY_CONFLICT", "message": "untrusted-body"},
        ValueError("untrusted-body"),
    ],
)
def test_other_http409_remains_generic_without_body_echo(node, monkeypatch, body):
    module, clock = node
    attach_transport(module, clock, monkeypatch, [(1, Response(body, 409))])
    result = module.main(business())
    value = output_agent(result, module)
    assert value["error_code"] == "DC_AGENT_HTTP_ERROR"
    assert value["status"] == "failed" and "HTTP 409" in value["message"]
    assert "untrusted-body" not in json.dumps(result)


def test_server_without_gzk_error_preserved(node, monkeypatch):
    module, clock = node
    attach_transport(
        module, clock, monkeypatch, [(1, Response({"error_code": "without_GZKJG"}, 422))]
    )
    value = output_agent(module.main(business()), module)
    assert value["status"] == "invalid_input" and value["error_code"] == "without_GZKJG"


@pytest.mark.parametrize(
    "bad_response",
    [
        Response(ValueError("raw-json-secret")),
        Response([]),
        Response({"agent": []}),
        Response({"agent": [{"status": "ready"}]}),
        Response({"agent": [], "x": float("nan")}),
    ],
)
def test_bad_response_protocol_diagnostic_never_processing(node, monkeypatch, bad_response):
    module, clock = node
    attach_transport(module, clock, monkeypatch, [(1, bad_response)])
    result = module.main(business(agent=[record(module)]))
    value = output_agent(result, module)
    assert value["status"] == "failed" and value["stage"] == "client_protocol"
    assert "raw-json-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "updates",
    [
        {"code_sha256": "a" * 64},
        {"code_sha256": "bad"},
        {"PythonCode": ""},
        {"PythonFile": "../evil.py"},
        {"artifact_version": ""},
        {"quality_status": "unreviewed"},
        {"cache_hit": ""},
        {"error_code": "MODEL_STREAM_ERROR"},
    ],
)
def test_ready_delivery_checks_fail_closed(node, monkeypatch, updates):
    module, clock = node
    attach_transport(
        module, clock, monkeypatch, [(1, Response({"agent": [record(module, "ready", **updates)]}))]
    )
    value = output_agent(module.main(business(agent=[record(module)])), module)
    assert value["status"] == "failed" and value["stage"] == "client_delivery"
    assert value["error_code"] == "DC_AGENT_DELIVERY_ERROR"
    assert value["job_id"] == JOB
    assert all(value[key] == "" for key in module._CODE_FIELDS)


def test_job_identity_mismatch_rejected(node, monkeypatch):
    module, clock = node
    attach_transport(
        module,
        clock,
        monkeypatch,
        [(1, Response({"agent": [record(module, "ready", job_id="other-job")]}))],
    )
    value = output_agent(module.main(business(agent=[record(module)])), module)
    assert value["status"] == "failed" and value["error_code"] == "DC_AGENT_PROTOCOL_ERROR"
    assert value["job_id"] == JOB and not value["PythonCode"]


@pytest.mark.parametrize("target", ["PythonCode", "artifact_version", "quality_status"])
def test_credentials_in_delivered_fields_blocked(node, monkeypatch, target):
    module, clock = node
    current = record(module, "ready")
    current[target] = current[target] + module.API_TOKEN
    current["code_sha256"] = hashlib.sha256(current["PythonCode"].encode("utf-8")).hexdigest()
    attach_transport(module, clock, monkeypatch, [(1, Response({"agent": [current]}))])
    result = module.main(business(agent=[record(module)]))
    value = output_agent(result, module)
    assert value["status"] == "failed" and value["stage"] == "client_delivery"
    assert module.API_TOKEN not in json.dumps(result)


def test_public_server_message_sanitized_without_new_fields(node, monkeypatch):
    module, clock = node
    current = record(
        module,
        "failed",
        error_code="MODEL_STREAM_ERROR",
        message="build_design失败 "
        + module.API_TOKEN
        + " Bearer other-secret api_key=third-secret",
    )
    attach_transport(module, clock, monkeypatch, [(1, Response({"agent": [current]}))])
    result = module.main(business(agent=[record(module)]))
    value = output_agent(result, module)
    assert value["error_code"] == "MODEL_STREAM_ERROR" and value["stage"] == "build_design"
    assert len(value) == 13 and "report_ref" not in value
    assert all(
        secret not in json.dumps(result)
        for secret in (module.API_TOKEN, "other-secret", "third-secret")
    )


@pytest.mark.parametrize(
    "legacy_report", [None, {}, "https://external.invalid/report?token=secret"]
)
@pytest.mark.parametrize("status", ["processing", "ready"])
def test_legacy_wire_report_is_ignored_and_not_copied(node, monkeypatch, legacy_report, status):
    module, clock = node
    attach_transport(
        module,
        clock,
        monkeypatch,
        [
            (
                104,
                Response(
                    {
                        "agent": [
                            record(
                                module,
                                status,
                                report_ref=legacy_report,
                            )
                        ]
                    }
                ),
            )
        ],
    )
    value = output_agent(module.main(business(agent=[record(module)])), module)
    assert value["status"] == status and value["error_code"] == ""
    assert len(value) == 13 and "report_ref" not in value


@pytest.mark.parametrize(
    "changes",
    [
        {"API_TOKEN": "REPLACE_WITH_DC_AGENT_API_TOKEN"},
        {"API_BASE_URL": "http://user:password@example.invalid"},
        {"API_BASE_URL": "http://example.invalid:invalid"},
    ],
)
def test_bad_connection_configuration_returns_safe_diagnostic(node, monkeypatch, changes):
    module, clock = node
    for key, value in changes.items():
        monkeypatch.setattr(module, key, value)
    calls = attach_transport(module, clock, monkeypatch, [])
    value = output_agent(module.main(business()), module)
    assert value["error_code"] == "DC_AGENT_CONFIGURATION_ERROR" and not calls
    assert "user:password" not in value["message"]


def test_no_state_after_submission_does_not_fabricate_processing(node, monkeypatch):
    module, clock = node
    attach_transport(
        module, clock, monkeypatch, [(104, Response({"job_id": JOB, "status": "processing"}, 202))]
    )
    value = output_agent(module.main(business()), module)
    assert value["error_code"] == "DC_AGENT_NO_SERVER_STATE" and value["status"] == "failed"
    assert value["job_id"] == JOB


def test_unexpected_ordinary_exception_safe_return(node, monkeypatch):
    module, clock = node
    attach_transport(
        module, clock, monkeypatch, [(1, RuntimeError(module.API_TOKEN + " internal-debug"))]
    )
    result = module.main(business())
    value = output_agent(result, module)
    assert value["status"] == "failed" and value["error_code"] == "DC_AGENT_CLIENT_ERROR"
    assert module.API_TOKEN not in json.dumps(result) and "internal-debug" not in json.dumps(result)
