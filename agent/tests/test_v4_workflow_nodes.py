"""Copyable V4 nodes: controlled HTTP/time plus real delivered Python, no live service."""

import hashlib
import json
import types
from pathlib import Path

import pytest
import requests

NODE_ROOT = Path(__file__).resolve().parents[1] / "workflow_agent_v4/工作流节点代码"
ACCESS_NAMES = (
    "01_Excel外部智能体接入_3708.py",
    "02_HTML外部智能体接入_763.py",
    "03_Markdown提示词外部智能体接入_2204.py",
)
EXECUTOR_NAMES = ("04_Excel平台执行器_4912.py", "05_HTML平台执行器_5242.py")
PLATFORM_FIELDS = (
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
    "PromptFile",
    "PromptCode",
)
PAGE_TITLE = "数据源\\统计数据"
JOB = "job-v4-contract-123"
TOKEN = "test-token-never-show-in-diagnostics"
SOURCE = "def main(params):\n    return {'code': 0, 'msg': 'ok', 'ExcelData': []}\n"
PROMPT = "完整专属提示词：必须读取本次 Markdown 并按真实表格和 GZK 提取。\n"


def load_node(name):
    path = NODE_ROOT / name
    module = types.ModuleType(path.stem)
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds <= 0.2
        self.now += seconds


class Response:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def transport(module, clock, monkeypatch, events):
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
            assert events, "Unexpected HTTP call; live network is prohibited"
            elapsed, result = events.pop(0)
            clock.now += elapsed
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(module.requests, "Session", Session)
    return calls


@pytest.fixture(params=ACCESS_NAMES, ids=("excel", "html", "markdown"))
def access(request, monkeypatch):
    module = load_node(request.param)
    monkeypatch.setattr(module, "API_BASE_URL", "http://example.invalid:8765")
    monkeypatch.setattr(module, "API_TOKEN", TOKEN)
    clock = Clock()
    monkeypatch.setattr(module, "time", clock)
    transport(module, clock, monkeypatch, [])
    return module, clock


@pytest.fixture(params=EXECUTOR_NAMES, ids=("excel", "html"))
def executor(request):
    return load_node(request.param)


def business(module, **changes):
    file_type = (
        "pdf"
        if getattr(module, "IS_PROMPT", False)
        else ("xlsx" if module.FILE_KEY == "fileMd5" else "html")
    )
    return {
        module.FILE_KEY: "different-source-md5-for-current-chain",
        "fileType": file_type,
        "pageTitle": PAGE_TITLE,
        "title": "2026年2月统计表",
        "publictime": "2026-03-10",
        "GZKJG": [{"rule_indicator_name": "真实指标", "rule_indicator_code": "123"}],
        **changes,
    }


def record(module, status="ready", source=None, page_title=PAGE_TITLE, **changes):
    fields = module.AGENT_FIELDS if hasattr(module, "AGENT_FIELDS") else module._AGENT_FIELDS
    value = dict.fromkeys(fields, "")
    value.update(
        status=status,
        stage="complete" if status == "ready" else "generate",
        job_id=JOB,
        elapsed_seconds="10.25",
    )
    if status == "ready":
        is_prompt = getattr(module, "IS_PROMPT", False)
        file_field, body_field = (
            ("PromptFile", "PromptCode") if is_prompt else ("PythonFile", "PythonCode")
        )
        body = source if source is not None else (PROMPT if is_prompt else SOURCE)
        value.update(
            **{
                file_field: page_title.replace("\\", "_") + (".md" if is_prompt else ".py"),
                body_field: body,
                "code_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "artifact_version": "20261027T000001Z-v4-fixture",
                "quality_status": "accepted",
                "cache_hit": "false",
            }
        )
    value.update(changes)
    return value


def wire(row):
    assert "artifact_kind" not in row
    return {"agent": [{**row, "report_ref": "/v4/jobs/" + JOB + "/reports/quality_report.json"}]}


def ready_events(row):
    return [
        (0.1, Response({"job_id": JOB, "status": "processing"}, 202)),
        (0.2, Response(wire(row))),
    ]


def diagnostic(call, prefix):
    with pytest.raises(RuntimeError) as caught:
        call()
    text = str(caught.value)
    assert text.startswith(prefix + ": ")
    assert len(text) < 2500
    assert TOKEN not in text
    value = json.loads(text.partition(": ")[2])
    assert set(value) >= {"status", "stage", "error_code", "message", "job_id"}
    assert all(
        isinstance(value[key], str)
        for key in ("status", "stage", "error_code", "message", "job_id")
    )
    return value


