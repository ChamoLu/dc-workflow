# 采集数据源：本次工作流的 pageTitle；通用接入节点，不写死业务来源。
# 数据来源编码、数据源路径代码、路径、目标表：由本次任务对应的规则确认。
# 规则来源：V3循环等待节点配置方案；本节点只负责代码交付通信。
# title、publictime 使用本次运行时入参，不使用示例标题或当前日期。
# 平台配置：单次超时120秒，异常重试0次；正常等待由外层count循环控制。
# API_BASE_URL沿用旧脚本的候选地址；部署时保留你已联通的实际地址。
# API_TOKEN只填在节点连接配置中，禁止放入agent、报告或工作流输出。

import hashlib
import hmac
import json
import math
import re
import time
from urllib.parse import quote, urlsplit

import requests

API_BASE_URL = "http://service-3.example.invalid"  # 候选地址；请使用现有已联通地址。
API_TOKEN = "REPLACE_WITH_DC_AGENT_API_TOKEN"  # 填服务端相同令牌，勿提交真实值。
ATTEMPT_SECONDS = 105.0  # 一轮最多主动等待105秒，给平台120秒上限留出收尾时间。
CONNECT_SECONDS = 5.0
POLL_SECONDS = 20.0
RETRY_PAUSE_SECONDS = 0.2
FINISH_MARGIN_SECONDS = 2.0

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
    "elapsed_seconds",
)
_FAILURES = {"failed", "review_required", "invalid_input"}
_CODE_FIELDS = ("PythonFile", "PythonCode", "code_sha256", "artifact_version")
_JOB_PATTERN = r"[A-Za-z0-9_-]{1,128}"


class _NodeError(Exception):
    def __init__(self, status, stage, code, message):
        super().__init__(message)
        self.status, self.stage, self.code, self.message = status, stage, code, message


class _WindowEnded(Exception):
    pass


def _input(message, code="DC_AGENT_INPUT_ERROR", stage="client_input"):
    raise _NodeError("invalid_input", stage, code, message)


def _protocol(message, delivery=False):
    raise _NodeError(
        "failed",
        "client_delivery" if delivery else "client_protocol",
        "DC_AGENT_DELIVERY_ERROR" if delivery else "DC_AGENT_PROTOCOL_ERROR",
        message,
    )


def _strict_json(value):
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def _safe_text(value, limit=2000):
    # 仅用于已通过结构校验的公开agent字段；绝不回显任意异常/HTTP正文。
    if isinstance(API_TOKEN, str) and API_TOKEN:
        value = value.replace(API_TOKEN, "[redacted]")
    value = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [redacted]", value)
    value = re.sub(
        r"(?i)\b(api[_-]?key|api[_-]?token|password|authorization)\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]",
        value,
    )
    return "".join(ch for ch in value if ord(ch) >= 32 or ch in "\n\t")[:limit]


def _text(params, key, required=False, limit=10000):
    value = params.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()):
        _input("%s必须为%s字符串，请检查节点输入映射。" % (key, "非空" if required else ""))
    if len(value) > limit:
        _input("%s超过服务接口允许的长度，请检查节点输入映射。" % key)
    return value


def _payload(params):
    result = {
        key: _text(params, key, required=key in {"fileMd5", "pageTitle"}, limit=limit)
        for key, limit in (
            ("fileMd5", 200),
            ("pageTitle", 1000),
            ("fileType", 20),
            ("title", 10000),
            ("publictime", 1000),
        )
    }
    file_type = result["fileType"].strip().lower()
    if file_type.startswith("."):
        file_type = file_type[1:]
    if file_type not in {"xls", "xlsx", "csv"}:
        _input("fileType必须映射实际文件扩展名xls、xlsx或csv，不能留空或猜测格式。")
    result["fileType"] = file_type
    rules = params.get("GZKJG")
    if isinstance(rules, str):
        try:
            rules = json.loads(rules)
        except (TypeError, ValueError):
            _input("GZKJG字符串必须是合法JSON数组。")
    if rules is None or rules == []:
        _input(
            "本次GZKJG没有有效规则，未调用外部生成服务；请检查规则库查询及入参映射。",
            "without_GZKJG",
            "preflight_rules",
        )
    if not isinstance(rules, list) or any(not isinstance(row, dict) for row in rules):
        _input("GZKJG必须为对象数组，或该数组的JSON字符串。")
    if len(rules) > 100000:
        _input("GZKJG超过服务接口允许的100000条记录。")
    if not any(
        isinstance(row.get("rule_indicator_name"), str)
        and row["rule_indicator_name"].strip()
        and row["rule_indicator_name"].strip().lower() not in {"empty", "null", "none"}
        for row in rules
    ):
        _input(
            "本次GZKJG没有有效rule_indicator_name，未调用外部生成服务；请检查规则库查询。",
            "without_GZKJG",
            "preflight_rules",
        )
    result["GZKJG"] = rules
    if "workflow_run_id" in params:
        result["workflow_run_id"] = _text(params, "workflow_run_id", required=True, limit=200)
    if not _strict_json(result):
        _input("业务入参必须为严格JSON，不能含NaN、无穷大或非JSON对象。")
    # 只构造业务字段白名单；agent/count不进入POST。
    return result


