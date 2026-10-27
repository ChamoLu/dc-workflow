"""Small synthetic maintained-workbook fixtures; production workbooks stay read-only."""

import openpyxl
import pytest


@pytest.fixture
def write_catalog():
    def write(directory, rows, *, sheet_name="任务列表", extra_headers=()):
        directory.mkdir(parents=True, exist_ok=True)
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = sheet_name
        sheet.append(
            [
                "数据来源编码",
                "采集数据源",
                "DataSourcePathCode",
                "指标名称示例",
                "pageTitle",
                "指标名称取值逻辑",
                "指标名称标准化处理",
                "fileType",
                "所属模块",
                "数据目标表",
                *extra_headers,
            ]
        )
        for row in rows:
            sheet.append(row)
        path = directory / "todo.xlsx"
        book.save(path)
        book.close()
        return path

    return write


@pytest.fixture
def write_reference():
    def write(path, code="reference-code", rows=(("参考名称", "参考片段"),)):
        book = openpyxl.Workbook()
        book.active.title = code
        for row in rows:
            book.active.append(row)
        book.save(path)
        book.close()
        return path

    return write
