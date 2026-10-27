"""Offline protocol/queue/worker tests, with isolated temporary runtime and code roots."""

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from dc_agent.assets.layout import AssetLayout
from dc_agent.config import Settings
from dc_agent.domain import AgentError, Result, Task
from dc_agent.entrypoints.http import create_app
from dc_agent.integration.contracts import AGENT_FIELDS, ExcelJobRequest
from dc_agent.integration.jobs import JobStore
from dc_agent.integration.worker import run_once

TOKEN = "integration-test-token-at-least-24-chars"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PAYLOAD = {
    "fileMd5": "example-file",
    "pageTitle": "站点\\统计",
    "fileType": "xlsx",
    "title": "2026年9月统计",
    "publictime": "2026-10-01",
    "GZKJG": [{"rule_indicator_name": "地区生产总值", "rule_indicator_code": "123"}],
}


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, runtime_dir=tmp_path / "runtime", api_token=TOKEN)


def submit(store, **kwargs):
    return store.submit(
        ExcelJobRequest(**PAYLOAD).task_payload(),
        run_id=kwargs.get("run_id"),
        ttl=kwargs.get("ttl", 900),
        timeout=kwargs.get("timeout", 360),
    )


def ready_result(
    settings, row, *, raw=b"def main(params):\r\n    return {'code': 0, 'ExcelData': []}\r\n"
):
    task = Task.model_validate(row["payload"])
    layout = AssetLayout(task.pageTitle, task.fileType)
    version_path = (
        layout.directory / ".versions" / task.source_key / "fixed-version" / layout.filename
    ).as_posix()
    path = settings.resolved_code() / version_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    validation = settings.resolved_runtime() / "artifacts" / "validation.json"
    validation.parent.mkdir(parents=True, exist_ok=True)
    validation.write_text(
        json.dumps({"checks": [{"status": "warning", "message": "An unresolved source issue"}]}),
        encoding="utf-8",
    )
    report = settings.resolved_runtime() / "tasks" / "internal-task" / "quality_report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"message": "report", "redact_me": TOKEN}), encoding="utf-8")
    return {
        "status": "ready",
        "task_id": "internal-task",
        "cache_hit": True,
        "artifact": {
            "layout_version": 2,
            "content_root": "code",
            "source_key": task.source_key,
            "pageTitle": task.pageTitle,
            "fileType": task.fileType,
            "kind": "python_excel",
            "version": "fixed-version",
            "version_path": version_path,
            "content_path": "this-current-pointer-is-not-used.py",
            "content_hash": hashlib.sha256(raw).hexdigest(),
            "contract": "main-ExcelData-v1",
            "quality_status": "usable_with_warnings",
            "validation_path": str(validation),
        },
        "diagnostics": {"quality_report": str(report)},
    }


def test_health_auth_and_validation_do_not_echo_input(settings):
    with TestClient(create_app(settings, start_worker=False)) as client:
        assert client.get("/health").json() == {"status": "ok", "protocol_version": "3"}
        assert client.post("/v3/excel/jobs", json=PAYLOAD).status_code == 401
        assert (
            client.post(
                "/v3/excel/jobs", json=PAYLOAD, headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )
        for invalid in (
            {"source_path": "secret-local-path"},
            {"fileType": "md"},
            {"GZKJG": "[]"},
            {"pageTitle": " "},
        ):
            response = client.post("/v3/excel/jobs", json={**PAYLOAD, **invalid}, headers=AUTH)
            assert response.status_code == 422
            assert "secret-local-path" not in response.text
            assert TOKEN not in response.text
        assert client.get("/docs").status_code == 404


def test_concurrent_submit_has_one_durable_job_and_original_deadline(settings):
    store = JobStore(settings.resolved_runtime())
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: submit(store), range(16)))
    assert len({row["job_id"] for row in rows}) == 1
    assert len({row["deadline_at"] for row in rows}) == 1
    reopened = JobStore(settings.resolved_runtime())
    assert reopened.get(rows[0]["job_id"])["payload"] == rows[0]["payload"]
    assert "source_path" not in PAYLOAD
    assert rows[0]["payload"]["source_path"] is None


