# -*- coding: utf-8 -*-
r"""
excelrule_row: 265
source_name: 河南省财政厅
path_code: 2085417117313806338
path: 河南省财政厅\信息公开\财政预决算\财政审计
source_url: https://czt.henan.gov.cn/2026/08-03/3381879.html
regex_sheet: 2085417117313806338
markdownMD5: 29FBDE88227480203C7E7514E278B6F5
网页核对: 原始HTML和Markdown均包含“2026年5月预算执行简要分析”财政收支正文。
Markdown边界: 标题、发布时间、正文财政收支段至“相关阅读”；运行时仅解析markdownMD5下载内容。
单位位置: 财政金额数值后紧邻“亿元”；增长/下降/占比等变化率数值后的%。
日期位置: 标题/正文“5月份”“1-5月累计”，取统计期2026-05-31，不取发布时间。
说明: B列支出累计两条原始正则在核对Markdown中未命中；不修正规则、不伪造输出。
"""

import calendar
import json
import re
import unicodedata
import urllib.request
from collections import defaultdict


DC_INPUT_TEMPLATE = {
    "markdownMD5": "29FBDE88227480203C7E7514E278B6F5",
    "GZK": {"GZKJG": []},
}

BASE_DOWNLOAD_URL = "http://service-1.example.invalid/api/file/download"
MARKDOWN_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb2312")
DEFAULT_TITLE = "2026年5月预算执行简要分析*地方经济"
NEGATIVE_WORDS = r"(?:减少|下降|降低|少增|多减)"
POSITIVE_WORDS = r"(?:增长|增加|上升|提高|多增|少减)"
UNIT_PATTERN = r"(?:万平方米|平方米|平方公里|万亿元|千亿元|百万元|亿元|万元|元|万吨|吨|万人|人|万户|户|万件|件|%|％)"

REGEX_RULES = [
    ("一般公共预算收入累计值", r"累计[,，]全省财政总收入.*?一般公共预算收入((?:(?!,|，|。|；|、).)*?)[亿万]元"),
    ("一般公共预算收入累计同比", r"累计[,，]全省财政总收入.*?一般公共预算收入(?:(?!，|。|；).)*?[亿万]元[,，、](?:(?!，|。|；).)*?[,，、](?:(?!，|。|；).)*?[,，、]((?:(?!，|。|；).)*?)%"),
    ("一般公共预算支出累计值", r"累计[,，]一般公共预算支出((?:(?!,|，|。|；|、).)*?)[亿万]元"),
    ("一般公共预算支出累计同比", r"累计[,，]一般公共预算支出(?:(?!，|。|；).)*?[亿万]元[,，、](?:(?!，|。|；).)*?[,，、](?:(?!，|。|；).)*?[,，、]((?:(?!，|。|；).)*?)%"),
    ("一般公共预算收入当期值", r"一般公共预算收入((?:(?!,|，|。|；|、).)*?)[亿万]元"),
    ("一般公共预算支出当期值", r"一般公共预算支出((?:(?!,|，|。|；|、).)*?)[亿万]元"),
]


def main(params):
    return _main_impl(params)


def download_markdown(params):
    markdown_md5 = str(params.get("markdownMD5") or DC_INPUT_TEMPLATE["markdownMD5"]).strip()
    if not markdown_md5:
        raise ValueError("缺少markdownMD5")
    with urllib.request.urlopen(BASE_DOWNLOAD_URL.rstrip("/") + "/" + markdown_md5, timeout=60) as response:
        raw = response.read()
    for encoding in MARKDOWN_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="ignore")


