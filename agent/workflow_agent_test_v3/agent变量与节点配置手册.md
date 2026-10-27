# agent 变量与两个节点配置手册

2026-10-25 更新：当前count循环、结构化诊断及普通结束方案以 [V3循环接入工作流节点配置手册](V3循环接入工作流节点配置手册.md) 为准。本文保留原14字段及旧异常重试编排作为历史说明；不要将旧“非ready抛错”行为与新脚本混用。

日期：2026-10-20（北京时间）。状态：变量与映射清单已落实为两个节点脚本及本机接口。完整代码见 [节点代码目录](工作流节点代码/README.md)，启动见 [V3 本机指南](V3本机启动与联调指南.md)，平台仍由用户配置和验收。

当前采用 **一个主变量 agent，所有选定的外部信息均放在其子字段中**。本文替代此前“agent_result JSON 字符串加多个顶层变量”的建议。2229、2442、8448 及后半链保持现状，7882 的 unitArray 参数名已由用户更正。

## 1. 主变量怎么建

进入工作流的模板配置，按 out_data 的方式新增一个手动配置变量：

| 项目 | 配置 |
|---|---|
| 变量名 | `agent`，全小写 |
| 数据类型 | `Array<Object>`，与当前 out_data 一致 |
| 描述 | 外部智能体解析代码与接入结果 |
| 来源 | 手动配置，对应现有导出配置中的 manual |
| 主键 | 不勾选 |
| 子字段 | 按下表增加，全部为 String |
| 默认内容 | 不填历史代码、固定状态或示例任务编号 |

本阶段是一文件一任务，agent 运行时采用“长度为 1 的对象数组”：数组中的唯一对象就是本次接入结果。它没有与指标条数一一对应的含义，也不按模型修复轮次追加记录。4912 必须检查数组恰好一条，不能随意选择第一条或最后一条。

这是一种明确约定的使用方式，不是把 String 字段塞入 JSON 冒充子字段，也不是按已证实的 Object 类型配置。需要通过一次受控返回测试，确认平台整体传入 agent 时保持该结构；如果实际出现列式对象或其他形态，按真实结构显式适配，不能静默取值。

## 2. 完整子字段清单：14 个，全部 String

字段名区分大小写。PythonFile、PythonCode 保留既有执行器常用拼写；其余字段使用下表名称。

| 序号 | 子字段名 | 类型 | 中文描述 | 取值与用途 |
|---|---|---|---|---|
| 1 | `status` | String | 外部接入状态 | ready、processing、failed、review_required、invalid_input；4912 只接受 ready |
| 2 | `stage` | String | 外部任务阶段 | 当前集成阶段 queued/running/waiting_for_asset/complete/failed/expired/interrupted/delivery；预检、生成和校验等内部细节保存在任务报告 |
| 3 | `job_id` | String | 对外任务编号 | 外部接入服务的持久任务 ID；用于状态和报告查询，不混同内部框架 task_id |
| 4 | `PythonFile` | String | 解析代码文件名 | 已登记的业务文件名；用于编译与日志，不是平台需要打开的服务器磁盘路径 |
| 5 | `PythonCode` | String | 完整解析代码 | 固定版本的完整源码，不加 Markdown 围栏，不改换行或引号 |
| 6 | `code_sha256` | String | 代码 SHA256 | PythonCode 的 UTF-8 bytes 校验值；防止正文截断、改写或串版本 |
| 7 | `artifact_version` | String | 代码资产版本 | 与正文绑定的不可变版本 ID；用于核对本次实际执行的代码 |
| 8 | `cache_hit` | String | 是否复用已有代码 | 严格使用字符串 true / false；暂未知时为空，不用未知状态伪填 false |
| 9 | `quality_status` | String | 代码质量状态 | 沿用框架的 accepted / usable_with_warnings；没有资产时为空 |
| 10 | `warning_summary` | String | 当前质量问题摘要 | 当前选中版本的未解决问题简述；没有则空，不复制所有历史候选意见 |
| 11 | `error_code` | String | 接入错误码 | 沿用明确错误码，包括 FilenoData 等原有大小写；成功时为空 |
| 12 | `message` | String | 接入结果说明 | 简要说明复用、新生成、回退或失败原因；全文留在报告中 |
| 13 | `report_ref` | String | 完整质量报告引用 | 服务实际提供的可查询报告引用或可访问地址；没有则空，不编造 URL |
| 14 | `elapsed_seconds` | String | 外部任务耗时秒数 | 从首次受理到本次返回的实际用时；平台重试不重置起点，例如字符串 142.35 |

