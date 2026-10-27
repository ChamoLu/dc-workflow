def main(params):

    OUTDATA_SOURCE = params.get("OUTDATA", [])
    GZKJG = params.get("GZKJG", [])
    publictime = params.get("publictime", "")

    OUTDATA = []
    matched_count = 0

    # SQL转换率查询使用的去重单位
    indicator_unit_array = []
    KB_unit_array = []
    indicator_unit_seen = set()
    KB_unit_seen = set()

    for data in OUTDATA_SOURCE:

        indicator_code = str(
            data.get("indicator_code") or ""
        ).strip()

        for rule in GZKJG:

            rule_code = str(
                rule.get("rule_indicator_code") or ""
            ).strip()

            if rule_code == indicator_code:

                matched_count += 1

                indicator_unit = str(
                    data.get("indicator_unit") or ""
                ).strip()

                KB_unit = str(
                    rule.get("rule_constant_name") or ""
                ).strip()

                # 采集单位去重
                if (
                    indicator_unit
                    and indicator_unit not in indicator_unit_seen
                ):
                    indicator_unit_seen.add(indicator_unit)
                    indicator_unit_array.append(indicator_unit)

                # 库表单位去重
                if (
                    KB_unit
                    and KB_unit not in KB_unit_seen
                ):
                    KB_unit_seen.add(KB_unit)
                    KB_unit_array.append(KB_unit)

                OUTDATA.append({
                    "indicator_code": indicator_code,
                    "indicator_name": data.get(
                        "indicator_name",
                        ""
                    ),
                    "indicator_data": data.get(
                        "indicator_data",
                        ""
                    ),
                    "indicator_unit": indicator_unit,
                    "KB_unit": KB_unit,
                    "unit_ratio": str(
                        rule.get(
                            "rule_unit_ratio"
                        ) or ""
                    ),
                    "indicator_release_frequency": rule.get(
                        "indicator_release_frequency",
                        ""
                    ),
                    "end_date": data.get(
                        "end_date",
                        ""
                    ),
                    "publictime": publictime,
                    "remark": data.get(
                        "remark",
                        ""
                    ),
                    "PLPL": data.get(
                        "PLPL",
                        ""
                    ),
                    "SJPLLB": data.get(
                        "SJPLLB",
                        ""
                    )
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
            "unit_ratio": "",
            "indicator_release_frequency": "",
            "end_date": "",
            "publictime": publictime,
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