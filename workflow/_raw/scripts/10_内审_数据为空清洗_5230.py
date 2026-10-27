def main(params):

    OUTDATA_SOURCE = params.get("OUTDATA", [])
    OUTDATA = []

    for item in OUTDATA_SOURCE:

        indicator_code_value = item.get("indicator_code")
        indicator_data_value = item.get("indicator_data")
        end_date_value = item.get("end_date")

        indicator_code = "" if indicator_code_value is None else str(indicator_code_value).strip()
        indicator_data = "" if indicator_data_value is None else str(indicator_data_value).strip()
        end_date = "" if end_date_value is None else str(end_date_value).strip()

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