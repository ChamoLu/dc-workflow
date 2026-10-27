"""Date readiness binds each record to its own evidence, using real workbook bytes."""

from io import BytesIO

import openpyxl
import pytest

from dc_agent.domain import Design, Evidence, Report, Task
from dc_agent.inspection import inspect_bytes
from dc_agent.validation.readiness import check_readiness


def inspect_tables(tables):
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in tables:
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    raw = BytesIO()
    book.save(raw)
    book.close()
    inspection = inspect_bytes(raw.getvalue(), "xlsx")
    assert [section["id"] for section in inspection["sections"]] == [name for name, _ in tables]
    return inspection


def task(title=""):
    return Task(
        pageTitle="scope-test",
        fileType="xlsx",
        fileMd5="local-bytes",
        title=title,
        GZKJG=[
            {"rule_indicator_name": "指标甲", "rule_indicator_code": "1"},
            {"rule_indicator_name": "指标乙", "rule_indicator_code": "2"},
        ],
    )


def design(position="not provided"):
    return Design(
        summary="Actual source scopes",
        name_logic="actual row labels",
        unit_logic="-",
        unit_position="not provided",
        date_logic="source bindings",
        date_position=position,
    )


def report(entries):
    result = Report(scope="test")
    for index, (data_ref, date_refs, end_date) in enumerate(entries):
        section, cell = data_ref.rsplit("!", 1)
        result.records.append(
            {
                "indicator_name": "指标甲" if index == 0 else "指标乙",
                "indicator_data": str(index + 1),
                "indicator_unit": "-",
                "end_date": end_date,
            }
        )
        result.evidence.append(
            Evidence(
                record_index=index,
                data_ref=data_ref,
                name_refs=[f"{section}!A{cell.lstrip('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"],
                date_refs=date_refs,
            )
        )
    return result


def presence(result):
    return next(check for check in result.checks if check.name == "end_date_presence")


@pytest.fixture
def mixed_sheets():
    return inspect_tables(
        [
            ("Dated", [["2026年1月"], ["指标甲", 1]]),
            ("Undated", [["指标", "值"], ["指标乙", 2]]),
        ]
    )


@pytest.mark.parametrize("title", ["", "2026年3月发布的统计表"])
def test_other_sheet_date_does_not_make_an_unknown_record_repairable(mixed_sheets, title):
    result = report(
        [
            ("Dated!B2", ["Dated!A1"], "2026-01-31"),
            ("Undated!B2", [], ""),
        ]
    )
    original = result.model_dump()["records"]
    check_readiness(task(title), design("Dated!A1"), result, mixed_sheets)
    check = presence(result)
    assert check.status == "warning"
    assert check.details["record_indices"] == []
    assert check.details["unknown_record_indices"] == [1]
    assert check.details["source_refs"] == []
    assert check.details["repairable"] is False
    assert check.details["title_fallback_allowed"] is False
    assert result.records == original


def test_mixed_missing_dates_fail_only_for_records_with_their_own_binding(mixed_sheets):
    result = report([("Dated!B2", ["Dated!A1"], ""), ("Undated!B2", [], "")])
    check_readiness(task("2026年3月统计"), design("Dated!A1"), result, mixed_sheets)
    check = presence(result)
    assert check.status == "fail"
    assert check.details["record_indices"] == [0]
    assert check.details["unknown_record_indices"] == [1]
    assert check.details["source_refs_by_record"] == {"0": ["Dated!A1"]}
    assert check.details["source_refs"] == ["Dated!A1"]
    assert "remain a warning" in check.message
    assert "Unknown-date records=[1]" in check.message


def test_same_sheet_multi_table_design_position_is_not_a_record_binding():
    inspection = inspect_tables(
        [
            (
                "Sheet",
                [
                    ["2026年1月"],
                    ["指标甲", 1],
                    [],
                    ["未给统计期间的另一张表"],
                    ["指标", "值"],
                    ["指标乙", 2],
                ],
            )
        ]
    )
    result = report([("Sheet!B2", ["Sheet!A1"], "2026-01-31"), ("Sheet!B6", [], "")])
    check_readiness(task("2026年3月统计"), design("Sheet!A1"), result, inspection)
    check = presence(result)
    assert check.status == "warning"
    assert check.details["record_indices"] == []
    assert check.details["unknown_record_indices"] == [1]
    assert check.details["design_source_refs"] == ["Sheet!A1"]
    assert check.details["source_refs"] == []


def test_design_only_source_date_is_conservative_even_on_one_sheet():
    inspection = inspect_tables([("Sheet", [["2026年1月"], ["指标甲", 1]])])
    result = report([("Sheet!B2", [], "")])
    check_readiness(task("2026年3月统计"), design("Sheet!A1"), result, inspection)
    check = presence(result)
    assert check.status == "warning"
    assert check.details["unknown_record_indices"] == [0]
    assert check.details["title_period_hint"] is None


