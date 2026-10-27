# 采集数据源：本次工作流的 pageTitle；通用动态执行器，不写死业务来源。
# 数据来源编码、数据源路径代码、路径、目标表：由交付解析器的中文审查注释记录。
# 规则来源：循环接入方案；保持解析器 main(params) 与四核心字段契约。
# 日期来自已核对的源表或运行时 title，不使用示例日期或当前日期。
# 本节点只执行 ready 代码；不做 GZK 匹配、单位换算、去重或精度调整。
# 独立命名空间不是安全沙箱；Python 捕获不能覆盖平台强制终止或硬超时。

import datetime as _dx_datetime
import decimal as _dx_decimal
import hashlib as _dx_hashlib
import hmac as _dx_hmac
import json as _dx_json
import math as _dx_math
import re as _dx_re

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
)
_BUSINESS_FIELDS = ("fileMd5", "title", "publictime", "fileType", "pageTitle")
_NONREADY = {"processing", "failed", "review_required", "invalid_input"}
_NUMERIC_TEXT = _dx_re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_JOB = _dx_re.compile(r"[A-Za-z0-9_-]{1,128}")
_VERSION = _dx_re.compile(r"[A-Za-z0-9_-]{1,200}")


def _summary(value, limit=600):
    if not isinstance(value, str):
        return ""
    text = _dx_re.sub(r"[\x00-\x1f\x7f]", " ", value)
    # 只输出有界摘要；完整异常、源码和原始 HTTP 响应不进入诊断结果。
    text = _dx_re.sub(r"(?i)\bbearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
    text = _dx_re.sub(
        r"(?i)(?:authorization|api[_-]?key|api[_-]?token|access[_-]?token|password|secret)"
        r"\s*[:=]\s*[^\s,;]+",
        "[REDACTED]",
        text,
    )
    text = _dx_re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", text)
    text = _dx_re.sub(r"https?://[^\s]+", "[URL omitted]", text)
    return text[:limit]


def _identifier(value, limit=160):
    if not isinstance(value, str) or not _dx_re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
        return ""
    return value[:limit]


def _job_id(value):
    return value if isinstance(value, str) and _JOB.fullmatch(value) else ""


def _artifact_version(value):
    return value if isinstance(value, str) and _VERSION.fullmatch(value) else ""


def _agent_copy(agent):
    result = {key: "" for key in _AGENT_FIELDS}
    if isinstance(agent, dict):
        for key in _AGENT_FIELDS:
            if isinstance(agent.get(key), str):
                result[key] = agent[key]
        job_id = _job_id(agent.get("job_id"))
        result.update(
            status=_identifier(agent.get("status")),
            job_id=job_id,
            artifact_version=_artifact_version(agent.get("artifact_version")),
            warning_summary=_summary(agent.get("warning_summary")),
            message=_summary(agent.get("message"), 2000),
            stage=_identifier(agent.get("stage")),
            error_code=_identifier(agent.get("error_code")),
            cache_hit=agent.get("cache_hit")
            if isinstance(agent.get("cache_hit"), str)
            and agent.get("cache_hit") in {"true", "false"}
            else "",
            quality_status=_identifier(agent.get("quality_status")),
            elapsed_seconds=agent.get("elapsed_seconds")
            if _dx_re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,6})?", result["elapsed_seconds"])
            else "",
        )
    return result


def _failure(status, stage, code, message, agent=None, detail=""):
    result = _agent_copy(agent)
    result.update(
        status=status,
        stage=_identifier(stage),
        error_code=_identifier(code),
        message=_summary(message + (" " + detail if detail else ""), 2000),
    )
    # 失败时保留任务身份与原因，清除代码和固定版本，防止残留源码被再次执行。
    for key in ("PythonFile", "PythonCode", "code_sha256", "artifact_version"):
        result[key] = ""
    return {"ExcelData": [], "agent": [result]}


def _ordinary_error(exc, filename):
    # 只记录异常类型及交付文件中的最内层行号，不读取异常文本/源码/局部变量。
    kind = type(exc).__name__
    if not _dx_re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", kind):
        kind = "Exception"
    line = None
    trace = exc.__traceback__
    while trace is not None:
        if trace.tb_frame.f_code.co_filename == filename:
            line = trace.tb_lineno
        trace = trace.tb_next
    if isinstance(exc, SyntaxError) and exc.filename == filename:
        line = exc.lineno
    location = "，交付文件 %s 第 %d 行" % (_summary(filename, 180), line) if line else ""
    return (
        "异常类型：%s%s。原始异常文本未输出，请结合本条消息及平台日志定位，或按 job_id 查看服务日志。"
        % (kind, location)
    )


