import pytest

from dc_agent.domain import Candidate, Design, Task
from dc_agent.validation import Validator
from dc_agent.validation.source_values import positive_percentage_points, title_month_end


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("提高1.20个百分点", ("1.20", "个百分点")),
        ("增加0百分点", ("0", "百分点")),
        ("上升＋１．２个百分点", ("1.2", "个百分点")),
        ("下降1.2个百分点", None),
        ("提高1.2%", None),
        ("提高1.2至2.4个百分点", None),
        ("0.02：37.56：62.42", None),
    ],
)
def test_explicit_source_annotation_has_one_nonnegative_number_and_exact_unit(text, expected):
    assert positive_percentage_points(text) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("经济动态2026年3月*地方经济", "2026-03-31"),
        ("经济动态2024年2月", "2024-02-29"),
        ("2026年3月和4月", None),
        ("2026年3月与2026年4月", None),
        ("2026年3月一季度", None),
        ("2026年13月", None),
    ],
)
def test_title_month_end_requires_unique_month(title, expected):
    assert title_month_end(title) == expected


@pytest.mark.parametrize(
    ("value", "unit", "passed"),
    [("1.20", "个百分点", True), ("2.4", "个百分点", False), ("1.20", "%", False)],
)
def test_annotated_number_still_requires_exact_source_value_and_unit(value, unit, passed):
    class Parser:
        def run(self, *args):
            return {
                "code": 0,
                "ExcelData": [
                    {
                        "indicator_name": "甲",
                        "indicator_data": value,
                        "indicator_unit": unit,
                        "end_date": "",
                    }
                ],
            }

    task = Task(
        pageTitle="site", fileType="csv", fileMd5="id", GZKJG=[{"rule_indicator_name": "甲"}]
    )
    candidate = Candidate(
        content='def main(params):\n return {"ExcelData": []}',
        evidence=[
            {
                "record_index": 0,
                "name_refs": ["csv!A2"],
                "data_ref": "csv!B2",
                "unit_refs": ["csv!B2"],
            }
        ],
    )
    inspection = {
        "format": "csv",
        "sections": [
            {
                "cells": [
                    {"ref": "csv!A2", "value": "甲"},
                    {"ref": "csv!B2", "value": "提高1.20个百分点"},
                ]
            }
        ],
    }
    report = Validator(Parser()).validate(task, candidate, inspection, b"source")
    assert report.passed is passed


def test_date_design_catches_empty_date_instead_of_accepting_overescaped_regex():
    class Parser:
        def run(self, *args):
            return {
                "code": 0,
                "ExcelData": [
                    {
                        "indicator_name": "甲",
                        "indicator_data": "1",
                        "indicator_unit": "-",
                        "end_date": "",
                    }
                ],
            }

    task = Task(
        pageTitle="site",
        fileType="csv",
        fileMd5="id",
        title="统计2026年3月",
        GZKJG=[{"rule_indicator_name": "甲"}],
    )
    candidate = Candidate(
        content='def main(params):\n return {"ExcelData": []}',
        evidence=[{"record_index": 0, "name_refs": ["csv!A2"], "data_ref": "csv!B2"}],
    )
    inspection = {
        "format": "csv",
        "sections": [{"cells": [{"ref": "csv!A2", "value": "甲"}, {"ref": "csv!B2", "value": 1}]}],
    }
    design = Design(
        summary="monthly",
        name_logic="source",
        unit_logic="-",
        unit_position="none",
        date_logic="monthly",
        date_position="runtime:title",
        covered_sections=["csv"],
    )
    report = Validator(Parser()).validate(task, candidate, inspection, b"source", design)
    assert not report.passed
    assert "2026-03-31" in next(
        check.message for check in report.checks if check.name == "runtime_title_date"
    )
