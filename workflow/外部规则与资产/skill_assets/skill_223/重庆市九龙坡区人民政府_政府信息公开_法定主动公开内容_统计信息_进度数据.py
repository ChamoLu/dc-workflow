# -*- coding: utf-8 -*-
r"""
excelrule_row: 263
source_name: 重庆市九龙坡区人民政府
path_code: 2084670901382680577
path: 重庆市九龙坡区人民政府\政府信息公开\法定主动公开内容\统计信息\进度数据
source_url: https://cqjlp.gov.cn/zwgk_251/zfxxgkml/tjxx/jdsj/202607/t20260724_15851699.html
regex_sheet: 2084670901382680577
markdownMD5: 2105403E0EFF46E12F0ACC15E01B5DC0
网页核对: 原始HTML包含“1-2季度，全区实现地区生产总值1142.50亿元...”和“六、财政”段。
Markdown边界: 正文开始至正文结束；运行时仅解析markdownMD5下载内容。
单位位置: 指标数值后紧邻单位；同比/增长/下降数值后的%。
日期位置: 正文“1-2季度/6月末”，取统计期2026-06-30，不取页面发布日期。
"""

import calendar
import json
import re
import unicodedata
import urllib.request
from collections import defaultdict


DC_INPUT_TEMPLATE = {
    "markdownMD5": "2105403E0EFF46E12F0ACC15E01B5DC0",
    "GZK": {"GZKJG": []},
}

BASE_DOWNLOAD_URL = "http://service-1.example.invalid/api/file/download"
MARKDOWN_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb2312")
NEGATIVE_WORDS = r"(?:减少|下降|降低|少增|多减)"
POSITIVE_WORDS = r"(?:增长|增加|上升|提高|多增|少减)"
UNIT_PATTERN = r"(?:万平方米|平方米|平方公里|万亿元|千亿元|百万元|亿元|万元|元|万吨|吨|万人|人|万户|户|万件|件|%|％)"
DEFAULT_TITLE = "重庆市九龙坡区2026年二季度主要经济情况简析"

REGEX_RULES = [
    ("一般公共预算收入累计值", r"一般公共预算收入((?:(?!,|，|。|；|、).)*?)[亿万]元"),
    ("一般公共预算收入累计同比", r"一般公共预算收入(?:(?!，|。|；).)*?[亿万]元[,，]((?:(?!，|。|；).)*?)%"),
    ("全区一般公共预算支出累计值", r"一般公共预算支出((?:(?!,|，|。|；|、).)*?)[亿万]元"),
    ("全区一般公共预算支出累计同比", r"一般公共预算支出(?:(?!，|。|；).)*?[亿万]元[,，]((?:(?!，|。|；).)*?)%"),
    ("GDP累计值", r"地区生产总值((?:(?!,|，|。|；|、).)*?)[亿万]元"),
    ("GDP累计同比", r"地区生产总值(?:(?!，|。|；).)*?[亿万]元[,，](?:(?!，|。|；).*?)[,，]((?:(?!，|。|；).)*?)%"),
    ("一产累计值", r"第一产业实现增加值((?:(?!,|，|。|；|、).)*?)[亿万]元"),
    ("一产累计同比", r"第一产业实现增加值(?:(?!，|。|；).)*?[亿万]元[,，]((?:(?!，|。|；).)*?)%"),
    ("二产累计值", r"第二产业实现增加值((?:(?!,|，|。|；|、).)*?)[亿万]元"),
    ("二产累计同比", r"第一产业实现增加值(?:(?!，|。|；).)*?[亿万]元[,，]((?:(?!，|。|；).)*?)%"),
    ("三产累计值", r"第三产业实现增加值((?:(?!,|，|。|；|、).)*?)[亿万]元"),
    ("三产累计同比", r"第三产业实现增加值(?:(?!，|。|；).)*?[亿万]元[,，]((?:(?!，|。|；).)*?)%"),
    ("税收累计值", r"税收收入((?:(?!,|，|。|；|、).)*?)[亿万]元"),
    ("税收累计同比", r"税收收入(?:(?!，|。|；).)*?[亿万]元[,，]((?:(?!，|。|；).)*?)%"),
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
    text = unicodedata.normalize("NFKC", str(title or DEFAULT_TITLE or markdown or ""))
    candidates = set()
    for year, month, day in re.findall(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?", text):
        year, month, day = int(year), int(month), int(day)
        if 1 <= month <= 12 and 1 <= day <= calendar.monthrange(year, month)[1]:
            candidates.add(f"{year:04d}-{month:02d}-{day:02d}")
    years = [int(item) for item in re.findall(r"(?<!\d)(20\d{2})(?!\d)", text)]
    default_year = years[0] if years else None
    if default_year:
        for quarter_text in re.findall(r"([一二三四])季度", text):
            quarter = "一二三四".index(quarter_text) + 1
            candidates.add(month_end(default_year, quarter * 3))
        for quarter in re.findall(r"(?:1\s*[-—至到~]\s*)?([1-4])季度", text):
            candidates.add(month_end(default_year, int(quarter) * 3))
        for month in re.findall(r"(?:1\s*[-—至到~]\s*)?(\d{1,2})月(?:份|末|底)?", text):
            candidates.add(month_end(default_year, int(month)))
        if "上半年" in text:
            candidates.add(f"{default_year:04d}-06-30")
        if "下半年" in text or "全年" in text or "年度" in text:
            candidates.add(f"{default_year:04d}-12-31")
        elif not candidates and re.search(rf"{default_year}年(?!\d|[-/.年]\d)", text):
            candidates.add(f"{default_year:04d}-12-31")
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


def extract_candidates(markdown, title=""):
    source = compact_text(markdown)
    end_date = extract_end_date(markdown, title)
    candidates = []
    span_names = defaultdict(set)
    for candidate_name, pattern in REGEX_RULES:
        for match in re.finditer(pattern, source):
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
