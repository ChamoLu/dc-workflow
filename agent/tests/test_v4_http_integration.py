"""Offline V4 three-route HTTP/worker delivery; no model or company network calls."""

import copy
import hashlib
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from dc_agent.assets.layout import AssetLayout
from dc_agent.assets.store import ArtifactStore
from dc_agent.config import Settings
from dc_agent.domain import Result, Task
from dc_agent.entrypoints.http import create_app
from dc_agent.infrastructure.storage import Repository
from dc_agent.integration.contracts import (
    AGENT_FIELDS,
    V4_AGENT_FIELDS,
    ExcelJobRequest,
    HtmlJobRequest,
    MarkdownJobRequest,
)
from dc_agent.integration.worker import run_once

TOKEN = "v4-isolated-http-token-at-least-24-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
RULES = [{"rule_indicator_name": "工业总产值", "rule_indicator_code": "v4-01"}]
COMMON = {
    "pageTitle": "V4测试\\真实来源",
    "title": "2026年9月统计",
    "publictime": "2026-10-01",
    "GZKJG": RULES,
}
ROUTES = (
    ("excel", "csv", "fileMd5", "python_excel"),
    ("html", "html", "markdownMD5", "python_html"),
    ("markdown", "pdf", "markdownMD5", "markdown_prompt"),
)
PYTHON_BODY = (
    "# source-specific fixture\r\ndef main(params):\r\n    return {'code': 0, 'ExcelData': []}\r\n"
)
PROMPT_BODY = (
    "采集数据源：V4测试；规则来源：隔离测试。\r\n"
    "只从本次 Markdown 提取，按 GZK 精确筛选；输出 out_data，含 indicator_code、"
    "indicator_name、indicator_data、indicator_unit、end_date。\r\n"
)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        runtime_dir=tmp_path / "runtime",
        code_dir=tmp_path / "code",
        api_token=TOKEN,
    )


def payload(route, *, file_type=None, source_id="current-source"):
    _, extension, id_field, _ = next(item for item in ROUTES if item[0] == route)
    return {**COMMON, "fileType": file_type or extension, id_field: source_id}


def publish(settings, task, *, content=None, quality_status="accepted"):
    """Create a legally registered immutable asset through the production store."""
    repo = Repository(settings.resolved_runtime())
    assets = ArtifactStore(settings, repo)
    try:
        registered = repo.register(task)
        token = repo.claim(task.source_key, 60)
        assert token
        try:
            report = (
                settings.resolved_runtime() / "tasks" / registered["id"] / "quality_report.json"
            )
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                json.dumps({"message": "offline report", "secret": TOKEN}), encoding="utf-8"
            )
            artifact = assets.publish(
                task,
                token,
                content or (PROMPT_BODY if task.kind == "markdown_prompt" else PYTHON_BODY),
                {
                    "version": uuid4().hex,
                    "kind": task.kind,
                    "contract": "prompt-out_data-five-fields-v1"
                    if task.kind == "markdown_prompt"
                    else "main-ExcelData-v1",
                    "quality_status": quality_status,
                    "diagnostics": {
                        "quality_report": str(report.relative_to(settings.resolved_runtime()))
                    },
                },
            )
        finally:
            repo.release(task.source_key, token)
        return registered["id"], artifact
    finally:
        repo.close()


def finish_ready(settings, store, row, **kwargs):
    task = Task.model_validate({**row["payload"], "request_id": "fixture-" + row["job_id"]})
    task_id, artifact = publish(settings, task, **kwargs)
    claim = store.claim("v4-test", 300)
    assert claim["job_id"] == row["job_id"]
    result = Result(status="ready", task_id=task_id, source_key=task.source_key, artifact=artifact)
    assert store.finish(row["job_id"], claim["worker_token"], result.model_dump())
    return artifact