def test_each_route_posts_only_current_business_and_projects_fifteen_strings(access, monkeypatch):
    module, clock = access
    delivered = record(module)
    assert len(wire(delivered)["agent"][0]) == 16
    calls = transport(module, clock, monkeypatch, ready_events(delivered))
    params = business(module, workflow_run_id="stable-run-1")
    params["fileType"] = " ." + params["fileType"].upper() + " "
    params["GZKJG"] = json.dumps(params["GZKJG"])
    params.update(
        agent=[{"job_id": "stale-job"}], PythonCode="stale", PromptCode="stale", API_TOKEN="stale"
    )
    output = module.main(params)
    row = output["agent"][0]
    assert tuple(row) == module.AGENT_FIELDS
    assert len(row) == 15 and all(isinstance(value, str) for value in row.values())
    assert "artifact_kind" not in row
    assert row["job_id"] == JOB and "report_ref" not in row
    assert set(output) == ({"agent", "PromptFile", "PromptCode"} if module.IS_PROMPT else {"agent"})
    if module.IS_PROMPT:
        assert output["PromptCode"] == PROMPT and output["PromptFile"] == row["PromptFile"]
    assert calls[0][1].endswith(module.POST_PATH)
    assert calls[1][1].endswith("/v4/jobs/" + JOB)
    posted = calls[0][2]["json"]
    assert set(posted) == {
        module.FILE_KEY,
        "pageTitle",
        "fileType",
        "title",
        "publictime",
        "GZKJG",
        "workflow_run_id",
    }
    assert posted["workflow_run_id"] == "stable-run-1" and isinstance(posted["GZKJG"], list)
    if module.FILE_KEY == "markdownMD5":
        assert "fileMd5" not in posted
    for _, _, kwargs, headers, trust_env in calls:
        assert kwargs["allow_redirects"] is False and trust_env is False
        assert headers["Authorization"] == "Bearer " + TOKEN
        assert kwargs["timeout"][0] <= 5 and kwargs["timeout"][1] <= 22
        assert kwargs.get("params", {}).get("wait_seconds", 0) <= 20
    json.dumps(output, ensure_ascii=False, allow_nan=False)


@pytest.mark.parametrize("name", (*ACCESS_NAMES, *EXECUTOR_NAMES))
def test_five_copyable_nodes_have_exact_fifteen_field_contract_without_route_marker(name):
    module = load_node(name)
    fields = module.AGENT_FIELDS if hasattr(module, "AGENT_FIELDS") else module._AGENT_FIELDS
    assert fields == PLATFORM_FIELDS
    assert not hasattr(module, "ARTIFACT_KIND")


def test_processing_stale_bodies_never_flow_to_downstream(access, monkeypatch):
    module, clock = access
    processing = record(module, "processing", PythonCode="old code", PromptCode="old prompt")
    events = ready_events(processing)
    events.append((0.1, Response(wire(record(module)))))
    calls = transport(module, clock, monkeypatch, events)
    output = module.main(business(module))
    assert output["agent"][0]["status"] == "ready"
    assert "old" not in output["agent"][0]["PythonCode"] + output["agent"][0]["PromptCode"]
    assert [call[0] for call in calls] == ["POST", "GET", "GET"]


def test_pending_exception_contains_job_and_repeat_post_uses_identical_payload(access, monkeypatch):
    module, clock = access
    events = [
        (1, Response({"job_id": JOB, "status": "processing"}, 202)),
        (112, Response(wire(record(module, "processing")))),
        *ready_events(record(module)),
    ]
    calls = transport(module, clock, monkeypatch, events)
    params = business(module, workflow_run_id="stable-across-retry")
    pending = diagnostic(lambda: module.main(params), "DC_AGENT_PENDING")
    assert pending["job_id"] == JOB and pending["status"] == "processing"
    assert pending["error_code"] == "TASK_PENDING"
    assert module.main(params)["agent"][0]["status"] == "ready"
    assert calls[0][2]["json"] == calls[2][2]["json"]


@pytest.mark.parametrize(
    "rules", [None, [], [None, {"rule_indicator_name": "empty"}], [{"rule_indicator_name": " "}]]
)
def test_without_rules_throws_before_transport(access, rules):
    module, _ = access
    value = diagnostic(lambda: module.main(business(module, GZKJG=rules)), "DC_AGENT_ERROR")
    assert value["error_code"] == "without_GZKJG" and value["status"] == "invalid_input"


