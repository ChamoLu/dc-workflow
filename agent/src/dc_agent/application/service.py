"""Asset lookup and task lifecycle; generation and source preflight are separate services."""

import json
import math
import time
import traceback
from pathlib import Path
from uuid import uuid4

from ..assets.store import ArtifactStore
from ..config import Settings
from ..domain import AgentError, Result, Task, digest
from ..generation import SkillRegistry
from ..infrastructure.files import write_json
from ..infrastructure.storage import Repository
from ..orchestration.workflow import GenerationWorkflow, load_saved_candidate
from ..ports import (
    ExecutionRunner,
    ModelGateway,
    ReferenceProvider,
    RuleContextProvider,
    SourceProvider,
)
from ..references import WorkbookRuleContext
from .diagnostics import diagnostic_paths, record_diagnostics
from .preflight import ContentPreflight, inspect_source


class AssetService:
    def __init__(
        self,
        settings: Settings,
        model: ModelGateway,
        source: SourceProvider,
        references: ReferenceProvider,
        runner: ExecutionRunner,
        skills: SkillRegistry | None = None,
        rule_context: RuleContextProvider | None = None,
    ):
        self.settings, self.model, self.source = settings, model, source
        self.references, self.runner = references, runner
        self.rule_context = rule_context or WorkbookRuleContext(settings)
        self.skills = skills or SkillRegistry(settings.skill_root)
        self.root = settings.resolved_runtime()
        self.repo = Repository(self.root)
        self.assets = ArtifactStore(settings, self.repo)
        self.preflight = ContentPreflight(model, settings)
        self.workflow = GenerationWorkflow(
            settings, model, runner, self.skills, self.repo, self.assets
        )

    def _prepare(self, task: Task, workspace: Path, deadline: float, *, saved=None, task_id=None):
        if time.monotonic() >= deadline:
            raise AgentError("BUDGET_EXCEEDED", "Task deadline exceeded")
        if task_id:
            self.repo.event(task_id, "inspect_source")
        raw = self.source.read(task)
        inspection = inspect_source(task, raw)
        if saved and inspection["sha256"] != saved[3]:
            raise AgentError(
                "RESUME_SOURCE_CHANGED", "Source bytes changed; retry without --resume-candidate"
            )
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "source.bin").write_bytes(raw)
        write_json(workspace / "inspection.json", inspection)
        if task_id:
            self.repo.event(task_id, "preflight")
        report = self.preflight.check(task, inspection, workspace, deadline)
        return {"inspection": inspection, "preflight": report}

    @staticmethod
    def _error(exc, stage):
        raw = str(getattr(exc, "code", "INTERNAL_ERROR"))
        code = (
            raw
            if raw
            in {"FilenoData", "todoFile_no_pageTitle", "pageTitle_redundancy", "without_GZKJG"}
            else raw.upper()
        )
        message = (
            str(exc)
            if isinstance(exc, (AgentError, ValueError)) or hasattr(exc, "code")
            else "Unexpected internal error; inspect local diagnostics"
        )
        return {
            "code": code,
            "message": message,
            "retryable": bool(getattr(exc, "retryable", False)),
            "stage": getattr(exc, "stage", None) or stage,
            **getattr(exc, "details", {}),
        }

    @staticmethod
    def _preflight_report(workspace: Path) -> dict | None:
        path = workspace / "preflight.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def check_content(self, task: Task) -> dict:
        """Explicit diagnostic command: model preflight only, with no asset generation."""
        task_id = str(uuid4())
        workspace = self.root / "preflights" / task_id
        workspace.mkdir(parents=True)
        try:
            prepared = self._prepare(task, workspace, time.monotonic() + self.settings.task_timeout)
            result = {
                "status": "preflight_passed",
                "task_id": task_id,
                "preflight": prepared["preflight"],
            }
        except Exception as exc:
            result = {
                "status": "failed",
                "task_id": task_id,
                "error": self._error(exc, "preflight"),
                "preflight": self._preflight_report(workspace),
            }
        write_json(workspace / "result.json", result)
        return result

    def run(
        self,
        task: Task,
        *,
        retry: bool = False,
        rebuild: bool = False,
        resume_candidate: bool = False,
        deadline: float | None = None,
    ) -> Result:
        if deadline is not None:
            if isinstance(deadline, bool) or not math.isfinite(deadline):
                raise AgentError("INVALID_INPUT", "Deadline must be a finite monotonic timestamp")
            deadline = min(deadline, time.monotonic() + self.settings.task_timeout)
        if resume_candidate and not retry:
            raise AgentError("INVALID_INPUT", "Resuming a saved candidate requires retry=True")
        row = self.repo.register(task)
        task_id, token = row["id"], None
        workspace = self.root / "tasks" / task_id
        workspace.mkdir(parents=True, exist_ok=True)
        result = None
        adapter_deadlines = []
        if deadline is not None:
            for adapter in (self.source, self.runner):
                if hasattr(adapter, "deadline"):
                    adapter_deadlines.append((adapter, adapter.deadline))
                    adapter.deadline = deadline
        try:
            if deadline is not None and time.monotonic() >= deadline:
                raise AgentError("BUDGET_EXCEEDED", "Task deadline exceeded before asset lookup")
            if row["result"] and row["result"]["status"] != "processing" and not retry:
                old = Result.model_validate(row["result"])
                if old.artifact:
                    current = self.repo.current(task.source_key)
                    if current and current["version"] == old.artifact["version"]:
                        old.artifact = self.assets.lookup(task)
                    else:
                        self.assets.verify_historical(task, old.artifact)
                        if old.artifact.get("layout_version") == 2:
                            old.artifact = {
                                **old.artifact,
                                "content_path": old.artifact["version_path"],
                                "historical": True,
                            }
                return old
            cached = None if rebuild else self.assets.lookup(task)
            if cached:
                result = Result(
                    status="ready",
                    task_id=task_id,
                    source_key=task.source_key,
                    cache_hit=True,
                    artifact=cached,
                    preflight={"status": "not_run", "reason": "asset_cache_hit"},
                    diagnostics=cached.get("diagnostics"),
                )
            else:
                self.assets.ensure_available(task)
                lease_seconds = (
                    self.settings.task_timeout
                    if deadline is None
                    else max(0, deadline - time.monotonic())
                )
                token = self.repo.claim(task.source_key, lease_seconds + 60)
                if not token:
                    # Do not replace a running owner's status during a concurrent idempotent call.
                    return Result(status="processing", task_id=task_id, source_key=task.source_key)
                cached = None if rebuild else self.assets.lookup(task, token)
                if cached:
                    result = Result(
                        status="ready",
                        task_id=task_id,
                        source_key=task.source_key,
                        cache_hit=True,
                        artifact=cached,
                        preflight={"status": "not_run", "reason": "asset_cache_hit"},
                        diagnostics=cached.get("diagnostics"),
                    )
                else:
                    artifact = self._generate(
                        task, task_id, workspace, token, resume_candidate, deadline=deadline
                    )
                    result = Result(
                        status="ready",
                        task_id=task_id,
                        source_key=task.source_key,
                        artifact=artifact,
                        preflight=self._preflight_report(workspace),
                        diagnostics=artifact.get("diagnostics"),
                    )
        except Exception as exc:
            error = self._error(exc, self.repo.last_stage(task_id) or "resolve_asset")
            error.update(diagnostic_paths(self.root, workspace))
            write_json(
                workspace / "failure.json",
                {
                    "exception_type": type(exc).__name__,
                    "error": error,
                    "frames": [
                        {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                        for frame in traceback.extract_tb(exc.__traceback__)
                    ],
                },
            )
            status = (
                "processing"
                if error["code"] == "ASSET_BUSY"
                else "review_required"
                if error["code"] in {"VALIDATION_FAILED", "EMPTY_EXTRACTION", "END_DATE_EMPTY"}
                else "failed"
            )
            result = Result(
                status=status,
                task_id=task_id,
                source_key=task.source_key,
                error=error,
                preflight=self._preflight_report(workspace),
            )
        finally:
            try:
                if result and not result.cache_hit and result.status != "processing":
                    result.diagnostics = record_diagnostics(
                        self.root, workspace, task, result.model_dump()
                    )
            finally:
                try:
                    if token:
                        self.repo.release(task.source_key, token)
                finally:
                    for adapter, previous in adapter_deadlines:
                        adapter.deadline = previous
        self.repo.set_result(result)
        write_json(workspace / "result.json", result.model_dump())
        return result

    def _generate(self, task, task_id, workspace, token, resume_candidate, *, deadline=None):
        if deadline is None:
            deadline = time.monotonic() + self.settings.task_timeout
        for name in ("selection.json", "repair_interruption.json"):
            (workspace / name).unlink(missing_ok=True)
        self.repo.event(task_id, "resolve_rule_context")
        rule_context = self.rule_context.load(task)
        generation_context = {
            "task_info": rule_context["task_info"],
            "references": rule_context["references"] + self.references.load(task),
        }
        write_json(workspace / "generation_context.json", generation_context)
        saved = load_saved_candidate(workspace, self.repo, task_id) if resume_candidate else None
        prepared = self._prepare(task, workspace, deadline, saved=saved, task_id=task_id)
        prepared.update(generation_context)
        if saved:
            self.assets.save_candidate(task, task_id, saved[0], saved[1].content)
        output, skill = self.workflow.run(task, task_id, workspace, prepared, saved, deadline)
        if time.monotonic() >= deadline and not output.get("selection"):
            raise AgentError("BUDGET_EXCEEDED", "Task deadline exceeded before publication")
        self.repo.event(task_id, "publish")
        version = str(uuid4())
        directory = self.root / "artifacts" / task.source_key / version
        directory.mkdir(parents=True)
        write_json(directory / "design.json", output["design"])
        write_json(directory / "validation.json", output["report"])
        write_json(directory / "generation_context.json", generation_context)
        metadata = {
            "version": version,
            "kind": task.kind,
            "profile": skill["profile"],
            "validation_path": str((directory / "validation.json").relative_to(self.root)),
            "validation_scope": output["report"]["scope"],
            "source_sha256": prepared["inspection"]["sha256"],
            "skill_sha256": skill["sha256"],
            "profile_sha256": skill["profile_sha256"],
            "generation_context_sha256": digest(generation_context),
            "generation_context_path": str(
                (directory / "generation_context.json").relative_to(self.root)
            ),
            "contract": "prompt-out_data-five-fields-v1"
            if task.kind == "markdown_prompt"
            else "main-ExcelData-v1",
            "preflight_path": str((workspace / "preflight.json").relative_to(self.root)),
            "selection": output["selection"],
            "quality_status": output["selection"]["quality_status"],
            "diagnostics": {
                **diagnostic_paths(self.root, workspace),
                "selection": output["selection"],
            },
        }
        return self.assets.publish(task, token, output["candidate"]["content"], metadata)

    def close(self):
        self.repo.close()
