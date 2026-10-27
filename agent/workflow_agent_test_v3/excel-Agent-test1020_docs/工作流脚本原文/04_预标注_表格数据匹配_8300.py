def main(params):
    # Excel解析结果
    OUTDATA_SOURCE = params.get("ExcelData", [])
    # 规则库匹配结果
    GZKJG = params.get("GZKJG", [])
    # 外部统一传入的发布日期
    publictime = params.get("publictime", "")

    OUTDATA = []
    matched_count = 0

    # SQL转换率查询使用的去重单位
    indicator_unit_array = []
    KB_unit_array = []
    indicator_unit_seen = set()
    KB_unit_seen = set()

    for data in OUTDATA_SOURCE:
        indicator_name = str(data.get("indicator_name") or "").strip()

        for rule in GZKJG:
            rule_name = str(rule.get("rule_indicator_name") or "").strip()

            if rule_name != indicator_name:
                continue

            matched_count += 1

            indicator_code = str(rule.get("rule_indicator_code") or "").strip()
            indicator_data = data.get("indicator_data", "")
            indicator_unit = str(data.get("indicator_unit") or "").strip()
            kb_unit = str(rule.get("rule_constant_name") or "").strip()
            convert_ratio = str(rule.get("convert_ratio") or "").strip()

            # 采集单位去重
            if indicator_unit and indicator_unit not in indicator_unit_seen:
                indicator_unit_seen.add(indicator_unit)
                indicator_unit_array.append(indicator_unit)

            # 库表单位去重
            if kb_unit and kb_unit not in KB_unit_seen:
                KB_unit_seen.add(kb_unit)
                KB_unit_array.append(kb_unit)

            OUTDATA.append({
                "indicator_code": indicator_code,
                "indicator_name": indicator_name,
                "indicator_data": indicator_data,
                "indicator_unit": indicator_unit,
                "KB_unit": kb_unit,
                "convert_ratio": convert_ratio,
                "unit_ratio": str(rule.get("rule_unit_ratio") or ""),
                "publictime": publictime,
                "end_date": data.get("end_date", ""),
                "remark": data.get("remark", ""),
                "PLPL": rule.get("PLPL", ""),
                "SJPLLB": ""
            })
            break

    # 所有指标都未匹配时输出标准空结构
    if matched_count == 0:
        OUTDATA.append({
            "indicator_code": "empty",
            "indicator_name": "",
            "indicator_data": "",
            "indicator_unit": "",
            "KB_unit": "",
            "convert_ratio": "",
            "unit_ratio": "",
            "publictime": publictime,
            "end_date": "",
            "remark": "",
            "PLPL": "",
            "SJPLLB": ""
        })

    # SQL节点必须有输入
    if not indicator_unit_array:
        indicator_unit_array = ["-"]

    if not KB_unit_array:
        KB_unit_array = ["-"]

    unitArray = {
        "indicator_unit": indicator_unit_array,
        "KB_unit": KB_unit_array
    }

    return {
        "OUTDATA": OUTDATA,
        "unitArray": unitArray
    }