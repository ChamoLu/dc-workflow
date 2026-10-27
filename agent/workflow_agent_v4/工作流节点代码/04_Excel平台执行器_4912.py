# 采集数据源：本次 pageTitle；升级生产通用代码执行节点 4912。
# 数据来源编码、数据源路径代码、路径、目标表：由交付解析器开头的中文审查注释记录。
# 规则来源：本目录 README.md；V4 agent 15 字段交付契约。
# 输入只读取 agent 父数组及白名单业务字段，不读取遗留顶层 PythonFile/PythonCode。
# title、公示日期等传本次运行值；日期如何取得由真实源表和专属解析器规则决定。
# 不清洗业务名称，不做单位换算、GZK 筛选、去重、四舍五入或缺失值补猜。
# 执行失败必须抛异常：当前已删除前置判断节点，不能正常返回空 ExcelData 进入后半链。
# 独立命名空间隔离变量；exec 不是不可信代码安全沙箱，平台应继续使用既有执行隔离。

import datetime as _agent_datetime
import decimal as _agent_decimal
import hashlib as _agent_hashlib
import hmac as _agent_hmac
import json as _agent_json
import math as _agent_math
import re as _agent_re

FILE_KEY = "fileMd5"
FILE_TYPES = {"xls", "xlsx", "csv"}
_AGENT_FIELDS = (
    "status",
    "stage",
    "job_id",
    "PythonFile",
    "PythonCode",
    "code_sha256",
    "artifact_version",
    "cache_hit",
    "quality_status",
    "warning_summary",
    "error_code",
    "message",
    "elapsed_seconds",
    "PromptFile",
    "PromptCode",
)
_BUSINESS_FIELDS = (FILE_KEY, "title", "publictime", "fileType", "pageTitle")
_NUMERIC_TEXT = _agent_re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _agent_safe_job(value):
    return (
        value
        if isinstance(value, str) and _agent_re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)
        else ""
    )


def _agent_stop(code, stage, message, job_id="", filename="", line=0, error_type=""):
    """不回显解析器异常文本、源码行、请求内容、响应正文、环境变量或完整 traceback。"""
    diagnostic = {
        "status": "failed",
        "stage": stage,
        "error_code": code,
        "message": message[:600],
        "job_id": _agent_safe_job(job_id),
    }
    if filename:
        diagnostic["artifact_file"] = filename
    if isinstance(line, int) and 0 < line <= 10000000:
        diagnostic["artifact_line"] = line
    if error_type:
        diagnostic["exception_type"] = error_type
    raise RuntimeError(
        "DC_AGENT_EXECUTION_ERROR: "
        + _agent_json.dumps(diagnostic, ensure_ascii=False, allow_nan=False)
    ) from None


def _agent_filename(value):
    if not isinstance(value, str) or not value.endswith(".py"):
        return False
    stem = value[:-3]
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *["COM%d" % i for i in range(1, 10)],
        *["LPT%d" % i for i in range(1, 10)],
    }
    try:
        byte_length = len(value.encode("utf-8"))
    except UnicodeError:
        return False
    return (
        0 < byte_length <= 255
        and bool(stem.strip())
        and stem not in {".", ".."}
        and not stem.endswith((" ", "."))
        and stem.split(".", 1)[0].upper() not in reserved
        and not any(ch in value for ch in '/\\<>:"|?*')
        and not any(ord(ch) < 32 for ch in value)
    )


