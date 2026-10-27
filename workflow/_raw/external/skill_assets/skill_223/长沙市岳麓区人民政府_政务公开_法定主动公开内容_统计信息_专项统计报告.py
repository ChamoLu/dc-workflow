# -*- coding: utf-8 -*-
r"""
采集数据源：长沙市岳麓区人民政府
数据来源编码：01020001931
数据源路径代码：2087181944050970626
路径：长沙市岳麓区人民政府\政务公开\法定主动公开内容\统计信息\专项统计报告
目标表：usrDFJJYXSJB
参考规则：html/html_rule/html0901.xlsx（第 6 行）；html/GZK_IndicatorName_html/指标名称正则逻辑_分路径代码.xlsx（Sheet 2087181944050970626）
网页核对链接：http://www.yuelu.gov.cn/yl_xxgk/xxgkml/tjxx/zxtjbg/202608/t20260803_12515961.html

指标名称取值逻辑：A 列 rule_indicator_name 仅在 B 列正则从实际 Markdown 匹配到同一网页指标后原样输出。
指标单位取值逻辑：地区生产总值、社会消费品零售总额取正文“亿元”；工业和固定资产投资及同比指标取同句百分号。
指标单位位置：各指标正文句中的“亿元”以及“同比增长/下降”后的百分号。
截止日期取值逻辑：从主标题“YYYY年1-6月……主要经济指标完成情况”读取统计期间，归一到 6 月 30 日。
截止日期位置：Markdown 主标题“2026年1-6月湘江新区行政区（岳麓区）主要经济指标完成情况”。
运行时输入：平台下载的 HTML 转换 Markdown；本代码不访问网页、不解析 HTML DOM。
"""

import json
import re
import unicodedata
from urllib.request import urlopen


BASE_DOWNLOAD_URL = "http://service-1.example.invalid/api/file/download/"
NEGATIVE_WORDS = ("减少", "下降", "降低", "少增", "多减")
PERIOD_RE = re.compile(r"(20\d{2})年1[-—–]6月.*?主要经济指标完成情况")
RULES = (
    ("地区生产总值累计值", r"GDP?((?:(?!,|，|。|；|、).)*?)[亿万]元", "亿元", False),
    ("地区生产总值累计同比", r"GDP?(?:(?!，|。|；).)*?[亿万]元[,，、]((?:(?!，|。|；).)*?)[%％]", "%", True),
    ("规上工业增加值累计同比", r"规模以上工业增加值((?:(?!，|。|；).)*?)[%％]", "%", True),
    ("固定资产投资累计同比", r"固定资产投资同比((?:(?!，|。|；).)*?)[%％]", "%", True),
    ("社会消费品零售总额累计值", r"社会消费品零售总额?((?:(?!,|，|。|；|、).)*?)[亿万]元", "亿元", False),
    ("社会消费品零售总额累计同比", r"社会消费品零售总额?(?:(?!，|。|；).)*?[亿万]元[,，、]((?:(?!，|。|；).)*?)[%％]", "%", True),
)


def main(params):
    markdown_md5 = params.get("markdownMD5")
    if not markdown_md5:
        return {"code": -1, "msg": "缺少 markdownMD5", "OUTDATA": []}
    try:
        records = parse_markdown(decode_markdown(download_markdown(markdown_md5)))
        json.dumps(records, ensure_ascii=False, allow_nan=False)
        return {"code": 0, "msg": "success", "OUTDATA": records}
    except Exception as exc:
        return {"code": -99, "msg": "解析失败:" + str(exc), "OUTDATA": []}


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


def markdown_blocks(markdown):
    normalized = unicodedata.normalize("NFKC", str(markdown or "")).replace("\r", "\n")
    blocks = []
    for paragraph in re.split(r"\n\s*\n", normalized):
        compact = re.sub(r"\s+", "", paragraph.replace("|", " "))
        compact = re.sub(r"^[#>*+-]+", "", compact)
        if compact:
            blocks.extend(piece for piece in re.split(r"(?<=[。；;])", compact) if piece)
    return blocks


def period_end(markdown):
    match = PERIOD_RE.search(unicodedata.normalize("NFKC", markdown))
    return f"{int(match.group(1)):04d}-06-30" if match else ""


def captured_number(match, is_rate):
    captured = next((value for value in match.groups() if value is not None), "")
    numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", unicodedata.normalize("NFKC", captured).replace(",", ""))
    if not numbers:
        return None
    number_text = numbers[-1]
    if is_rate and any(word in match.group(0) for word in NEGATIVE_WORDS) and not number_text.startswith("-"):
        number_text = "-" + number_text.lstrip("+")
    return float(number_text) if "." in number_text else int(number_text)


def parse_markdown(markdown):
    end_date = period_end(markdown)
    blocks = markdown_blocks(markdown)
    records = []
    for indicator_name, pattern, unit, is_rate in RULES:
        matches = []
        for block in blocks:
            match = re.search(pattern, block)
            if match:
                value = captured_number(match, is_rate)
                if value is not None:
                    matches.append(value)
        if len(matches) == 1:
            records.append({"indicator_name": indicator_name, "indicator_data": matches[0], "indicator_unit": unit, "end_date": end_date})
    return records
