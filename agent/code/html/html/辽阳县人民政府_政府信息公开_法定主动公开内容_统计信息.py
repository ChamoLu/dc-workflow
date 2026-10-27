"""辽阳县统计信息 Markdown 解析器；路径代码2087109316577619970。
设计检查：提供的Markdown L1—L21；参考rule_html.xlsx同名Sheet A1:B8。
参考产业名称省略“增加值”，本代码采用L17实际业务名称；参考正则
未兼容转换空格，故按源段落解析。不使用GZK白名单或运行时规则库。
金额、同比单位逐片段绑定；期间来自正文或Markdown标题，不使用发布时间。
本代码未在此生成环境执行，不宣称已完成平台回归。
"""
import re
import calendar
import unicodedata
from urllib.parse import quote
import requests

BASE = 'http://service-1.example.invalid/api/file/download/'
NUM = r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
LABEL = r'(?:地区生产总值|第[一二三]产业增加值)'


def normalize(text):
    text = unicodedata.normalize('NFKC', text)
    # Only remove conversion whitespace between Chinese characters/digits.
    return re.sub(r'(?<=[\u4e00-\u9fff0-9])\s+(?=[\u4e00-\u9fff0-9])', '', text)


def period(text):
    text = normalize(text)
    pattern = r'(\d{4})年\s*(上半年|全年|[一二三四1234]季度|第[一二三四1234]季度|(?:1[-—至到])?\d{1,2}月)'
    found = []
    for m in re.finditer(pattern, text):
        year = int(m.group(1))
        term = m.group(2)
        if term == '上半年':
            month = 6
        elif term == '全年':
            month = 12
        elif '季度' in term:
            q = term.replace('第', '').replace('季度', '')
            month = int(q) * 3 if q.isdigit() else ('一二三四'.index(q) + 1) * 3
        else:
            month = int(re.search(r'(\d{1,2})月', term).group(1))
        if 1 <= year <= 9999 and 1 <= month <= 12:
            date = '%04d-%02d-%02d' % (year, month, calendar.monthrange(year, month)[1])
            if date not in found:
                found.append(date)
    return found[0] if len(found) == 1 else ''


def extract(text):
    lines = text.splitlines()
    normalized = [normalize(line) for line in lines]
    # Prefer dated business prose, then Markdown headings. Never use publication lines.
    global_date, global_refs = '', []
    for heading in (False, True):
        candidates = []
        for i, line in enumerate(normalized):
            if bool(line.lstrip().startswith('#')) != heading:
                continue
            if not re.search(LABEL, line):
                continue
            date = period(line)
            if date:
                candidates.append((date, 'L%d' % (i + 1)))
        dates = {d for d, ref in candidates}
        if len(dates) == 1:
            global_date = candidates[0][0]
            global_refs = [candidates[0][1]]
            break
        if len(dates) > 1:
            break
    records, evidence = [], []
    amount = re.compile('(' + LABEL + r')\s*(' + NUM + r')\s*(亿元)')
    growth = re.compile(r'同比\s*(增长|上升|增加|下降|减少|降低|少增|多减)?\s*(' + NUM + r')\s*(%)')
    for i, line in enumerate(normalized):
        ref = 'L%d' % (i + 1)
        anchors = list(re.finditer(LABEL, line))
        for j, anchor in enumerate(anchors):
            end = anchors[j + 1].start() if j + 1 < len(anchors) else len(line)
            fragment = line[anchor.start():end]
            a = amount.match(fragment)
            if not a:
                continue
            # Local explicit periods override document-level periods.
            prefix = line[:anchor.start()]
            local_date = period(prefix)
            date = local_date or global_date
            date_refs = [ref] if local_date else list(global_refs)
            label = a.group(1)
            def append(suffix, value, unit):
                records.append({'indicator_name': label + suffix,
                                'indicator_data': value.replace(',', ''),
                                'indicator_unit': unit, 'end_date': date})
                evidence.append({'record_index': len(records) - 1,
                                 'name_refs': [ref], 'data_ref': ref,
                                 'unit_refs': [ref], 'date_refs': date_refs[:]})
            append('累计值', a.group(2), a.group(3))
            # Stop at a sentence or semicolon, but not decimal points.
            tail = re.split(r'[。;；]', fragment[a.end():], maxsplit=1)[0]
            g = growth.search(tail)
            if g:
                value = g.group(2)
                if g.group(1) in ('下降', '减少', '降低', '少增', '多减'):
                    value = '-' + value.lstrip('+-')
                append('累计同比', value, g.group(3))
    missing = sum(not r['end_date'] for r in records)
    msg = 'ok' if records else 'No supported business records found in downloaded Markdown'
    if missing:
        msg += '; %d records lack reliable statistical periods' % missing
    return {'code': 0, 'msg': msg, 'ExcelData': records}, evidence


def run(params):
    md5 = params.get('markdownMD5')
    if not isinstance(md5, str) or not md5.strip():
        return {'code': 1, 'msg': 'markdownMD5 is required', 'ExcelData': []}, []
    try:
        response = requests.get(BASE + quote(md5.strip(), safe=''), timeout=60)
        response.raise_for_status()
        text = response.content.decode('utf-8-sig')
        return extract(text)
    except Exception as exc:
        return {'code': 1, 'msg': 'Markdown download/parse failed: ' + str(exc), 'ExcelData': []}, []


def main(params):
    result, _ = run(params)
    return result


def _dc_validate(params):
    result, evidence = run(params)
    result['_dc_evidence'] = evidence
    return result
