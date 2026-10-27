import requests
from io import StringIO
import calendar

BASE_DOWNLOAD_URL = "http://service-1.example.invalid/api/file/download/"

def format_end_date(date_str):
    """
    将YYYYMM转换为YYYY-MM-DD（月末）
    例如：
    202607 -> 2026-07-31
    """
    date_str = str(date_str).strip()
    if len(date_str) != 6:
        return date_str
    year = int(date_str[:4])
    month = int(date_str[4:6])
    last_day = calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}-{last_day:02d}"

def main(params):
    file_md5 = params.get("fileMd5")

    if not file_md5:
        return {
            "code": -1,
            "msg": "缺少fileMd5",
            "OUTDATA": []
        }

    OUTDATA = []

    try:
        url = BASE_DOWNLOAD_URL + file_md5
        response = requests.get(url)

        if response.status_code != 200:
            return {
                "code": -1,
                "msg": "文件下载失败",
                "OUTDATA": []
            }

        content = response.content.decode("utf-8")

        for line in StringIO(content):
            line = line.strip()
            if not line:
                continue

            parts = line.split(",")
            if len(parts) != 2:
                continue

            OUTDATA.append({
                "end_date": format_end_date(parts[0]),
                "indicator_data": parts[1].strip()
            })

        return {
            "code": 0,
            "msg": "解析成功",
            "OUTDATA": OUTDATA
        }

    except Exception as e:
        return {
            "code": -1,
            "msg": str(e),
            "OUTDATA": []
        }