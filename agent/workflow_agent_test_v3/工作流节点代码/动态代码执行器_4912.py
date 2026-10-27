# 采集数据源：本次工作流的 pageTitle；通用动态执行器，不写死业务来源。
# 数据来源编码、数据源路径代码、路径、目标表：由交付解析器的中文审查注释记录。
# 规则来源：agent变量与节点配置手册.md；输出沿用 code/msg/ExcelData 契约。
# 日期来自解析器核对的源表或本次运行时 title，不使用示例日期或当前日期。
# 本节点不做指标清洗、单位换算、去重、精度调整或 GZK 筛选。
# 独立命名空间只避免污染平台绑定，不是执行不可信代码的安全沙箱。

import datetime as _agent_datetime
import decimal as _agent_decimal
import hashlib as _agent_hashlib
import hmac as _agent_hmac
import json as _agent_json
import math as _agent_math
import re as _agent_re

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
    "report_ref",
    "elapsed_seconds",
)
_BUSINESS_FIELDS = ("fileMd5", "title", "publictime", "fileType", "pageTitle")
_NUMERIC_TEXT = _agent_re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _agent_validate_result(result):
    if not isinstance(result, dict):
        raise RuntimeError("DC_AGENT_EXECUTION_ERROR: 解析器 main 必须返回字典")
    code = result.get("code")
    if isinstance(code, bool) or not isinstance(code, (int, float)) or code != 0:
        raise RuntimeError("DC_AGENT_EXECUTION_ERROR: 解析器未返回数字成功码 0")
    if not isinstance(result.get("msg"), str):
        raise RuntimeError("DC_AGENT_EXECUTION_ERROR: 解析器 msg 必须为字符串")
    records = result.get("ExcelData")
    if not isinstance(records, list) or not records:
        raise RuntimeError("DC_AGENT_EXECUTION_ERROR: ExcelData 必须是非空对象数组")
    for index, record in enumerate(records):
        prefix = "DC_AGENT_EXECUTION_ERROR: ExcelData[%d] " % index
        if not isinstance(record, dict):
            raise RuntimeError(prefix + "必须为对象")
        for key in ("indicator_name", "indicator_data", "indicator_unit", "end_date"):
            if key not in record:
                raise RuntimeError(prefix + "缺少四核心字段之一：" + key)
        for key in ("indicator_name", "indicator_unit"):
            if not isinstance(record[key], str) or not record[key].strip():
                raise RuntimeError(prefix + key + " 必须为非空字符串")
        if not isinstance(record["end_date"], str):
            raise RuntimeError(prefix + "end_date 必须为字符串，无法确定日期时允许空串")
        if record["end_date"]:
            try:
                _agent_datetime.date.fromisoformat(record["end_date"])
            except ValueError:
                raise RuntimeError(prefix + "end_date 必须为有效的 ISO 日期或空串") from None
        value = record["indicator_data"]
        if isinstance(value, bool):
            raise RuntimeError(prefix + "indicator_data 不能为布尔值")
        if isinstance(value, int):
            continue
        if isinstance(value, float):
            if _agent_math.isfinite(value):
                continue
        elif isinstance(value, str) and _NUMERIC_TEXT.fullmatch(value.replace(",", "").strip()):
            try:
                # 仅为验证移除显示千分位，输出仍原样保留；与既有 Validator/7882 一致。
                if _agent_decimal.Decimal(value.replace(",", "").strip()).is_finite():
                    continue
            except _agent_decimal.InvalidOperation:
                pass
        raise RuntimeError(prefix + "indicator_data 必须为有限数值或数值字符串")
    try:
        _agent_json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        raise RuntimeError("DC_AGENT_EXECUTION_ERROR: 解析结果不是严格 JSON 或包含 NaN") from None
    return {"code": result["code"], "msg": result["msg"], "ExcelData": records}


def main(params):
    if not isinstance(params, dict):
        raise ValueError("main(params) 的 params 必须为字典")
    records = params.get("agent")
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise ValueError("agent 必须为恰好一个对象的数组，请整体映射 agent 父变量")
    agent = records[0]
    if any(key not in agent or not isinstance(agent[key], str) for key in _AGENT_FIELDS):
        raise ValueError("agent 的 14 个字段必须全部存在且为 String")
    if agent["status"] != "ready":
        # 只列状态、错误码和 job_id，避免完整服务文本意外进入日志。
        raise RuntimeError(
            "DC_AGENT_NOT_READY: status=%s error_code=%s job_id=%s"
            % (agent["status"], agent["error_code"], agent["job_id"])
        )
    if not agent["job_id"].strip() or not agent["artifact_version"].strip() or agent["error_code"]:
        raise ValueError("ready 必须包含 job_id、固定版本且 error_code 为空")
    if agent["quality_status"] not in {"accepted", "usable_with_warnings"} or agent[
        "cache_hit"
    ] not in {"true", "false"}:
        raise ValueError("ready 的 quality_status 或 cache_hit 不符合契约")
    filename = agent["PythonFile"]
    if (
        not filename.endswith(".py")
        or "/" in filename
        or "\\" in filename
        or any(ord(ch) < 32 for ch in filename)
    ):
        raise ValueError("PythonFile 必须为不包含目录或控制字符的 .py 业务文件名")
    source = agent["PythonCode"]
    expected = agent["code_sha256"]
    if not source.strip() or not _agent_re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise ValueError("PythonCode 不能为空，code_sha256 必须为 64 位十六进制")
    actual = _agent_hashlib.sha256(source.encode("utf-8")).hexdigest()
    if not _agent_hmac.compare_digest(expected.lower(), actual):
        raise ValueError("PythonCode SHA256 校验失败，禁止执行截断或改写后的源码")
    business = {}
    for key in _BUSINESS_FIELDS:
        value = params.get(key, "")
        if not isinstance(value, str) or (key in {"fileMd5", "pageTitle"} and not value.strip()):
            raise ValueError(
                "业务入参 %s 必须为%s字符串"
                % (key, "非空" if key in {"fileMd5", "pageTitle"} else "")
            )
        business[key] = value
    namespace = {"__name__": "dc_agent_delivered_parser", "__file__": filename}
    # 不用 globals().update，也不将服务令牌、agent、GZK 或平台全局绑定交给解析器。
    compiled = compile(source, filename, "exec")
    exec(compiled, namespace, namespace)
    parser_main = namespace.get("main")
    if not callable(parser_main):
        raise RuntimeError("DC_AGENT_EXECUTION_ERROR: 交付源码缺少可调用的 main(params)")
    result = parser_main(business)
    return _agent_validate_result(result)
