# 采集数据源：本次工作流的 pageTitle；只连接错误出口和原4589的空结果分支。
# 规则来源：循环接入方案；复用既有 agent 的13个 String 字段，仅输入 agent、count。
# 输出 agent 和空 out_data；普通成功分支沿用原有结束节点，不经过本节点。
# 不猜日期或补造指标；错误详情复用既有 agent 字段，按 job_id 关联服务日志。

import re as _ef_re

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


def _summary(value, limit=2000):
    if not isinstance(value, str):
        return ""
    text = _ef_re.sub(r"[\x00-\x1f\x7f]", " ", value)
    text = _ef_re.sub(r"(?i)\bbearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
    text = _ef_re.sub(
        r"(?i)(?:authorization|api[_-]?key|api[_-]?token|access[_-]?token|password|secret)\s*[:=]\s*[^\s,;]+",
        "[REDACTED]",
        text,
    )
    text = _ef_re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", text)
    return _ef_re.sub(r"https?://[^\s]+", "[URL omitted]", text)[:limit]


def _identifier(value):
    return (
        value
        if isinstance(value, str) and _ef_re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value)
        else ""
    )


def _copy_agent(record):
    result = {key: "" for key in _AGENT_FIELDS}
    if not isinstance(record, dict):
        return result
    for key in ("status", "stage", "error_code", "quality_status"):
        result[key] = _identifier(record.get(key))
    job = record.get("job_id")
    if isinstance(job, str) and _ef_re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job):
        result["job_id"] = job
    result["message"] = _summary(record.get("message"))
    result["warning_summary"] = _summary(record.get("warning_summary"), 600)
    cache = record.get("cache_hit")
    result["cache_hit"] = cache if isinstance(cache, str) and cache in {"true", "false"} else ""
    elapsed = record.get("elapsed_seconds")
    if isinstance(elapsed, str) and _ef_re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,6})?", elapsed):
        result["elapsed_seconds"] = elapsed
    # 本节点仅输出错误；四个代码交付字段保持空字符串，不能沿用上一轮源码。
    return result


def main(params):
    rows = params.get("agent") if isinstance(params, dict) else None
    record = (
        rows[0] if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict) else None
    )
    result = _copy_agent(record)
    if (
        record is None
        or set(record) != set(_AGENT_FIELDS)
        or any(not isinstance(record[key], str) for key in _AGENT_FIELDS)
    ):
        result.update(
            status="invalid_input",
            stage="workflow_diagnostic",
            error_code="DC_AGENT_PROTOCOL_ERROR",
            message="错误出口未收到恰好一个、包含13个 String 字段的 agent 对象；请检查变量映射。",
        )
    elif result["status"] in {"failed", "review_required", "invalid_input"}:
        # 保留外部失败或4912平台失败的原始错误码、实际阶段与已脱敏原因。
        if not result["message"]:
            result["message"] = "任务未成功，外部响应未提供详细原因；请按 job_id 查看服务日志。"
    elif result["status"] == "processing":
        count = params.get("count")
        if not result["job_id"] or record["error_code"]:
            result.update(
                status="failed",
                stage="workflow_wait",
                error_code="DC_AGENT_PROTOCOL_ERROR",
                message="processing 响应必须包含有效 job_id，且 error_code 为空；已阻止将异常响应误报为正常等待超时。",
            )
        elif isinstance(count, bool) or not isinstance(count, int) or count < 0:
            result.update(
                status="invalid_input",
                stage="workflow_wait",
                error_code="WORKFLOW_COUNTER_INVALID",
                message="等待计数 count 必须为非负整数；本节点未继续等待或重新提交任务。",
            )
        else:
            result.update(
                status="failed",
                stage="workflow_wait",
                error_code="WORKFLOW_WAIT_TIMEOUT",
                message="外部任务最后观测状态为 processing；本次已等待 %d 轮，工作流停止等待。该诊断不代表后台任务已失败或已取消，可按 job_id 查询最终状态。"
                % count,
            )
    elif result["status"] == "ready":
        result.update(
            status="review_required",
            stage="workflow_postprocess",
            error_code="OUT_DATA_EMPTY",
            message="解析器已交付，但原4589空结果分支未获得有效 out_data；请检查后半链的 GZK 名称匹配、规则编码、日期来源及清洗日志。此处不补造数据或日期，可按 job_id 查看服务日志。",
        )
    else:
        result.update(
            status="failed",
            stage="workflow_diagnostic",
            error_code="DC_AGENT_PROTOCOL_ERROR",
            message="错误出口收到未知 agent.status；请检查外部响应和工作流变量映射。",
        )
    return {"agent": [result], "out_data": []}
