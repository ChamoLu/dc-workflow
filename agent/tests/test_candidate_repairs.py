import ast

import pytest

from dc_agent.domain import Candidate, Design, Task
from dc_agent.validation.candidate_repairs import propose_regex_escape_repair


def facts():
    task = Task(
        pageTitle="site",
        fileType="csv",
        fileMd5="id",
        title="经济动态2026年3月",
        GZKJG=[{"rule_indicator_name": "甲"}],
    )
    design = Design(
        summary="month",
        name_logic="source",
        unit_logic="source",
        unit_position="csv!B2",
        date_logic="month end",
        date_position="runtime:title",
        covered_sections=["csv"],
    )
    inspection = {"sections": [{"cells": [{"ref": "csv!B2", "value": "提高1.20个百分点"}]}]}
    return task, design, inspection


@pytest.mark.parametrize("raw_literal", [False, True])
def test_repairs_only_known_overescaped_regex_constants_preserving_unicode_comments(raw_literal):
    task, design, inspection = facts()
    patterns = [
        r"(?<!\d)(20\d{2})年\s*(1[0-2]|[1-9])月(?!\d)",
        r"(?:提高|上升|增加)\s*(\d+(?:\.\d+)?)\s*(个百分点|百分点)",
    ]
    broken = [pattern.replace("\\", "\\\\") for pattern in patterns]
    literals = [("r'" + pattern + "'") if raw_literal else repr(pattern) for pattern in broken]
    content = (
        "# 中文审查头保留\nimport re\n日期 = re.findall("
        + literals[0]
        + ", title)\n变化 = re.fullmatch("
        + literals[1]
        + ", value)\n路径 = r'C:\\data\\files'\n"
    )
    proposal = propose_regex_escape_repair(Candidate(content=content), task, design, inspection)
    assert proposal is not None
    assert proposal.candidate.content.startswith("# 中文审查头保留\n")
    assert "路径 = r'C:\\data\\files'" in proposal.candidate.content
    calls = [
        node
        for node in ast.walk(ast.parse(proposal.candidate.content))
        if isinstance(node, ast.Call)
    ]
    assert [call.args[0].value for call in calls] == patterns
    assert {ref for edit in proposal.edits for ref in edit["source_refs"]} == {
        "runtime:title",
        "csv!B2",
    }


@pytest.mark.parametrize("case", ["correct", "unrelated", "no_title_permission", "no_source_proof"])
def test_no_blanket_backslash_rewrite_or_repairs_without_source_support(case):
    task, design, inspection = facts()
    pattern = r"(?<!\d)(20\d{2})年\s*(1[0-2]|[1-9])月(?!\d)"
    if case != "correct":
        pattern = pattern.replace("\\", "\\\\")
    if case == "unrelated":
        pattern = r"C:\\data\\files"
    if case == "no_title_permission":
        design = design.model_copy(update={"date_position": "csv!A1"})
    if case == "no_source_proof":
        task = task.model_copy(update={"title": "没有统计期"})
    content = "import re\nvalue = re.search(" + repr(pattern) + ", title)"
    assert propose_regex_escape_repair(Candidate(content=content), task, design, inspection) is None