def test_mixed_gzk_rows_are_preserved_for_backend_audit(access, monkeypatch):
    module, clock = access
    rules = [
        None,
        4,
        "noise",
        {},
        {"rule_indicator_name": "真实指标", "rule_indicator_code": "123"},
    ]
    calls = transport(module, clock, monkeypatch, ready_events(record(module)))
    module.main(business(module, GZKJG=rules))
    assert calls[0][2]["json"]["GZKJG"] == rules


@pytest.mark.parametrize(
    "change", ["missing_md5", "wrong_type", "nan_rules", "bad_rules_json", "blank_run_id"]
)
def test_invalid_inputs_do_not_connect(access, change):
    module, _ = access
    params = business(module)
    if change == "missing_md5":
        params.pop(module.FILE_KEY)
        params["fileMd5" if module.FILE_KEY == "markdownMD5" else "markdownMD5"] = "no-fallback"
    elif change == "wrong_type":
        params["fileType"] = "txt"
    elif change == "nan_rules":
        params["GZKJG"][0]["amount"] = float("nan")
    elif change == "bad_rules_json":
        params["GZKJG"] = "not-json " + TOKEN
    else:
        params["workflow_run_id"] = " "
    value = diagnostic(lambda: module.main(params), "DC_AGENT_ERROR")
    assert value["stage"].startswith("preflight_")


@pytest.mark.parametrize(
    "change",
    [
        "wrong_job",
        "wrong_extension",
        "mixed_artifact",
        "no_hash",
        "tampered_hash",
        "no_version",
        "bad_quality",
        "no_cache",
        "bad_filename",
        "source_mismatch",
        "non_string",
        "missing_wire_field",
        "infinite_elapsed",
        "surrogate",
    ],
)
def test_unusable_ready_delivery_never_returns_normally(access, monkeypatch, change):
    module, clock = access
    row = record(module)
    prompt = module.IS_PROMPT
    body_field = "PromptCode" if prompt else "PythonCode"
    file_field = "PromptFile" if prompt else "PythonFile"
    if change == "wrong_job":
        row["job_id"] = "foreign-job"
    elif change == "wrong_extension":
        row[file_field] = row[file_field][:-3] + ".txt"
    elif change == "mixed_artifact":
        row["PythonCode" if prompt else "PromptCode"] = "wrong body"
    elif change == "no_hash":
        row["code_sha256"] = ""
    elif change == "tampered_hash":
        row[body_field] += "changed"
    elif change == "no_version":
        row["artifact_version"] = " "
    elif change == "bad_quality":
        row["quality_status"] = "rejected"
    elif change == "no_cache":
        row["cache_hit"] = ""
    elif change == "bad_filename":
        row[file_field] = "../unsafe" + (".md" if prompt else ".py")
    elif change == "source_mismatch":
        row[file_field] = "other_source" + (".md" if prompt else ".py")
    elif change == "non_string":
        row["message"] = 123
    elif change == "missing_wire_field":
        row.pop("PromptFile")
    elif change == "infinite_elapsed":
        row["elapsed_seconds"] = "inf"
    else:
        row[body_field] = "\ud800"
    transport(module, clock, monkeypatch, ready_events(row))
    value = diagnostic(lambda: module.main(business(module)), "DC_AGENT_ERROR")
    assert value["job_id"] == JOB and value["stage"] == "delivery_check"
    if change == "source_mismatch":
        assert value["error_code"] == "ARTIFACT_SOURCE_MISMATCH"
    elif change == "mixed_artifact":
        assert value["error_code"] == "ARTIFACT_BODY_CONFLICT"


@pytest.mark.parametrize("status", ["failed", "review_required", "invalid_input"])
def test_terminal_service_errors_are_bounded_and_redacted(access, monkeypatch, status):
    module, clock = access
    row = record(
        module,
        status,
        stage="semantic_review",
        error_code="END_DATE_EMPTY",
        message=TOKEN + SOURCE * 500,
    )
    transport(module, clock, monkeypatch, ready_events(row))
    value = diagnostic(lambda: module.main(business(module)), "DC_AGENT_ERROR")
    assert value["status"] == status and value["stage"] == "semantic_review"
    assert value["error_code"] == "END_DATE_EMPTY" and SOURCE not in value["message"]