@pytest.mark.parametrize("route,extension,id_field,kind", ROUTES)
def test_three_routes_submit_queue_and_immutable_typed_delivery(
    settings, route, extension, id_field, kind
):
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        assert client.post(f"/v4/{route}/jobs", json=payload(route)).status_code == 401
        posted = client.post(f"/v4/{route}/jobs", json=payload(route), headers=AUTH)
        assert posted.status_code == 202
        job_id = posted.json()["job_id"]
        row = app.state.store.get(job_id)
        assert row["payload"][id_field] == "current-source"
        assert row["payload"]["source_path"] is None
        pending = client.get(f"/v4/jobs/{job_id}", headers=AUTH).json()["agent"][0]
        assert set(pending) == set(V4_AGENT_FIELDS)
        assert "artifact_kind" not in pending
        assert pending["status"] == "processing"
        assert not pending["PythonCode"] and not pending["PromptCode"]
        artifact = finish_ready(settings, app.state.store, row)
        # Current files are deliberately changed. Delivery must read the fixed version.
        current = settings.resolved_code() / AssetLayout(COMMON["pageTitle"], extension).current
        current.write_text("changed current pointer", encoding="utf-8")
        response = client.get(f"/v4/jobs/{job_id}", headers={**AUTH, "Host": "untrusted.example"})
        assert response.status_code == 200
        item = response.json()["agent"][0]
        assert set(item) == set(V4_AGENT_FIELDS)
        assert len(item) == 16 and all(isinstance(value, str) for value in item.values())
        assert item["status"] == "ready" and "artifact_kind" not in item
        body_field, file_field = (
            ("PromptCode", "PromptFile")
            if kind == "markdown_prompt"
            else ("PythonCode", "PythonFile")
        )
        other_body, other_file = (
            ("PythonCode", "PythonFile")
            if kind == "markdown_prompt"
            else ("PromptCode", "PromptFile")
        )
        assert item[body_field] == (PROMPT_BODY if kind == "markdown_prompt" else PYTHON_BODY)
        assert item[file_field] == "V4测试_真实来源" + (
            ".md" if kind == "markdown_prompt" else ".py"
        )
        assert not item[other_body] and not item[other_file]
        assert "\r\n" in item[body_field]
        assert (
            hashlib.sha256(item[body_field].encode("utf-8")).hexdigest()
            == artifact["content_hash"]
            == item["code_sha256"]
        )
        assert item["report_ref"].startswith(f"/v4/jobs/{job_id}/reports/")
        assert "untrusted.example" not in item["report_ref"]
        report = client.get(item["report_ref"], headers=AUTH)
        assert (
            report.status_code == 200 and TOKEN not in report.text and "[redacted]" in report.text
        )
        for name in ("source.bin", "candidate-0.json", "other.json"):
            assert client.get(f"/v4/jobs/{job_id}/reports/{name}", headers=AUTH).status_code == 404


@pytest.mark.parametrize("route,extension,id_field,kind", ROUTES)
def test_real_worker_new_job_and_registered_reuse_keep_route_source_identity(
    settings, route, extension, id_field, kind
):
    app = create_app(settings, start_worker=False)
    observed = []

    class ControlledService:
        """Controlled producer, using the real artifact registry and immutable publication."""

        def __init__(self, current_settings):
            self.repo = Repository(current_settings.resolved_runtime())
            self.assets = ArtifactStore(current_settings, self.repo)

        def run(self, task, *, deadline):
            observed.append((task.kind, task.source_id, task.parser_params(), deadline))
            registered = self.repo.register(task)
            artifact = self.assets.lookup(task)
            cache_hit = artifact is not None
            if artifact is None:
                _, artifact = publish(settings, task)
            return Result(
                status="ready",
                task_id=registered["id"],
                source_key=task.source_key,
                cache_hit=cache_hit,
                artifact=artifact,
            )

        def close(self):
            self.repo.close()

    with TestClient(app) as client:
        first = client.post(
            f"/v4/{route}/jobs", json=payload(route, source_id="source-first"), headers=AUTH
        ).json()["job_id"]
        assert run_once(settings, app.state.store, service_factory=ControlledService)
        first_item = client.get(f"/v4/jobs/{first}", headers=AUTH).json()["agent"][0]
        assert first_item["status"] == "ready" and first_item["cache_hit"] == "false"
        second = client.post(
            f"/v4/{route}/jobs", json=payload(route, source_id="source-second"), headers=AUTH
        ).json()["job_id"]
        assert second != first
        assert run_once(settings, app.state.store, service_factory=ControlledService)
        second_item = client.get(f"/v4/jobs/{second}", headers=AUTH).json()["agent"][0]
        assert second_item["status"] == "ready" and second_item["cache_hit"] == "true"
        assert first_item["artifact_version"] == second_item["artifact_version"]
        assert [(row[0], row[1]) for row in observed] == [
            (kind, "source-first"),
            (kind, "source-second"),
        ]
        assert observed[1][2][id_field] == "source-second"
        other = "markdownMD5" if id_field == "fileMd5" else "fileMd5"
        assert observed[1][2][other] is None


