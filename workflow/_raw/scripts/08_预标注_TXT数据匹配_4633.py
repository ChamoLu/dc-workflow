def main(params):
    # 上一步OUTDATA
    OUTDATA_SOURCE = params.get("OUTDATA", [])
    # 规则库匹配结果
    GZKJG = params.get("GZKJG", [])
    # 外部统一传入的发布日期
    publictime = params.get("publictime", "")

    OUTDATA = []
    matched_count = 0

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
            indicator_release_frequency = str(
                rule.get("indicator_release_frequency") or ""
            ).strip()

            OUTDATA.append({
                "indicator_code": indicator_code,
                "indicator_name": indicator_name,
                "indicator_data": indicator_data,
                "indicator_unit": indicator_unit,
                "KB_unit": kb_unit,
                "convert_ratio": convert_ratio,
                "unit_ratio": str(rule.get("rule_unit_ratio") or ""),
                "indicator_release_frequency": indicator_release_frequency,
                "publictime": publictime,
                "end_date": data.get("end_date", ""),
                "remark": data.get("remark", ""),
                "PLPL": rule.get("PLPL", "")
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
            "indicator_release_frequency": "",
            "publictime": publictime,
            "end_date": "",
            "remark": "",
            "PLPL": ""
        })

    return {
        "OUTDATA": OUTDATA
    }