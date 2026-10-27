# 采集数据源：本次工作流的 pageTitle；通用接入节点，不写死业务来源。
# 数据来源编码、数据源路径代码、路径、目标表：由本次任务对应的规则确认。
# 规则来源：agent变量与节点配置手册.md、本机通信与全流程联调方案.md。
# 本节点只传输代码交付信息，不解析表格，不改写指标名称、单位、数值或日期。
# title、publictime 使用平台本次入参；不使用示例标题或当前日期。
# 连接配置：仅修改下方 API_BASE_URL / API_TOKEN，令牌不要配置到 agent 变量。

import hashlib
import hmac
import json
import math
import re
import time
from urllib.parse import quote, urlsplit

import requests

API_BASE_URL = "http://service-3.example.invalid"  # 本机候选内网地址，按实际监听地址修改。
API_TOKEN = "REPLACE_WITH_DC_AGENT_API_TOKEN"  # 填服务端相同令牌，勿提交真实值。
ATTEMPT_SECONDS = 115.0  # 小于平台单次 120 秒，给输出提交预留约 5 秒。
CONNECT_SECONDS = 5.0
POLL_SECONDS = 20.0
RETRY_PAUSE_SECONDS = 0.2

AGENT_FIELDS = (
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
_FAILURES = {"failed", "review_required", "invalid_input"}
_CODE_FIELDS = ("PythonFile", "PythonCode", "code_sha256", "artifact_version")


class _WithoutGZKJG(ValueError):
    pass


def _strict_json(value):
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("输入或响应必须是严格 JSON，不能包含 NaN 或非 JSON 对象") from exc


def _text(params, key, required=False):
    value = params.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError("%s 必须为%s字符串" % (key, "非空" if required else ""))
    return value


def _payload(params):
    if not isinstance(params, dict):
        raise ValueError("main(params) 的 params 必须为字典")
    result = {
        key: _text(params, key, required=key in {"fileMd5", "pageTitle"})
        for key in ("fileMd5", "pageTitle", "fileType", "title", "publictime")
    }
    file_type = result["fileType"].strip().lower()
    if file_type.startswith("."):
        file_type = file_type[1:]
    if file_type not in {"xls", "xlsx", "csv"}:
        reason = "为空或未映射" if not file_type else "不是 xls / xlsx / csv"
        raise ValueError(
            "fileType %s；请将接入节点的 fileType 输入绑定模板 fileType。"
            "原始 Excel 文件需传实际扩展名，本节点不会默认猜成 xlsx。" % reason
        )
    result["fileType"] = file_type
    rules = params.get("GZKJG")
    if rules is None:
        raise _WithoutGZKJG("without_GZKJG: 本次规则库结果为空")
    if isinstance(rules, str):
        try:
            rules = json.loads(rules)
        except (TypeError, ValueError) as exc:
            raise ValueError("GZKJG 字符串必须是合法 JSON 数组") from exc
    if rules is None:
        raise _WithoutGZKJG("without_GZKJG: 本次规则库结果为空")
    if not isinstance(rules, list) or any(not isinstance(x, dict) for x in rules):
        raise ValueError("GZKJG 必须为非空对象数组，或该数组的 JSON 字符串")
    # 8448 按各列最长长度组行，末尾可能有空名称。与既有 Task 的筛除策略一致：
    # 保留原数组交服务端记录 invalid_rule_rows，不能因一条空名称阻断其余有效规则。
    if not any(
        isinstance(row.get("rule_indicator_name"), str)
        and row["rule_indicator_name"].strip()
        and row["rule_indicator_name"].strip().lower() not in {"empty", "null", "none"}
        for row in rules
    ):
        raise _WithoutGZKJG("without_GZKJG: GZKJG 至少需要一条有效的 rule_indicator_name")
    result["GZKJG"] = rules
    if "workflow_run_id" in params:
        result["workflow_run_id"] = _text(params, "workflow_run_id", required=True)
    _strict_json(result)
    return result


def _configuration():
    parts = urlsplit(API_BASE_URL)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("API_BASE_URL 必须是无凭据、查询串或片段的 HTTP(S) 服务地址")
    if (
        not isinstance(API_TOKEN, str)
        or not API_TOKEN.strip()
        or API_TOKEN == "REPLACE_WITH_DC_AGENT_API_TOKEN"
        or "\n" in API_TOKEN
        or "\r" in API_TOKEN
    ):
        raise ValueError("请先把 API_TOKEN 配置为与本机服务一致的访问令牌")
    return API_BASE_URL.rstrip("/")


def _retry(job_id):
    raise RuntimeError(
        "DC_AGENT_RETRY: 本次等待窗口结束，请按原输入重试同一任务；job_id=%s"
        % (job_id or "尚未取得，请按原输入重新提交")
    )


def _response_json(response):
    if response.status_code != 200 and response.status_code != 202:
        if response.status_code == 422:
            raise RuntimeError(_input_error(response))
        # 不回显响应正文、请求头或底层异常，防止凭据进入平台日志。
        raise RuntimeError("DC_AGENT_HTTP_ERROR: HTTP %s" % response.status_code)
    try:
        value = response.json()
    except (TypeError, ValueError) as exc:
        raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: 服务响应不是合法 JSON") from exc
    _strict_json(value)
    if not isinstance(value, dict):
        raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: 服务响应必须为 JSON 对象")
    return value


def _input_error(response):
    # 只显示新服务明确提供的字段位置/类型/编号，不回显任意 message/input/ctx。
    summary = "DC_AGENT_HTTP_ERROR: HTTP 422；请求已到服务端，但入参校验失败"
    try:
        body = response.json()
    except (TypeError, ValueError):
        return summary + "；请更新并重启本机服务以显示具体字段"
    if not isinstance(body, dict) or body.get("error_code") not in {
        "INVALID_INPUT",
        "without_GZKJG",
    }:
        return summary + "；请更新并重启本机服务以显示具体字段"
    if body["error_code"] == "without_GZKJG":
        summary += "; error_code=without_GZKJG；没有有效规则，未启动代码生成"
    if not isinstance(body.get("issues"), list):
        return summary + "；请更新并重启本机服务以显示具体字段"
    phase = body.get("phase")
    if isinstance(phase, str) and phase in {"request_schema", "task_contract"}:
        summary += "; phase=" + phase
    fields = r"(?:body|fileMd5|pageTitle|fileType|title|publictime|GZKJG|workflow_run_id|rule_indicator_name|rule_indicator_code|wait_seconds|unknown_field)"
    location = fields + r"(?:\[[0-9]{1,6}\])?(?:\." + fields + r"(?:\[[0-9]{1,6}\])?)*"
    allowed_errors = {
        "missing",
        "literal_error",
        "string_type",
        "string_too_short",
        "string_too_long",
        "list_type",
        "dict_type",
        "too_short",
        "too_long",
        "extra_forbidden",
        "value_error",
        "json_invalid",
        "finite_number",
        "greater_than_equal",
        "less_than_equal",
        "float_parsing",
        "validation_error",
    }
    issues = body.get("issues")
    if isinstance(issues, list):
        for issue in issues[:10]:
            if not isinstance(issue, dict):
                continue
            field, error = issue.get("field"), issue.get("error_type")
            if not isinstance(field, str) or len(field) > 200 or not re.fullmatch(location, field):
                continue
            if not isinstance(error, str) or error not in allowed_errors:
                continue
            summary += "; " + field + "=" + error
            kind = issue.get("input_type")
            if isinstance(kind, str) and kind in {
                "str",
                "int",
                "float",
                "bool",
                "list",
                "dict",
                "NoneType",
                "missing",
                "other",
            }:
                summary += "(input_type=" + kind + ")"
    diagnostic_id = body.get("diagnostic_id")
    if isinstance(diagnostic_id, str) and re.fullmatch(r"[0-9a-f]{32}", diagnostic_id):
        summary += "; diagnostic_id=" + diagnostic_id
    return summary


def _agent(response, job_id):
    records = response.get("agent")
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: agent 必须是恰好一个对象的数组")
    raw = records[0]
    if any(key not in raw or not isinstance(raw[key], str) for key in AGENT_FIELDS):
        raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: agent 的 14 个字段必须全部存在且为 String")
    record = {key: raw[key] for key in AGENT_FIELDS}
    if record["job_id"] != job_id:
        raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: 返回的 job_id 与当前任务不一致")
    status = record["status"]
    if status not in {"ready", "processing"} | _FAILURES:
        raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: 未知的外部任务状态")
    if record["cache_hit"] not in {"", "true", "false"}:
        raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: cache_hit 必须为 true / false 字符串或空串")
    elapsed = record["elapsed_seconds"]
    if elapsed:
        try:
            seconds = float(elapsed)
        except ValueError as exc:
            raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: elapsed_seconds 格式错误") from exc
        if not math.isfinite(seconds) or seconds < 0:
            raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: elapsed_seconds 必须为有限非负秒数")
    if status != "ready":
        # 任何未就绪结果都清除代码，绝不沿用上次版本或服务意外附带的正文。
        for key in _CODE_FIELDS:
            record[key] = ""
        return record
    if record["error_code"]:
        raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: ready 结果不能携带 error_code")
    filename = record["PythonFile"]
    if (
        not filename.endswith(".py")
        or not filename.strip()
        or "/" in filename
        or "\\" in filename
        or any(ord(ch) < 32 for ch in filename)
    ):
        raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: PythonFile 必须为有效的 .py 业务文件名")
    if not record["artifact_version"].strip() or not record["PythonCode"].strip():
        raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: ready 必须包含固定版本和完整源码")
    if record["quality_status"] not in {"accepted", "usable_with_warnings"}:
        raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: ready 的 quality_status 无效")
    if record["cache_hit"] not in {"true", "false"}:
        raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: ready 必须明确 cache_hit")
    digest = record["code_sha256"]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: code_sha256 格式错误")
    actual = hashlib.sha256(record["PythonCode"].encode("utf-8")).hexdigest()
    if not hmac.compare_digest(digest.lower(), actual):
        raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: PythonCode SHA256 校验失败")
    return record


def main(params):
    """平台入口。重试始终重新 POST 同一输入，服务端负责持久幂等。"""
    started = time.monotonic()
    try:
        payload = _payload(params)
    except _WithoutGZKJG:
        record = dict.fromkeys(AGENT_FIELDS, "")
        record.update(
            status="invalid_input",
            stage="preflight_rules",
            error_code="without_GZKJG",
            message="本次 GZKJG 没有有效规则，未调用外部生成服务；请检查规则库查询及入参映射",
            elapsed_seconds="0.00",
        )
        return {"agent": [record]}
    base = _configuration()
    deadline = started + ATTEMPT_SECONDS
    job_id = ""
    with requests.Session() as session:
        session.trust_env = False  # 内网地址直连，不将认证请求转发给环境代理。
        session.headers.update(
            {"Authorization": "Bearer " + API_TOKEN, "Accept": "application/json"}
        )

        def call(method, path, body=None, wait=False):
            remaining = deadline - time.monotonic()
            if remaining <= 2.0:
                _retry(job_id)
            connect = min(CONNECT_SECONDS, remaining / 3.0)
            read = min(POLL_SECONDS + 2.0, remaining - connect - 1.0)
            kwargs = {"timeout": (connect, read), "allow_redirects": False}
            if body is not None:
                kwargs["json"] = body
            if wait:
                kwargs["params"] = {"wait_seconds": max(0.0, min(POLL_SECONDS, read - 1.0))}
            try:
                response = session.request(method, base + path, **kwargs)
            except requests.RequestException:
                # 连接断开也可能已成功受理；下一次 POST 将取得原任务。
                raise RuntimeError(
                    "DC_AGENT_RETRY: 请求连接失败或超时，按原输入重试；job_id=%s"
                    % (job_id or "尚未取得")
                ) from None
            return _response_json(response)

        submitted = call("POST", "/v3/excel/jobs", payload)
        job_id = submitted.get("job_id")
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
            raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: 受理响应缺少有效 job_id")
        if submitted.get("status") not in {"processing", "ready"} | _FAILURES:
            raise RuntimeError("DC_AGENT_PROTOCOL_ERROR: 受理响应 status 无效")
        path = "/v3/excel/jobs/" + quote(job_id, safe="")
        while True:
            record = _agent(call("GET", path, wait=True), job_id)
            if record["status"] != "processing":
                return {"agent": [record]}
            remaining = deadline - time.monotonic()
            if remaining <= 2.0:
                _retry(job_id)
            # 防止服务立即返回 processing 时出现无间隔的高频查询。
            time.sleep(min(RETRY_PAUSE_SECONDS, max(0.0, remaining - 2.0)))
