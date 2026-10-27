import re

# ======================
# 判断是否为数字
# ======================
def check_number(value):
    if value is None:
        return False
    value = str(value).strip()
    if value == "":
        return False
    return re.match(r"^-?\d+(\.\d+)?$", value) is not None

def main(params):
    # ======================
    # 获取采集数据
    # ======================
    indicator_name = (params.get("indicator_name") or "").strip()
    indicator_data = params.get("indicator_data")

    if not check_number(indicator_data):
        indicator_data = "empty"

    indicator_unit = params.get("indicator_unit") or ""
    end_date = params.get("end_date") or ""
    publictime = params.get("publictime") or ""
    remark = params.get("remark") or ""
    rid = params.get("rid") or ""

    # ======================
    # 获取规则库
    # ======================
    GZKJG = params.get("GZKJG", [])

    OUTDATA = []
    matched = False
    KB_unit = ""

    # ======================
    # 指标匹配
    # ======================
    for rule in GZKJG:
        rule_name = (
            rule.get("rule_indicator_name") or ""
        ).strip()

        if rule_name == indicator_name:
            matched = True

            KB_unit = (
                rule.get("rule_constant_name") or ""
            )

            OUTDATA.append({
                "indicator_code": str(
                    rule.get("rule_indicator_code") or ""
                ),
                "indicator_name": indicator_name,
                "indicator_data": indicator_data,
                "indicator_unit": indicator_unit,
                "unit_ratio": str(
                    rule.get("rule_unit_ratio") or ""
                ),
                "indicator_release_frequency": rule.get(
                    "indicator_release_frequency",
                    ""
                ),
                "end_date": end_date,
                "rid": rid,
                "publictime": publictime,
                "remark": remark
            })

    # ======================
    # 未匹配处理
    # ======================
    if not matched:
        OUTDATA.append({
            "indicator_code": "empty",
            "indicator_name": indicator_name,
            "indicator_data": indicator_data,
            "indicator_unit": indicator_unit,
            "unit_ratio": "",
            "indicator_release_frequency": "",
            "end_date": end_date,
            "rid": rid,
            "publictime": publictime,
            "remark": remark
        })

    # ======================
    # 输出
    # ======================
    return {
        "OUTDATA": OUTDATA,
        "KB_unit": KB_unit
    }