使用 String 是为了与已核对的模板子字段类型一致，避免本轮依赖尚未确认的 Boolean、Number 等类型。外部 JSON 的布尔值、耗时在接入边界转换为约定字符串，不把布尔 false 错当作缺失。

这 14 个字段都属于外部任务或代码交付信息。首版不把平台执行结果再混进 agent：4912 的运行成功或失败使用平台节点结果和日志查看，实际业务记录仍放在 excel_data、out_data。

无需接收模型名称、token 用量、聊天历史、推理内容、每轮候选全文、服务器绝对路径、参考工作簿全文、GZK 副本或本地验证全部记录。这些需要时通过 job_id 和 report_ref 回查。平台已有的 fileMd5、pageTitle、title、fileType 不在 agent 内重复保存。

## 3. 字段的来源如何映射

外部接口返回的数据先由接入脚本投影为这 14 个字段；不把原始响应整个塞进 agent。

| agent 子字段 | 信息来源 |
|---|---|
| status、stage | 接入任务的归一状态及当前真实阶段 |
| job_id | 外部任务服务记录的对外 ID；内部框架 task_id 的对应关系留在服务端 |
| PythonFile | 已登记资产的业务文件名 |
| PythonCode | 从该任务绑定的不可变版本读取并核验的源码 |
| code_sha256 | 已登记的 content_hash，并与返回正文重新核对 |
| artifact_version | artifact.version |
| cache_hit | 现有 Result.cache_hit，转换为字符串 |
| quality_status | artifact.quality_status；当前实现取 accepted 或 usable_with_warnings |
| warning_summary | 当前选中候选的问题摘要；按保存报告整理，不额外调用模型写一份新审查 |
| error_code、message | 原始错误码与简要原因，或明确的成功说明 |
| report_ref | 受控报告访问层提供的引用；不直接透传任意本地文件路径 |
| elapsed_seconds | 接入服务记录的本次任务累计耗时 |

成功交付时，status 必须为 ready，job_id、PythonFile、PythonCode、code_sha256、artifact_version 和 quality_status 应有有效值。error_code 为空；warning_summary 根据实际情况为空或有内容。stage 按服务记录取值，不为了填满字段造阶段或耗时。

业务失败时，status、job_id（已经受理时）、error_code、message 等按真实情况提供；四个代码字段 PythonFile、PythonCode、code_sha256、artifact_version 一律为空，避免沿用上一次成功代码。尚未受理成功时 job_id 可为空。

## 4. 外部框架接入节点怎么配置

### 4.1 节点类型、位置与基本设置

| 项目 | 配置 |
|---|---|
| 节点类型 | 脚本，Python3 |
| 建议名称 | 外部框架接入 |
| 上游 | 8448：预标注_规则库查询转 |
| 下游 | 4912：动态代码执行器 |
| 单次时限 | 120 秒 |
| 失败重试 | 按用户当前要求使用平台现有重试配置，并以 360 秒作为接入总预算 |
| 入口 | main(params)，后续实现脚本时保持此接口 |

把原来的 8448 → 4912 连线改为 8448 → 新接入节点 → 4912。其余连线保持。新节点 ID 使用平台实际生成的 ID。

### 4.2 输入参数逐项配置

| 参数名 | 类型 | 选择的模板字段 | 引用 |
|---|---|---|---|
| `fileMd5` | String | fileMd5 | `#template_config__0__fileMd5` |
| `pageTitle` | String | pageTitle | `#template_config__0__pageTitle` |
| `title` | String | title | `#template_config__0__title` |
| `fileType` | String | fileType | `#template_config__0__fileType` |
| `publictime` | String | publictime | `#template_config__0__publictime` |
| `GZKJG` | 与现有 8300 的对应入参保持一致 | GZK 下的 GZKJG | `#template_config__0__GZK.GZKJG` |

当前快照将 GZKJG 声明为 String，而 8448 实际返回对象数组。首版沿用现有映射，由接入边界检查实际值；仅在收到合法 JSON 字符串时解码一次，最终构造框架所需的规则数组。此处不改 2442 或 8448。

如果平台实际提供稳定运行 ID，可以另加可选输入 workflow_run_id；没有则暂不建这个输入，不用 rid 或随机猜测的字段代替。服务地址和认证令牌属于连接配置，不放在 agent 的业务子字段中，也不传给解析代码。