def test_explicit_run_identity_conflict_and_fingerprint_ttl(settings):
    store = JobStore(settings.resolved_runtime())
    explicit = submit(store, run_id="workflow-1")
    with store._connection() as db:
        db.execute("UPDATE jobs SET idempotent_until=0,status='failed'")
    assert submit(store, run_id="workflow-1")["job_id"] == explicit["job_id"]
    changed = ExcelJobRequest(**{**PAYLOAD, "title": "Changed title"}).task_payload()
    with pytest.raises(AgentError, match="different input"):
        store.submit(changed, run_id="workflow-1", ttl=900, timeout=360)
    first = submit(store)
    with store._connection() as db:
        db.execute(
            "UPDATE jobs SET idempotent_until=0,status='failed' WHERE job_id=?", (first["job_id"],)
        )
    assert submit(store)["job_id"] != first["job_id"]


def test_worker_passes_original_generation_deadline_and_closes_service(settings, monkeypatch):
    store = JobStore(settings.resolved_runtime())
    row = submit(store)
    with store._connection() as db:
        db.execute("UPDATE jobs SET created_at=created_at-45 WHERE job_id=?", (row["job_id"],))
    observed = {}

    class Service:
        def run(self, task, *, deadline):
            observed.update(task=task, remaining=deadline - time.monotonic())
            return Result(
                status="failed",
                task_id="internal",
                source_key=task.source_key,
                error={"code": "SYNTHETIC", "message": "must not echo " + TOKEN},
            )

        def close(self):
            observed["closed"] = True

    assert run_once(settings, store, service_factory=lambda _: Service())
    saved = store.get(row["job_id"])
    assert 250 < observed["remaining"] <= 255
    assert observed["task"].request_id == f"http-v3-{row['job_id']}"
    assert observed["task"].source_path is None
    assert observed["closed"]
    assert saved["status"] == "failed"
    with TestClient(create_app(settings, start_worker=False)) as client:
        item = client.get(f"/v3/excel/jobs/{row['job_id']}", headers=AUTH).json()["agent"][0]
        assert TOKEN not in json.dumps(item)
        assert item["error_code"] == "SYNTHETIC"
        assert not item["PythonCode"]


def test_busy_worker_defer_keeps_same_job_task_and_budget(settings):
    store = JobStore(settings.resolved_runtime())
    row = submit(store)
    seen = []

    class Service:
        def run(self, task, *, deadline):
            seen.append((task.request_id, deadline))
            return Result(
                status="processing" if len(seen) == 1 else "failed",
                task_id="internal",
                source_key=task.source_key,
            )

        def close(self):
            pass

    assert run_once(settings, store, service_factory=lambda _: Service())
    deferred = store.get(row["job_id"])
    assert deferred["stage"] == "waiting_for_asset"
    assert deferred["worker_token"] is None
    with store._connection() as db:
        db.execute("UPDATE jobs SET available_at=0 WHERE job_id=?", (row["job_id"],))
    assert run_once(settings, store, service_factory=lambda _: Service())
    assert seen[0][0] == seen[1][0]
    assert abs(seen[0][1] - seen[1][1]) < 0.1
    assert submit(store)["job_id"] == row["job_id"]


def test_late_completion_is_fenced_but_publication_reserve_is_allowed(settings):
    store = JobStore(settings.resolved_runtime())
    row = submit(store)
    claimed = store.claim("owner", 300)
    assert not store.finish(row["job_id"], "wrong-token", {"status": "ready"})
    with store._connection() as db:
        db.execute("UPDATE jobs SET generation_deadline=0 WHERE job_id=?", (row["job_id"],))
    assert store.finish(row["job_id"], claimed["worker_token"], {"status": "ready"})
    another = submit(store, run_id="another-run")
    claimed = store.claim("owner", 300)
    with store._connection() as db:
        db.execute("UPDATE jobs SET deadline_at=0 WHERE job_id=?", (another["job_id"],))
    assert not store.finish(another["job_id"], claimed["worker_token"], {"status": "ready"})
    assert store.get(another["job_id"])["error_code"] == "BUDGET_EXCEEDED"
    # Expiry keeps the ownership token until watchdog cleans the process tree.
    assert store.active("owner")[0]["job_id"] == another["job_id"]


