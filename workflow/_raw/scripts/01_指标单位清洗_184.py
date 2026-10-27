from decimal import Decimal, InvalidOperation

def to_decimal(value, default="1"):
    try:
        if value is None:
            return Decimal(default)

        # 如果是数组，取第一项
        if isinstance(value, list):
            if len(value) == 0:
                return Decimal(default)
            value = value[0]

        # 如果是对象，尝试取 convert_ratio
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

    convert_ratio = to_decimal(params.get("convert_ratio"), "1")

    for item in OUTDATA:
        value = to_decimal(item.get("indicator_data"), "0")
        item["indicator_data"] = str(value * convert_ratio)

    return {
        "OUTDATA": OUTDATA
    }