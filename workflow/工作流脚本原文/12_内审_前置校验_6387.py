# -*- coding: utf-8 -*-

"""
前置校验规则：

输入：
    OUTDATA

输出：
    OUTDATA
    warningdata

校验规则：

1. indicator_code 长度不是10位：
   → 放入 warningdata
   → remark = "指标代码长度不是10位"

2. publictime > 当前系统日期：
   → 放入 warningdata
   → remark = "信息发布日期-信息发布日期大于当前系统日期"

3. publictime < end_date：
   → 放入 warningdata
   → remark = "信息发布日期-信息发布日期小于截止日期"

4. indicator_release_frequency 根据披露频率校验 end_date：

   月：
   → end_date 必须是所在月份最后一天
   → 否则 remark = "截止日期不是月末"

   季：
   → end_date 必须是：
      03-31
      06-30
      09-30
      12-31
   → 否则 remark = "截止日期不是季末"

   半年：
   → end_date 必须是：
      06-30
      12-31
   → 否则 remark = "截止日期不是半年末"

   年：
   → end_date 必须是：
      12-31
   → 否则 remark = "截止日期不是年末"

5. problem_fin_time 结合 end_date 独立判断：

   1002：
   → end_date 必须是月末
   → 否则 remark = "非月末数据"

   1003：
   → end_date 必须是季末
   → 否则 remark = "非季末数据"

   1004：
   → end_date 必须是年末
   → 否则 remark = "非年末数据"

处理：
- indicator_release_frequency 和 problem_fin_time 是两个独立字段，
  分别执行各自的校验逻辑。
- indicator_data 是否为0不再校验。
- unit_ratio 不再校验。
- 同一条数据存在多个问题时，warningdata 只放一次。
- 多个问题合并到 remark。
- 相同 remark 不重复。
- OUTDATA 原样输出，不删除数据。
- warningdata 无异常时生成 empty 占位数据。
"""

import calendar
import datetime


def has_value(value):
    """判断字段是否有值"""

    if value is None:
        return False

    if isinstance(value, str):
        return value.strip() != ""

    return True


def parse_date(value):
    """
    将常见日期格式转换为 date。
    解析失败返回 None。
    """

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

    # ISO格式兜底
    try:
        return datetime.datetime.fromisoformat(
            text.replace("Z", "+00:00")
        ).date()
    except Exception:
        pass

    return None


def is_month_end(value):
    """
    判断是否为月末。
    """

    dt = parse_date(value)

    if dt is None:
        return False

    last_day = calendar.monthrange(
        dt.year,
        dt.month
    )[1]

    return dt.day == last_day


def is_quarter_end(value):
    """
    判断是否为季末。

    合法日期：
    03-31
    06-30
    09-30
    12-31
    """

    dt = parse_date(value)

    if dt is None:
        return False

    return (
        dt.month in (3, 6, 9, 12)
        and is_month_end(dt)
    )


def is_half_year_end(value):
    """
    判断是否为半年末。

    合法日期：
    06-30
    12-31
    """

    dt = parse_date(value)

    if dt is None:
        return False

    return (
        dt.month in (6, 12)
        and is_month_end(dt)
    )


def is_year_end(value):
    """
    判断是否为年末。

    合法日期：
    12-31
    """

    dt = parse_date(value)

    if dt is None:
        return False

    return (
        dt.month == 12
        and dt.day == 31
    )


def add_remark(remarks, remark):
    """
    添加remark，相同remark不重复。
    """

    if remark not in remarks:
        remarks.append(remark)


