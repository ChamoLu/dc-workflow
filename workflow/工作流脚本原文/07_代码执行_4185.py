def main(params):

    OUTDATA = params.get("OUTDATA", [])

    warningdata = params.get("warningdata", [])

    # 合并数据
    OUTDATA.extend(warningdata)

    return {
        "OUTDATA": OUTDATA
    }