"""Existing read-only Skill adapter; project registry reserved for future work."""

import hashlib
from pathlib import Path

from ..domain import AgentError, Task

SKILLS = {
    "python_excel": "excel-rule-parser-codegen",
    "python_html": "html-rule-parser-codegen",
    "markdown_prompt": "markdown-table-prompt-extraction",
}


class SkillRegistry:
    def __init__(self, root: Path | None = None):
        self.root = root or Path.home() / ".agents" / "skills"

    def load(self, task: Task) -> dict:
        name = SKILLS[task.kind]
        path = self.root / name / "SKILL.md"
        if not path.is_file():
            raise AgentError("SKILL_MISSING", f"Configure DC_SKILL_ROOT; missing {name}/SKILL.md")
        raw = path.read_text(encoding="utf-8-sig")
        adaptation = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("profiles")
            .joinpath("source_gzk.md")
            .read_text(encoding="utf-8")
        )
        return {
            "name": name,
            "sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "profile": "source_rules_v2",
            "profile_sha256": hashlib.sha256(adaptation.encode()).hexdigest(),
            "content": raw,
            "adaptation": adaptation,
        }
