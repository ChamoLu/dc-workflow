# V4 平台节点独立代码

本目录交付可直接粘贴的完整节点代码。每份 `.py` 独立包含 `main(params)`，不导入本地工程模块；三个接入节点依赖平台已有的 `requests`。本轮只交付方案与代码，由用户亲自在生产工作流配置，没有修改线上工作流。

当前版本已撤销`artifact_kind`，平台agent为15个String字段。已按旧版配置时，请删除该子字段、同步更新本目录三个接入脚本和两个执行器，并重启本机服务；完整映射与升级顺序见[生产工作流节点配置手册](../V4生产工作流节点配置手册.md)。

## 节点及连线

| 文件 | 使用位置 | 正常输出 | 后继 |
|---|---|---|---|
| `01_Excel外部智能体接入_3708.py` | 脚本替换 3708 Excel Agent | `agent` | 4912 |
| `02_HTML外部智能体接入_763.py` | 脚本替换 763 HTML Agent | `agent` | 5242 |
| `03_Markdown提示词外部智能体接入_2204.py` | 脚本替换 2204 md-Agent | `agent`、`PromptFile`、`PromptCode` | 453 |
| `04_Excel平台执行器_4912.py` | 修改现有 4912 通用执行器 | `ExcelData` | 原 8300 及后半链 |
| `05_HTML平台执行器_5242.py` | 修改现有 5242 执行器 | `ExcelData` | 原 8300 及后半链 |
| `06_Markdown平台执行提示词_453.md` | 替换 453 的任务提示词，保持平台文件提取节点 | `out_data` | 原 2938 及后半链 |

暂时删除 9802、5519、2950；前驱直接连接上述后继。3060 类型分流、GZK 查询、匹配、换算、完整内审及人工标注链保留。`PLPL` 是 `indicator_release_frequency` 的变量名，本目录不修改该业务字段。

本轮没有 `count`、初始化节点、画布回环或新判断节点。未来循环方案另行实现。

## 三个接入节点的输入

| `main(params)` 输入名 | Excel 3708 | HTML 763 | Markdown 2204 |
|---|---|---|---|
| `fileMd5` | 本次模板 `fileMd5`，必填 | 不配置 | 不配置 |
| `markdownMD5` | 不配置 | 本次模板 `markdownMD5`，必填 | 本次模板 `markdownMD5`，必填 |
| `pageTitle` | 本次模板 `pageTitle`，必填 | 同左 | 同左 |
| `fileType` | 实际 `xls` / `xlsx` / `csv` | 实际 `html` / `htm` / `shtml` | 实际 `md` / `markdown` / `pdf` / `png` / `jpg` / `jpeg` / `doc` / `docx` |
| `title` | 本次模板 `title` | 同左 | 同左 |
| `publictime` | 本次模板 `publictime` | 同左 | 同左 |
| `GZKJG` | 8448 整理后的 `GZK.GZKJG` 子数组 | 同左 | 同左 |
| `workflow_run_id` | 可选，仅绑定平台真实且重试间稳定的任务运行 ID | 同左 | 同左 |

`GZKJG` 可传数组或其 JSON 字符串。至少要有一条有效 `rule_indicator_name`；混合列表中的无名行原样交后端过滤并审计。不要把整个外层 `GZK` 对象绑定到名为 `GZKJG` 的输入。

没有真实稳定运行 ID 时，删除 `workflow_run_id` 输入映射；不要填空串、当前时间、随机数或示例 ID。三个接入节点忽略遗留的 `agent`、`PythonFile`、`PythonCode`、`PromptFile` 和 `PromptCode` 输入，始终按本次业务参数 POST。HTML/Markdown 不允许用 `fileMd5` 代替缺失的 `markdownMD5`。

各脚本只填写 `API_BASE_URL` 和 `API_TOKEN` 两个连接常量。仓库保留候选本机地址与令牌占位，不能把真实令牌放入 `agent` 或其他平台输出变量。请求关闭环境代理和 HTTP 重定向。

公开 HTTP 路由为：

