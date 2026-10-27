# -*- coding: utf-8 -*-

"""
区分嫌疑数据规则：

输入：
    OUTDATA

输出：
    OUTDATA
    warningdata

校验规则：

1. indicator_release_frequency 为“月”时：
   end_date 必须是对应月份最后一天
   → remark = "截止日期不是月末"

2. indicator_release_frequency 为“季”时：
   end_date 必须是3月、6月、9月、12月的最后一天
   → remark = "截止日期不是季末"

3. indicator_release_frequency 为“半年”时：
   end_date 必须是6月或12月的最后一天
   → remark = "截止日期不是半年末"

4. indicator_release_frequency 为“年”时：
   end_date 必须是12月31日
   → remark = "截止日期不是年末"

5. 同一条数据存在多个问题：
   → warningdata 只放一次
   → remark 合并多个问题

6. OUTDATA 原样输出，不删除数据。

7. warningdata 无异常时：
   indicator_code = "empty"
   indicator_name = "empty"

说明：
- indicator_data 为0不再作为异常判断。
"""

import calendar
import datetime


def is_empty(value):
    """判断字段是否为空"""

    if value is None:
        return True

    if isinstance(value, str):
        return value.strip() == ""

    return False


def parse_date(value):
    """将常见日期格式转换为date，解析失败返回None"""

    if value is None:
        return None

    if isinstance(value, datetime.datetime):
        return value.date()

    if isinstance(value, datetime.date):
        return value

    text = str(value).strip()

    if not text:
        return None

    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%Y年%m月%d日",
        "%Y年%m月%d号",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    ]

    for fmt in formats:

        try:
            return datetime.datetime.strptime(
                text,
                fmt
            ).date()

        except ValueError:
            continue

    return None


def is_month_end(value):
    """判断是否为月末"""

    dt = parse_date(value)

    if dt is None:
        return False

    last_day = calendar.monthrange(
        dt.year,
        dt.month
    )[1]

    return dt.day == last_day


def is_quarter_end(value):
    """判断是否为季末"""

    dt = parse_date(value)

    if dt is None:
        return False

    return (
        dt.month in (3, 6, 9, 12)
        and is_month_end(dt)
    )


def is_half_year_end(value):
    """判断是否为半年末"""

    dt = parse_date(value)

    if dt is None:
        return False

    return (
        dt.month in (6, 12)
        and is_month_end(dt)
    )


def is_year_end(value):
    """判断是否为年末"""

    dt = parse_date(value)

    if dt is None:
        return False

    return (
        dt.month == 12
        and dt.day == 31
    )


def main(params):

    OUTDATA = params.get(
        "OUTDATA",
        []
    )

    if not isinstance(
        OUTDATA,
        list
    ):
        OUTDATA = []

    warningdata = []

    for data in OUTDATA:

        if not isinstance(
            data,
            dict
        ):
            continue

        remarks = []

        # =========================
        # 获取频率和截止日期
        # =========================

        frequency = str(
            data.get(
                "indicator_release_frequency"
            ) or ""
        ).strip()

        end_date = data.get(
            "end_date"
        )

        # =========================
        # 规则1：月频
        # =========================

        if frequency == "月":

            if not is_empty(
                end_date
            ):

                if not is_month_end(
                    end_date
                ):

                    remarks.append(
                        "截止日期不是月末"
                    )

        # =========================
        # 规则2：季频
        # =========================

        elif frequency == "季":

            if not is_empty(
                end_date
            ):

                if not is_quarter_end(
                    end_date
                ):

                    remarks.append(
                        "截止日期不是季末"
                    )

        # =========================
        # 规则3：半年频
        # =========================

        elif frequency == "半年":

            if not is_empty(
                end_date
            ):

                if not is_half_year_end(
                    end_date
                ):

                    remarks.append(
                        "截止日期不是半年末"
                    )

        # =========================
        # 规则4：年频
        # =========================

        elif frequency == "年":

            if not is_empty(
                end_date
            ):

                if not is_year_end(
                    end_date
                ):

                    remarks.append(
                        "截止日期不是年末"
                    )

        # =========================
        # 有异常则进入warningdata
        # =========================

        if remarks:

            warning_item = data.copy()

            warning_item[
                "remark"
            ] = "；".join(
                remarks
            )

            warningdata.append(
                warning_item
            )

    # =========================
    # 没有异常时生成占位数据
    # =========================

    if not warningdata:

        warningdata.append({
            "indicator_code": "empty",
            "indicator_name": "empty"
        })

    return {
        "OUTDATA": OUTDATA,
        "warningdata": warningdata
    }