def test_restart_interrupts_only_claimed_job_and_queued_budget_does_not_reset(settings):
    store = JobStore(settings.resolved_runtime())
    first = submit(store, run_id="first")
    store.claim("dead-worker", 300)
    second = submit(store, run_id="second")
    store.recover(code="SERVICE_RESTARTED")
    assert store.get(first["job_id"])["error_code"] == "SERVICE_RESTARTED"
    assert store.get(second["job_id"])["status"] == "queued"
    assert store.get(second["job_id"])["deadline_at"] == second["deadline_at"]
    assert not store.active("dead-worker")


def test_ready_uses_fixed_version_preserves_crlf_and_exact_14_string_fields(settings):
    app = create_app(settings, start_worker=False)
    store = app.state.store
    row = submit(store)
    claimed = store.claim("owner", 300)
    ready = ready_result(settings, row)
    assert store.finish(row["job_id"], claimed["worker_token"], ready)
    with TestClient(app) as client:
        response = client.get(
            f"/v3/excel/jobs/{row['job_id']}", headers={**AUTH, "Host": "evil.example"}
        )
        assert response.status_code == 200
        item = response.json()["agent"][0]
        assert set(item) == set(AGENT_FIELDS)
        assert all(isinstance(value, str) for value in item.values())
        assert "\r\n" in item["PythonCode"]
        assert hashlib.sha256(item["PythonCode"].encode("utf-8")).hexdigest() == item["code_sha256"]
        assert item["report_ref"].startswith("/v3/")
        assert "evil.example" not in item["report_ref"]
        assert item["PythonFile"] == "站点_统计.py"
        assert item["cache_hit"] == "true"
        assert "unresolved" in item["warning_summary"]


@pytest.mark.parametrize("corruption", ["hash", "path", "contract", "identity", "credential"])
def test_delivery_rejects_corrupt_or_wrong_identity_code(settings, corruption):
    app = create_app(settings, start_worker=False)
    store = app.state.store
    row = submit(store)
    claimed = store.claim("owner", 300)
    ready = ready_result(
        settings,
        row,
        raw=("# " + TOKEN).encode() if corruption == "credential" else b"def main(params): pass\n",
    )
    if corruption == "hash":
        ready["artifact"]["content_hash"] = "a" * 64
    elif corruption == "path":
        ready["artifact"]["version_path"] = "../../outside.py"
    elif corruption == "contract":
        ready["artifact"]["contract"] = "prompt-out_data-five-fields-v1"
    elif corruption == "identity":
        ready["artifact"]["pageTitle"] = "Different source"
    store.finish(row["job_id"], claimed["worker_token"], ready)
    with TestClient(app) as client:
        response = client.get(f"/v3/excel/jobs/{row['job_id']}", headers=AUTH)
        item = response.json()["agent"][0]
        assert item["status"] == "failed"
        assert item["error_code"] == "ARTIFACT_CORRUPT"
        assert item["PythonCode"] == ""
        assert TOKEN not in response.text


def test_report_whitelist_root_boundary_and_secret_redaction(settings):
    app = create_app(settings, start_worker=False)
    store = app.state.store
    row = submit(store)
    claimed = store.claim("owner", 300)
    ready = ready_result(settings, row)
    # Even a forged diagnostics entry cannot make an arbitrary external file readable.
    ready["diagnostics"]["quality_report_markdown"] = "../../quality_report.md"
    store.finish(row["job_id"], claimed["worker_token"], ready)
    base = f"/v3/excel/jobs/{row['job_id']}/reports/"
    with TestClient(app) as client:
        assert client.get(base + "quality_report.json").status_code == 401
        response = client.get(base + "quality_report.json", headers=AUTH)
        assert response.status_code == 200
        assert TOKEN not in response.text
        assert "[redacted]" in response.text
        for name in ("source.bin", "candidate-0.json", "quality_report.md"):
            assert client.get(base + name, headers=AUTH).status_code == 404


