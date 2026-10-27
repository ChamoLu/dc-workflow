# 采集数据源：本次 pageTitle；替换生产节点 763，通用 HTML 代码接入节点。
# 数据来源编码、数据源路径代码、路径、目标表：沿用本次规则及交付资产的审查信息。
# 规则来源：本目录 README.md；V4 HTTP 交付契约。实际表格解析由外部框架生成的资产承担。
# 本节点仅使用本次 markdownMD5 下载 Markdown；禁止改用 fileMd5；title/publictime 只传本次值，不推断统计截止日期。
# 配置：平台单次超时 120 秒、异常重试 3 次；只在 ready 且交付校验通过时正常返回。
# 没有画布回环。115 秒窗口耗尽抛 DC_AGENT_PENDING；异常重试重新 POST 相同输入，后端幂等恢复。
# 不依赖失败节点写回 agent；输入中遗留的 agent/Python*/Prompt* 均不作为本次交付。
# 只修改 API_BASE_URL/API_TOKEN；令牌不映射为平台变量，不提交真实值。

import hashlib
import hmac
import json
import math
import re
import time
import unicodedata
from urllib.parse import quote, urlsplit

import requests

API_BASE_URL = "http://service-3.example.invalid"  # 候选本机地址；由配置人员按实际可达地址填写。
API_TOKEN = "REPLACE_WITH_DC_AGENT_API_TOKEN"
ATTEMPT_SECONDS = 115.0
CONNECT_SECONDS = 5.0
POLL_SECONDS = 20.0
RETRY_PAUSE_SECONDS = 0.2

IS_PROMPT = False
FILE_KEY = "markdownMD5"
FILE_TYPES = {"html", "htm", "shtml"}
POST_PATH = "/v4/html/jobs"
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
    "PromptFile",
    "PromptCode",
)
_TERMINAL_FAILURES = {"failed", "review_required", "invalid_input"}
_JOB_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
_TAG_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,100}")


def _safe_note(value, limit=600):
    """保留公开的具体原因；丢弃对象正文/堆栈/源码，脱敏凭据和 URL。"""
    if not isinstance(value, str) or not value.strip():
        return ""
    value = "".join(" " if unicodedata.category(ch) in {"Cc", "Cf", "Cs"} else ch for ch in value)
    value = re.sub(r"\s+", " ", value).strip()
    # 后台已脱敏的占位不是 JSON 数组；再次经过平台边界时仍保留具体失败原因。
    shape = re.sub(r"(?i)\[(?:redacted|URL omitted)\]", "REDACTED", value)
    if (
        shape.startswith(("{", "[", "<"))
        or re.search(r"[:=]\s*[\[{<]", shape)
        or re.search(
            r"(?i)\b(?:request|response|raw)[_ -]?(?:body|headers|payload)\s*[:=]|Traceback \(most recent call last\)|\bdef\s+\w+\s*\(|\bclass\s+\w+\s*[:(]",
            value,
        )
    ):
        return ""
    if isinstance(API_TOKEN, str) and API_TOKEN:
        value = value.replace(API_TOKEN, "[redacted]")
    value = re.sub(r"(?i)\bhttps?://[^\s<>\"'，；、;]+", "[URL omitted]", value)
    value = re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s,;\"'<>，；、]+", r"\1 [redacted]", value)
    value = re.sub(
        r"(?i)([\"']?(?:authorization|x[-_]api[-_]key|api[-_ ]?(?:key|token)|(?:access|refresh)[-_ ]?token|token|secret|password)[\"']?\s*[:=]\s*)(?:\[redacted\]|\"[^\"]*\"|'[^']*'|[^\s,;}\]，；、]+)",
        r"\1[redacted]",
        value,
    )
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[redacted]", value)
    return value[:limit]


def _safe_tag(value, fallback):
    if (
        isinstance(value, str)
        and _TAG_PATTERN.fullmatch(value)
        and (not isinstance(API_TOKEN, str) or not API_TOKEN or API_TOKEN not in value)
    ):
        return value
    return fallback


def _safe_job(value):
    if (
        isinstance(value, str)
        and _JOB_PATTERN.fullmatch(value)
        and (not isinstance(API_TOKEN, str) or not API_TOKEN or API_TOKEN not in value)
    ):
        return value
    return ""


def _stop(error_code, stage, message, job_id="", status="failed", pending=False, issues=None):
    """仅输出受控摘要；不串接源码、请求正文、响应正文、URL、令牌或原始异常。"""
    diagnostic = {
        "status": status
        if status in {"processing", "failed", "review_required", "invalid_input"}
        else "failed",
        "stage": _safe_tag(stage, "delivery_check"),
        "error_code": _safe_tag(error_code, "PROTOCOL_ERROR"),
        "message": _safe_note(message, 600)
        or "当前阶段未完成，请按错误码与任务标识检查本机诊断报告",
        "job_id": _safe_job(job_id),
    }
    if issues:
        diagnostic["issues"] = issues[:8]
    prefix = "DC_AGENT_PENDING" if pending else "DC_AGENT_ERROR"
    raise RuntimeError(
        prefix + ": " + json.dumps(diagnostic, ensure_ascii=False, allow_nan=False)
    ) from None