@pytest.mark.parametrize(
    "route,extensions",
    [
        ("excel", ("xls", "xlsx", "csv")),
        ("html", ("html", "htm", "shtml")),
        ("markdown", ("md", "markdown", "pdf", "png", "jpg", "jpeg", "doc", "docx")),
    ],
)
def test_declared_extensions_normalize_and_only_use_their_route_identifier(
    settings, route, extensions
):
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        for extension in extensions:
            response = client.post(
                f"/v4/{route}/jobs",
                json=payload(route, file_type=" ." + extension.upper() + " "),
                headers=AUTH,
            )
            assert response.status_code == 202
            row = app.state.store.get(response.json()["job_id"])
            assert row["payload"]["fileType"] == extension
        for extension in {"txt", "json", "exe", "csv" if route != "excel" else "pdf"}:
            assert (
                client.post(
                    f"/v4/{route}/jobs", json=payload(route, file_type=extension), headers=AUTH
                ).status_code
                == 422
            )


@pytest.mark.parametrize("route,extension,id_field,kind", ROUTES)
@pytest.mark.parametrize(
    "extra",
    [
        {"source_path": "private-local-path"},
        {"reference_paths": ["private-local-path"]},
        {"rebuild": True},
        {"retry": True},
        {"request_id": "private-local-path"},
        {"source_metadata": {"secret": "private-local-path"}},
    ],
)
def test_public_requests_forbid_development_controls_without_echoing_values(
    settings, route, extension, id_field, kind, extra
):
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        response = client.post(f"/v4/{route}/jobs", json={**payload(route), **extra}, headers=AUTH)
        assert response.status_code == 422
        assert "private-local-path" not in response.text
        with app.state.store._connection() as db:
            assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.parametrize("route,extension,id_field,kind", ROUTES)
@pytest.mark.parametrize(
    "rules", [[], None, "[]", [{"rule_indicator_name": "empty"}], [{"rule_indicator_name": None}]]
)
def test_no_valid_gzk_is_rejected_before_queueing(
    settings, route, extension, id_field, kind, rules
):
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        response = client.post(
            f"/v4/{route}/jobs", json={**payload(route), "GZKJG": rules}, headers=AUTH
        )
        assert response.status_code == 422 and response.json()["error_code"] == "without_GZKJG"
        with app.state.store._connection() as db:
            assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.parametrize("route,extension,id_field,kind", ROUTES)
def test_run_id_idempotency_conflict_and_legacy_cross_route_compatibility(
    settings, route, extension, id_field, kind
):
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        body = {**payload(route), "workflow_run_id": "stable-production-run"}
        first = client.post(f"/v4/{route}/jobs", json=body, headers=AUTH)
        again = client.post(f"/v4/{route}/jobs", json=body, headers=AUTH)
        job_id = first.json()["job_id"]
        assert again.json()["job_id"] == job_id
        conflict = client.post(
            f"/v4/{route}/jobs", json={**body, "title": "different"}, headers=AUTH
        )
        assert conflict.status_code == 409
        finish_ready(settings, app.state.store, app.state.store.get(job_id))
        legacy = client.get(f"/v3/excel/jobs/{job_id}", headers=AUTH)
        if route == "excel":
            assert legacy.status_code == 200 and set(legacy.json()["agent"][0]) == set(AGENT_FIELDS)
            assert client.post("/v3/excel/jobs", json=body, headers=AUTH).json()["job_id"] == job_id
        else:
            assert legacy.status_code == 404
            assert (
                client.get(
                    f"/v3/excel/jobs/{job_id}/reports/quality_report.json", headers=AUTH
                ).status_code
                == 404
            )
            assert app.state.store.get(job_id)["status"] == "ready"
            assert (
                client.get(f"/v4/jobs/{job_id}", headers=AUTH).json()["agent"][0]["status"]
                == "ready"
            )


