"""Real loopback HTTP + Windows spawn worker + both platform scripts.

Uses a small pre-registered transport fixture, with no model configuration and no
company-network calls. This verifies transport and process assembly, not business
extraction correctness. All runtime/code data stays under pytest's temporary root.
"""

import importlib.util
import multiprocessing
import socket
import threading
import time
from pathlib import Path

import requests
import uvicorn

from dc_agent.assets.store import ArtifactStore
from dc_agent.config import Settings
from dc_agent.domain import Task
from dc_agent.entrypoints.http import create_app
from dc_agent.infrastructure.storage import Repository
from dc_agent.integration.worker import terminate_worker_tree

TOKEN = "live-smoke-token-with-no-production-credentials"
NODES = Path(__file__).resolve().parents[1] / "workflow_agent_test_v3" / "工作流节点代码"
CODE = (
    "# Transport fixture only\r\n"
    "def main(params):\r\n"
    "    return {'code': 0, 'msg': 'success', 'ExcelData': [{\r\n"
    "        'indicator_name': params['pageTitle'], 'indicator_data': '0.0000',\r\n"
    "        'indicator_unit': '-', 'end_date': ''}]}\r\n"
)


def _serve(settings, sock, stop):
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level="error"))

    def stop_later():
        stop.wait()
        server.should_exit = True

    threading.Thread(target=stop_later, daemon=True).start()
    server.run(sockets=[sock])


def _node(filename):
    spec = importlib.util.spec_from_file_location("live_node", NODES / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_http_spawn_worker_and_workflow_scripts(tmp_path):
    settings = Settings(
        _env_file=None,
        runtime_dir=tmp_path / "runtime",
        api_token=TOKEN,
        model_base_url="",
        model_name="",
        model_api_key="",
        api_worker_poll_interval=0.05,
    )
    task = Task(
        pageTitle="本机HTTP传输测试",
        fileType="xlsx",
        fileMd5="transport-only",
        title="本次输入标题",
        publictime="",
        GZKJG=[{"rule_indicator_name": "fixture"}],
    )
    repo = Repository(settings.resolved_runtime())
    token = repo.claim(task.source_key, 60)
    try:
        ArtifactStore(settings, repo).publish(
            task,
            token,
            CODE,
            {
                "version": "loopback-fixture",
                "kind": "python_excel",
                "contract": "main-ExcelData-v1",
                "quality_status": "accepted",
            },
        )
    finally:
        repo.release(task.source_key, token)
        repo.close()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    context = multiprocessing.get_context("spawn")
    stop = context.Event()
    process = context.Process(target=_serve, args=(settings, sock, stop))
    process.start()
    try:
        deadline = time.monotonic() + 20
        with requests.Session() as session:
            session.trust_env = False
            while True:
                try:
                    response = session.get(base + "/health", timeout=0.3)
                    if response.status_code == 200 and response.json()["status"] == "ok":
                        break
                except requests.RequestException:
                    pass
                assert time.monotonic() < deadline, "Loopback API/worker startup failed"
                time.sleep(0.05)
            assert session.post(base + "/v3/excel/jobs", json={}).status_code == 401
        access = _node("外部框架接入.py")
        executor = _node("动态代码执行器_4912.py")
        access.API_BASE_URL, access.API_TOKEN = base, TOKEN
        access.POLL_SECONDS = 0.1
        business = {
            key: getattr(task, key)
            for key in ("fileMd5", "pageTitle", "fileType", "title", "publictime")
        }
        request = {**business, "GZKJG": task.model_dump()["GZKJG"]}
        delivered = access.main(request)
        item = delivered["agent"][0]
        assert item["status"] == "ready" and item["cache_hit"] == "true"
        assert item["PythonCode"] == CODE  # CRLF and Unicode survive real HTTP untouched.
        assert len(item) == 14 and all(isinstance(value, str) for value in item.values())
        assert access.main(request)["agent"][0]["job_id"] == item["job_id"]
        result = executor.main({**business, **delivered})
        assert result == {
            "code": 0,
            "msg": "success",
            "ExcelData": [
                {
                    "indicator_name": task.pageTitle,
                    "indicator_data": "0.0000",
                    "indicator_unit": "-",
                    "end_date": "",
                }
            ],
        }
    finally:
        stop.set()
        process.join(timeout=20)
        if process.is_alive():
            terminate_worker_tree(process)
        sock.close()
    assert process.exitcode == 0