def _finite_number(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return _dx_math.isfinite(value)
    if isinstance(value, str) and _NUMERIC_TEXT.fullmatch(value.replace(",", "").strip()):
        try:
            return _dx_decimal.Decimal(value.replace(",", "").strip()).is_finite()
        except _dx_decimal.InvalidOperation:
            return False
    return False


def _result_problem(result):
    if not isinstance(result, dict):
        return "PARSER_RESULT_CONTRACT_ERROR", "解析器 main(params) 必须返回字典。", ""
    code = result.get("code")
    if isinstance(code, bool) or not isinstance(code, (int, float)) or not _dx_math.isfinite(code):
        return "PARSER_RESULT_CONTRACT_ERROR", "解析器 code 必须为有限数字，成功码为 0。", ""
    if code != 0:
        detail = "parser_code=%s；parser_msg=%s" % (code, _summary(result.get("msg")))
        return "PARSER_RETURNED_ERROR", "解析器返回非零业务错误码，未作为成功结果继续处理。", detail
    if not isinstance(result.get("msg"), str):
        return "PARSER_RESULT_CONTRACT_ERROR", "解析器 msg 必须为字符串。", ""
    records = result.get("ExcelData")
    if not isinstance(records, list):
        return "PARSER_RESULT_CONTRACT_ERROR", "ExcelData 必须为对象数组。", ""
    if not records:
        return (
            "EMPTY_EXTRACTION",
            "解析器执行完成，但 ExcelData 为空，需要核查源文件和提取逻辑。",
            "",
        )
    for index, record in enumerate(records):
        prefix = "ExcelData[%d] " % index
        if not isinstance(record, dict):
            return "PARSER_RESULT_CONTRACT_ERROR", prefix + "必须为对象。", ""
        for key in ("indicator_name", "indicator_data", "indicator_unit", "end_date"):
            if key not in record:
                return "PARSER_RESULT_CONTRACT_ERROR", prefix + "缺少字段 " + key + "。", ""
        for key in ("indicator_name", "indicator_unit"):
            if not isinstance(record[key], str) or not record[key].strip():
                return "PARSER_RESULT_CONTRACT_ERROR", prefix + key + " 必须为非空字符串。", ""
        date = record["end_date"]
        if not isinstance(date, str):
            return (
                "PARSER_RESULT_CONTRACT_ERROR",
                prefix + "end_date 必须为字符串，未知时允许空串。",
                "",
            )
        if date:
            try:
                if not _dx_re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date):
                    raise ValueError
                _dx_datetime.date.fromisoformat(date)
            except ValueError:
                return (
                    "PARSER_RESULT_CONTRACT_ERROR",
                    prefix + "end_date 必须为有效 YYYY-MM-DD 或空串。",
                    "",
                )
        if not _finite_number(record["indicator_data"]):
            return (
                "PARSER_RESULT_CONTRACT_ERROR",
                prefix + "indicator_data 必须为有限数值或数值字符串。",
                "",
            )
    try:
        _dx_json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return (
            "PARSER_RESULT_CONTRACT_ERROR",
            "解析结果必须可严格 JSON 序列化，不能包含 NaN 或额外非序列化对象。",
            "",
        )
    return None


