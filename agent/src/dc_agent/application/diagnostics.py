"""Full per-task quality reports and an append-only cross-task issue ledger."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..domain import Task, dumps
from ..infrastructure.files import write_json
from ..infrastructure.locking import publication_lock


def diagnostic_paths(root: Path, workspace: Path) -> dict:
    return {
        "task_directory": str(workspace.resolve()),
        "quality_report": str((workspace / "quality_report.json").resolve()),
        "quality_report_markdown": str((workspace / "quality_report.md").resolve()),
        "issue_log": str((root / "diagnostics" / "quality_issues.jsonl").resolve()),
        "repair_issues": str((workspace / "repair_issues.json").resolve()),
        "repair_memory": str((root / "repair_memory" / "verified_cases.sqlite").resolve()),
    }


def record_diagnostics(root: Path, workspace: Path, task: Task, result: dict) -> dict:
    paths = diagnostic_paths(root, workspace)
    selection_path = workspace / "selection.json"
    selection = (
        json.loads(selection_path.read_text(encoding="utf-8"))
        if selection_path.is_file() and not result.get("cache_hit")
        else (result.get("artifact") or {}).get("selection")
    )
    numbers = set()
    for path in workspace.iterdir():
        match = re.fullmatch(r"(?:candidate|validation|review)-(\d+)\.json", path.name)
        if match:
            numbers.add(int(match[1]))
    attempts = []
    for number in sorted(numbers):
        item = {"attempt": number, "issues": []}
        for prefix in ("candidate", "validation", "review"):
            path = workspace / f"{prefix}-{number}.json"
            if not path.is_file():
                continue
            item[f"{prefix}_path"] = str(path.resolve())
            if prefix == "candidate":
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            if prefix == "validation":
                item["record_count"] = len(raw.get("records", []))
                item["issues"] = [
                    check
                    for check in raw.get("checks", [])
                    if check["status"] in {"fail", "warning"}
                ]
            else:
                item["review"] = raw
        attempts.append(item)
    error = result.get("error")
    interruption = workspace / "repair_interruption.json"
    interrupted = (
        json.loads(interruption.read_text(encoding="utf-8")) if interruption.is_file() else None
    )
    selected = next(
        (a for a in attempts if selection and a["attempt"] == selection["selected_attempt"]),
        {},
    )
    document = {
        "report_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "task_id": workspace.name,
        "pageTitle": task.pageTitle,
        "fileType": task.fileType,
        "source_key": task.source_key,
        "status": result["status"],
        "cache_hit": result.get("cache_hit", False),
        "selection": selection,
        "selected_candidate_issues": selected.get("issues", []),
        "attempts": attempts,
        "error": error,
        "repair_interruption": interrupted,
        "paths": paths,
        "publication_warning": "ready means a reusable asset is available; usable_with_warnings does not mean all semantic concerns or omissions were fixed. Consult selected_candidate_issues and the original full review.",
    }
    repair_path = workspace / "repair_issues.json"
    document["repair"] = (
        json.loads(repair_path.read_text(encoding="utf-8")) if repair_path.is_file() else None
    )
    write_json(Path(paths["quality_report"]), document)
    lines = [
        "# 生成任务完整质量报告",
        "",
        f"- 任务：{workspace.name}",
        f"- 来源：{task.pageTitle}",
        f"- 状态：{result['status']}",
        f"- 选择结果：{dumps(selection)}",
        "",
        "ready 表示已有可复用资产；usable_with_warnings 表示仍有未解决问题，不能视为完整性或全部语义已通过。",
        "原始 validation-N.json / review-N.json 保留原判断；下面的原因不做长度截断。",
        "",
    ]
    for attempt in attempts:
        lines.extend([f"## candidate-{attempt['attempt']}", ""])
        for key in ("candidate_path", "validation_path", "review_path"):
            if key in attempt:
                lines.append(f"- {key}：{attempt[key]}")
        lines.extend(["", "```json", json.dumps(attempt, ensure_ascii=False, indent=2), "```", ""])
    lines.extend(
        [
            "## 中断或失败信息",
            "",
            "```json",
            json.dumps(
                {"error": error, "repair_interruption": interrupted}, ensure_ascii=False, indent=2
            ),
            "```",
            "",
        ]
    )
    Path(paths["quality_report_markdown"]).write_text("\n".join(lines), encoding="utf-8")
    if error or interrupted or any(a["issues"] for a in attempts):
        ledger = Path(paths["issue_log"])
        ledger.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "event_id": str(uuid4()),
            **{
                key: document[key]
                for key in (
                    "updated_at",
                    "task_id",
                    "pageTitle",
                    "fileType",
                    "source_key",
                    "status",
                    "selection",
                    "error",
                    "repair_interruption",
                )
            },
            "selected_candidate_issues": document["selected_candidate_issues"],
            "attempts": attempts,
            "quality_report": paths["quality_report"],
            "quality_report_markdown": paths["quality_report_markdown"],
        }
        with publication_lock(ledger.with_suffix(".lock")):
            with ledger.open("a", encoding="utf-8") as stream:
                stream.write(dumps(entry) + "\n")
    return {**paths, "selection": selection}