### 4.3 输出参数：只配置一个父变量映射

| 返回参数名 | 类型 | 写入的模板字段 |
|---|---|---|
| `agent` | Array<Object> | `#template_config__0__agent` |

后续脚本的成功返回结构约定为“字典的 agent 键对应一个长度为 1 的数组，数组对象包含上述 14 个字段”。这是返回结构设计，本轮不提供可直接运行的脚本。

整体输出父变量就会带上子字段，不同时再配 14 个叶子输出映射，避免父子重复写入。不要将返回参数名填成 agent.PythonCode；点号路径用于选择模板子字段，不自动意味着脚本返回了嵌套对象。

如实施时发现平台只能通过逐个叶子映射可靠写入，再改成对应的逐字段方式，并验证每个叶子的实际类型；首选仍是现有 out_data 已采用的整体数组写回。

### 4.4 接入脚本后续需要实现的行为

1. 读取本次现有字段，验证 fileMd5、pageTitle 和有效 GZKJG；title 等使用平台本次带入的值。
2. 构造稳定请求身份，向外部服务创建或取得任务 J。优先用真实运行 ID；没有时按稳定业务输入指纹关联同一有效期任务。
3. 每次节点尝试主动等待约 115 秒，给 120 秒上限留约 5 秒收尾；按三次尝试约 345 秒估算，360 秒仅为服务端硬上限，平台实际重试行为待联调核实。外部服务在 HTTP 断开后继续管理 J。
4. 若平台重试，重新取得同一 J，不重置受理时间、不重复启动生成、不给模型重新发放一份完整预算。
5. ready 时检查请求身份、main-ExcelData-v1 契约、固定版本正文和 SHA256，再生成完整的 agent 单条记录。
6. usable_with_warnings 按 V2 约定可交付，但在 quality_status、warning_summary、report_ref 中保留问题。

processing 不是可以交给 4912 的成功结果。接近本次上限仍未就绪，使用经验证能够触发平台重试的失败信号；不能假设正常返回一个非零 code 就会重试。

首版仍按 360 秒总预算设计，远程生成建议约 300 秒，其余预留传输、排队、发布和重试衔接。重试计数与间隔按实际平台记录；单次 HTTP 不能直接等满 360 秒。本地 CLI 的 600 秒配置不在此阶段修改。

## 5. 动态代码执行器 4912 怎么配置

### 5.1 基本设置

保留原 4912 节点，建议重命名为“动态代码执行器”。节点类型仍为 Python3，入口仍为 main(params)。将原来写死的上街区解析脚本替换为通用加载包装器；原文导出文件保留作为基线，不修改原快照。

4912 仅下载业务文件并执行收到的解析代码，不再等待模型生成。单次时限仍为 120 秒，重试使用平台既有设置；不要把外部生成的 360 秒预算在 4912 再发放一遍。解析器必须按只读提取接口设计，避免重试产生外部写入。

### 5.2 输入参数：整体读取 agent

| 参数名 | 类型 | 选择的模板字段 | 说明 |
|---|---|---|---|
| `agent` | Array<Object> | `#template_config__0__agent` | 整个父变量；包装器从唯一对象读取 status、代码、hash 等 |
| `fileMd5` | String | `#template_config__0__fileMd5` | 当前原始文件 |
| `title` | String | `#template_config__0__title` | 本次自动随入的文章标题 |
| `publictime` | String | `#template_config__0__publictime` | 当前发布信息，可为空 |
| `fileType` | String | `#template_config__0__fileType` | 当前文件类型，用于必要的包装器检查 |
| `pageTitle` | String | `#template_config__0__pageTitle` | 当前路径，用于包装器和日志核对 |

不再单独配置顶层 PythonCode、PythonFile、agent_result 或 agent_status。因为 agent 是 Array<Object>，单独选择 agent.PythonCode 可能得到一个字符串数组；4912 整体读取并严格校验长度，能够避免把列表传给 compile。

### 5.3 输出参数：保持原接口

| 返回参数名 | 类型 | 写入的模板字段 |
|---|---|---|
| `ExcelData` | Array<Object> | `#template_config__0__excel_data` |

excel_data 原有四个子字段 indicator_name、indicator_data、indicator_unit、end_date 保持；8300 继续用入参名 ExcelData 读取。不要把输出写到 agent 或 out_data，后续单位查询、匹配和清洗均保持原编排。

