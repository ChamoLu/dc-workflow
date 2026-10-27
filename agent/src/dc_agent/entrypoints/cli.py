"""Local entrypoints only. Example result files provide metadata, never source values."""

import argparse
import importlib.metadata
import json
import sys
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from ..bootstrap import build_service
from ..config import Settings
from ..domain import AgentError, Task, dumps
from ..infrastructure import ChatCompletionsGateway, FileSourceProvider, write_json
from ..inspection import inspect_bytes


def load_task(
    task_path: Path | None = None, example: Path | None = None, source_path: Path | None = None
) -> Task:
    if example:
        metadata = json.loads((example / "result.json").read_text(encoding="utf-8-sig"))
        # Historical excel_data/out_data are deliberately excluded from model context.
        payload = {
            name: metadata.get(name)
            for name in ("pageTitle", "fileType", "fileMd5", "markdownMD5", "title", "publictime")
        }
        for name in ("pageTitle", "fileMd5"):
            provided = json.loads((example / f"{name}.json").read_text(encoding="utf-8-sig"))[name]
            if provided != payload[name]:
                raise AgentError("EXAMPLE_CONFLICT", f"{name}.json conflicts with result metadata")
        rules = json.loads((example / "GZKJG.json").read_text(encoding="utf-8-sig"))
        payload["GZKJG"] = rules["GZKJG"] if isinstance(rules, dict) else rules
    elif task_path:
        payload = json.loads(task_path.read_text(encoding="utf-8-sig"))
        for name in ("source_path",):
            if payload.get(name):
                payload[name] = str((task_path.parent / payload[name]).resolve())
        payload["reference_paths"] = [
            str((task_path.parent / path).resolve()) for path in payload.get("reference_paths", [])
        ]
    else:
        raise AgentError("INVALID_INPUT", "Specify --task or --example")
    if source_path:
        payload["source_path"] = str(source_path.resolve())
    return Task.model_validate(payload)