def test_specific_public_failure_reason_survives_with_credentials_redacted(access, monkeypatch):
    module, clock = access
    note = (
        "Model stream error: provider=DashScope; code=invalid_parameter; message=模型流提前终止；Authorization: Bearer other-secret; api_key=sk-mock-secret-1234; token="
        + TOKEN
        + "; https://secret.example/path?token=hidden"
    )
    row = record(module, "failed", stage="generate", error_code="MODEL_STREAM_ERROR", message=note)
    transport(module, clock, monkeypatch, ready_events(row))
    value = diagnostic(lambda: module.main(business(module)), "DC_AGENT_ERROR")
    assert value["error_code"] == "MODEL_STREAM_ERROR"
    assert "模型流提前终止" in value["message"] and "invalid_parameter" in value["message"]
    assert "other-secret" not in value["message"] and "sk-mock-secret-1234" not in value["message"]
    assert "secret.example" not in value["message"]


def test_already_redacted_service_reason_survives_second_boundary(access, monkeypatch):
    module, clock = access
    note = "Model stream error; token=[redacted]; api_key=[redacted]; endpoint=[URL omitted]; message=具体提供方拒绝原因"
    row = record(module, "failed", error_code="MODEL_STREAM_ERROR", message=note)
    transport(module, clock, monkeypatch, ready_events(row))
    value = diagnostic(lambda: module.main(business(module)), "DC_AGENT_ERROR")
    assert "具体提供方拒绝原因" in value["message"]
    assert "[redacted]]" not in value["message"]


def test_ready_notes_are_scrubbed_while_artifact_bytes_and_hash_stay_unchanged(access, monkeypatch):
    module, clock = access
    row = record(
        module,
        message="已交付；Authorization: Bearer secret-ready; api_key=sk-mock-secret-ready; token="
        + TOKEN,
        warning_summary="诊断 https://private.example/report；secret='secret-warning'; "
        + "说明" * 3000,
    )
    transport(module, clock, monkeypatch, ready_events(row))
    output = module.main(business(module))["agent"][0]
    encoded = json.dumps(output, ensure_ascii=False)
    assert (
        TOKEN not in encoded and "secret-ready" not in encoded and "secret-warning" not in encoded
    )
    assert "private.example" not in encoded and len(output["warning_summary"]) <= 2000
    assert output["PythonCode"] == row["PythonCode"] and output["PromptCode"] == row["PromptCode"]
    assert output["code_sha256"] == row["code_sha256"]


def test_artifact_containing_current_api_token_is_rejected_without_rewriting_source(
    access, monkeypatch
):
    module, clock = access
    row = record(module, source=SOURCE + "# " + TOKEN)
    transport(module, clock, monkeypatch, ready_events(row))
    value = diagnostic(lambda: module.main(business(module)), "DC_AGENT_ERROR")
    assert value["error_code"] == "CREDENTIAL_IN_ARTIFACT"


@pytest.mark.parametrize("status", [302, 401, 403, 500])
def test_http_failures_do_not_follow_redirects_or_expose_body(access, monkeypatch, status):
    module, clock = access
    calls = transport(module, clock, monkeypatch, [(0.1, Response({"message": TOKEN}, status))])
    value = diagnostic(lambda: module.main(business(module)), "DC_AGENT_ERROR")
    assert value["error_code"] == "HTTP_ERROR" and len(calls) == 1


def test_http_422_uses_only_whitelisted_schema_fields(access, monkeypatch):
    module, clock = access
    body = {
        "issues": [
            {"field": module.FILE_KEY, "error_type": "missing", "input": TOKEN},
            {"field": "GZKJG[0].rule_indicator_name", "error_type": {"secret": TOKEN}},
            {"field": TOKEN, "error_type": "value_error"},
        ],
        "message": TOKEN,
    }
    transport(module, clock, monkeypatch, [(0.1, Response(body, 422))])
    value = diagnostic(lambda: module.main(business(module)), "DC_AGENT_ERROR")
    assert value["issues"] == [{"field": module.FILE_KEY, "error_type": "missing"}]


def test_http_422_without_gzk_retains_specific_error_code(access, monkeypatch):
    module, clock = access
    body = {"error_code": "without_GZKJG", "issues": [], "message": TOKEN}
    transport(module, clock, monkeypatch, [(0.1, Response(body, 422))])
    value = diagnostic(lambda: module.main(business(module)), "DC_AGENT_ERROR")
    assert value["error_code"] == "without_GZKJG"


