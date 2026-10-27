"""Real downloaded source regression; historical JSON is comparison evidence only."""

import json
from pathlib import Path

import pytest

from dc_agent.entrypoints.cli import load_task
from dc_agent.inspection import inspect_bytes
from dc_agent.validation import number

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runtime" / "source_cache" / "example.xlsx"


@pytest.mark.skipif(
    not SOURCE.is_file(), reason="Download the real example source before this regression"
)
def test_real_example_original_cells_and_historical_comparison():
    inspection = inspect_bytes(SOURCE.read_bytes(), "xlsx")
    task = load_task(example=ROOT / "example")
    assert inspection["coverage_complete"]
    assert len(inspection["sections"]) == 1
    sheet = inspection["sections"][0]
    cells = {cell["ref"].split("!")[-1]: cell["value"] for cell in sheet["cells"]}
    assert cells["C2"] == "单位：万元"
    assert cells["C3"] == "累计"
    assert cells["D3"] == "比去年同期±%"
    assert cells["C4"] == 441442
    assert cells["D13"] == -33.87
    assert cells["C18"] == 3257568.002719
    assert cells["C11"] == "—"
    assert "2026年3月" in task.title
    assert len(task.GZKJG) == 21
    historical = json.loads((ROOT / "example" / "result.json").read_text(encoding="utf-8"))
    assert len(historical["excel_data"]) == 62
    assert len(historical["out_data"]) == 21
    # Comparison only: not a new generated parser or proof of all historical records.
    by_name = {item["indicator_name"]: item for item in historical["out_data"]}
    assert number(by_name["地区生产总值累计"]["indicator_data"]) == number(cells["C4"])
    assert number(
        by_name["万元工业增加值能耗(吨标准煤/万元)比去年同期±%"]["indicator_data"]
    ) == number(cells["D13"])
    assert number(by_name["金融机构各项存款余额*累计"]["indicator_data"]) == number(cells["C18"])