def check_model(settings: Settings) -> int:
    """Small explicit network probe, without business files, GZK or skills."""
    probe = settings.model_copy(update={"model_max_tokens": 128, "model_retries": 0})
    gateway = ChatCompletionsGateway(probe)
    target = settings.resolved_runtime() / "diagnostics" / f"model-check-{uuid4()}.json"
    result = {}
    try:
        reply = gateway.complete(
            "diagnostic_probe",
            'Return only JSON: {"ok":true}.',
            {"ping": True},
            {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
            min(90, settings.model_timeout),
        )
        if reply.get("ok") is not True:
            raise AgentError("MODEL_PROBE_INVALID", "Model probe did not return ok=true")
        result = {"status": "model_available"}
    except AgentError as exc:
        result = {
            "status": "model_unavailable",
            "error": {"code": exc.code, "message": str(exc), "retryable": exc.retryable},
        }
    result.update(metrics=gateway.last_call_metrics, diagnostic_path=str(target))
    write_json(target, result)
    print(dumps(result))
    return 0 if result["status"] == "model_available" else 3


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Local DC extraction asset generator")
    parser.add_argument(
        "command",
        nargs="?",
        choices=[
            "check-env",
            "check-model",
            "inspect",
            "preflight",
            "run",
            "task-status",
            "deactivate",
        ],
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--task", type=Path)
    group.add_argument("--example", type=Path)
    parser.add_argument("--source-path", type=Path)
    parser.add_argument("--task-id")
    parser.add_argument(
        "--retry",
        action="store_true",
        help="Retry same failed request using durable operation journals",
    )
    parser.add_argument(
        "--resume-candidate",
        action="store_true",
        help="Revalidate latest saved candidate and repair; requires run --task-id ID --retry",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Create a new candidate; does not deactivate current asset",
    )
    parser.add_argument(
        "--local-reviewed",
        action="store_true",
        help="Opt into unsandboxed local execution for reviewed development candidates",
    )
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        print("\n先检查本地环境：python run_agent.py check-env")
        print("显式检查模型接口：python run_agent.py check-model")
        print(
            "运行案例：python run_agent.py run --example example --source-path runtime/source_cache/example.xlsx"
        )
        return 0
    service = None
    try:
        if args.resume_candidate and not (args.command == "run" and args.task_id and args.retry):
            raise AgentError(
                "INVALID_INPUT", "Use --resume-candidate with run --task-id ID --retry"
            )
        settings = Settings()
        if args.local_reviewed:
            settings = settings.model_copy(
                update={"runner": "local", "allow_unsafe_local_execution": True}
            )
        if args.command == "check-env":
            versions = {}
            for package in (
                "langgraph",
                "langgraph-checkpoint-sqlite",
                "pydantic",
                "SQLAlchemy",
                "openpyxl",
                "xlrd",
                "httpx",
            ):
                versions[package] = importlib.metadata.version(package)
            print(
                dumps(
                    {
                        "python": sys.version,
                        "executable": sys.executable,
                        "packages": versions,
                        "model_configured": bool(
                            settings.model_base_url
                            and settings.model_name
                            and settings.model_api_key.get_secret_value()
                        ),
                        "runner": settings.runner,
                        "runtime_dir": str(settings.resolved_runtime()),
                        "code_dir": str(settings.resolved_code()),
                        "rule_dir": str(settings.resolved_rules()),
                    }
                )
            )
            return 0
        if args.command == "check-model":
            return check_model(settings)
        if args.command == "task-status":
            from ..infrastructure.storage import Repository

            repo = Repository(settings.resolved_runtime())
            try:
                row = repo.task(args.task_id or "")
                print(dumps(row if row else {"error": "TASK_NOT_FOUND"}))
                return 0 if row else 2
            finally:
                repo.close()
        if args.command == "run" and args.task_id:
            if args.task or args.example or args.source_path or not args.retry:
                raise AgentError(
                    "INVALID_INPUT",
                    "Use run --task-id ID --retry without --task, --example or --source-path",
                )
            from ..infrastructure.storage import Repository

            repo = Repository(settings.resolved_runtime())
            try:
                row = repo.task(args.task_id)
            finally:
                repo.close()
            if row is None:
                raise AgentError("TASK_NOT_FOUND", "No saved task with the supplied task_id")
            task = Task.model_validate(row["payload"])
        else:
            task = load_task(args.task, args.example, args.source_path)
        if args.command == "inspect":
            inspection = inspect_bytes(
                FileSourceProvider(settings).read(task),
                task.fileType if task.kind == "python_excel" else "markdown",
            )
            target = settings.resolved_runtime() / "inspections" / f"{inspection['sha256']}.json"
            write_json(target, inspection)
            print(
                dumps(
                    {
                        "status": "inspected",
                        "source_key": task.source_key,
                        "format": inspection["format"],
                        "sha256": inspection["sha256"],
                        "sections": [
                            {"id": s["id"], "cells": len(s["cells"])}
                            for s in inspection["sections"]
                        ],
                        "inspection_path": str(target),
                    }
                )
            )
            return 0
        service = build_service(settings)
        if args.command == "preflight":
            result = service.check_content(task)
            print(dumps(result))
            return 0 if result["status"] == "preflight_passed" else 3
        if args.command == "deactivate":
            service.repo.deactivate(task.source_key)
            print(dumps({"status": "deactivated", "source_key": task.source_key}))
            return 0
        options = {"retry": args.retry, "rebuild": args.rebuild}
        if args.resume_candidate:
            options["resume_candidate"] = True
        result = service.run(task, **options)
        print(result.model_dump_json(indent=2))
        return 0 if result.status == "ready" else 3
    except (AgentError, ValidationError, ValueError, OSError) as exc:
        # Never dump settings or HTTP response bodies here.
        code = getattr(exc, "code", "INVALID_INPUT")
        if isinstance(exc, ValidationError) and any(
            error.get("type") == "without_GZKJG" for error in exc.errors()
        ):
            code = "without_GZKJG"
        message = (
            str(exc)
            if not isinstance(exc, ValidationError)
            else "Task/config validation failed; check required fields and types"
        )
        print(dumps({"status": "invalid_input", "error": {"code": code, "message": message}}))
        return 2
    finally:
        if service:
            service.close()


if __name__ == "__main__":
    raise SystemExit(main())
