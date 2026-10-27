"""Exercise standalone DC node scripts without a DC server, network, or model call."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

NODE_DIR = Path(__file__).resolve().parents[1] / "workflow_agent_test_v3" / "工作流节点代码"
SOURCE = """import datetime
import typing
def main(params):
    assert set(params) == {'fileMd5', 'title', 'publictime', 'fileType', 'pageTitle'}
    return {'code': 0, 'msg': 'success', 'ExcelData': [
        {'indicator_name': '实际指标(万元)', 'indicator_data': 0,
         'indicator_unit': '万元', 'end_date': ''}]}
"""


def load_node(filename, name):
    spec = importlib.util.spec_from_file_location(name, NODE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def access():
    module = load_node("外部框架接入.py", "workflow_access")
    module.API_TOKEN = "unit-test-token"
    return module


@pytest.fixture
def executor():
    return load_node("动态代码执行器_4912.py", "workflow_executor")


def ready(source=SOURCE):
    return {
        "status": "ready",
        "stage": "completed",
        "job_id": "job-1",
        "PythonFile": "路径_实际指标.py",
        "PythonCode": source,
        "code_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "artifact_version": "version-1",
        "cache_hit": "false",
        "quality_status": "accepted",
        "warning_summary": "",
        "error_code": "",
        "message": "完成",
        "report_ref": "/v3/excel/jobs/job-1/reports/quality.json",
        "elapsed_seconds": "1.25",
    }


def inputs(**changes):
    result = {
        "fileMd5": "actual-file-id",
        "pageTitle": r"实际数据源\统计数据",
        "fileType": "xls",
        "title": "2026年9月统计月报",
        "publictime": "",
        "GZKJG": [{"rule_indicator_name": "实际指标", "rule_indicator_code": "001"}],
    }
    result.update(changes)
    return result


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Response:
    def __init__(self, body, status=200):
        self.body = body
        self.status_code = status

    def json(self):
        return copy.deepcopy(self.body)


class Transport:
    def __init__(self, module, responses, clock=None):
        self.module = module
        self.responses = list(responses)
        self.requests = []
        self.sessions = []
        self.clock = clock

    def session(self):
        owner = self

        class Session:
            def __init__(self):
                self.headers = {}
                self.trust_env = True
                owner.sessions.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def request(self, method, url, **kwargs):
                owner.requests.append((method, url, copy.deepcopy(kwargs)))
                value = owner.responses.pop(0)
                if isinstance(value, Exception):
                    raise value
                if callable(value):
                    value = value()
                return value

        return Session()

    def install(self, monkeypatch):
        monkeypatch.setattr(self.module.requests, "Session", self.session)
        if self.clock:
            monkeypatch.setattr(self.module, "time", self.clock)
        return self


def test_access_projects_only_fourteen_fields_and_exact_input(access, monkeypatch):
    record = ready()
    record["internal_token_usage"] = 123
    transport = Transport(
        access,
        [Response({"job_id": "job-1", "status": "processing"}, 202), Response({"agent": [record]})],
    ).install(monkeypatch)
    result = access.main(inputs(API_TOKEN="do-not-forward", unrelated="discard"))
    assert result == {"agent": [ready()]}
    assert tuple(result["agent"][0]) == access.AGENT_FIELDS
    method, url, kwargs = transport.requests[0]
    assert method == "POST" and url.endswith("/v3/excel/jobs")
    assert kwargs["json"] == inputs()
    assert kwargs["allow_redirects"] is False
    assert transport.sessions[0].headers["Authorization"] == "Bearer unit-test-token"
    assert transport.sessions[0].trust_env is False
    assert max(kwargs["timeout"]) <= 22.0
    assert transport.requests[1][2]["params"]["wait_seconds"] <= 20.0


def test_retry_reposts_identical_input_and_finds_same_job(access, monkeypatch):
    clock = Clock()
    record = ready()
    processing = dict(record, status="processing", stage="generation", PythonCode="old code")

    def spend_window():
        clock.now += access.ATTEMPT_SECONDS
        return Response({"agent": [processing]})

    transport = Transport(
        access,
        [
            Response({"job_id": "job-1", "status": "processing"}),
            spend_window,
            Response({"job_id": "job-1", "status": "processing"}),
            Response({"agent": [record]}),
        ],
        clock,
    ).install(monkeypatch)
    payload = inputs(workflow_run_id="platform-stable-run")
    with pytest.raises(RuntimeError, match="DC_AGENT_RETRY.*job_id=job-1"):
        access.main(payload)
    assert access.main(payload) == {"agent": [record]}
    posts = [request for request in transport.requests if request[0] == "POST"]
    assert len(posts) == 2
    assert posts[0][2]["json"] == posts[1][2]["json"] == payload
    assert len({request[1] for request in transport.requests if request[0] == "GET"}) == 1


def test_terminal_failure_returns_single_record_with_no_stale_code(access, monkeypatch):
    failure = dict(ready(), status="failed", error_code="FilenoData", message="源文件无数据")
    Transport(
        access, [Response({"job_id": "job-1", "status": "failed"}), Response({"agent": [failure]})]
    ).install(monkeypatch)
    record = access.main(inputs())["agent"][0]
    assert record["status"] == "failed" and record["error_code"] == "FilenoData"
    assert all(record[key] == "" for key in access._CODE_FIELDS)


@pytest.mark.parametrize(
    "rules",
    [
        False,
        {},
        "{}",
        "false",
        "not-json",
        [False],
        [{"rule_indicator_name": "x", "ratio": float("nan")}],
        '[{"rule_indicator_name":"x","ratio":NaN}]',
    ],
)
def test_access_rejects_invalid_rules_before_network(access, rules):
    with pytest.raises(ValueError):
        access.main(inputs(GZKJG=rules))


def test_access_decodes_rules_once_and_preserves_values(access, monkeypatch):
    original = inputs()
    transport = Transport(
        access, [Response({"job_id": "job-1", "status": "ready"}), Response({"agent": [ready()]})]
    ).install(monkeypatch)
    assert access.main(inputs(GZKJG=json.dumps(original["GZKJG"])))
    assert transport.requests[0][2]["json"] == original
    with pytest.raises(ValueError):
        access._payload(inputs(GZKJG=json.dumps(json.dumps(original["GZKJG"]))))


def test_access_preserves_mixed_blank_rule_rows_for_backend_audit(access):
    rules = inputs()["GZKJG"] + [
        {"rule_indicator_name": "", "rule_indicator_code": "extra-column-tail"},
        {"rule_indicator_name": None},
        {"rule_indicator_name": "null"},
        {"rule_indicator_name": False},
    ]
    assert access._payload(inputs(GZKJG=rules))["GZKJG"] == rules
    with pytest.raises(ValueError, match="至少需要一条有效"):
        access._payload(inputs(GZKJG=[{"rule_indicator_name": "none"}]))


@pytest.mark.parametrize(
    "key,value",
    [
        ("fileMd5", False),
        ("pageTitle", ""),
        ("title", None),
        ("publictime", 123),
        ("fileType", []),
        ("workflow_run_id", ""),
    ],
)
def test_access_does_not_coerce_business_types(access, key, value):
    with pytest.raises(ValueError):
        access.main(inputs(**{key: value}))


@pytest.mark.parametrize(
    "changes",
    [
        {"PythonCode": SOURCE + "\n"},
        {"code_sha256": "bad"},
        {"job_id": "another-job"},
        {"artifact_version": ""},
        {"PythonFile": "../parser.py"},
        {"PythonFile": "x.txt"},
        {"cache_hit": False},
        {"quality_status": "rejected"},
        {"error_code": "bad"},
        {"elapsed_seconds": "NaN"},
        {"elapsed_seconds": "-1"},
        {"status": "unknown"},
    ],
)
def test_access_rejects_bad_ready_protocol(access, changes):
    with pytest.raises(RuntimeError):
        access._agent({"agent": [dict(ready(), **changes)]}, "job-1")


@pytest.mark.parametrize("records", [None, {}, [], [ready(), ready()], ["ready"]])
def test_access_rejects_invalid_agent_shape(access, records):
    with pytest.raises(RuntimeError):
        access._agent({"agent": records}, "job-1")


@pytest.mark.parametrize("status", [301, 401, 403, 422, 500])
def test_http_errors_do_not_echo_response_body_or_token(access, monkeypatch, status):
    transport = Transport(access, [Response({"secret": access.API_TOKEN}, status)]).install(
        monkeypatch
    )
    with pytest.raises(RuntimeError) as error:
        access.main(inputs())
    assert access.API_TOKEN not in str(error.value)
    assert "HTTP %d" % status in str(error.value)
    assert len(transport.requests) == 1


def test_transport_error_is_sanitized_and_retryable(access, monkeypatch):
    Transport(
        access, [access.requests.ConnectionError("Authorization: Bearer unit-test-token")]
    ).install(monkeypatch)
    with pytest.raises(RuntimeError, match="DC_AGENT_RETRY") as error:
        access.main(inputs())
    assert access.API_TOKEN not in str(error.value)


def test_placeholder_token_fails_before_network(access):
    access.API_TOKEN = "REPLACE_WITH_DC_AGENT_API_TOKEN"
    with pytest.raises(ValueError, match="API_TOKEN"):
        access.main(inputs())


def execute(executor, source=SOURCE, record_changes=None, **params):
    record = ready(source)
    record.update(record_changes or {})
    return executor.main(inputs(agent=[record], **params))


def test_executor_runs_real_source_zero_valid_and_whitelist_isolated(executor):
    marker = object()
    executor.datetime = marker
    executor.typing = marker
    original_main = executor.main
    result = execute(executor, API_TOKEN="must-not-leak")
    assert result["ExcelData"][0]["indicator_data"] == 0
    assert result["ExcelData"][0]["indicator_name"] == "实际指标(万元)"
    assert executor.datetime is marker and executor.typing is marker
    assert executor.main is original_main
    assert "datetime" not in original_main.__globals__ or executor.datetime is marker


@pytest.mark.parametrize("records", [None, "[]", {}, [], [ready(), ready()], [True]])
def test_executor_rejects_invalid_agent_shape(executor, records):
    with pytest.raises(ValueError):
        executor.main(inputs(agent=records))


@pytest.mark.parametrize("status", ["processing", "failed", "review_required", "invalid_input"])
def test_executor_never_executes_non_ready(executor, status):
    source = "raise AssertionError('must not execute')"
    with pytest.raises(RuntimeError, match="DC_AGENT_NOT_READY"):
        execute(executor, source, {"status": status})


@pytest.mark.parametrize(
    "changes",
    [
        {"code_sha256": "0" * 64},
        {"artifact_version": ""},
        {"PythonFile": "bad/name.py"},
        {"job_id": ""},
        {"cache_hit": True},
        {"quality_status": "rejected"},
        {"error_code": "failure"},
        {"stage": 2},
    ],
)
def test_executor_rejects_ready_metadata_before_execution(executor, changes):
    with pytest.raises(ValueError):
        execute(executor, record_changes=changes)


@pytest.mark.parametrize("source", ["value = 1", "main = 1"])
def test_executor_requires_callable_main(executor, source):
    with pytest.raises(RuntimeError, match="main"):
        execute(executor, source)


def test_executor_syntax_and_runtime_errors_stop_node(executor):
    with pytest.raises(SyntaxError):
        execute(executor, "def main(: pass")
    with pytest.raises(ValueError, match="download failed"):
        execute(executor, "def main(params):\n raise ValueError('download failed')")


def source_with_result(expression):
    return "def main(params):\n return " + expression


@pytest.mark.parametrize(
    "expression",
    [
        "None",
        "[]",
        "{'code': False, 'msg': 'x', 'ExcelData': [1]}",
        "{'code': '0', 'msg': 'x', 'ExcelData': [1]}",
        "{'code': 1, 'msg': 'x', 'ExcelData': [1]}",
        "{'code': 0, 'msg': 123, 'ExcelData': [1]}",
        "{'code': 0, 'msg': 'x', 'ExcelData': []}",
        "{'code': 0, 'msg': 'x', 'ExcelData': [{}]}",
    ],
)
def test_executor_rejects_bad_return_contract(executor, expression):
    with pytest.raises(RuntimeError):
        execute(executor, source_with_result(expression))


@pytest.mark.parametrize(
    "value",
    [
        "False",
        "None",
        "float('nan')",
        "float('inf')",
        "'NaN'",
        "'Infinity'",
        "'-'",
        "''",
        "'1_000'",
        "{}",
    ],
)
def test_executor_rejects_invalid_values(executor, value):
    expression = (
        "{'code': 0, 'msg': 'x', 'ExcelData': [{"
        "'indicator_name': 'x', 'indicator_data': " + value + ","
        "'indicator_unit': '-', 'end_date': ''}]}"
    )
    with pytest.raises(RuntimeError):
        execute(executor, source_with_result(expression))


@pytest.mark.parametrize(
    "value", ["0", "0.0", "'0.000000000000000000'", "'-1.25e-3'", "' +.5 '", "'1,000.0000'"]
)
def test_executor_preserves_precision_units_and_values(executor, value):
    expression = (
        "{'code': 0, 'msg': 'x', 'ExcelData': [{"
        "'indicator_name': '指标(单位)', 'indicator_data': " + value + ","
        "'indicator_unit': '-', 'end_date': ''}]}"
    )
    result = execute(executor, source_with_result(expression))
    assert result["ExcelData"][0]["indicator_data"] == eval(value)
    assert result["ExcelData"][0]["indicator_unit"] == "-"
    assert result["ExcelData"][0]["end_date"] == ""


def test_executor_strict_json_rejects_invalid_extra_fields(executor):
    source = SOURCE.replace("'code': 0", "'extra': float('nan'), 'code': 0")
    with pytest.raises(RuntimeError, match="严格 JSON"):
        execute(executor, source)


@pytest.mark.parametrize("date_text", ["2026-02-29", "2026-13-01", "not-date"])
def test_executor_rejects_invalid_date_without_guessing(executor, date_text):
    source = SOURCE.replace("'end_date': ''", "'end_date': " + repr(date_text))
    with pytest.raises(RuntimeError, match="有效的 ISO 日期"):
        execute(executor, source)


def test_executor_requires_all_four_fields_and_does_not_fill_them(executor):
    for key in ("indicator_name", "indicator_data", "indicator_unit", "end_date"):
        source = (
            "def main(params):\n"
            " record = {'indicator_name': 'x', 'indicator_data': 1, "
            "'indicator_unit': '-', 'end_date': ''}\n"
            " del record[%r]\n return {'code': 0, 'msg': 'x', 'ExcelData': [record]}" % key
        )
        with pytest.raises(RuntimeError, match=key):
            execute(executor, source)