def main(params):

    # ==================================================
    # 获取输入
    # ==================================================

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

    today = datetime.date.today()

    # ==================================================
    # 逐条校验 OUTDATA
    # ==================================================

    for data in OUTDATA:

        if not isinstance(
            data,
            dict
        ):
            continue

        remarks = []

        # ==================================================
        # 获取字段
        # ==================================================

        indicator_code = str(
            data.get(
                "indicator_code"
            ) or ""
        ).strip()

        publictime = data.get(
            "publictime",
            ""
        )

        end_date = data.get(
            "end_date",
            ""
        )

        indicator_release_frequency = str(
            data.get(
                "indicator_release_frequency"
            ) or ""
        ).strip()

        problem_fin_time = data.get(
            "problem_fin_time",
            ""
        )

        # ==================================================
        # 日期转换
        # ==================================================

        publictime_date = parse_date(
            publictime
        )

        end_date_obj = parse_date(
            end_date
        )

        # ==================================================
        # 规则1：
        # indicator_code 必须为10位
        # ==================================================

        if len(indicator_code) != 10:

            add_remark(
                remarks,
                "指标代码长度不是10位"
            )

        # ==================================================
        # 规则2：
        # 信息发布日期不能大于当前系统日期
        # ==================================================

        if (
            publictime_date is not None
            and publictime_date > today
        ):

            add_remark(
                remarks,
                "信息发布日期-信息发布日期大于当前系统日期"
            )

        # ==================================================
        # 规则3：
        # 信息发布日期不能小于截止日期
        # ==================================================

        if (
            publictime_date is not None
            and end_date_obj is not None
            and publictime_date < end_date_obj
        ):

            add_remark(
                remarks,
                "信息发布日期-信息发布日期小于截止日期"
            )

        # ==================================================
        # 规则4：
        # indicator_release_frequency
        # 根据披露频率判断对应周期末
        # ==================================================

        if end_date_obj is not None:

            # =========================
            # 月频
            # =========================

            if indicator_release_frequency == "月":

                if not is_month_end(
                    end_date_obj
                ):

                    add_remark(
                        remarks,
                        "截止日期不是月末"
                    )

            # =========================
            # 季频
            # =========================

            elif indicator_release_frequency == "季":

                if not is_quarter_end(
                    end_date_obj
                ):

                    add_remark(
                        remarks,
                        "截止日期不是季末"
                    )

            # =========================
            # 半年频
            # =========================

            elif indicator_release_frequency == "半年":

                if not is_half_year_end(
                    end_date_obj
                ):

                    add_remark(
                        remarks,
                        "截止日期不是半年末"
                    )

            # =========================
            # 年频
            # =========================

            elif indicator_release_frequency == "年":

                if not is_year_end(
                    end_date_obj
                ):

                    add_remark(
                        remarks,
                        "截止日期不是年末"
                    )

        # ==================================================
        # 规则5：
        # problem_fin_time 独立判断
        #
        # 1002 → 月末
        # 1003 → 季末
        # 1004 → 年末
        # ==================================================

        if (
            has_value(
                problem_fin_time
            )
            and end_date_obj is not None
        ):

            problem_text = str(
                problem_fin_time
            )

            # =========================
            # 1002：必须为月末
            # =========================

            if (
                "1002" in problem_text
                and not is_month_end(
                    end_date_obj
                )
            ):

                add_remark(
                    remarks,
                    "非月末数据"
                )

            # =========================
            # 1003：必须为季末
            # =========================

            if (
                "1003" in problem_text
                and not is_quarter_end(
                    end_date_obj
                )
            ):

                add_remark(
                    remarks,
                    "非季末数据"
                )

            # =========================
            # 1004：必须为年末
            # =========================

            if (
                "1004" in problem_text
                and not is_year_end(
                    end_date_obj
                )
            ):

                add_remark(
                    remarks,
                    "非年末数据"
                )

        # ==================================================
        # 有任意异常
        # → 放入 warningdata
        # ==================================================

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

    # ==================================================
    # 没有异常
    # → 生成 empty 占位
    # ==================================================

    if not warningdata:

        warningdata.append({
            "indicator_code": "empty",
            "indicator_name": "empty",
            "indicator_data": "empty",
            "end_date": "",
            "publictime": "",
            "indicator_release_frequency": "",
            "problem_fin_time": "",
            "remark": ""
        })

    # ==================================================
    # 输出
    # ==================================================

    return {
        "OUTDATA": OUTDATA,
        "warningdata": warningdata
    }