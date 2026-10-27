"""Conservative source-supported normalization; no fuzzy extraction or calculations."""

import calendar
import re
import unicodedata


def positive_percentage_points(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFKC", value).strip()
    match = re.fullmatch(r"(?:提高|上升|增加)\s*(\+?\d+(?:\.\d+)?)\s*(个百分点|百分点)", text)
    return (match[1].lstrip("+"), match[2]) if match else None


def title_month_end(title: str) -> str | None:
    text = unicodedata.normalize("NFKC", title)
    if any(word in text for word in ("季度", "半年", "全年")):
        return None
    periods = re.findall(r"(?<!\d)((?:19|20)\d{2})年(1[0-2]|0?[1-9])月", text)
    months = re.findall(r"(?<!\d)(?:1[0-2]|0?[1-9])月", text)
    if len(periods) != 1 or len(months) != 1:
        return None
    year, month = map(int, periods[0])
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
