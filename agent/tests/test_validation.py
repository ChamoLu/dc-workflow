import pytest

from dc_agent.domain import Candidate, Task
from dc_agent.validation import Validator


class Runner:
    def run(self, *args):
        return {
            "code": 0,
            "ExcelData": [
                {
                    "indicator_name": "甲",
                    "indicator_data": "999",
                    "indicator_unit": "-",
                    "end_date": "",
                }
            ],
        }


def test_real_source_value_overrides_model_evidence():
    task = Task(
        pageTitle="site", fileType="csv", fileMd5="id", GZKJG=[{"rule_indicator_name": "甲"}]
    )
    candidate = Candidate(
        content='def main(params):\n return {"ExcelData": []}',
        evidence=[{"record_index": 0, "name_refs": ["csv!A2"], "data_ref": "csv!B2"}],
    )
    inspection = {
        "format": "csv",
        "sections": [
            {"cells": [{"ref": "csv!A2", "value": "甲"}, {"ref": "csv!B2", "value": "1"}]}
        ],
    }
    report = Validator(Runner()).validate(task, candidate, inspection, b"source")
    assert not report.passed
    assert "differs" in next(c.message for c in report.checks if c.name == "source_evidence")


@pytest.mark.parametrize(
    "code",
    [
        'import os\ndef main(params): return {"ExcelData":[]}',
        'def main(params):\n return eval("ExcelData")',
        'def main():\n return {"ExcelData": []}',
    ],
)
def test_static_rejects_unexpected_interfaces(code):
    with pytest.raises(ValueError):
        Validator(Runner())._static(code)


def test_parser_error_message_reaches_repair_feedback():
    class FailedParser:
        def run(self, *args):
            return {"code": 1, "msg": "Missing fileServiceUrl", "ExcelData": []}

    task = Task(
        pageTitle="site", fileType="csv", fileMd5="id", GZKJG=[{"rule_indicator_name": "甲"}]
    )
    candidate = Candidate(content='def main(params):\n return {"ExcelData": []}')
    report = Validator(FailedParser()).validate(task, candidate, {}, b"source")
    assert not report.passed
    assert "Missing fileServiceUrl" in report.checks[-1].message


@pytest.mark.parametrize("source_value", ["0.02：37.56：62.42", "提高1.2个百分点"])
def test_compound_source_values_require_review_with_source_location(source_value):
    class CompoundParser(Runner):
        def run(self, *args):
            result = super().run(*args)
            result["ExcelData"][0]["indicator_data"] = source_value
            return result

    task = Task(
        pageTitle="site", fileType="csv", fileMd5="id", GZKJG=[{"rule_indicator_name": "甲"}]
    )
    candidate = Candidate(
        content='def main(params):\n return {"ExcelData": []}',
        evidence=[{"record_index": 0, "name_refs": ["csv!A2"], "data_ref": "csv!B2"}],
    )
    report = Validator(CompoundParser()).validate(task, candidate, {}, b"source")
    assert not report.passed
    assert source_value in report.checks[-1].message
    assert "csv!B2" in report.checks[-1].message
