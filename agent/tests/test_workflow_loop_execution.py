"""Standalone nodes reuse only the existing agent object and business output arrays."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

NODE_DIR = (
    Path(__file__).resolve().parents[1]
    / "workflow_agent_test_v3"
    / "工作流节点代码"
    / "循环接入方案"
)
SOURCE = """import datetime
def main(params):
    assert set(params) == {'fileMd5', 'title', 'publictime', 'fileType', 'pageTitle'}
    return {'code': 0, 'msg': 'success', 'ExcelData': [
        {'indicator_name': '实际指标(万元)', 'indicator_data': 0,
         'indicator_unit': '万元', 'end_date': ''}]}
"""
AGENT_FIELDS = {
    "status",
    "stage",
    "job_id",
    "PythonFile",
    "PythonCode",
    "code_sha256",
    "artifact_version",
    "cache_hit",
    "quality_status",
    "warning_summary",
    "error_code",
    "message",
    "elapsed_seconds",
}
CODE_FIELDS = {"PythonFile", "PythonCode", "code_sha256", "artifact_version"}


def load_node(filename, name):
    spec = importlib.util.spec_from_file_location(name, NODE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def executor():
    return load_node("04_动态代码执行器_4912_诊断版.py", "loop_executor")


@pytest.fixture
def formatter():
    return load_node("05_错误信息整理.py", "loop_error_formatter")


def ready(source=SOURCE, **changes):
    record = {
        "status": "ready",
        "stage": "completed",
        "job_id": "job-1",
        "PythonFile": "实际数据源_指标.py",
        "PythonCode": source,
        "code_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "artifact_version": "version-1",
        "cache_hit": "false",
        "quality_status": "accepted",
        "warning_summary": "",
        "error_code": "",
        "message": "完成",
        "elapsed_seconds": "2.5",
    }
    record.update(changes)
    return record


def inputs(**changes):
    payload = {
        "fileMd5": "actual-file-id",
        "pageTitle": r"实际数据源\统计数据",
        "fileType": "xls",
        "title": "2026年9月统计月报",
        "publictime": "",
        "agent": [ready()],
        "GZKJG": [{"rule_indicator_name": "不可传入解析器"}],
        "API_TOKEN": "do-not-forward",
        "count": 1,
    }
    payload.update(changes)
    return payload


def execute(executor, source=SOURCE, **changes):
    return executor.main(inputs(agent=[ready(source, **changes)]))


def returned_source(expression):
    return "def main(params):\n return " + expression


def assert_shape(result, output_name):
    assert set(result) == {"agent", output_name}
    assert isinstance(result["agent"], list) and len(result["agent"]) == 1
    row = result["agent"][0]
    assert set(row) == AGENT_FIELDS
    assert all(isinstance(value, str) for value in row.values())
    assert len(row["message"]) <= 2000
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    return row


def assert_cleared(row):
    assert all(row[key] == "" for key in CODE_FIELDS)


def test_executor_really_executes_isolated_whitelist_and_preserves_zero(executor):
    original_main = executor.main
    marker = object()
    executor.datetime = marker
    payload = inputs()
    before = copy.deepcopy(payload)
    result = executor.main(payload)
    row = assert_shape(result, "ExcelData")
    assert row["status"] == "ready" and row["stage"] == "platform_execute_complete"
    assert row["error_code"] == "" and "1 条" in row["message"] and "尚未完成" in row["message"]
    assert result["ExcelData"][0]["indicator_data"] == 0
    assert result["ExcelData"][0]["end_date"] == ""
    assert all(row[key] == before["agent"][0][key] for key in CODE_FIELDS)
    assert executor.datetime is marker and executor.main is original_main
    assert payload == before


@pytest.mark.parametrize("status", ["processing", "failed", "review_required", "invalid_input"])
def test_executor_nonready_never_compiles_and_keeps_original_reason(executor, monkeypatch, status):
    def prohibited(*args, **kwargs):
        raise AssertionError("compile must not run")

    monkeypatch.setattr("builtins.compile", prohibited)
    result = execute(
        executor,
        "deliberately invalid!",
        status=status,
        stage="build_design",
        error_code="MODEL_STREAM_ERROR",
        message="模型接口返回错误。",
    )
    row = assert_shape(result, "ExcelData")
    assert result["ExcelData"] == [] and row["status"] == status
    assert row["error_code"] == "MODEL_STREAM_ERROR" and row["stage"] == "build_design"
    assert row["message"] == "模型接口返回错误。" and row["job_id"] == "job-1"
    assert_cleared(row)


@pytest.mark.parametrize(
    "agent", [None, {}, [], [True], [ready(), ready()], [ready(cache_hit=[])], [ready(stage={})]]
)
def test_executor_shape_errors_return_diagnostic(executor, agent):
    result = executor.main(inputs(agent=agent))
    row = assert_shape(result, "ExcelData")
    assert row["status"] == "invalid_input" and result["ExcelData"] == []
    assert row["error_code"] == "DC_AGENT_PROTOCOL_ERROR"
    assert_cleared(row)


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"status": "mystery"}, "DC_AGENT_PROTOCOL_ERROR"),
        ({"code_sha256": "0" * 64}, "DC_AGENT_CODE_HASH_MISMATCH"),
        ({"artifact_version": ""}, "DC_AGENT_DELIVERY_INVALID"),
        ({"PythonFile": "../bad.py"}, "DC_AGENT_DELIVERY_INVALID"),
        ({"quality_status": "rejected"}, "DC_AGENT_DELIVERY_INVALID"),
        ({"cache_hit": True}, "DC_AGENT_PROTOCOL_ERROR"),
        ({"extra_secret": "never-output"}, "DC_AGENT_PROTOCOL_ERROR"),
        ({"error_code": "MODEL_STREAM_ERROR"}, "DC_AGENT_DELIVERY_INVALID"),
    ],
)
def test_executor_delivery_validation(executor, changes, code):
    result = execute(executor, **changes)
    row = assert_shape(result, "ExcelData")
    assert row["error_code"] == code and row["stage"] == "platform_delivery"
    assert result["ExcelData"] == [] and "never-output" not in json.dumps(result)
    assert_cleared(row)


@pytest.mark.parametrize(
    "source,stage,kind",
    [
        ("def main(: pass", "platform_compile", "SyntaxError"),
        ("raise ValueError('password=DO_NOT_EXPOSE')", "platform_load", "ValueError"),
        (
            "def main(params):\n raise RuntimeError('secret=DO_NOT_EXPOSE')",
            "platform_execute",
            "RuntimeError",
        ),
        (
            "def main(params):\n raise SystemExit('Bearer DO_NOT_EXPOSE')",
            "platform_execute",
            "SystemExit",
        ),
    ],
)
def test_executor_safe_exception_type_stage_and_line(executor, source, stage, kind):
    result = execute(executor, source)
    row = assert_shape(result, "ExcelData")
    assert row["stage"] == stage and kind in row["message"]
    assert "实际数据源_指标.py" in row["message"] and "行" in row["message"]
    assert "DO_NOT_EXPOSE" not in json.dumps(result) and source not in json.dumps(result)
    assert "按 job_id 查看服务日志" in row["message"]
    assert_cleared(row)


@pytest.mark.parametrize("source", ["value = 1", "main = 1"])
def test_executor_missing_main_is_structured(executor, source):
    row = assert_shape(execute(executor, source), "ExcelData")
    assert row["error_code"] == "PARSER_MAIN_MISSING" and row["stage"] == "platform_load"


def test_executor_preserves_nonzero_parser_code_and_safe_message(executor):
    result = execute(
        executor,
        returned_source("{'code': 73, 'msg': 'download failed; password=abc123', 'ExcelData': []}"),
    )
    row = assert_shape(result, "ExcelData")
    assert (
        row["error_code"] == "PARSER_RETURNED_ERROR"
        and row["stage"] == "platform_result_validation"
    )
    assert "parser_code=73" in row["message"] and "download failed" in row["message"]
    assert "abc123" not in json.dumps(result)
    assert_cleared(row)


@pytest.mark.parametrize(
    "expression,code",
    [
        ("None", "PARSER_RESULT_CONTRACT_ERROR"),
        ("{'code': False, 'msg': 'x', 'ExcelData': []}", "PARSER_RESULT_CONTRACT_ERROR"),
        ("{'code': '0', 'msg': 'x', 'ExcelData': []}", "PARSER_RESULT_CONTRACT_ERROR"),
        ("{'code': 0, 'msg': 1, 'ExcelData': []}", "PARSER_RESULT_CONTRACT_ERROR"),
        ("{'code': 0, 'msg': 'x', 'ExcelData': []}", "EMPTY_EXTRACTION"),
        ("{'code': 0, 'msg': 'x', 'ExcelData': [{}]}", "PARSER_RESULT_CONTRACT_ERROR"),
    ],
)
def test_executor_invalid_returns(executor, expression, code):
    result = execute(executor, returned_source(expression))
    row = assert_shape(result, "ExcelData")
    assert row["error_code"] == code and result["ExcelData"] == []
    assert row["stage"] == "platform_result_validation"
    assert_cleared(row)
    if code == "EMPTY_EXTRACTION":
        assert row["status"] == "review_required"


@pytest.mark.parametrize(
    "value", ["False", "None", "float('nan')", "float('inf')", "'NaN'", "'-'", "''", "{}"]
)
def test_executor_rejects_invalid_values_without_rewriting(executor, value):
    row = execute(executor, SOURCE.replace("'indicator_data': 0", "'indicator_data': " + value))[
        "agent"
    ][0]
    assert (
        row["error_code"] == "PARSER_RESULT_CONTRACT_ERROR" and "indicator_data" in row["message"]
    )


@pytest.mark.parametrize(
    "value", ["0", "0.0", "'0.000000000000000000'", "'-1.25e-3'", "'1,000.0000'"]
)
def test_executor_preserves_numerical_representation(executor, value):
    result = execute(executor, SOURCE.replace("'indicator_data': 0", "'indicator_data': " + value))
    assert result["ExcelData"][0]["indicator_data"] == eval(value)
    assert result["ExcelData"][0]["indicator_unit"] == "万元"


@pytest.mark.parametrize(
    "date", ["2026-02-29", "2026-13-01", "20260930", "2026-W40-3", None, False]
)
def test_executor_date_is_not_guessed(executor, date):
    result = execute(executor, SOURCE.replace("'end_date': ''", "'end_date': " + repr(date)))
    assert (
        result["agent"][0]["error_code"] == "PARSER_RESULT_CONTRACT_ERROR"
        and result["ExcelData"] == []
    )


def test_executor_rejects_nonstrict_extra_json_field(executor):
    source = SOURCE.replace("'code': 0", "'extra': float('nan'), 'code': 0")
    assert execute(executor, source)["agent"][0]["error_code"] == "PARSER_RESULT_CONTRACT_ERROR"


@pytest.mark.parametrize(
    "changes",
    [
        {"job_id": "job/1"},
        {"job_id": "job:1"},
        {"job_id": "j" * 129},
        {"artifact_version": "version.1"},
        {"artifact_version": "version/1"},
        {"artifact_version": "v" * 201},
    ],
)
def test_executor_identity_and_version_prevent_execution(executor, monkeypatch, changes):
    def prohibited(*args, **kwargs):
        raise AssertionError("compile must not run")

    monkeypatch.setattr("builtins.compile", prohibited)
    result = execute(executor, **changes)
    row = assert_shape(result, "ExcelData")
    assert row["error_code"] == "DC_AGENT_DELIVERY_INVALID" and result["ExcelData"] == []


@pytest.mark.parametrize(
    "key,value",
    [("fileMd5", ""), ("pageTitle", ""), ("title", None), ("publictime", 123), ("fileType", [])],
)
def test_executor_rejects_invalid_business_fields(executor, key, value):
    row = assert_shape(executor.main(inputs(**{key: value})), "ExcelData")
    assert row["error_code"] == "DC_AGENT_INPUT_ERROR" and row["status"] == "invalid_input"
    assert_cleared(row)


@pytest.mark.parametrize("status", ["failed", "review_required", "invalid_input"])
def test_error_formatter_preserves_root_cause_and_clears_stale_data(formatter, status):
    record = ready(
        status=status,
        stage="build_design",
        error_code="MODEL_STREAM_ERROR",
        message="模型接口返回错误。",
    )
    before = copy.deepcopy(record)
    result = formatter.main(
        {
            "agent": [record],
            "count": "invalid counter must not overwrite cause",
            "out_data": [{"old": "stale"}],
        }
    )
    row = assert_shape(result, "out_data")
    assert result["out_data"] == [] and row["status"] == status
    assert row["error_code"] == "MODEL_STREAM_ERROR" and row["stage"] == "build_design"
    assert row["message"] == "模型接口返回错误。" and record == before
    assert_cleared(row)


@pytest.mark.parametrize("count", [0, 1, 3, 10])
def test_error_formatter_processing_means_wait_exhausted_not_backend_failed(formatter, count):
    result = formatter.main(
        {"agent": [ready(status="processing", stage="generate_code")], "count": count}
    )
    row = assert_shape(result, "out_data")
    assert row["error_code"] == "WORKFLOW_WAIT_TIMEOUT" and row["stage"] == "workflow_wait"
    assert (
        row["status"] == "failed"
        and "processing" in row["message"]
        and "%d 轮" % count in row["message"]
    )
    assert "不代表后台任务已失败" in row["message"]
    assert_cleared(row)


@pytest.mark.parametrize("count", [True, "1", -1, None, 1.5])
def test_error_formatter_wait_requires_integer_counter(formatter, count):
    result = formatter.main({"agent": [ready(status="processing")], "count": count})
    row = assert_shape(result, "out_data")
    assert row["error_code"] == "WORKFLOW_COUNTER_INVALID"


def test_error_formatter_ready_only_means_original_4589_empty_branch(formatter):
    result = formatter.main(
        {"agent": [ready(stage="platform_execute_complete")], "count": 1, "out_data": []}
    )
    row = assert_shape(result, "out_data")
    assert row["status"] == "review_required" and row["stage"] == "workflow_postprocess"
    assert row["error_code"] == "OUT_DATA_EMPTY" and result["out_data"] == []
    assert "4589" in row["message"] and "不补造数据" in row["message"]
    assert_cleared(row)


@pytest.mark.parametrize(
    "changes", [{"job_id": ""}, {"job_id": "bad/id"}, {"error_code": "MODEL_STREAM_ERROR"}]
)
def test_error_formatter_processing_requires_valid_identity_and_no_error(formatter, changes):
    result = formatter.main({"agent": [ready(status="processing", **changes)], "count": 3})
    row = assert_shape(result, "out_data")
    assert row["error_code"] == "DC_AGENT_PROTOCOL_ERROR"
    assert "正常等待超时" in row["message"]


@pytest.mark.parametrize(
    "rows",
    [
        None,
        [],
        {},
        [True],
        [{}],
        [ready(), ready()],
        [ready(status="mystery")],
        [ready(PythonCode=123)],
        [ready(extra="hidden")],
    ],
)
def test_error_formatter_malformed_agent_returns_protocol_diagnostic(formatter, rows):
    result = formatter.main({"agent": rows, "count": 1})
    row = assert_shape(result, "out_data")
    assert result["out_data"] == [] and row["error_code"] == "DC_AGENT_PROTOCOL_ERROR"
    assert_cleared(row)


def test_long_valid_identity_and_version_survive_success(executor):
    job_id, version = "j" * 128, "v" * 200
    row = assert_shape(execute(executor, job_id=job_id, artifact_version=version), "ExcelData")
    assert row["status"] == "ready" and row["job_id"] == job_id
    assert row["artifact_version"] == version and len(row) == 13


def test_metadata_is_bounded_sanitized_and_no_new_top_level_fields(executor, formatter):
    message = (
        "Authorization: Bearer HIDDEN_TOKEN; password=HIDDEN_PASSWORD; https://example.com/?token=HIDDEN_URL; "
        + "X" * 4000
    )
    record = ready(
        status="failed",
        stage="build_design",
        error_code="MODEL_STREAM_ERROR",
        message=message,
        warning_summary="api_key=HIDDEN_KEY",
    )
    execution = executor.main(inputs(agent=[record]))
    final = formatter.main({"agent": [record], "count": 3})
    for result, output_name in ((execution, "ExcelData"), (final, "out_data")):
        row = assert_shape(result, output_name)
        assert "HIDDEN_" not in json.dumps(result) and len(row["message"]) <= 2000
        assert_cleared(row)


@pytest.mark.parametrize("node_name", ["executor", "formatter"])
def test_nondict_params_return_existing_contract_only(request, node_name):
    node = request.getfixturevalue(node_name)
    result = node.main(None)
    row = assert_shape(result, "ExcelData" if node_name == "executor" else "out_data")
    assert row["status"] == "invalid_input"