def _agent(response, expected_job_id):
    records = response.get("agent")
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        _protocol("agent必须是恰好一个对象的数组。")
    raw = records[0]
    if any(key not in raw or not isinstance(raw[key], str) for key in AGENT_FIELDS):
        _protocol("agent的13个字段必须全部存在且为String。")
    record = {key: raw[key] for key in AGENT_FIELDS}
    if record["job_id"] != expected_job_id:
        _protocol("返回的job_id与当前任务不一致，已阻止使用其他任务的代码。")
    status = record["status"]
    if status not in {"ready", "processing"} | _FAILURES:
        _protocol("外部任务状态未知，无法决定是否执行代码。")
    if not re.fullmatch(_JOB_PATTERN, record["job_id"]) and not (
        status in _FAILURES and record["job_id"] == ""
    ):
        _protocol("agent.job_id格式无效。")
    if record["stage"] and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,199}", record["stage"]):
        _protocol("agent.stage格式无效。")
    if record["error_code"] and not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]{0,100}", record["error_code"]
    ):
        _protocol("agent.error_code格式无效。")
    if status == "processing" and record["error_code"]:
        _protocol("processing不能同时携带错误码，已停止等待并保留协议诊断。")
    if record["cache_hit"] not in {"", "true", "false"}:
        _protocol("cache_hit必须为true、false字符串或空串。")
    if record["elapsed_seconds"]:
        try:
            seconds = float(record["elapsed_seconds"])
        except ValueError:
            _protocol("elapsed_seconds不是有效秒数。")
        if not math.isfinite(seconds) or seconds < 0:
            _protocol("elapsed_seconds必须为有限非负秒数。")
    for key in ("message", "warning_summary"):
        record[key] = _safe_text(record[key])
    if status != "ready":
        for key in _CODE_FIELDS:
            record[key] = ""
        if (
            isinstance(API_TOKEN, str)
            and API_TOKEN
            and any(API_TOKEN in value for value in record.values())
        ):
            _protocol("任务元数据包含节点访问令牌，已阻止将凭据带入工作流输出。")
        return record
    if record["error_code"]:
        _protocol("ready结果不能携带error_code。", delivery=True)
    filename = record["PythonFile"]
    if (
        not filename.endswith(".py")
        or not filename.strip()
        or "/" in filename
        or "\\" in filename
        or any(ord(ch) < 32 for ch in filename)
    ):
        _protocol("PythonFile必须为不带目录的有效.py业务文件名。", delivery=True)
    if not record["artifact_version"].strip() or not record["PythonCode"].strip():
        _protocol("ready必须包含固定版本和完整源码。", delivery=True)
    if record["quality_status"] not in {"accepted", "usable_with_warnings"}:
        _protocol("ready的quality_status必须为accepted或usable_with_warnings。", delivery=True)
    if record["cache_hit"] not in {"true", "false"}:
        _protocol("ready必须明确cache_hit。", delivery=True)
    digest = record["code_sha256"]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        _protocol("code_sha256格式错误。", delivery=True)
    actual = hashlib.sha256(record["PythonCode"].encode("utf-8")).hexdigest()
    if not hmac.compare_digest(digest.lower(), actual):
        _protocol("PythonCode的SHA256完整性校验失败，已阻止交付。", delivery=True)
    if (
        isinstance(API_TOKEN, str)
        and API_TOKEN
        and any(API_TOKEN in value for value in record.values())
    ):
        _protocol("交付信息包含节点访问令牌，已阻止交付并清空源码。", delivery=True)
    return record


def _resume_agent(params):
    if "agent" not in params or params["agent"] is None or params["agent"] == []:
        return None
    records = params["agent"]
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        _input("agent父变量必须是单对象数组；首次运行允许未设置、None、空数组或13字段全空对象。")
    raw = records[0]
    if any(key not in raw or not isinstance(raw[key], str) for key in AGENT_FIELDS):
        _input("agent父变量的13个字段必须完整且均为String；不能将子字段替代父变量传入。")
    if all(raw[key] == "" for key in AGENT_FIELDS):
        return None
    try:
        return _agent({"agent": records}, raw["job_id"])
    except _NodeError:
        _input("传入agent包含不合法状态、任务身份或交付信息；已阻止意外重新提交任务。")


