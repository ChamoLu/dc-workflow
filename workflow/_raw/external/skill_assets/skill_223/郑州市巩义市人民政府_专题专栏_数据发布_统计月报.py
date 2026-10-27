# -*- coding: utf-8 -*-
r"""
采集数据源：郑州市巩义市人民政府
数据来源编码：01020001640
数据源路径代码：2082506910644461569
路径：郑州市巩义市人民政府\专题专栏\数据发布\统计月报
目标表：usrDFJJYXSJB
excelrule_row: 298
参考规则：excelrule/excelrule.xlsx 第 298 行；excelname拼接规则/指标名称正则逻辑_分路径代码.xlsx Sheet 2082506910644461569
网页核对链接：https://public.gongyishi.gov.cn/D1003X/10131025.jhtml
生成期核对：markdownMD5 2815936109744AE6804BDFB1B60179FC，10/18 条正则命中

指标名称取值逻辑：A列 rule_indicator_name 仅在B列正则从实际 Markdown 匹配到同一网页指标后原样输出。
指标单位取值逻辑：各指标绝对量按HTML正文或表格中数值对应的实际计量单位取值；同比、增长、下降、增速、占比等百分比指标统一取“%”。没有明确单位的绝对量不自行补造。
指标单位位置：HTML正文/表格数值对应单位；同比/增长/下降/增速/占比数值后的“%”
截止日期取值逻辑：title为“2026年5月经济动态”，本行指标为月频，截止日期取月度数据期2026-05-31，不取发布日期。
截止日期位置：外部字段title“2026年5月经济动态” + publictime“2026-06-30 00:00:00”
运行时输入：平台下载的 HTML 转换 Markdown；本代码不访问网页、不解析 HTML DOM、不使用 fileMd5 兜底。
运行时输出：平台代码节点的 OUTDATA。
"""

import calendar
import json
import re
import unicodedata
from collections import defaultdict
from urllib.request import urlopen


BASE_DOWNLOAD_URL = "http://service-1.example.invalid/api/file/download"
DEFAULT_MARKDOWN_MD5 = "2815936109744AE6804BDFB1B60179FC"
DEFAULT_TITLE = "2026年5月经济动态*地方经济"
DEFAULT_PUBLICTIME = "2026-06-30 00:00:00"
COMPARE_PUBLICTIME = False
FIXED_END_DATE = "2026-05-31"
NEGATIVE_WORDS = ("减少", "下降", "降低", "少增", "多减")
POSITIVE_WORDS = ("增长", "增加", "上升", "提高", "多增", "少减")
UNIT_RE = re.compile(r"(万人次|亿人次|万人|亿元|万元|万平方米|平方米|平方公里|亿千瓦时|万千瓦时|万吨|吨|万辆|万单|km/h|公里/小时|%|％|元|人|户|件)")
RULES = (('地区生产总值累计值', '地区生产总值完成(\\-?\\d+\\.?\\d*)亿元'), ('地区生产总值累计同比', '地区生产总值完成\\-?\\d+\\.?\\d*亿元，同?比?([增长|下降]+\\-?\\d+\\.?\\d*)%'), ('第一产业累计值', '第一产业增加值(\\-?\\d+\\.?\\d*)亿元'), ('第一产业累计同比', '第一产业增加值\\-?\\d+\\.?\\d*亿元，同?比?([增长|下降]+\\-?\\d+\\.?\\d*)%'), ('第二产业累计值', '第二产业增加值(\\-?\\d+\\.?\\d*)亿元'), ('第二产业累计同比', '第二产业增加值\\-?\\d+\\.?\\d*亿元，同?比?([增长|下降]+\\-?\\d+\\.?\\d*)%'), ('第三产业累计值', '第三产业增加值(\\-?\\d+\\.?\\d*)亿元'), ('第三产业累计同比', '第三产业增加值\\-?\\d+\\.?\\d*亿元，同?比?([增长|下降]+\\-?\\d+\\.?\\d*)%'), ('规模工业增加值增速', '规模工业增加值增速为(\\-?\\d+\\.?\\d*)%'), ('固定资产投资增速', '固定资产投资增速为(\\-?\\d+\\.?\\d*)%'), ('社会消费品零售总额累计值', '社会消费品零售总额完成(\\-?\\d+\\.?\\d*)亿元'), ('社会消费品零售总额累计同比', '社会消费品零售总额完成\\-?\\d+\\.?\\d*亿元，增速为(\\-?\\d+\\.?\\d*)%'), ('一般公共预算收入累计值', '一般公共预算收入完成(\\-?\\d+\\.?\\d*)亿元'), ('一般公共预算收入累计同比', '一般公共预算收入完成\\-?\\d+\\.?\\d*亿元，同?比?([增长|下降]+\\-?\\d+\\.?\\d*)%'), ('金融机构各项存款余额', '金融机构各项存款余额为(\\-?\\d+\\.?\\d*)亿元'), ('金融机构各项存款余额增减量', '金融机构各项存款余额为\\-?\\d+\\.?\\d*亿元，比年初([增长|下降]+\\-?\\d+\\.?\\d*)%'), ('各项贷款余额累计值', '各项贷款余额为(\\-?\\d+\\.?\\d*)亿元'), ('各项贷款余额累计同比', '各项贷款余额为\\-?\\d+\\.?\\d*亿元，比年初([增长|下降]+\\-?\\d+\\.?\\d*)%'))


