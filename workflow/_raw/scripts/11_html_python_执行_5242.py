def main(params):
    python_code = params["PythonCode"]
    python_file = params.get("PythonFile", "routed_parser.py")

    parser_namespace = {
        "__name__": "__routed_parser__",
        "__file__": python_file
    }

    exec(
        compile(python_code, python_file, "exec"),
        parser_namespace,
        parser_namespace
    )

    target_main = parser_namespace["main"]

    parser_params = dict(params)
    parser_params.pop("PythonCode", None)
    parser_params.pop("PythonFile", None)

    return target_main(parser_params)