def _configuration():
    try:
        parts = urlsplit(API_BASE_URL)
        valid = (
            isinstance(API_BASE_URL, str)
            and parts.scheme in {"http", "https"}
            and parts.hostname
            and not parts.username
            and not parts.password
            and not parts.query
            and not parts.fragment
        )
        parts.port  # 提前检查非法端口，异常不携带原始配置到输出。
    except (TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise _NodeError(
            "failed",
            "client_configuration",
            "DC_AGENT_CONFIGURATION_ERROR",
            "API_BASE_URL必须是无凭据、查询串或片段的HTTP(S)服务地址；请保留已联通的实际地址。",
        )
    if (
        not isinstance(API_TOKEN, str)
        or not API_TOKEN.strip()
        or API_TOKEN == "REPLACE_WITH_DC_AGENT_API_TOKEN"
        or "\n" in API_TOKEN
        or "\r" in API_TOKEN
    ):
        raise _NodeError(
            "failed",
            "client_configuration",
            "DC_AGENT_CONFIGURATION_ERROR",
            "请把API_TOKEN配置为与本机服务一致的访问令牌，不要将令牌写入agent。",
        )
    return API_BASE_URL.rstrip("/")


def _input_http_error(response):
    summary = "HTTP 422：服务端拒绝业务入参；请检查fileMd5、fileType、pageTitle和GZKJG映射。"
    code = "DC_AGENT_HTTP_422"
    try:
        body = response.json()
    except (TypeError, ValueError):
        return code, summary
    if not isinstance(body, dict) or body.get("error_code") not in {
        "INVALID_INPUT",
        "without_GZKJG",
    }:
        return code, summary
    if body["error_code"] == "without_GZKJG":
        return "without_GZKJG", "服务端确认本次GZKJG无有效规则，未启动代码生成。"
    fields = r"(?:body|fileMd5|pageTitle|fileType|title|publictime|GZKJG|workflow_run_id|rule_indicator_name|rule_indicator_code|unknown_field)"
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
            if (
                isinstance(field, str)
                and len(field) <= 200
                and re.fullmatch(location, field)
                and isinstance(error, str)
                and error in allowed_errors
            ):
                summary += "；" + field + "=" + error
    diagnostic_id = body.get("diagnostic_id")
    if isinstance(diagnostic_id, str) and re.fullmatch(r"[0-9a-f]{32}", diagnostic_id):
        summary += "；diagnostic_id=" + diagnostic_id
    return code, summary


def _response_json(response):
    status = response.status_code
    if status not in {200, 202}:
        if status == 409:
            try:
                body = response.json()
            except (TypeError, ValueError):
                body = None
            detail = body.get("detail") if isinstance(body, dict) else None
            if isinstance(detail, dict) and detail.get("error_code") == "IDEMPOTENCY_CONFLICT":
                raise _NodeError(
                    "invalid_input",
                    "client_input",
                    "IDEMPOTENCY_CONFLICT",
                    "同一workflow_run_id绑定了不同输入，请检查运行身份与输入。",
                )
        if status == 422:
            code, summary = _input_http_error(response)
            raise _NodeError("invalid_input", "client_input", code, summary)
        if status in {401, 403}:
            raise _NodeError(
                "failed",
                "client_configuration",
                "DC_AGENT_AUTH_ERROR",
                "HTTP %s：服务拒绝认证，请核对节点与服务端令牌；诊断中不回显令牌。" % status,
            )
        if status == 404:
            raise _NodeError(
                "failed",
                "client_transport",
                "DC_AGENT_JOB_NOT_FOUND",
                "HTTP 404：服务地址、接口路径或当前任务不存在；请按job_id核对本机服务记录。",
            )
        safe_status = str(status) if isinstance(status, int) and 100 <= status <= 599 else "未知"
        raise _NodeError(
            "failed",
            "client_transport",
            "DC_AGENT_HTTP_ERROR",
            "HTTP %s：外部服务请求未成功；响应正文未回显，请检查本机服务日志。" % safe_status,
        )
    try:
        value = response.json()
    except (TypeError, ValueError):
        _protocol("服务返回的内容不是合法JSON；请检查是否访问了正确的外部服务。")
    if not isinstance(value, dict) or not _strict_json(value):
        _protocol("服务响应必须为严格JSON对象，不能包含NaN或非JSON对象。")
    return value


def _failure(error, job_id, started):
    record = dict.fromkeys(AGENT_FIELDS, "")
    record.update(
        status=error.status,
        stage=error.stage,
        job_id=_safe_text(job_id, 128),
        error_code=error.code,
        message=_safe_text(error.message),
        elapsed_seconds="%.2f" % max(0.0, time.monotonic() - started),
    )
    return {"agent": [record]}


def main(params):
    """首次空agent时提交任务；后续从输入agent取得job_id直接续查。"""
    started = time.monotonic()
    job_id = ""
    try:
        if not isinstance(params, dict):
            _input("main(params)的params必须为字典。")
        incoming = params.get("agent")
        if isinstance(incoming, list) and len(incoming) == 1 and isinstance(incoming[0], dict):
            incoming_id = incoming[0].get("job_id")
            if isinstance(incoming_id, str) and re.fullmatch(_JOB_PATTERN, incoming_id):
                job_id = incoming_id
        previous = _resume_agent(params)
        if previous is not None:
            job_id = previous["job_id"]
            if previous["status"] != "processing":
                return {"agent": [previous]}
        payload = _payload(params)
        base = _configuration()
        deadline = started + ATTEMPT_SECONDS
        latest = None  # 只接受本轮实际GET得到的状态，不能拿旧状态掩盖通信异常。
        with requests.Session() as session:
            session.trust_env = False
            session.headers.update(
                {"Authorization": "Bearer " + API_TOKEN, "Accept": "application/json"}
            )

            def call(method, path, body=None, wait=False):
                remaining = deadline - time.monotonic()
                if remaining <= FINISH_MARGIN_SECONDS:
                    raise _WindowEnded()
                connect = min(CONNECT_SECONDS, remaining / 3.0)
                read = min(POLL_SECONDS + 1.0, remaining - connect - 1.0)
                if read <= 0:
                    raise _WindowEnded()
                kwargs = {"timeout": (connect, read), "allow_redirects": False}
                if body is not None:
                    kwargs["json"] = body
                if wait:
                    kwargs["params"] = {"wait_seconds": max(0.0, min(POLL_SECONDS, read - 1.0))}
                try:
                    response = session.request(method, base + path, **kwargs)
                except requests.RequestException:
                    raise _NodeError(
                        "failed",
                        "client_transport",
                        "DC_AGENT_NETWORK_ERROR",
                        "连接外部服务失败或超时；提交请求可能已被受理。请保留同一业务输入和workflow_run_id排查，勿随机创建新任务。",
                    ) from None
                return _response_json(response)

            if not job_id:
                submitted = call("POST", "/v3/excel/jobs", payload)
                received_id = submitted.get("job_id")
                if not isinstance(received_id, str) or not re.fullmatch(_JOB_PATTERN, received_id):
                    _protocol("任务受理响应缺少有效job_id；未生成新的随机任务身份。")
                job_id = received_id
                if submitted.get("status") not in {"ready", "processing"} | _FAILURES:
                    _protocol("任务受理响应的status无效。")
            path = "/v3/excel/jobs/" + quote(job_id, safe="")
            while True:
                try:
                    record = _agent(call("GET", path, wait=True), job_id)
                except _WindowEnded:
                    if latest is not None:
                        return {"agent": [latest]}
                    raise _NodeError(
                        "failed",
                        "client_transport",
                        "DC_AGENT_NO_SERVER_STATE",
                        "本轮等待窗口结束前未取得有效任务状态；未将未知状态伪装为processing，请按job_id检查服务。",
                    ) from None
                # 必须先处理已经收到的ready/失败，再检查时间；迟到的有效终态不能被窗口覆盖。
                if record["status"] != "processing":
                    return {"agent": [record]}
                latest = record
                remaining = deadline - time.monotonic()
                if remaining <= FINISH_MARGIN_SECONDS:
                    return {"agent": [latest]}
                time.sleep(min(RETRY_PAUSE_SECONDS, max(0.0, remaining - FINISH_MARGIN_SECONDS)))
    except _NodeError as error:
        return _failure(error, job_id, started)
    except _WindowEnded:
        return _failure(
            _NodeError(
                "failed",
                "client_transport",
                "DC_AGENT_NO_SERVER_STATE",
                "本轮等待窗口结束前未取得有效任务状态；请保留同一业务输入排查服务。",
            ),
            job_id,
            started,
        )
    except Exception:
        # 平台硬超时/进程强杀无法保证捕获；普通异常统一安全返回，不输出原始异常或局部变量。
        return _failure(
            _NodeError(
                "failed",
                "client_protocol",
                "DC_AGENT_CLIENT_ERROR",
                "接入节点发生未预期的普通异常；未输出原始异常以保护凭据，请核对节点代码和服务日志。",
            ),
            job_id,
            started,
        )