def main(params):
    agent = None
    filename = ""
    stage = "platform_delivery"
    try:
        if not isinstance(params, dict):
            return _failure(
                "invalid_input",
                stage,
                "DC_AGENT_INPUT_ERROR",
                "main(params) 的 params 必须为字典。",
            )
        rows = params.get("agent")
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            return _failure(
                "invalid_input",
                stage,
                "DC_AGENT_PROTOCOL_ERROR",
                "agent 必须为恰好一个对象的数组，请整体映射 agent 父变量。",
            )
        agent = rows[0]
        if set(agent) != set(_AGENT_FIELDS) or any(
            not isinstance(agent[key], str) for key in _AGENT_FIELDS
        ):
            return _failure(
                "invalid_input",
                stage,
                "DC_AGENT_PROTOCOL_ERROR",
                "agent 必须且只能包含契约中的 13 个 String 字段。",
                agent,
            )
        if agent["status"] != "ready":
            if agent["status"] not in _NONREADY:
                return _failure(
                    "failed",
                    stage,
                    "DC_AGENT_PROTOCOL_ERROR",
                    "收到未知外部任务状态，禁止执行。",
                    agent,
                )
            # 防御性检查：正常编排应在本节点之前判断非 ready；此处保留外部原始原因。
            return _failure(
                agent["status"],
                agent["stage"],
                agent["error_code"],
                agent["message"] or "外部任务尚未交付 ready 代码，本节点未执行。",
                agent,
            )
        if (
            not _job_id(agent["job_id"])
            or not _artifact_version(agent["artifact_version"])
            or agent["error_code"]
        ):
            return _failure(
                "failed",
                stage,
                "DC_AGENT_DELIVERY_INVALID",
                "ready 的 job_id 和固定版本格式必须有效，且 error_code 为空。",
                agent,
            )
        if agent["quality_status"] not in {"accepted", "usable_with_warnings"} or agent[
            "cache_hit"
        ] not in {"true", "false"}:
            return _failure(
                "failed",
                stage,
                "DC_AGENT_DELIVERY_INVALID",
                "ready 的 quality_status 或 cache_hit 不符合契约。",
                agent,
            )
        filename = agent["PythonFile"]
        if (
            not filename.endswith(".py")
            or "/" in filename
            or "\\" in filename
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in filename)
        ):
            return _failure(
                "failed",
                stage,
                "DC_AGENT_DELIVERY_INVALID",
                "PythonFile 必须为不包含目录或控制字符的 .py 业务文件名。",
                agent,
            )
        source = agent["PythonCode"]
        expected = agent["code_sha256"]
        if not source.strip() or not _dx_re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            return _failure(
                "failed",
                stage,
                "DC_AGENT_DELIVERY_INVALID",
                "PythonCode 不能为空，code_sha256 必须为 64 位十六进制。",
                agent,
            )
        actual = _dx_hashlib.sha256(source.encode("utf-8")).hexdigest()
        if not _dx_hmac.compare_digest(expected.lower(), actual):
            return _failure(
                "failed",
                stage,
                "DC_AGENT_CODE_HASH_MISMATCH",
                "源码 SHA256 校验失败，禁止执行截断或改写后的源码。",
                agent,
            )
        business = {}
        for key in _BUSINESS_FIELDS:
            value = params.get(key, "")
            if not isinstance(value, str) or (
                key in {"fileMd5", "pageTitle"} and not value.strip()
            ):
                return _failure(
                    "invalid_input",
                    stage,
                    "DC_AGENT_INPUT_ERROR",
                    "业务入参 %s 必须为%s字符串。"
                    % (key, "非空" if key in {"fileMd5", "pageTitle"} else ""),
                    agent,
                )
            business[key] = value
        namespace = {"__name__": "dc_agent_delivered_parser", "__file__": filename}
        stage = "platform_compile"
        compiled = compile(source, filename, "exec")
        stage = "platform_load"
        exec(compiled, namespace, namespace)
        parser_main = namespace.get("main")
        if not callable(parser_main):
            return _failure(
                "failed", stage, "PARSER_MAIN_MISSING", "交付源码缺少可调用的 main(params)。", agent
            )
        stage = "platform_execute"
        result = parser_main(business)
        stage = "platform_result_validation"
        problem = _result_problem(result)
        if problem:
            code, message, detail = problem
            return _failure(
                "review_required" if code == "EMPTY_EXTRACTION" else "failed",
                stage,
                code,
                message,
                agent,
                detail,
            )
        delivered = _agent_copy(agent)
        delivered.update(
            status="ready",
            stage="platform_execute_complete",
            error_code="",
            message="平台解析器执行成功，取得 %d 条 ExcelData；后续匹配、单位换算和清洗尚未完成。"
            % len(result["ExcelData"]),
        )
        return {"ExcelData": result["ExcelData"], "agent": [delivered]}
    except (Exception, SystemExit) as exc:
        code = {
            "platform_delivery": "DC_AGENT_DELIVERY_ERROR",
            "platform_compile": "PARSER_COMPILE_ERROR",
            "platform_load": "PARSER_LOAD_ERROR",
            "platform_execute": "PARSER_EXECUTE_ERROR",
            "platform_result_validation": "PARSER_RESULT_VALIDATION_ERROR",
        }[stage]
        return _failure(
            "failed",
            stage,
            code,
            "交付代码在 %s 阶段发生异常。" % stage,
            agent,
            _ordinary_error(exc, filename),
        )