def test_connection_failure_is_retryable_by_stable_repost_without_exception_text(
    access, monkeypatch
):
    module, clock = access
    transport(module, clock, monkeypatch, [(0.1, requests.Timeout(TOKEN))])
    value = diagnostic(lambda: module.main(business(module)), "DC_AGENT_ERROR")
    assert value["error_code"] == "HTTP_CONNECTION_FAILED" and value["stage"] == "http_submit"


@pytest.mark.parametrize(
    "bad_token", [None, 4, "", "REPLACE_WITH_DC_AGENT_API_TOKEN", "header\ninjection"]
)
def test_bad_token_is_diagnosed_without_connecting(access, monkeypatch, bad_token):
    module, _ = access
    monkeypatch.setattr(module, "API_TOKEN", bad_token)
    value = diagnostic(lambda: module.main(business(module)), "DC_AGENT_ERROR")
    assert value["error_code"] == "CONFIGURATION_ERROR"


def result_source(data="0", date="2024-02-29", **changes):
    row = {
        "indicator_name": "真实指标(万元)",
        "indicator_data": data,
        "indicator_unit": "万元",
        "end_date": date,
        **changes,
    }
    return (
        "def main(params):\n    return " + repr({"code": 0, "msg": "ok", "ExcelData": [row]}) + "\n"
    )


def run_executor(module, source, **changes):
    delivered = record(module, source=source)
    assert "artifact_kind" not in delivered and len(delivered) == 15
    return module.main({**business(module), "agent": [delivered], **changes})


@pytest.mark.parametrize(
    "data", [0, 0.0, "0", "0.0000000000000000012300", "1,234.567890000", "-2.4e-30"]
)
def test_real_parser_preserves_zero_precision_units_and_date(executor, data):
    result = run_executor(executor, result_source(data))
    row = result["ExcelData"][0]
    assert row["indicator_data"] == data and type(row["indicator_data"]) is type(data)
    assert row["indicator_name"] == "真实指标(万元)" and row["indicator_unit"] == "万元"
    assert row["end_date"] == "2024-02-29"
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_parser_only_receives_current_five_business_fields_and_separate_namespace(executor):
    expected = {executor.FILE_KEY, "title", "publictime", "fileType", "pageTitle"}
    source = (
        "canary = 'isolated'\ndef main(params):\n    assert set(params) == " + repr(expected) + "\n"
    )
    source += (
        "    assert params["
        + repr(executor.FILE_KEY)
        + "] == 'different-source-md5-for-current-chain'\n"
    )
    source += (
        "    return "
        + repr(
            {
                "code": 0,
                "msg": "ok",
                "ExcelData": [
                    {
                        "indicator_name": "真实指标",
                        "indicator_data": "0",
                        "indicator_unit": "-",
                        "end_date": "",
                    }
                ],
            }
        )
        + "\n"
    )
    result = run_executor(
        executor,
        source,
        PythonCode="raise RuntimeError('stale')",
        PythonFile="stale.py",
        PromptCode="stale",
        API_TOKEN=TOKEN,
        GZK={"secret": TOKEN},
    )
    assert result["ExcelData"][0]["end_date"] == ""
    assert not hasattr(executor, "canary")


@pytest.mark.parametrize("date", ["2026-02-30", "20260228", "2026-2-28", "2026-W09-6", None])
def test_invalid_dates_fail_without_repair_or_current_date(executor, date):
    value = diagnostic(
        lambda: run_executor(executor, result_source(date=date)), "DC_AGENT_EXECUTION_ERROR"
    )
    assert value["stage"] == "platform_result_validate"


@pytest.mark.parametrize(
    "expression",
    ["True", "None", "float('nan')", "float('inf')", "'-'", "''", "'NaN'", "'Infinity'"],
)
def test_invalid_numeric_outputs_fail_without_fabricated_records(executor, expression):
    source = result_source().replace("'indicator_data': '0'", "'indicator_data': " + expression)
    value = diagnostic(lambda: run_executor(executor, source), "DC_AGENT_EXECUTION_ERROR")
    assert value["error_code"] == "INVALID_PARSER_RESULT"