def test_own_period_columns_are_separate_bindings_and_not_a_global_union():
    inspection = inspect_tables(
        [
            (
                "Sheet",
                [
                    ["指标", "2026年1月", "2026年2月"],
                    ["指标甲", 1, 2],
                ],
            )
        ]
    )
    result = report([("Sheet!B2", ["Sheet!B1"], "2026-01-31"), ("Sheet!C2", ["Sheet!C1"], "")])
    check_readiness(task("2026年3月统计"), design("Sheet!B1 and Sheet!C1"), result, inspection)
    check = presence(result)
    assert check.status == "fail"
    assert check.details["record_indices"] == [1]
    assert check.details["unknown_record_indices"] == []
    assert check.details["source_refs"] == ["Sheet!C1"]
    assert check.details["source_refs_by_record"] == {"1": ["Sheet!C1"]}


def test_explicit_runtime_title_binding_stays_local_even_with_another_source_period(mixed_sheets):
    result = report(
        [
            ("Dated!B2", ["Dated!A1"], "2026-01-31"),
            ("Undated!B2", ["runtime:title"], ""),
        ]
    )
    check_readiness(task("2026年3月统计"), design("Dated!A1"), result, mixed_sheets)
    check = presence(result)
    assert check.status == "fail"
    assert check.details["record_indices"] == [1]
    assert check.details["source_refs"] == ["runtime:title"]
    bound = next(check for check in result.checks if check.name == "runtime_evidence_date")
    assert bound.details["record_indices"] == [1]
    assert bound.details["expected"] == "2026-03-31"


@pytest.mark.parametrize("position", ["not provided", "runtime:title"])
def test_unique_title_fallback_remains_available_for_a_single_undated_source(position):
    inspection = inspect_tables([("Sheet", [["指标", "值"], ["指标甲", 1]])])
    result = report([("Sheet!B2", [], "")])
    check_readiness(task("2026年3月经济指标"), design(position), result, inspection)
    check = presence(result)
    assert check.status == "fail"
    assert check.details["record_indices"] == [0]
    assert check.details["unknown_record_indices"] == []
    assert check.details["source_refs_by_record"] == {"0": ["runtime:title"]}
    assert check.details["title_period_hint"] == "2026-03-31"
    assert check.details["title_fallback_allowed"] is True


def test_multiple_undated_sheets_do_not_inherit_an_implicit_title_period():
    inspection = inspect_tables(
        [
            ("First", [["指标", "值"], ["指标甲", 1]]),
            ("Second", [["指标", "值"], ["指标乙", 2]]),
        ]
    )
    result = report([("First!B2", [], ""), ("Second!B2", [], "")])
    check_readiness(task("2026年3月统计"), None, result, inspection)
    check = presence(result)
    assert check.status == "warning"
    assert check.details["unknown_record_indices"] == [0, 1]
    assert check.details["title_fallback_allowed"] is False


@pytest.mark.parametrize(
    "period", ["2026年1月", "2026 年 1 月", "2026Q1", "第一季度", "上半年", "2026-01-31", 2026.0]
)
def test_source_period_hints_block_implicit_title_even_without_date_evidence(period):
    inspection = inspect_tables([("Sheet", [[period], ["指标甲", 1]])])
    result = report([("Sheet!B2", [], "")])
    check_readiness(task("2026年3月统计"), None, result, inspection)
    check = presence(result)
    assert check.status == "warning"
    assert check.details["unknown_record_indices"] == [0]
    assert check.details["source_period_refs"] == ["Sheet!A1"]


def test_date_in_sheet_title_also_blocks_global_runtime_fallback():
    inspection = inspect_tables([("2026年1月", [["指标", "值"], ["指标甲", 1]])])
    result = report([("2026年1月!B2", [], "")])
    check_readiness(task("2026年3月统计"), None, result, inspection)
    assert presence(result).status == "warning"


@pytest.mark.parametrize("title", ["", "2026年3月与2026年4月统计", "2026年第一季度"])
def test_no_reliable_unique_title_keeps_dates_unknown(title):
    inspection = inspect_tables([("Sheet", [["指标", "值"], ["指标甲", 1]])])
    result = report([("Sheet!B2", [], "")])
    check_readiness(task(title), None, result, inspection)
    assert presence(result).status == "warning"
    assert presence(result).details["unknown_record_indices"] == [0]


def test_unknown_reference_and_missing_data_scope_do_not_provide_a_date():
    inspection = inspect_tables([("Sheet", [["2026年1月"], ["指标甲", 1]])])
    result = report([("Sheet!B2", ["Unknown!A1"], "")])
    check_readiness(task(), None, result, inspection)
    assert presence(result).status == "warning"
    assert presence(result).details["record_indices"] == []


def test_named_checks_are_preserved_when_all_dates_are_complete(mixed_sheets):
    result = report([("Dated!B2", ["Dated!A1"], "2026-01-31")])
    check_readiness(task(), design("Dated!A1"), result, mixed_sheets)
    assert presence(result).status == "pass"
    assert presence(result).details["record_indices"] == []
    assert presence(result).details["unknown_record_indices"] == []
    assert (
        next(check for check in result.checks if check.name == "downstream_readiness").status
        == "pass"
    )