def test_background_completion_survives_submit_request_and_long_poll(settings):
    class OfflineWorker:
        def __init__(self, _settings, store):
            self.store, self.stop_event = store, threading.Event()
            self.thread = threading.Thread(target=self.loop)

        def loop(self):
            while not self.stop_event.wait(0.01):
                row = self.store.claim("offline-worker", 300)
                if row:
                    time.sleep(0.05)
                    self.store.finish(
                        row["job_id"], row["worker_token"], ready_result(settings, row)
                    )

        def start(self):
            self.thread.start()

        def stop(self):
            self.stop_event.set()
            self.thread.join(timeout=2)

        def is_alive(self):
            return self.thread.is_alive()

    with TestClient(create_app(settings, worker_factory=OfflineWorker)) as client:
        response = client.post("/v3/excel/jobs", json=PAYLOAD, headers=AUTH)
        job_id = response.json()["job_id"]
        assert response.status_code == 202
        retry = client.post("/v3/excel/jobs", json=PAYLOAD, headers=AUTH)
        assert retry.json()["job_id"] == job_id
        item = client.get(f"/v3/excel/jobs/{job_id}?wait_seconds=1", headers=AUTH).json()["agent"][
            0
        ]
        assert item["status"] == "ready"


def test_actual_spawn_worker_lifespan_starts_and_stops_without_model(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["status"] == "ok"
        supervisor = client.app.state.worker
        assert supervisor.process.pid
        process = supervisor.process
    assert not process.is_alive()


def test_short_token_is_rejected(settings):
    with pytest.raises(ValueError, match="24"):
        create_app(settings.model_copy(update={"api_token": settings.api_token.__class__("short")}))


def test_mixed_invalid_gzk_rows_are_filtered_and_json_array_string_is_supported(settings):
    app = create_app(settings, start_worker=False)
    mixed = PAYLOAD["GZKJG"] + [
        {"rule_indicator_name": ""},
        {"rule_indicator_name": None},
        {"rule_indicator_name": "empty"},
        None,
    ]
    with TestClient(app) as client:
        response = client.post(
            "/v3/excel/jobs", json={**PAYLOAD, "GZKJG": json.dumps(mixed)}, headers=AUTH
        )
        assert response.status_code == 202
        row = app.state.store.get(response.json()["job_id"])
        assert len(row["payload"]["GZKJG"]) == 1
        assert row["payload"]["invalid_rule_rows"] == 4
        all_invalid = client.post(
            "/v3/excel/jobs", json={**PAYLOAD, "GZKJG": mixed[1:]}, headers=AUTH
        )
        assert all_invalid.status_code == 422


def test_late_return_stops_worker_before_it_can_claim_the_next_job(settings):
    store = JobStore(settings.resolved_runtime())
    row = submit(store, run_id="first")
    second = submit(store, run_id="second")
    stop_after_job = threading.Event()

    class LateService:
        def run(self, task, *, deadline):
            with store._connection() as db:
                db.execute("UPDATE jobs SET deadline_at=0 WHERE job_id=?", (row["job_id"],))
            return Result(status="failed", task_id="internal", source_key=task.source_key)

        def close(self):
            pass

    # Simulate a task whose wall deadline passes while synchronous generation runs.
    # The production worker checks the captured deadline after cleanup, then exits.
    original = store.claim

    def claim(owner, timeout):
        claimed = original(owner, timeout)
        if claimed:
            claimed["deadline_at"] = 0
        return claimed

    store.claim = claim
    assert run_once(
        settings, store, service_factory=lambda _: LateService(), stop_after_job=stop_after_job
    )
    assert stop_after_job.is_set()
    assert store.get(row["job_id"])["status"] == "failed"
    assert store.get(second["job_id"])["status"] == "queued"