def normalize_key(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("％", "%").replace("（", "(").replace("）", ")")
    return re.sub(r"[\s、,，'#•■]", "", text)


def compact_text(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    return re.sub(r"\s+", "", text)


def clean_data_value(value):
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"[\s,，]", "", text)
    text = re.sub(NEGATIVE_WORDS, "-", text)
    text = re.sub(POSITIVE_WORDS, "", text)
    text = re.sub(r"^(?:同比|环比|比上年|比年初)", "", text)
    text = re.sub(UNIT_PATTERN + r"$", "", text).replace("％", "%").rstrip("%")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return None
    return text[1:] if text.startswith("+") else text


def extract_unit(match):
    whole = match.group(0)
    tail = whole[match.end(1) - match.start(0):]
    direct = re.match(r"\s*(" + UNIT_PATTERN + r")", tail)
    if direct:
        return direct.group(1).replace("％", "%")
    captured_unit = re.search(r"(" + UNIT_PATTERN + r")\s*$", match.group(1))
    return captured_unit.group(1).replace("％", "%") if captured_unit else "-"


def month_end(year, month):
    if 1900 <= year <= 2200 and 1 <= month <= 12:
        return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
    return ""


def extract_end_date(markdown, title=""):
    title_text = unicodedata.normalize("NFKC", str(title or DEFAULT_TITLE or ""))
    text = title_text or unicodedata.normalize("NFKC", str(markdown or ""))
    candidates = set()
    for year, month, day in re.findall(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?", text):
        year, month, day = int(year), int(month), int(day)
        if 1 <= month <= 12 and 1 <= day <= calendar.monthrange(year, month)[1]:
            candidates.add(f"{year:04d}-{month:02d}-{day:02d}")
    years = [int(item) for item in re.findall(r"(?<!\d)(20\d{2})(?!\d)", text)]
    default_year = years[0] if years else None
    if default_year:
        for month in re.findall(r"(?:1\s*[-—至到~]\s*)?(\d{1,2})月(?:份|末|底)?", text):
            candidates.add(month_end(default_year, int(month)))
    return max((item for item in candidates if item), default="")


def row_value(row, key):
    if not isinstance(row, dict):
        return None
    if key in row:
        return row[key]
    escaped = key.replace("_", r"\_")
    if escaped in row:
        return row[escaped]
    for actual_key, value in row.items():
        if str(actual_key).replace("\\", "") == key:
            return value
    return None


def build_gzk_index(params):
    gzk = params.get("GZK") or {}
    rows = gzk.get("GZKJG") if isinstance(gzk, dict) else None
    if rows is None:
        rows = params.get("GZKJG") or []
    if isinstance(rows, dict):
        rows = [rows]
    index = defaultdict(list)
    for row in rows if isinstance(rows, list) else []:
        name = row_value(row, "rule_indicator_name")
        code = row_value(row, "rule_indicator_code")
        if name is not None and code is not None and str(name).strip() and str(code).strip():
            index[normalize_key(name)].append(row)
    return index


def is_current_period_match(source, match):
    current_pos = source.rfind("5月份", 0, match.start())
    cumulative_pos = source.rfind("1-5月累计", 0, match.start())
    return current_pos > cumulative_pos


def is_accepted_match(source, candidate_name, match):
    if "当期" in candidate_name:
        return is_current_period_match(source, match)
    return True


def extract_candidates(markdown, title=""):
    source = compact_text(markdown)
    end_date = extract_end_date(markdown, title)
    candidates = []
    span_names = defaultdict(set)
    for candidate_name, pattern in REGEX_RULES:
        for match in re.finditer(pattern, source):
            if not is_accepted_match(source, candidate_name, match):
                continue
            data = clean_data_value(match.group(1))
            if data is None:
                continue
            span = (match.start(1), match.end(1))
            span_names[span].add(normalize_key(candidate_name))
            candidates.append({
                "candidate_name": candidate_name,
                "indicator_data": data,
                "indicator_unit": extract_unit(match),
                "end_date": end_date,
                "source_span": span,
            })
    ambiguous_spans = {span for span, names in span_names.items() if len(names) > 1}
    return [item for item in candidates if item["source_span"] not in ambiguous_spans]


def _main_impl(params):
    params = params if isinstance(params, dict) else {}
    try:
        markdown = download_markdown(params)
        if not markdown.strip() or markdown.lstrip().lower().startswith(("<!doctype html", "<html")):
            raise ValueError("markdownMD5 下载结果为空或为原始 HTML")
        outdata = []
        for candidate in extract_candidates(markdown, params.get("title") or ""):
            outdata.append({
                "indicator_name": candidate["candidate_name"],
                "indicator_data": candidate["indicator_data"],
                "indicator_unit": candidate["indicator_unit"],
                "end_date": candidate["end_date"],
            })
        json.dumps(outdata, ensure_ascii=False, allow_nan=False)
        return {"code": 0, "msg": "success", "OUTDATA": outdata, "out_data": outdata}
    except ValueError as exc:
        return {"code": -1, "msg": str(exc), "OUTDATA": [], "out_data": []}
    except Exception as exc:
        return {"code": -99, "msg": "解析失败:" + str(exc), "OUTDATA": [], "out_data": []}
