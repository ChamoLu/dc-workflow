# 明溪县人民政府；路径2087461727796207617；目标表usrDFJJYXSJB。
# 已检查所提供Markdown全部L1-L15。参考仅核对rule_html.xlsx同路径Sheet A1:B4。
# 参考累计口径不受正文支持，不添加累计限定；保留源文“地方”。
# 仅下载转换Markdown。日期优先正文完整年月，否则使用title唯一年月，
# 并核对正文月份；不读取publictime。金额、比例逐分句绑定源单位。
# 提取本期金额、预算完成比例和同比；同期金额、预算金额、增减额暂不输出。
# 未在生成环境执行下载回归；覆盖范围不代表源数据全部覆盖。
import re
import calendar
import unicodedata
from urllib.parse import quote
import requests

BASE = 'http://service-1.example.invalid/api/file/download/'
NUM = r'([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)'


def norm(text):
    return unicodedata.normalize('NFKC', text).strip()


def periods(text):
    return set((int(y), int(m)) for y, m in re.findall(r'(?<!\d)(\d{4})\s*年\s*(\d{1,2})\s*月', text) if 1 <= int(m) <= 12 and 1 <= int(y) <= 9999)


def month_end(pair):
    if not pair:
        return ''
    y, m = pair
    return '%04d-%02d-%02d' % (y, m, calendar.monthrange(y, m)[1])


def extract(text, title):
    lines = text.splitlines()
    normalized = [norm(x) for x in lines]
    full = periods('\n'.join(normalized))
    title_periods = periods(norm(title))
    default = next(iter(full)) if len(full) == 1 else None
    title_default = next(iter(title_periods)) if len(title_periods) == 1 else None
    full_refs = ['L%d' % (i + 1) for i, line in enumerate(normalized) if periods(line)]
    data, evidence = [], []
    category = ''
    category_ref = None
    section_month = None
    month_ref = None
    unresolved_dates = False

    def append(name, value, unit, ref, parents, date, date_refs):
        data.append({'indicator_name': name, 'indicator_data': value.replace(',', ''), 'indicator_unit': unit, 'end_date': date})
        evidence.append({'record_index': len(data) - 1, 'name_refs': list(dict.fromkeys(parents + [ref])), 'data_ref': ref, 'unit_refs': [ref], 'date_refs': date_refs})

    for index, line in enumerate(normalized):
        ref = 'L%d' % (index + 1)
        if re.search(r'一般公共预算收支基本情况', line):
            category, category_ref = '一般公共预算', ref
            section_month, month_ref = None, None
            continue
        if re.search(r'基金预算收支基本情况', line):
            category, category_ref = '政府性基金预算', ref
            section_month, month_ref = None, None
            continue
        if not category:
            continue
        local_months = set(int(m) for m in re.findall(r'(?<!\d)(\d{1,2})\s*月', line) if 1 <= int(m) <= 12)
        if len(local_months) == 1:
            section_month, month_ref = next(iter(local_months)), ref
        local_full = periods(line)
        pair = None
        date_refs = []
        if len(local_full) == 1:
            pair = next(iter(local_full))
            date_refs = [ref]
        elif not local_full:
            candidate = default if default else (title_default if not full else None)
            if candidate and (section_month is None or section_month == candidate[1]):
                pair = candidate
                date_refs = list(full_refs) if default else ['runtime:title']
                if month_ref:
                    date_refs.append(month_ref)
        date = month_end(pair)
        parent_income = None
        for clause in re.split(r'[。;；]', line):
            clause = clause.strip()
            if not clause:
                continue
            # Remove only observed enumeration, period and introductory labels.
            clause = re.sub(r'^\d+\s*[、.]\s*', '', clause)
            clause = re.sub(r'^(?:\d{4}\s*年\s*)?\d{1,2}\s*月\s*全县', '', clause)
            clause = re.sub(r'^支出科目变动情况[:：]\s*', '', clause)
            clause = re.sub(r'^其中[:：]?\s*', '', clause)
            match = re.match(r'([^,，。;；:：\d]+?)(?:完成)?\s*' + NUM + r'\s*(万元)', clause)
            if not match:
                continue
            label, value, unit = match.groups()
            label = label.strip()
            if not label:
                continue
            if label.startswith('政府性基金预算收入'):
                parent_income = label
            if label == '国有土地使用权出让金':
                if not parent_income:
                    continue
                name = parent_income + label
            elif '预算' in label:
                name = label
            elif label.endswith('支出'):
                name = category + label
            else:
                continue
            parents = [category_ref] if category_ref else []
            append(name, value, unit, ref, parents, date, date_refs)
            if not date:
                unresolved_dates = True
            tail = clause[match.end():]
            budget = re.search(r'占年初预算\s*' + NUM + r'\s*万元的\s*' + NUM + r'\s*(%)', tail)
            if budget:
                append(name + '占年初预算比例', budget.group(2), budget.group(3), ref, parents, date, date_refs)
            growth = re.search(r'(增长|下降)\s*' + NUM + r'\s*(%)', tail)
            if growth:
                value = growth.group(2)
                if growth.group(1) == '下降' and not value.startswith('-') and value.lstrip('+').replace(',', '') not in ('0', '0.0'):
                    value = '-' + value.lstrip('+')
                append(name + '同比', value, growth.group(3), ref, parents, date, date_refs)
    msg = '解析完成；未提取同期金额、年度预算金额及增减额'
    if unresolved_dates:
        msg += '；部分统计期无法可靠确定，已留空'
    if not data:
        msg = '未识别到支持的预算数据；不代表源文件无数据'
    return {'code': 0, 'msg': msg, 'ExcelData': data}, evidence


def run(params):
    md5 = params.get('markdownMD5')
    if not md5:
        return {'code': 1, 'msg': '缺少markdownMD5', 'ExcelData': []}, []
    try:
        response = requests.get(BASE + quote(str(md5), safe=''), timeout=60)
        response.raise_for_status()
        raw = response.content
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('gb18030')
        return extract(text, str(params.get('title') or ''))
    except Exception as exc:
        return {'code': 1, 'msg': '下载或解析失败: ' + str(exc), 'ExcelData': []}, []


def main(params):
    result, _ = run(params)
    return result


def _dc_validate(params):
    result, evidence = run(params)
    result['_dc_evidence'] = evidence
    return result
