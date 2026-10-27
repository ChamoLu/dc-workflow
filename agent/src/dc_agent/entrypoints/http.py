"""Authenticated HTTP adapter. Requests never execute generation synchronously."""

import asyncio
import hmac
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError

from ..config import Settings
from ..domain import AgentError
from ..integration.contracts import (
    EXCEL_TYPES,
    TERMINAL,
    ExcelJobRequest,
    HtmlJobRequest,
    MarkdownJobRequest,
)
from ..integration.delivery import REPORT_NAMES, public_result, report_paths, scrub
from ..integration.input_errors import validation_diagnostic
from ..integration.jobs import JobStore
from ..integration.worker import WorkerSupervisor


def create_app(
    settings: Settings | None = None, *, worker_factory=None, start_worker=True
) -> FastAPI:
    settings = settings or Settings()
    token = settings.api_token.get_secret_value()
    if len(token.strip()) < 24:
        raise ValueError("DC_API_TOKEN must contain at least 24 characters")
    store = JobStore(settings.resolved_runtime())
    worker_factory = worker_factory or WorkerSupervisor

    @asynccontextmanager
    async def lifespan(app):
        worker = worker_factory(settings, store) if start_worker else None
        app.state.worker = worker
        if worker:
            worker.start()
        try:
            yield
        finally:
            if worker:
                worker.stop()

    app = FastAPI(
        title="DC Agent V4",
        version="4",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store = store
    app.state.settings = settings
    bearer = HTTPBearer(auto_error=False)

    def authenticated(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None or not hmac.compare_digest(
            credentials.credentials.encode("utf-8"), token.encode("utf-8")
        ):
            raise HTTPException(
                401, "Invalid integration credential", headers={"WWW-Authenticate": "Bearer"}
            )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, error):
        # FastAPI's default includes raw rejected input; do not echo a token or arbitrary path.
        return JSONResponse(
            status_code=422,
            content=validation_diagnostic(settings, "request_schema", error.errors()),
        )

    @app.get("/health")
    def health():
        worker = getattr(app.state, "worker", None)
        return {
            "status": "ok" if (worker is None or worker.is_alive()) else "degraded",
            "protocol_version": "3",
        }

    @app.get("/v4/health")
    def health_v4():
        return {**health(), "protocol_version": "4"}

    def submit_job(request):
        try:
            payload = request.task_payload()
            row = store.submit(
                payload,
                run_id=request.workflow_run_id,
                ttl=settings.api_idempotency_ttl,
                timeout=settings.api_job_timeout,
            )
        except AgentError as exc:
            raise HTTPException(
                409,
                {"error_code": exc.code, "message": "Workflow run already uses different input"},
            ) from None
        except ValidationError as exc:
            return JSONResponse(
                status_code=422,
                content=validation_diagnostic(settings, "task_contract", exc.errors()),
            )
        except ValueError:
            return JSONResponse(
                status_code=422,
                content=validation_diagnostic(
                    settings, "task_contract", [{"loc": ("body",), "type": "value_error"}]
                ),
            )
        return {
            "job_id": row["job_id"],
            "status": "processing" if row["status"] == "queued" else row["status"],
        }

    @app.post("/v3/excel/jobs", status_code=202, dependencies=[Depends(authenticated)])
    def submit(request: ExcelJobRequest):
        return submit_job(request)

    @app.post("/v4/excel/jobs", status_code=202, dependencies=[Depends(authenticated)])
    def submit_excel_v4(request: ExcelJobRequest):
        return submit_job(request)

    @app.post("/v4/html/jobs", status_code=202, dependencies=[Depends(authenticated)])
    def submit_html_v4(request: HtmlJobRequest):
        return submit_job(request)

    @app.post("/v4/markdown/jobs", status_code=202, dependencies=[Depends(authenticated)])
    def submit_markdown_v4(request: MarkdownJobRequest):
        return submit_job(request)

    def find_job(job_id, *, excel_only=False):
        row = store.get(job_id)
        if row is None:
            raise HTTPException(404, "Integration job not found")
        # V3 shares the durable store with V4.  A V3 URL must never invalidate a
        # healthy HTML/prompt job by trying to deliver it as an Excel parser.
        if excel_only and row["payload"].get("fileType") not in EXCEL_TYPES:
            raise HTTPException(404, "Integration job is not available on this route")
        return row

    async def query_job(job_id, wait_seconds, *, protocol_version):
        waited = 0.0
        limit = min(wait_seconds, settings.api_poll_max_wait)
        row = find_job(job_id, excel_only=protocol_version == 3)
        while row["status"] not in TERMINAL and waited < limit:
            delay = min(0.25, limit - waited)
            await asyncio.sleep(delay)
            waited += delay
            row = find_job(job_id, excel_only=protocol_version == 3)
        try:
            return public_result(settings, row, protocol_version=protocol_version)
        except (AgentError, OSError, ValueError, KeyError, TypeError):
            store.invalidate(job_id, "ARTIFACT_CORRUPT")
            return public_result(settings, find_job(job_id), protocol_version=protocol_version)

    @app.get("/v3/excel/jobs/{job_id}", dependencies=[Depends(authenticated)])
    async def query(job_id: str, wait_seconds: float = Query(default=0, ge=0, le=60)):
        return await query_job(job_id, wait_seconds, protocol_version=3)

    @app.get("/v4/jobs/{job_id}", dependencies=[Depends(authenticated)])
    async def query_v4(job_id: str, wait_seconds: float = Query(default=0, ge=0, le=60)):
        return await query_job(job_id, wait_seconds, protocol_version=4)

    def serve_report(job_id, report_name, *, excel_only=False):
        row = find_job(job_id, excel_only=excel_only)
        if report_name not in REPORT_NAMES:
            raise HTTPException(404, "Quality report not found")
        path = report_paths(settings, row).get(report_name)
        if path is None:
            raise HTTPException(404, "Quality report not found")
        try:
            if path.stat().st_size > 10_000_000:
                raise HTTPException(413, "Quality report exceeds response size limit")
            raw = path.read_bytes()
            if len(raw) > 10_000_000:
                raise HTTPException(413, "Quality report exceeds response size limit")
            text = scrub(raw.decode("utf-8"), settings)
        except (OSError, UnicodeError):
            raise HTTPException(404, "Quality report is unavailable") from None
        return Response(
            content=text,
            media_type="application/json" if report_name.endswith(".json") else "text/markdown",
        )

    @app.get("/v3/excel/jobs/{job_id}/reports/{report_name}", dependencies=[Depends(authenticated)])
    def report(job_id: str, report_name: str):
        return serve_report(job_id, report_name, excel_only=True)

    @app.get("/v4/jobs/{job_id}/reports/{report_name}", dependencies=[Depends(authenticated)])
    def report_v4(job_id: str, report_name: str):
        return serve_report(job_id, report_name)

    return app