包装器仍要求目标 main(params) 返回 code/msg/ExcelData；成功应 code=0。平台变量映射仍只需写 ExcelData，code/msg 用于包装器判断和节点日志，不因字典里有 code 字段就认为平台一定会停止或重试。

### 5.4 包装器后续需要实现的行为

1. 校验 agent 为约定的单条记录结构，status 恰好为 ready；空数组、多条记录、processing 或失败状态均不得执行。
2. 校验 PythonCode/PythonFile、版本和 SHA256 均有效。代码按原文 UTF-8 计算 hash，不对源码应用指标名称清洗，不去逗号、引号或更改换行。
3. 使用独立字典作为 globals/locals 编译和加载；确认存在可调用的 main。不能覆盖包装器自己的 main 或平台日志所需的 datetime 绑定。
4. 仅组装解析器需要的业务参数，沿用当前 main 接口中的 fileMd5、title、publictime 等；不把 agent、GZK、服务令牌或任意配置整个传给目标解析器。
5. 调用实际解析器，检查返回字典、成功码、非空有效 ExcelData、四核心字段及严格 JSON 可序列化性；零值有效，数值不强制转 float，不换算或四舍五入。
6. 单位缺失按规则使用 -；日期仍按实际源表和允许的本次 title 等字段获取，无法确定不补猜。具体业务单位换算继续留在现有后半链。
7. 校验成功后原样返回业务记录给 excel_data。不要拿外部本地验证保存的数据替代平台执行。

语法、依赖、下载、入口、返回契约或运行失败，必须通过平台确实识别的执行失败机制停止，不能仅正常返回 ExcelData=[] 让下游继续。独立字典用于避免命名污染，不等于安全沙箱。

## 6. 错误和变量写回的实际边界

agent.status 只表示外部代码交付状态。它在 4912 失败时仍可以保持 ready，因为“代码已交付”和“本次平台运行成功”是两件事。平台执行失败用 4912 的真实日志查看，不把外部 status 改成 failed 掩盖故障位置。

接入节点已经正常写回结果时，可以在 agent 中查看错误或告警。但节点如果抛异常、被杀或在完成前超时，平台未必提交本次输出；不能保证 agent.error_code 一定被写回。因此：

- 仍在处理中并触发重试的原任务身份，由服务端持久化，不能只依靠 agent.job_id。
- 失败原因和完整报告在服务端与平台失败日志中保存，即使变量未写回也可以查证。
- 若平台正常返回失败记录，则写完整单条 agent，清空代码字段；4912 必须先拒绝非 ready，不能继续执行。
- 是否正常返回失败记录还是抛异常，以实际平台停止和重试语义决定，不把“写回错误字段”和“保证节点失败”同时当成未经验证的能力。

这样不用修改后半链，也不需要为了保存失败字段而额外增加强制判断节点。

## 7. 哪些可以现在先配置，哪些仍待实现

按本文建立 agent 及 14 个 String 子字段，并设置两个节点输入输出映射后，复制 [完整节点代码](工作流节点代码/README.md)，填写接入脚本的服务地址及独立令牌，再按 [V3 本机指南](V3本机启动与联调指南.md) 启动服务。代码和接口已经实现；平台运行端到本机的真实可达性、异常重试行为及最终业务结果仍需用户测试确认。

后续用户已亲自基本完成上述平台配置。当前通信范围限定为本机全流程，见 [本机通信与全流程联调方案](本机通信与全流程联调方案.md)；公司服务器迁移留待未来必做工作，不作为当前前置条件。

后续实施先用受控返回核对父变量写回、单条数组、源码换行及 hash，再用受控延迟任务验证超过 120 秒后的同任务重试，最后做真实生成和用户平台验收。配置测试不能冒充真实模型生成。

核对顺序：

1. agent 位于模板根，14 个字段都在它的 children 下，全部 String。
2. 接入节点的返回键 agent 整体写回父变量，4912 的输入 agent 整体读取父变量。
3. PythonCode 是源码字符串；agent 单条数组不变成 JSON 字符串或一套列式字符串数组。
4. ready 时 hash、版本、代码三者一致；失败或 processing 不会执行。
5. 4912 的 ExcelData 仍写入原 excel_data，8300 及后续节点不变。
6. 超时重试始终关联同一个服务端任务，累计预算不重置。

本文仅交付变量与节点配置设计，没有新增实现代码、在线平台修改、模型调用或联调结论。