def _strict_json(value, stage, job_id=""):
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        _stop(
            "INVALID_JSON",
            stage,
            "输入或交付必须是严格 JSON，不能包含 NaN、Infinity 或非 JSON 对象",
            job_id,
        )


def _text(params, key, required=False, limit=10000):
    value = params.get(key, "")
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        _stop(
            "INVALID_INPUT",
            "preflight_input",
            "%s 必须为%s字符串，长度不超过 %d" % (key, "非空" if required else "", limit),
            status="invalid_input",
        )
    return value


def _payload(params):
    if not isinstance(params, dict):
        _stop(
            "INVALID_INPUT",
            "preflight_input",
            "main(params) 的 params 必须为字典",
            status="invalid_input",
        )
    result = {
        FILE_KEY: _text(params, FILE_KEY, required=True, limit=200),
        "pageTitle": _text(params, "pageTitle", required=True, limit=1000),
        "fileType": _text(params, "fileType", required=True, limit=30),
        "title": _text(params, "title"),
        "publictime": _text(params, "publictime", limit=1000),
    }
    result["fileType"] = result["fileType"].strip().lower().lstrip(".")
    if result["fileType"] not in FILE_TYPES:
        _stop(
            "INVALID_INPUT",
            "preflight_input",
            "fileType 与当前接入链路不符；必须绑定本次真实扩展名",
            status="invalid_input",
        )
    rules = params.get("GZKJG")
    if isinstance(rules, str):
        try:
            rules = json.loads(rules)
        except (TypeError, ValueError):
            _stop(
                "INVALID_INPUT",
                "preflight_rules",
                "GZKJG 字符串必须是合法 JSON 数组",
                status="invalid_input",
            )
    if rules is None or rules == []:
        _stop(
            "without_GZKJG",
            "preflight_rules",
            "本次没有有效 GZKJG，未提交生成任务；请检查 2442/8448 的查询及映射",
            status="invalid_input",
        )
    if not isinstance(rules, list):
        _stop(
            "INVALID_INPUT",
            "preflight_rules",
            "GZKJG 必须为规则数组或该数组的 JSON 字符串",
            status="invalid_input",
        )
    if not any(
        isinstance(row, dict)
        and isinstance(row.get("rule_indicator_name"), str)
        and row["rule_indicator_name"].strip()
        and row["rule_indicator_name"].strip().lower() not in {"empty", "null", "none"}
        for row in rules
    ):
        _stop(
            "without_GZKJG",
            "preflight_rules",
            "GZKJG 至少需要一条有效 rule_indicator_name；未提交生成任务",
            status="invalid_input",
        )
    # 保留其余规则行交后端记录 invalid_rule_rows，不由接入节点反向造名或删源数据。
    result["GZKJG"] = rules
    if "workflow_run_id" in params:
        result["workflow_run_id"] = _text(params, "workflow_run_id", required=True, limit=200)
    _strict_json(result, "preflight_input")
    return result


def _configuration():
    if not isinstance(API_BASE_URL, str):
        _stop("CONFIGURATION_ERROR", "preflight_config", "API_BASE_URL 必须是 HTTP(S) 服务根地址")
    try:
        parts = urlsplit(API_BASE_URL)
        valid = parts.scheme in {"http", "https"} and bool(parts.hostname) and parts.port != 0
    except ValueError:
        valid = False
        parts = None
    if (
        not valid
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/"}
    ):
        _stop(
            "CONFIGURATION_ERROR",
            "preflight_config",
            "API_BASE_URL 必须是无凭据、路径、查询串或片段的 HTTP(S) 服务根地址",
        )
    if (
        not isinstance(API_TOKEN, str)
        or not API_TOKEN.strip()
        or API_TOKEN == "REPLACE_WITH_DC_AGENT_API_TOKEN"
        or "\n" in API_TOKEN
        or "\r" in API_TOKEN
    ):
        _stop(
            "CONFIGURATION_ERROR",
            "preflight_config",
            "请在脚本常量 API_TOKEN 填入与服务一致的访问令牌",
        )
    return API_BASE_URL.rstrip("/")


def _pending(job_id, stage):
    _stop(
        "TASK_PENDING",
        stage,
        "115 秒主动等待窗口结束；请保持业务输入及可选 workflow_run_id 不变重试，后端将幂等恢复同一任务",
        job_id,
        status="processing",
        pending=True,
    )