@pytest.mark.parametrize(
    "change",
    [
        "processing",
        "prompt_only_delivery",
        "bad_hash",
        "mixed_prompt",
        "source_mismatch",
        "no_version",
        "missing_field",
        "bad_md5",
        "bad_type",
    ],
)
def test_executor_rejects_unready_cross_route_stale_or_unbound_input(executor, change):
    params = {**business(executor), "agent": [record(executor, source=result_source())]}
    row = params["agent"][0]
    if change == "processing":
        row["status"] = "processing"
    elif change == "prompt_only_delivery":
        row["PythonFile"] = row["PythonCode"] = ""
        row["PromptFile"] = PAGE_TITLE.replace("\\", "_") + ".md"
        row["PromptCode"] = PROMPT
    elif change == "bad_hash":
        row["PythonCode"] += "# changed"
    elif change == "mixed_prompt":
        row["PromptCode"] = "wrong asset"
    elif change == "source_mismatch":
        params["pageTitle"] = "another_source"
    elif change == "no_version":
        row["artifact_version"] = ""
    elif change == "missing_field":
        row.pop("elapsed_seconds")
    elif change == "bad_md5":
        params.pop(executor.FILE_KEY)
        params["fileMd5" if executor.FILE_KEY == "markdownMD5" else "markdownMD5"] = (
            "forbidden-fallback"
        )
    else:
        params["fileType"] = "txt"
    value = diagnostic(lambda: executor.main(params), "DC_AGENT_EXECUTION_ERROR")
    assert value["stage"] == "platform_preflight"
    if change == "source_mismatch":
        assert value["error_code"] == "ARTIFACT_SOURCE_MISMATCH"
    elif change in {"prompt_only_delivery", "mixed_prompt"}:
        assert value["error_code"] == "ARTIFACT_BODY_CONFLICT"


@pytest.mark.parametrize(
    "source,stage,line",
    [
        ("def main(:\n    pass\n", "platform_compile", 1),
        ("raise RuntimeError(" + repr(TOKEN) + ")\n", "platform_initialize", 1),
        ("def main(params):\n    raise ValueError(" + repr(TOKEN) + ")\n", "platform_execute", 2),
        ("def main(params):\n    raise SystemExit(" + repr(TOKEN) + ")\n", "platform_execute", 2),
    ],
)
def test_execution_diagnostics_pin_file_phase_and_line_without_source_or_exception_body(
    executor, source, stage, line
):
    value = diagnostic(lambda: run_executor(executor, source), "DC_AGENT_EXECUTION_ERROR")
    assert value["stage"] == stage and value["artifact_line"] == line
    assert value["artifact_file"] == PAGE_TITLE.replace("\\", "_") + ".py"


@pytest.mark.parametrize(
    "result",
    [
        {"code": 0, "msg": "ok", "OUTDATA": []},
        {"code": 0, "msg": "ok", "ExcelData": []},
        {"code": False, "msg": "ok", "ExcelData": [{}]},
        {"code": -1, "msg": TOKEN, "ExcelData": [{}]},
        {"code": 0, "ExcelData": [{}]},
        {"code": 0, "msg": "ok", "ExcelData": [{"indicator_name": "name"}]},
        [],
    ],
)
def test_broken_parser_contract_never_returns_successful_empty_output(executor, result):
    source = "def main(params):\n    return " + repr(result) + "\n"
    value = diagnostic(lambda: run_executor(executor, source), "DC_AGENT_EXECUTION_ERROR")
    assert value["stage"] == "platform_result_validate"


@pytest.mark.parametrize("bytes_length", [241, 255])
def test_portable_filename_byte_limit_accepts_legal_long_names_on_access(
    access, monkeypatch, bytes_length
):
    module, clock = access
    page_title = "a" * (bytes_length - 3)
    transport(module, clock, monkeypatch, ready_events(record(module, page_title=page_title)))
    assert module.main(business(module, pageTitle=page_title))["agent"][0]["status"] == "ready"


@pytest.mark.parametrize("bytes_length", [241, 255])
def test_portable_filename_byte_limit_accepts_legal_long_names_on_executor(executor, bytes_length):
    page_title = "a" * (bytes_length - 3)
    params = {
        **business(executor, pageTitle=page_title),
        "agent": [record(executor, source=result_source(), page_title=page_title)],
    }
    assert executor.main(params)["ExcelData"][0]["indicator_data"] == "0"


def test_utf8_multibyte_filename_over_255_bytes_is_rejected(access, monkeypatch):
    module, clock = access
    page_title = "中" * 85
    transport(module, clock, monkeypatch, ready_events(record(module, page_title=page_title)))
    value = diagnostic(
        lambda: module.main(business(module, pageTitle=page_title)), "DC_AGENT_ERROR"
    )
    assert value["error_code"] == "ARTIFACT_INVALID"