| 接口 | 用途 |
|---|---|
| `POST /v4/excel/jobs` | 创建或幂等恢复 Excel/CSV 任务 |
| `POST /v4/html/jobs` | 创建或幂等恢复 HTML Python 任务 |
| `POST /v4/markdown/jobs` | 创建或幂等恢复 Markdown 专属提示词任务 |
| `GET /v4/jobs/{job_id}` | 三路共用状态与交付查询，支持 `wait_seconds` |
| `GET /v4/jobs/{job_id}/reports/{report_name}` | 通过本机服务按任务定位允许公开的报告 |

HTML 代码和 Markdown 提示词始终处理 `markdownMD5` 对应的本次 Markdown；上游扩展名只保留资产归属、路由与审查信息，不决定运行时表格结构。

## `agent` 变量和输出映射

新建/调整父变量 `agent` 为 `Array<Object>`。以下 15 个子字段全部是 **String**，按顺序配置：

```text
status
stage
job_id
PythonFile
PythonCode
code_sha256
artifact_version
cache_hit
quality_status
warning_summary
error_code
message
elapsed_seconds
PromptFile
PromptCode
```

HTTP 服务返回 16 字段，额外的 `report_ref` 只用于服务报告定位；平台脚本校验后主动投影为 15 字段，不在平台恢复该字段。`cache_hit` 为字符串 `true` / `false`，`elapsed_seconds` 也是字符串。

三个接入节点的返回键 `agent` 整体映射到父变量 `agent`，不要将其拆成互不关联的顶层代码变量。Markdown 节点还要把返回的 `PromptFile`、`PromptCode` 分别映射到已有顶层同名 String 变量；两处内容与该次 `agent` 交付完全相同，供 453 原有变量绑定使用。

| 固定接入链路 | 正文与业务文件名 | 必须空置的字段 |
|---|---|---|
| Excel 3708 | `PythonCode` / `PythonFile` | `PromptCode`、`PromptFile` |
| HTML 763 | `PythonCode` / `PythonFile` | `PromptCode`、`PromptFile` |
| Markdown 2204 | `PromptCode` / `PromptFile` | `PythonCode`、`PythonFile` |

链路由当前固定节点及其 `fileType` 输入决定，不新增任何链路标识字段。执行时继续核对本次 `pageTitle` 对应文件名和正文契约。

三路共用 `code_sha256`，其值始终是**交付完整正文原样 UTF-8 编码**的 SHA256，不改写换行或其他正文。就绪还要求固定 `artifact_version`、允许的质量状态及合法文件名。业务文件名必须等于本次 `pageTitle` 中反斜杠替换为下划线后加 `.py` 或 `.md`，并与后台一致限制为不超过 255 个 UTF-8 字节。

`ready` 且 `quality_status=accepted` 或 `usable_with_warnings` 可以交付。后者仍有语义问题需要查看报告；`ready` 只表示资产已可用，平台随后仍须正式执行。

## 等待与错误行为

三个接入节点设置平台 **单次超时 120 秒、异常重试次数 3**。代码主动窗口为 115 秒，单次长轮询 `wait_seconds` 不超过 20 秒；连接/读取超时随着剩余窗口缩短。平台“重试次数 3”是否另加首次执行以平台实际语义为准，总等待时长不保证恰好 360 秒，且始终受后台原任务预算独立约束。

接入节点只在交付完整且校验通过的 `ready` 状态正常返回。处理中超过主动窗口抛 `DC_AGENT_PENDING`，包含 `job_id`；平台异常重试重新 POST 相同规范业务输入，服务端通过持久幂等取得原任务。此过程不依赖失败节点写回 `agent`。

终态失败、空 GZK、连接错误或交付校验失败抛 `DC_AGENT_ERROR`。每个异常都包含有界 JSON 诊断：`status`、`stage`、`error_code`、`message`、`job_id`。服务端公开的具体失败摘要会保留，令牌、Bearer/Basic 值、常见密钥和 URL 会脱敏，原始请求/响应对象、源码和 traceback 不进入摘要。若交付正文含当前服务令牌，直接拒绝交付，不通过改写正文绕过 hash。

