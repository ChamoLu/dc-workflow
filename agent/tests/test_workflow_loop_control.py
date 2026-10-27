"""Minimal loop uses only the existing agent and one new integer count."""

import copy
import hashlib
import importlib.util
from pathlib import Path

import pytest

NODES = (
    Path(__file__).resolve().parents[1]
    / "workflow_agent_test_v3"
    / "工作流节点代码"
    / "循环接入方案"
)


def load(name):
    spec = importlib.util.spec_from_file_location("minimal_" + name, NODES / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_initializer_only_returns_count():
    init = load("01_循环初始化")
    result = init.main(
        {
            "count": 99,
            "agent": [{"PythonCode": "stale"}],
            "out_data": [{"old": 1}],
            "title": "本次实际标题",
        }
    )
    assert result == {"count": 0}


def test_counter_returns_only_incremented_count():
    counter = load("03_计数自增")
    count = 0
    for expected in (1, 2, 3):
        result = counter.main({"count": count})
        assert result == {"count": expected}
        count = result["count"]


@pytest.mark.parametrize("value", [True, False, -1, "0", 0.0, None, [], {}])
def test_counter_rejects_misconfigured_integer(value):
    with pytest.raises(ValueError, match="count必须为非负整型"):
        load("03_计数自增").main({"count": value})


def record(status, **changes):
    result = dict.fromkeys(load("02_外部框架接入_循环版").AGENT_FIELDS, "")
    result.update(status=status, job_id="job-1", stage="running", elapsed_seconds="105.00")
    result.update(changes)
    return result


def test_three_rounds_same_job_last_ready_enters_actual_executor(monkeypatch):
    init = load("01_循环初始化")
    access = load("02_外部框架接入_循环版")
    counter = load("03_计数自增")
    executor = load("04_动态代码执行器_4912_诊断版")
    access.API_TOKEN = "offline-token"
    source = (
        "def main(params):\n"
        " assert set(params) == {'fileMd5','pageTitle','title','fileType','publictime'}\n"
        " return {'code':0,'msg':'ok','ExcelData':[{'indicator_name':'实际指标',"
        "'indicator_data':'1.2500','indicator_unit':'万元','end_date':'2026-09-30'}]}\n"
    )
    processing = record("processing")
    ready = record(
        "ready",
        PythonFile="parser.py",
        PythonCode=source,
        code_sha256=hashlib.sha256(source.encode()).hexdigest(),
        artifact_version="version-1",
        quality_status="accepted",
        cache_hit="false",
    )
    requests = []

    class Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(access, "time", clock)

    class Session:
        def __init__(self):
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, url, **kwargs):
            requests.append((method, url, kwargs))
            if method == "POST":
                body = {"job_id": "job-1", "status": "processing"}
            elif len([r for r in requests if r[0] == "GET"]) <= 2:
                clock.now += access.ATTEMPT_SECONDS
                body = {"agent": [processing]}
            else:
                body = {"agent": [ready]}

            class Response:
                status_code = 200

                def json(self):
                    return copy.deepcopy(body)

            return Response()

    monkeypatch.setattr(access.requests, "Session", Session)
    business = {
        "fileMd5": "actual-file",
        "pageTitle": "实际来源",
        "fileType": "xlsx",
        "title": "2026年9月月报",
        "publictime": "",
        "GZKJG": [{"rule_indicator_name": "实际指标", "rule_indicator_code": "001"}],
    }
    state = init.main({})
    for expected_count in (1, 2, 3):
        state.update(access.main({**business, "agent": state.get("agent")}))
        state.update(counter.main({"count": state["count"]}))
        assert state["count"] == expected_count
        if state["agent"][0]["status"] == "ready":
            break
        assert state["agent"][0]["status"] == "processing" and state["count"] < 3
    assert state["count"] == 3 and state["agent"][0]["status"] == "ready"
    assert len([r for r in requests if r[0] == "POST"]) == 1
    assert len({r[1] for r in requests if r[0] == "GET"}) == 1
    result = executor.main({**business, "agent": state["agent"]})
    assert set(result) == {"ExcelData", "agent"}
    assert result["agent"][0]["status"] == "ready"
    assert result["agent"][0]["stage"] == "platform_execute_complete"
    assert result["ExcelData"][0]["indicator_data"] == "1.2500"


def test_processing_limit_exits_with_existing_agent_fields_only():
    counter = load("03_计数自增")
    errors = load("05_错误信息整理")
    count = 0
    for _ in range(3):
        count = counter.main({"count": count})["count"]
    result = errors.main({"count": count, "agent": [record("processing")]})
    assert set(result) == {"agent", "out_data"}
    assert result["out_data"] == []
    assert result["agent"][0]["error_code"] == "WORKFLOW_WAIT_TIMEOUT"
    assert result["agent"][0]["stage"] == "workflow_wait"
    assert "processing" in result["agent"][0]["message"]


def test_no_rules_exits_without_network_and_keeps_original_code(monkeypatch):
    init = load("01_循环初始化")
    access = load("02_外部框架接入_循环版")
    errors = load("05_错误信息整理")

    def no_network():
        raise AssertionError("must not create a network session")

    monkeypatch.setattr(access.requests, "Session", no_network)
    state = init.main({})
    state.update(
        access.main(
            {
                "fileMd5": "actual",
                "pageTitle": "actual",
                "title": "",
                "fileType": "xlsx",
                "publictime": "",
                "GZKJG": [],
                "agent": state.get("agent"),
            }
        )
    )
    result = errors.main({"agent": state["agent"], "count": 1})
    assert result["agent"][0]["error_code"] == "without_GZKJG"
    assert result["agent"][0]["job_id"] == ""
    assert result["out_data"] == []
