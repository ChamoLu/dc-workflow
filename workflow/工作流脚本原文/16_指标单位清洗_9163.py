from decimal import Decimal, InvalidOperation


def to_decimal(value, default="1"):
    try:
        if value is None:
            return Decimal(default)

        if isinstance(value, list):
            if len(value) == 0:
                return Decimal(default)
            value = value[0]

        if isinstance(value, dict):
            value = value.get("convert_ratio", default)

        value = str(value).replace(",", "").strip()

        if value == "":
            return Decimal(default)

        return Decimal(value)

    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def main(params):
    OUTDATA = params.get("OUTDATA", [])

    # 转换率为空/异常时默认 1
    convert_ratio = to_decimal(
        params.get("convert_ratio"),
        "1"
    )

    for item in OUTDATA:
        indicator_data = item.get("indicator_data")

        # 前面已经校验过，empty 直接传递
        if indicator_data == "empty":
            continue

        value = to_decimal(indicator_data, "0")

        item["indicator_data"] = str(
            value * convert_ratio
        )

    return {
        "OUTDATA": OUTDATA
    }