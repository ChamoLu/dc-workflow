def main(params):

    OUTDATA_SOURCE = params.get("OUTDATA", [])
    OUTDATA = []

    for item in OUTDATA_SOURCE:

        indicator_code = str(
            item.get("indicator_code") or ""
        ).strip()

        indicator_data = str(
            item.get("indicator_data") or ""
        ).strip()

        end_date = str(
            item.get("end_date") or ""
        ).strip()

        # 删除无效数据
        if indicator_code == "" or indicator_code.lower() == "empty":
            continue

        if indicator_data == "" or indicator_data.lower() == "empty":
            continue

        if end_date == "":
            continue

        OUTDATA.append(item)

    # 全部过滤后，保留占位数据
    if not OUTDATA:
        OUTDATA.append({
            "indicator_code": "empty",
            "indicator_data": "empty",
            "end_date": "",
            "remark": "empty"
        })

    return {
        "OUTDATA": OUTDATA
    }