def _agent_delivery(params):
    if not isinstance(params, dict):
        _agent_stop("INVALID_INPUT", "platform_preflight", "main(params) 的 params 必须为字典")
    rows = params.get("agent")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        _agent_stop(
            "INVALID_INPUT",
            "platform_preflight",
            "agent 必须为恰好一个对象的数组；请整体映射上游 agent 父变量",
        )
    agent = rows[0]
    job_id = _agent_safe_job(agent.get("job_id"))
    if any(key not in agent or not isinstance(agent[key], str) for key in _AGENT_FIELDS):
        _agent_stop(
            "INVALID_AGENT",
            "platform_preflight",
            "agent 的 15 个平台字段必须全部存在且为 String",
            job_id,
        )
    if agent["status"] != "ready" or agent["error_code"]:
        _agent_stop(
            "ASSET_NOT_READY",
            "platform_preflight",
            "当前 agent 未就绪或携带错误码，已阻止平台执行",
            job_id,
        )
    if agent["PromptFile"] or agent["PromptCode"]:
        _agent_stop(
            "ARTIFACT_BODY_CONFLICT",
            "platform_preflight",
            "平台代码执行器只接受 PythonFile/PythonCode 正文，PromptFile/PromptCode 必须为空",
            job_id,
        )
    if (
        not job_id
        or not agent["artifact_version"].strip()
        or any(ord(ch) < 32 for ch in agent["artifact_version"])
    ):
        _agent_stop(
            "ARTIFACT_INVALID",
            "platform_preflight",
            "ready 必须包含有效 job_id 和固定 artifact_version",
            job_id,
        )
    if agent["quality_status"] not in {"accepted", "usable_with_warnings"} or agent[
        "cache_hit"
    ] not in {"true", "false"}:
        _agent_stop(
            "ARTIFACT_NOT_ACCEPTED",
            "platform_preflight",
            "ready 的 quality_status 或 cache_hit 不符合交付契约",
            job_id,
        )
    try:
        seconds = float(agent["elapsed_seconds"])
    except ValueError:
        seconds = -1
    if not _agent_math.isfinite(seconds) or seconds < 0:
        _agent_stop(
            "INVALID_AGENT",
            "platform_preflight",
            "elapsed_seconds 必须为有限非负秒数字符串",
            job_id,
        )
    filename, source = agent["PythonFile"], agent["PythonCode"]
    if not _agent_filename(filename) or not source.strip():
        _agent_stop(
            "ARTIFACT_INVALID",
            "platform_preflight",
            "PythonFile 必须是有效业务文件名且 PythonCode 必须是完整非空正文",
            job_id,
        )
    digest = agent["code_sha256"]
    if not _agent_re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        _agent_stop(
            "ARTIFACT_HASH_INVALID",
            "platform_preflight",
            "code_sha256 必须为 64 位十六进制",
            job_id,
            filename,
        )
    try:
        actual = _agent_hashlib.sha256(source.encode("utf-8")).hexdigest()
    except UnicodeError:
        _agent_stop(
            "ARTIFACT_INVALID",
            "platform_preflight",
            "交付源码不能编码为有效 UTF-8",
            job_id,
            filename,
        )
    if not _agent_hmac.compare_digest(digest.lower(), actual):
        _agent_stop(
            "ARTIFACT_HASH_MISMATCH",
            "platform_preflight",
            "源码 SHA256 校验失败，已阻止执行改写或截断后的资产",
            job_id,
            filename,
        )
    business = {}
    for key in _BUSINESS_FIELDS:
        value = params.get(key, "")
        if not isinstance(value, str) or (
            key in {FILE_KEY, "pageTitle", "fileType"} and not value.strip()
        ):
            _agent_stop(
                "INVALID_INPUT",
                "platform_preflight",
                "业务入参 %s 必须为%s字符串"
                % (key, "非空" if key in {FILE_KEY, "pageTitle", "fileType"} else ""),
                job_id,
                filename,
            )
        business[key] = value
    business["fileType"] = business["fileType"].strip().lower().lstrip(".")
    if business["fileType"] not in FILE_TYPES:
        _agent_stop(
            "INVALID_INPUT",
            "platform_preflight",
            "本次 fileType 与执行器链路不符",
            job_id,
            filename,
        )
    expected_name = business["pageTitle"].replace("\\", "_") + ".py"
    if filename != expected_name:
        _agent_stop(
            "ARTIFACT_SOURCE_MISMATCH",
            "platform_preflight",
            "交付文件名与本次 pageTitle 不一致，已阻止同类历史资产串源",
            job_id,
            filename,
        )
    # 只传五项本次业务参数。agent、GZKJG、GZK、令牌及顶层旧源码均不传给解析器。
    return agent, business


