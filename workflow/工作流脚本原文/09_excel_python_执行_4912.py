def main(params):
    python_code = params.get("PythonCode")
    python_file = params.get("PythonFile")

    if python_file == "empty":
        return {
            "code": -1,
            "msg": "未配置 pageTitle，PythonFile 为 empty",
            "ExcelData": []
        }

    if not isinstance(python_file, str) or not python_file.strip():
        return {
            "code": -1,
            "msg": "PythonFile 必须是非空 string，实际类型：%s" % type(python_file).__name__,
            "ExcelData": []
        }

    if not isinstance(python_code, str):
        return {
            "code": -1,
            "msg": "PythonCode 必须是 string，实际类型：%s" % type(python_code).__name__,
            "ExcelData": []
        }

    if not python_code.strip():
        return {
            "code": -1,
            "msg": "PythonCode 为空",
            "ExcelData": []
        }

    parser_namespace = {
        "__name__": "__routed_parser__",
        "__file__": python_file
    }

    exec(
        compile(python_code, python_file, "exec"),
        parser_namespace,
        parser_namespace
    )

    target_main = parser_namespace.get("main")
    if not callable(target_main):
        return {
            "code": -1,
            "msg": "路由代码中未找到可调用的 main(params)",
            "ExcelData": []
        }

    parser_params = dict(params)
    parser_params.pop("PythonCode", None)
    parser_params.pop("PythonFile", None)

    return target_main(parser_params)