因为本轮已删除前置判断节点，执行失败不能正常返回空指标或空提示词。平台应在异常时停止当前链，避免执行后继和误用历史变量。异常重试耗尽后的记录直接按节点日志和 `job_id` 排查；本轮没有额外诊断合并节点。

## 4912 与 5242 执行器配置

两个执行器输入都必须整体映射上游 `agent` 父数组，再映射本次五个业务字段：

- 4912：`fileMd5`、`title`、`publictime`、`fileType`、`pageTitle`。
- 5242：`markdownMD5`、`title`、`publictime`、`fileType`、`pageTitle`。

不配置顶层 `PythonFile`、`PythonCode`，不把 `GZK`、`GZKJG`、令牌或其他全局变量传给交付解析器。执行器重新检查就绪状态、正文契约、文件名与当前来源绑定、版本、质量和 hash，再在独立命名空间编译、加载、调用交付 `main(params)`。

函数沿用 `{code, msg, ExcelData}` 返回契约；**平台输出配置只需将返回键 `ExcelData` 映射到已有 `excel_data` 父数组**，原四字段子结构继续使用。HTML 也必须返回 `ExcelData`，不回退兼容 `OUTDATA`。

执行器验证四个必填字段、有限数值、有效 ISO 日期或空串和严格 JSON。它不清洗或换算业务值；数字 0、数值字符串精度、括号单位和未知日期空串原样保留。空数组、错误返回码、非法结果、编译/加载/执行异常均抛 `DC_AGENT_EXECUTION_ERROR`，不会正常返回空 `ExcelData`。可定位的源码异常记录交付文件名和行号，但不回显异常文本或源码行。

两个执行器设为 **单次超时 120 秒 / 异常重试次数 0**，避免正式解析执行被无条件重复；真实源文件执行耗时应在平台验收时核对。**3 次异常重试仅用于三个外部接入节点及原 453**。本轮没有将后台下载 mock 代码放入生产节点。

## 453 配置

保留生产 `file_extract` 平台大模型节点及其既有模型设置。文件输入必须绑定本次 `markdownMD5`，保持单文件读取；将 `06_Markdown平台执行提示词_453.md` 的完整正文复制到任务提示词。

正文中的 `{{PromptCode}}`、`{{GZK.GZKJG}}`、`{{title}}`、`{{publictime}}` 是**配置人员定位用的占位标记**，必须原位替换为平台对应真实变量标签/引用 token；不能把双花括号当平台模板语法直接保存。`PromptCode` 要插入完整 String 正文，不是文件名、hash 或版本号。规则段优先沿用生产节点已有可用的 **`GZK` 父对象真实 token**，模型从中读取 `GZKJG` 子数组；平台确实支持子字段引用时才直接插入 `GZK.GZKJG`。必须替换原占位位置，不能只在末尾追加一个 token。

输出仍映射 `out_data`；模型只输出 JSON 的五个 String 字段：`indicator_code`、`indicator_name`、`indicator_data`、`indicator_unit`、`end_date`。成功唯一匹配后的代码和名称来自同一条真实 GZK 记录，数值、单位、日期来自本次 Markdown 及专属规则允许的运行时字段。`publictime` 不作为统计日期。后续元数据仍由原 2938 及业务链补齐，不让模型新增规则库字段。

## 本机验证范围

针对性测试为 `dc_workflow/agent/tests/test_v4_workflow_nodes.py`：受控 HTTP 和时间、三路 POST/GET、处理中重试、失败诊断与脱敏、旧值隔离、正文错配与 hash、来源绑定、UTF-8 文件名限制、真实小段源码的编译和执行、四字段、零值/精度/日期和严格 JSON。

独立的 `test_v4_live_service.py` 使用真正本机 Uvicorn HTTP、spawn 后台 worker 及受控资产发布，验证三路到两个代码执行器/Markdown 提示词交接。本轮测试没有调用真实模型或公司网络，也不代表生产 DC 平台的真实验收。正式测试报告保留在 `runtime/diagnostics/`。