def main(params):
    return _main_impl(params)


def _main_impl(params):
    params = params if isinstance(params, dict) else {}
    try:
        markdown_md5 = str(params.get("markdownMD5") or DEFAULT_MARKDOWN_MD5 or "").strip()
        if not markdown_md5:
            raise ValueError("缺少 markdownMD5")
        markdown = decode_markdown(download_markdown(markdown_md5))
        ensure_converted_markdown(markdown)
        records = parse_markdown(markdown, params.get("title") or DEFAULT_TITLE)
        json.dumps(records, ensure_ascii=False, allow_nan=False)
        return {"code": 0, "msg": "success", "OUTDATA": records, "out_data": records}
    except ValueError as exc:
        return {"code": -1, "msg": str(exc), "OUTDATA": [], "out_data": []}
    except Exception as exc:
        return {"code": -99, "msg": "解析失败:" + str(exc), "OUTDATA": [], "out_data": []}


def download_markdown(markdown_md5, timeout=60):
    with urlopen(BASE_DOWNLOAD_URL.rstrip("/") + "/" + str(markdown_md5), timeout=timeout) as response:
        return response.read()


def decode_markdown(raw):
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def ensure_converted_markdown(markdown):
    head = str(markdown or "").lstrip()[:1024].lower()
    if head.startswith("<!doctype html") or "<html" in head:
        raise ValueError("markdownMD5 下载结果为原始 HTML，请检查代码节点是否仍使用旧版 fileMd5 或缓存")


def compact_text(markdown):
    text = unicodedata.normalize("NFKC", str(markdown or ""))
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    return re.sub(r"\s+", "", text)