def _input_issues(response):
    """HTTP 422 只取白名单字段位置和错误类型，不读取任意 message/input/ctx。"""
    try:
        body = response.json()
    except (TypeError, ValueError):
        return []
    if not isinstance(body, dict) or not isinstance(body.get("issues"), list):
        return []
    atom = r"(?:body|fileMd5|markdownMD5|pageTitle|fileType|title|publictime|GZKJG|workflow_run_id|rule_indicator_name|rule_indicator_code|wait_seconds|unknown_field)"
    location = atom + r"(?:\[[0-9]{1,6}\])?(?:\." + atom + r"(?:\[[0-9]{1,6}\])?)*"
    errors = {
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
    issues = []
    for row in body["issues"][:8]:
        if not isinstance(row, dict):
            continue
        field, error = row.get("field"), row.get("error_type")
        if (
            isinstance(field, str)
            and len(field) <= 160
            and re.fullmatch(location, field)
            and isinstance(error, str)
            and error in errors
        ):
            issues.append({"field": field, "error_type": error})
    return issues


def _response_json(response, stage, job_id):
    if response.status_code not in {200, 202}:
        if response.status_code == 422:
            try:
                body = response.json()
            except (TypeError, ValueError):
                body = None
            code = (
                "without_GZKJG"
                if isinstance(body, dict) and body.get("error_code") == "without_GZKJG"
                else "HTTP_INPUT_REJECTED"
            )
            _stop(
                code,
                stage,
                "HTTP 422：服务端拒绝入参；按 issues 核对节点输入映射",
                job_id,
                status="invalid_input",
                issues=_input_issues(response),
            )
        message = "HTTP %d：请求未成功；请按 job_id 检查服务日志及访问配置" % response.status_code
        _stop("HTTP_ERROR", stage, message, job_id)
    try:
        value = response.json()
    except (TypeError, ValueError):
        _stop("PROTOCOL_ERROR", stage, "服务响应不是合法 JSON", job_id)
    _strict_json(value, stage, job_id)
    if not isinstance(value, dict):
        _stop("PROTOCOL_ERROR", stage, "服务响应必须为 JSON 对象", job_id)
    return value


def _filename(value, suffix):
    if not isinstance(value, str) or not value.endswith(suffix):
        return False
    stem = value[: -len(suffix)]
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


def _agent(response, job_id, page_title):
    records = response.get("agent")
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        _stop("PROTOCOL_ERROR", "delivery_check", "agent 必须是恰好一个对象的数组", job_id)
    raw = records[0]
    if any(
        key not in raw or not isinstance(raw[key], str) for key in (*AGENT_FIELDS, "report_ref")
    ):
        _stop(
            "PROTOCOL_ERROR",
            "delivery_check",
            "V4 服务 agent 的 16 个字段必须全部存在且为 String",
            job_id,
        )
    record = {key: raw[key] for key in AGENT_FIELDS}  # 平台投影严格为 15 字段，不映射 report_ref。
    if record["job_id"] != job_id:
        _stop(
            "JOB_ID_MISMATCH",
            "delivery_check",
            "服务返回的 job_id 与本次 POST 取得的任务不一致",
            job_id,
        )
    status = record["status"]
    if status not in {"ready", "processing"} | _TERMINAL_FAILURES:
        _stop("PROTOCOL_ERROR", "delivery_check", "外部任务 status 不在允许集合内", job_id)
    if record["cache_hit"] not in {"", "true", "false"}:
        _stop(
            "PROTOCOL_ERROR", "delivery_check", "cache_hit 必须是 true / false 字符串或空串", job_id
        )
    try:
        seconds = float(record["elapsed_seconds"])
    except ValueError:
        seconds = -1
    if not math.isfinite(seconds) or seconds < 0:
        _stop(
            "PROTOCOL_ERROR", "delivery_check", "elapsed_seconds 必须为有限非负秒数字符串", job_id
        )
    if status in _TERMINAL_FAILURES:
        _stop(
            record["error_code"] or "GENERATION_FAILED",
            record["stage"],
            record["message"]
            or "外部任务已终止，未交付可执行资产；请按 error_code、stage 和 job_id 查看本机诊断报告",
            job_id,
            status=status,
        )
    if status == "processing":
        # 不输出处理中响应，即使服务意外附带旧源码/旧提示词也不会写回平台变量。
        return record
    for key, value in record.items():
        if key not in {"message", "warning_summary"} and API_TOKEN in value:
            _stop(
                "CREDENTIAL_IN_ARTIFACT",
                "delivery_check",
                "交付资产或元数据包含本机服务访问令牌，已拒绝交付；正文未作改写",
                job_id,
            )
    record["message"] = _safe_note(record["message"], 600)
    record["warning_summary"] = _safe_note(record["warning_summary"], 2000)
    if record["error_code"] or record["quality_status"] not in {"accepted", "usable_with_warnings"}:
        _stop(
            "ARTIFACT_NOT_ACCEPTED",
            "delivery_check",
            "ready 资产携带错误码或质量状态不允许交付",
            job_id,
        )
    if record["cache_hit"] not in {"true", "false"} or not record["artifact_version"].strip():
        _stop(
            "ARTIFACT_INVALID",
            "delivery_check",
            "ready 必须声明固定 artifact_version 及明确 cache_hit",
            job_id,
        )
    is_prompt = IS_PROMPT
    file_field, body_field = (
        ("PromptFile", "PromptCode") if is_prompt else ("PythonFile", "PythonCode")
    )
    unused = ("PythonFile", "PythonCode") if is_prompt else ("PromptFile", "PromptCode")
    if any(record[key] for key in unused):
        _stop(
            "ARTIFACT_BODY_CONFLICT",
            "delivery_check",
            "本节点只接受对应的完整正文，另一组代码或提示词字段必须为空",
            job_id,
        )
    if (
        not _filename(record[file_field], ".md" if is_prompt else ".py")
        or not record[body_field].strip()
    ):
        _stop(
            "ARTIFACT_INVALID",
            "delivery_check",
            "ready 必须交付有效业务文件名及完整非空正文",
            job_id,
        )
    expected_name = page_title.replace("\\", "_") + (".md" if is_prompt else ".py")
    if record[file_field] != expected_name:
        _stop(
            "ARTIFACT_SOURCE_MISMATCH",
            "delivery_check",
            "交付文件名与本次 pageTitle 不一致，已阻止同类历史资产串源",
            job_id,
        )
    digest = record["code_sha256"]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        _stop("ARTIFACT_HASH_INVALID", "delivery_check", "code_sha256 必须为 64 位十六进制", job_id)
    try:
        actual = hashlib.sha256(record[body_field].encode("utf-8")).hexdigest()
    except UnicodeError:
        _stop("ARTIFACT_INVALID", "delivery_check", "交付正文不能编码为有效 UTF-8", job_id)
    if not hmac.compare_digest(digest.lower(), actual):
        _stop(
            "ARTIFACT_HASH_MISMATCH",
            "delivery_check",
            "交付正文 SHA256 与固定版本声明不符，已阻止执行",
            job_id,
        )
    return record


def main(params):
    """平台入口；只有已校验 ready 能正常返回，其他状态抛结构化诊断。"""
    started = time.monotonic()
    payload = _payload(params)
    base = _configuration()
    deadline = started + ATTEMPT_SECONDS
    job_id = ""
    with requests.Session() as session:
        session.trust_env = False  # 本机/内网服务直连，不向环境代理转发认证请求。
        session.headers.update(
            {"Authorization": "Bearer " + API_TOKEN, "Accept": "application/json"}
        )

        def call(method, path, body=None, wait=False):
            remaining = deadline - time.monotonic()
            stage = "http_submit" if method == "POST" else "http_poll"
            if remaining <= 2:
                _pending(job_id, stage)
            connect = min(CONNECT_SECONDS, remaining / 3)
            read = min(POLL_SECONDS + 2, remaining - connect - 1)
            kwargs = {"timeout": (connect, read), "allow_redirects": False}
            if body is not None:
                kwargs["json"] = body
            if wait:
                kwargs["params"] = {"wait_seconds": max(0.0, min(POLL_SECONDS, read - 1))}
            try:
                response = session.request(method, base + path, **kwargs)
            except requests.RequestException:
                _stop(
                    "HTTP_CONNECTION_FAILED",
                    stage,
                    "连接失败或超时；请求可能已受理，保持原输入重试将由后端幂等恢复，不要更换运行标识",
                    job_id,
                )
            return _response_json(response, stage, job_id)

        receipt = call("POST", POST_PATH, payload)
        job_id = _safe_job(receipt.get("job_id"))
        if not job_id or receipt.get("status") not in {"processing", "ready"} | _TERMINAL_FAILURES:
            _stop(
                "PROTOCOL_ERROR", "http_submit", "受理响应必须包含合法 job_id 与已知 status", job_id
            )
        path = "/v4/jobs/" + quote(job_id, safe="")
        while True:
            record = _agent(call("GET", path, wait=True), job_id, payload["pageTitle"])
            if record["status"] == "ready":
                output = {"agent": [record]}
                if IS_PROMPT:
                    output.update(PromptFile=record["PromptFile"], PromptCode=record["PromptCode"])
                return output
            remaining = deadline - time.monotonic()
            if remaining <= 2:
                _pending(job_id, record["stage"])
            time.sleep(min(RETRY_PAUSE_SECONDS, max(0.0, remaining - 2)))
