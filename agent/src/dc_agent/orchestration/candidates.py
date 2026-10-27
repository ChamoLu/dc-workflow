"""Keep source-validated candidates independently of later repair failures."""

import hashlib
from copy import deepcopy
from pathlib import Path

from ..domain import Check, Report
from ..infrastructure.files import write_json


class CandidatePool:
    def __init__(self, workspace: Path, kind: str, fingerprint: str):
        self.workspace, self.kind, self.fingerprint = workspace, kind, fingerprint
        self.entries: dict[int, dict] = {}

    @staticmethod
    def mechanically_usable(report: Report, kind: str) -> bool:
        checks = [c for c in report.checks if c.name != "model_semantic_review"]
        if not checks or any(c.status == "fail" for c in checks):
            return False
        passed = {c.name for c in checks if c.status == "pass"}
        if kind == "markdown_prompt":
            return "prompt_structure" in passed
        return bool(report.records) and {"runtime_contract", "source_evidence"} <= passed

    def observe(self, state: dict, *, reviewed: bool = False, known: bool = False):
        report = Report.model_validate(state["report"])
        attempt = state["attempt"]
        if not self.mechanically_usable(report, self.kind):
            self.entries.pop(attempt, None)
            return
        # A prompt has no runtime data test. It needs an accepted semantic review.
        if self.kind == "markdown_prompt" and not (reviewed and known and state["approved"]):
            return
        self.entries[attempt] = deepcopy(
            {
                "attempt": attempt,
                "candidate": state["candidate"],
                "design": state["design"],
                "report": state["report"],
                "semantic_status": "accepted"
                if reviewed and known and state["approved"]
                else "issues_found"
                if reviewed and known
                else "unverified",
                "quality_pending": state.get("quality_pending", False),
            }
        )
        self.save()

    @staticmethod
    def score(entry: dict) -> tuple:
        report = Report.model_validate(entry["report"])
        # Distinct real cells avoid rewarding duplicate records or GZK match counts.
        tier = {"accepted": 2, "issues_found": 1, "unverified": 0}[entry["semantic_status"]]
        covered = len({e.data_ref for e in report.evidence})
        return tier, covered, -int(entry["quality_pending"])

    @property
    def best(self) -> dict | None:
        # Stable tie-breaking keeps the earlier candidate rather than the latest rewrite.
        return max(
            sorted(self.entries.values(), key=lambda e: e["attempt"]),
            key=self.score,
            default=None,
        )

    def save(self):
        write_json(
            self.workspace / "retained_candidates.json",
            {
                "fingerprint": self.fingerprint,
                "candidates": list(self.entries.values()),
            },
        )

    def select(self, reason: str, latest_attempt: int) -> dict:
        selected = deepcopy(self.best)
        report = Report.model_validate(selected["report"])
        unresolved = selected["semantic_status"] != "accepted"
        if unresolved:
            # Keep the original failed review in validation-N.json; the published
            # report explicitly records that the business requested usable fallback.
            for check in report.checks:
                if check.name == "model_semantic_review" and check.status == "fail":
                    check.status = "warning"
            report.checks.append(
                Check(
                    name="usable_candidate_fallback",
                    status="warning",
                    message="Basic execution/source checks passed; semantic review has unresolved issues or was not completed. This version is usable with warnings, not fully semantically approved. See the full task quality report.",
                )
            )
        selection = {
            "policy": "best_usable_candidate_v1",
            "selected_attempt": selected["attempt"],
            "latest_attempt": latest_attempt,
            "fallback_used": selected["attempt"] != latest_attempt or unresolved,
            "semantic_status": selected["semantic_status"],
            "quality_status": "usable_with_warnings"
            if unresolved or any(c.status == "warning" for c in report.checks)
            else "accepted",
            "stop_reason": reason,
            "content_sha256": hashlib.sha256(selected["candidate"]["content"].encode()).hexdigest(),
            "record_count": len(report.records),
            "source_data_ref_count": len({e.data_ref for e in report.evidence}),
        }
        write_json(self.workspace / "selection.json", selection)
        return {
            "candidate": selected["candidate"],
            "design": selected["design"],
            "report": report.model_dump(),
            "selection": selection,
        }