@pytest.mark.parametrize("route,extension,id_field,kind", ROUTES)
@pytest.mark.parametrize(
    "corruption",
    [
        "kind",
        "contract",
        "source_key",
        "identity",
        "file_type",
        "version",
        "path",
        "hash",
        "quality",
        "credential",
    ],
)
def test_all_route_delivery_guards_preserve_fixed_source_version_and_contract(
    settings, route, extension, id_field, kind, corruption
):
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        job_id = client.post(f"/v4/{route}/jobs", json=payload(route), headers=AUTH).json()[
            "job_id"
        ]
        row = app.state.store.get(job_id)
        finish_ready(
            settings,
            app.state.store,
            row,
            content=("# " + TOKEN) if corruption == "credential" else None,
        )
        row = app.state.store.get(job_id)
        result = copy.deepcopy(row["result"])
        artifact = result["artifact"]
        if corruption == "kind":
            artifact["kind"] = "python_html" if kind != "python_html" else "python_excel"
        elif corruption == "contract":
            artifact["contract"] = (
                "main-ExcelData-v1"
                if kind == "markdown_prompt"
                else "prompt-out_data-five-fields-v1"
            )
        elif corruption == "source_key":
            artifact["source_key"] = "other-source"
        elif corruption == "identity":
            artifact["pageTitle"] = "other-page"
        elif corruption == "file_type":
            artifact["fileType"] = "xlsx" if extension != "xlsx" else "xls"
        elif corruption == "version":
            artifact["version"] = "other-version"
        elif corruption == "path":
            artifact["version_path"] = "../../outside"
        elif corruption == "hash":
            artifact["content_hash"] = "0" * 64
        elif corruption == "quality":
            artifact["quality_status"] = "rejected"
        with app.state.store._connection() as db:
            db.execute("UPDATE jobs SET result=? WHERE job_id=?", (json.dumps(result), job_id))
        response = client.get(f"/v4/jobs/{job_id}", headers=AUTH)
        item = response.json()["agent"][0]
        assert item["status"] == "failed" and item["stage"] == "delivery"
        assert "artifact_kind" not in item and item["error_code"] == "ARTIFACT_CORRUPT"
        assert not item["PythonCode"] and not item["PromptCode"]
        assert TOKEN not in response.text


def test_v4_health_and_v3_ready_jobs_can_be_delivered_by_v4(settings):
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok", "protocol_version": "3"}
        assert client.get("/v4/health").json() == {"status": "ok", "protocol_version": "4"}
        job_id = client.post("/v3/excel/jobs", json=payload("excel"), headers=AUTH).json()["job_id"]
        finish_ready(settings, app.state.store, app.state.store.get(job_id))
        item = client.get(f"/v4/jobs/{job_id}", headers=AUTH).json()["agent"][0]
        assert item["status"] == "ready" and "artifact_kind" not in item
        assert set(item) == set(V4_AGENT_FIELDS)


@pytest.mark.parametrize("route,extension,id_field,kind", ROUTES)
@pytest.mark.parametrize("status", ["failed", "review_required", "invalid_input"])
def test_terminal_jobs_omit_route_kind_and_do_not_expose_bodies(
    settings, route, extension, id_field, kind, status
):
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        job_id = client.post(f"/v4/{route}/jobs", json=payload(route), headers=AUTH).json()[
            "job_id"
        ]
        claim = app.state.store.claim("v4-failure", 300)
        app.state.store.finish(
            job_id,
            claim["worker_token"],
            {
                "status": status,
                "error": {
                    "code": "MODEL_STREAM_ERROR",
                    "stage": "build_design",
                    "message": "upstream unavailable",
                },
            },
        )
        item = client.get(f"/v4/jobs/{job_id}", headers=AUTH).json()["agent"][0]
        assert set(item) == set(V4_AGENT_FIELDS) and all(
            isinstance(value, str) for value in item.values()
        )
        assert len(item) == 16 and item["status"] == status
        assert "artifact_kind" not in item and item["stage"] == "build_design"
        assert (
            item["error_code"] == "MODEL_STREAM_ERROR" and item["message"] == "upstream unavailable"
        )
        assert not item["PythonCode"] and not item["PromptCode"]


@pytest.mark.parametrize("malformed", [[], "not an artifact", 23, True])
def test_malformed_ready_artifact_is_reported_as_delivery_failure(settings, malformed):
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        job_id = client.post("/v4/html/jobs", json=payload("html"), headers=AUTH).json()["job_id"]
        claim = app.state.store.claim("v4-malformed", 300)
        app.state.store.finish(
            job_id, claim["worker_token"], {"status": "ready", "artifact": malformed}
        )
        item = client.get(f"/v4/jobs/{job_id}", headers=AUTH).json()["agent"][0]
        assert item["status"] == "failed" and item["error_code"] == "ARTIFACT_CORRUPT"
        assert "artifact_kind" not in item


@pytest.mark.parametrize(
    "model,route",
    [(ExcelJobRequest, "excel"), (HtmlJobRequest, "html"), (MarkdownJobRequest, "markdown")],
)
def test_request_models_filter_invalid_rule_rows_and_accept_json_array_strings(model, route):
    body = {**payload(route), "GZKJG": json.dumps(RULES + [None, {"rule_indicator_name": "empty"}])}
    parsed = model(**body).task_payload()
    assert len(parsed["GZKJG"]) == 1 and parsed["invalid_rule_rows"] == 2