def normalize_number(value, whole_match):
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[\s,，]", "", text)
    for word in POSITIVE_WORDS:
        text = text.replace(word, "")
    is_negative = any(word in str(whole_match or "") for word in NEGATIVE_WORDS)
    for word in NEGATIVE_WORDS:
        text = text.replace(word, "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = match.group(0)
    if is_negative and not number.startswith("-"):
        number = "-" + number.lstrip("+")
    return number[1:] if number.startswith("+") else number


def extract_unit(match):
    whole = unicodedata.normalize("NFKC", match.group(0))
    tail = whole[match.end(1) - match.start(0):]
    direct = UNIT_RE.match(tail)
    if direct:
        return direct.group(1).replace("％", "%")
    captured_unit = None
    for captured in match.groups():
        if captured is None:
            continue
        found = list(UNIT_RE.finditer(unicodedata.normalize("NFKC", str(captured))))
        if found:
            captured_unit = found[-1].group(1)
    return captured_unit.replace("％", "%") if captured_unit else "-"


def month_end(year, month):
    if 1900 <= year <= 2200 and 1 <= month <= 12:
        return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
    return ""


def extract_calendar_dates(text):
    dates = set()
    for year, month, day in re.findall(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?", text):
        year, month, day = int(year), int(month), int(day)
        if 1 <= month <= 12 and 1 <= day <= calendar.monthrange(year, month)[1]:
            dates.add(f"{year:04d}-{month:02d}-{day:02d}")
    return dates


def extract_period_dates(text):
    candidates = set()
    years = [int(item) for item in re.findall(r"(?<!\d)(20\d{2})(?!\d)", text)]
    default_year = years[0] if years else None
    if not default_year:
        return candidates
    for month in re.findall(r"(?:1\s*[-—至到~]\s*)?(\d{1,2})月(?:份|末|底)?", text):
        value = month_end(default_year, int(month))
        if value:
            candidates.add(value)
    for quarter in re.findall(r"(?:一|第?1|1)\s*季度", text):
        candidates.add(month_end(default_year, 3))
    for quarter in re.findall(r"(?:二|第?2|2)\s*季度", text):
        candidates.add(month_end(default_year, 6))
    for quarter in re.findall(r"(?:三|第?3|3)\s*季度", text):
        candidates.add(month_end(default_year, 9))
    for quarter in re.findall(r"(?:四|第?4|4)\s*季度", text):
        candidates.add(month_end(default_year, 12))
    if "上半年" in text:
        candidates.add(f"{default_year:04d}-06-30")
    if "下半年" in text or "全年" in text or "年度" in text:
        candidates.add(f"{default_year:04d}-12-31")
    elif not candidates and re.search(rf"{default_year}年(?!\d|[-/.年]\d)", text):
        candidates.add(f"{default_year:04d}-12-31")
    return candidates


def extract_end_date(markdown, title=""):
    if FIXED_END_DATE:
        return FIXED_END_DATE
    text = unicodedata.normalize("NFKC", str(markdown or "") + "\n" + str(title or ""))
    period_dates = extract_period_dates(text)
    fallback_dates = extract_calendar_dates(text)
    public_dates = extract_calendar_dates(unicodedata.normalize("NFKC", DEFAULT_PUBLICTIME))
    if COMPARE_PUBLICTIME:
        return max(period_dates | fallback_dates | public_dates, default="")
    if period_dates:
        return max(period_dates)
    if fallback_dates:
        return max(fallback_dates)
    return max(public_dates, default="")


def compatible_pattern(pattern):
    return (
        pattern
        .replace("，", "[,，]")
        .replace("、", "[、,，]")
        .replace("。", "[。.]")
        .replace("；", "[；;]")
    )


def parse_markdown(markdown, title=""):
    source = compact_text(markdown)
    end_date = extract_end_date(markdown, title)
    candidates = []
    span_names = defaultdict(set)
    for indicator_name, pattern in RULES:
        for match in re.finditer(compatible_pattern(pattern), source):
            value = normalize_number(match.group(1), match.group(0))
            if value is None:
                continue
            span = (match.start(1), match.end(1))
            span_names[span].add(indicator_name)
            candidates.append({
                "indicator_name": indicator_name,
                "indicator_data": value,
                "indicator_unit": extract_unit(match),
                "end_date": end_date,
                "source_span": span,
            })
    ambiguous = {span for span, names in span_names.items() if len(names) > 1}
    return [
        {
            "indicator_name": item["indicator_name"],
            "indicator_data": item["indicator_data"],
            "indicator_unit": item["indicator_unit"],
            "end_date": item["end_date"],
        }
        for item in candidates
        if item["source_span"] not in ambiguous
    ]
