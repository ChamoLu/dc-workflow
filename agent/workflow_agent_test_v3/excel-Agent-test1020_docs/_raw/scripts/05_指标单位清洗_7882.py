from decimal import Decimal, InvalidOperation


def to_decimal(value, default="0"):
    """将输入转换为Decimal，空值或非法值使用默认值。"""
    try:
        if value is None:
            return Decimal(default)
        if isinstance(value, list):
            if not value:
                return Decimal(default)
            value = value[0]
        value = str(value).replace(",", "").strip()
        if value == "":
            return Decimal(default)
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def add_rule(rule_map, indicator_unit, KB_unit, ratio):
    """将单位转换规则加入字典。"""
    indicator_unit = str(indicator_unit or "").strip()
    KB_unit = str(KB_unit or "").strip()
    ratio = str(ratio or "").strip()

    if indicator_unit and KB_unit and ratio:
        rule_map[(indicator_unit, KB_unit)] = ratio


def main(params):
    # 指标数据
    OUTDATA = params.get("OUTDATA", [])
    # SQL转换率查询结果
    unitArray = params.get("unitArray", {})

    # 建立“采集单位+库表单位”与换算比例的映射
    unit_ratio_map = {}

    # 兼容SQL返回的列式结构
    if isinstance(unitArray, dict):
        indicator_units = unitArray.get("indicator_unit", [])
        KB_units = unitArray.get("KB_unit", [])
        ratios = unitArray.get("convert_ratio", [])

        if not isinstance(indicator_units, list):
            indicator_units = [indicator_units]
        if not isinstance(KB_units, list):
            KB_units = [KB_units]
        if not isinstance(ratios, list):
            ratios = [ratios]

        data_count = min(
            len(indicator_units),
            len(KB_units),
            len(ratios)
        )

        for index in range(data_count):
            add_rule(
                unit_ratio_map,
                indicator_units[index],
                KB_units[index],
                ratios[index]
            )

    # 逐条匹配OUTDATA并进行单位换算
    for item in OUTDATA:
        indicator_unit = str(
            item.get("indicator_unit") or ""
        ).strip()

        KB_unit = str(
            item.get("KB_unit") or ""
        ).strip()

        matched_ratio = unit_ratio_map.get(
            (indicator_unit, KB_unit)
        )

        # SQL匹配不到时转换率默认为1
        if matched_ratio is not None:
            final_ratio = matched_ratio
        else:
            final_ratio = "1"

        item["convert_ratio"] = final_ratio

        # 有效指标数据才进行单位换算
        indicator_data = item.get("indicator_data", "")

        if (
            indicator_data is None
            or str(indicator_data).strip() == ""
            or str(indicator_data).strip() == "empty"
        ):
            continue

        value = to_decimal(indicator_data, "0")
        ratio = to_decimal(final_ratio, "1")

        item["indicator_data"] = str(
            value * ratio
        )

    return {
        "OUTDATA": OUTDATA
    }