# -*- coding: utf-8 -*-
"""
固定模板 Excel 配置表 -> JSON 转换脚本（内置逐格往返校验）
适用画像：单 Sheet、单行表头、无合并单元格、无公式、单元格为文本格式。

用法：
    python excel_to_json.py <输入.xlsx> [输出.json] [--sheet Sheet名] [--empty null|empty_string|skip]

空值策略（--empty）：
    null         空单元格 -> null（默认，最忠实于 Excel 空单元格）
    empty_string 空单元格 -> ""
    skip         空单元格 -> 该键不输出
"""
import sys
import json
import argparse
from datetime import datetime, date
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


def normalize(v):
    """把 openpyxl 单元格值转成 JSON 可序列化的 Python 值。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    # 文本格式的数字读出来本就是 str；真数值（int/float/bool）保留其类型
    return v


def xlsx_to_records(path, sheet_name=None, empty="null"):
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet_name] if sheet_name else wb.worksheets[0]

    ncol = ws.max_column
    headers = [ws.cell(row=1, column=c).value for c in range(1, ncol + 1)]
    # 表头完整性校验：固定模板要求第一行每列都有非空、唯一表头
    missing = [get_column_letter(i + 1) for i, h in enumerate(headers) if h is None or not str(h).strip()]
    if missing:
        raise ValueError(f"表头缺失/为空的列: {missing}；固定模板不允许空表头")
    if len(set(headers)) != len(headers):
        raise ValueError(f"表头存在重复: {[h for h in headers if headers.count(h) > 1]}")

    def empty_value():
        return None if empty == "null" else ("" if empty == "empty_string" else None)

    records = []
    for r in range(2, ws.max_row + 1):
        raw = [ws.cell(row=r, column=c).value for c in range(1, ncol + 1)]
        # 跳过整行全空
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in raw):
            continue
        rec = {}
        for h, v in zip(headers, raw):
            is_empty = v is None or (isinstance(v, str) and not v.strip())
            if is_empty:
                if empty == "skip":
                    continue
                rec[h] = empty_value()
            else:
                rec[h] = normalize(v)
        records.append(rec)
    return ws.title, headers, records


def roundtrip_verify(path, sheet_name, headers, records, empty="null"):
    """把生成的 records 与源 xlsx 逐格比对，返回 (总校验格数, 不一致列表)。"""
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet_name]
    ncol = ws.max_column
    mismatches = []
    checked = 0

    # 数据行（剔除源表整行全空后的行，与转换逻辑保持一致）
    data_rows = []
    for r in range(2, ws.max_row + 1):
        raw = [ws.cell(row=r, column=c).value for c in range(1, ncol + 1)]
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in raw):
            continue
        data_rows.append(r)

    if len(data_rows) != len(records):
        mismatches.append(f"记录数不符: 源={len(data_rows)} 行, JSON={len(records)} 条")

    for rec, r in zip(records, data_rows):
        for c, h in enumerate(headers, start=1):
            checked += 1
            cell_v = ws.cell(row=r, column=c).value
            cell_empty = cell_v is None or (isinstance(cell_v, str) and not cell_v.strip())
            if cell_empty:
                expected = None if empty in ("null", "skip") else ""
                if empty == "skip":
                    got = rec.get(h, "__ABSENT__")
                    if got != "__ABSENT__" and got is not None:
                        mismatches.append(f"{get_column_letter(c)}{r}({h}): 期望键缺失, 实际={got!r}")
                else:
                    if rec.get(h) != expected:
                        mismatches.append(f"{get_column_letter(c)}{r}({h}): 期望={expected!r}, 实际={rec.get(h)!r}")
            else:
                expected = normalize(cell_v)
                if rec.get(h) != expected:
                    mismatches.append(f"{get_column_letter(c)}{r}({h}): 期望={expected!r}, 实际={rec.get(h)!r}")
    return checked, mismatches, data_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output", nargs="?")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--empty", choices=["null", "empty_string", "skip"], default="null")
    args = ap.parse_args()

    sheet, headers, records = xlsx_to_records(args.input, args.sheet, args.empty)

    out = args.output or (args.input.rsplit(".", 1)[0] + ".json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    checked, mismatches, data_rows = roundtrip_verify(
        args.input, sheet, headers, records, args.empty
    )

    print(f"Sheet: {sheet}")
    print(f"字段数: {len(headers)}  记录数: {len(records)}  逐格校验点数: {checked}")
    if mismatches:
        print(f"往返校验发现 {len(mismatches)} 处不一致:")
        for m in mismatches[:50]:
            print("  -", m)
        sys.exit(1)
    print("往返校验通过：JSON 与源 Excel 逐格 100% 一致。")
    print(f"输出: {out}")


if __name__ == "__main__":
    main()