def _agent_exception(exc, stage, job_id, filename):
    line = 0
    if isinstance(exc, SyntaxError) and exc.filename == filename:
        line = exc.lineno or 0
    trace = exc.__traceback__
    while trace:
        if trace.tb_frame.f_code.co_filename == filename:
            line = trace.tb_lineno
        trace = trace.tb_next
    allowed_types = {
        "SyntaxError",
        "IndentationError",
        "TabError",
        "NameError",
        "TypeError",
        "ValueError",
        "KeyError",
        "IndexError",
        "AttributeError",
        "ImportError",
        "ModuleNotFoundError",
        "RuntimeError",
        "OSError",
        "TimeoutError",
        "ZeroDivisionError",
        "SystemExit",
        "KeyboardInterrupt",
        "AssertionError",
    }
    error_type = type(exc).__name__ if type(exc).__name__ in allowed_types else "ParserException"
    _agent_stop(
        "PARSER_EXCEPTION",
        stage,
        "交付解析器在当前阶段发生异常；请按交付文件、行号及 job_id 查本机报告，异常正文已省略",
        job_id,
        filename,
        line,
        error_type,
    )


def _agent_validate_result(result, job_id, filename):
    def fail(message):
        _agent_stop("INVALID_PARSER_RESULT", "platform_result_validate", message, job_id, filename)

    if not isinstance(result, dict):
        fail("解析器 main(params) 必须返回字典")
    code = result.get("code")
    if isinstance(code, bool) or not isinstance(code, (int, float)) or code != 0:
        fail("解析器未返回数字成功码 0；为避免无数据流入后半链，本节点已停止")
    if not isinstance(result.get("msg"), str):
        fail("解析器 msg 必须为字符串")
    records = result.get("ExcelData")
    if not isinstance(records, list) or not records:
        fail("ExcelData 必须是非空对象数组；HTML 也使用 ExcelData，不兼容 OUTDATA 回退")
    for index, record in enumerate(records):
        prefix = "ExcelData[%d] " % index
        if not isinstance(record, dict):
            fail(prefix + "必须为对象")
        for key in ("indicator_name", "indicator_data", "indicator_unit", "end_date"):
            if key not in record:
                fail(prefix + "缺少四核心字段之一：" + key)
        for key in ("indicator_name", "indicator_unit"):
            if not isinstance(record[key], str) or not record[key].strip():
                fail(prefix + key + " 必须为非空字符串；明确无单位时由解析器填写 -")
        date = record["end_date"]
        if not isinstance(date, str):
            fail(prefix + "end_date 必须为字符串，来源无法确定时允许空串")
        if date:
            if not _agent_re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date):
                fail(prefix + "end_date 必须为 YYYY-MM-DD 或空串")
            try:
                _agent_datetime.date.fromisoformat(date)
            except ValueError:
                fail(prefix + "end_date 不是有效日期")
        value = record["indicator_data"]
        valid_number = isinstance(value, int) and not isinstance(value, bool)
        if isinstance(value, float):
            valid_number = _agent_math.isfinite(value)
        elif isinstance(value, str):
            numeric = value.replace(",", "").strip()
            if _NUMERIC_TEXT.fullmatch(numeric):
                try:
                    valid_number = _agent_decimal.Decimal(numeric).is_finite()
                except _agent_decimal.InvalidOperation:
                    valid_number = False
        if not valid_number:
            fail(prefix + "indicator_data 必须为有限数值或数值字符串；不能为布尔值、空值或 NaN")
        # 上面只验证，不更改源值、零值、精度、单位、名称、日期或记录顺序。
    try:
        _agent_json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        fail("解析结果必须能严格 JSON 序列化，不能含 NaN、Infinity 或非 JSON 对象")
    return {"code": code, "msg": result["msg"], "ExcelData": records}


def main(params):
    """平台把返回键 ExcelData 映射至已有 excel_data 父数组。"""
    agent, business = _agent_delivery(params)
    filename, source, job_id = agent["PythonFile"], agent["PythonCode"], agent["job_id"]
    namespace = {"__name__": "dc_agent_delivered_parser", "__file__": filename}
    try:
        compiled = compile(source, filename, "exec")
    except BaseException as exc:
        _agent_exception(exc, "platform_compile", job_id, filename)
    try:
        exec(compiled, namespace, namespace)
    except BaseException as exc:
        _agent_exception(exc, "platform_initialize", job_id, filename)
    parser_main = namespace.get("main")
    if not callable(parser_main):
        _agent_stop(
            "MISSING_MAIN",
            "platform_initialize",
            "交付源码缺少可调用的 main(params)",
            job_id,
            filename,
        )
    try:
        result = parser_main(business)
    except BaseException as exc:
        _agent_exception(exc, "platform_execute", job_id, filename)
    return _agent_validate_result(result, job_id, filename)
