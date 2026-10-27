def main(params):
    data = params.get("GZK", {})

    codes = data.get("ZBDM", [])
    names = data.get("ZBMC", [])
    constants = data.get("ZBDW", [])
    ratios = data.get("unit_ratio", [])
    frequencies = data.get("indicator_release_frequency", [])

    max_len = max(
        len(codes),
        len(names),
        len(constants),
        len(ratios),
        len(frequencies)
    )

    GZKJG = []

    for i in range(max_len):
        GZKJG.append({
            "rule_indicator_code": codes[i] if i < len(codes) else "",
            "rule_indicator_name": names[i] if i < len(names) else "",
            "rule_constant_name": constants[i] if i < len(constants) else "",
            "rule_unit_ratio": ratios[i] if i < len(ratios) else "",
            "indicator_release_frequency": frequencies[i] if i < len(frequencies) else ""
        })

    return {
        "GZKJG": GZKJG
    }
