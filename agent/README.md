# DC 外部解析资产智能体 V0.4.0

本工程在本地查找、生成、验证和保存解析代码/提示词，正式业务执行继续留在公司工作流平台。本地 CLI 为 `python run_agent.py`，工作流 HTTP 服务入口为 `python run_service.py`。

2026-10-26生产联调：用户已完成V4配置。上街区6月Excel在4912失败的原因是历史业务解析器按3月固定行号读到了6月表头，已按真实表头定位修复并登记新资产，原3月/当前6月文件分别67/70条通过本地执行器回归；平台复测待用户完成。[故障原因、代码、版本与复测说明](workflow_agent_v4/上街区Excel平台执行失败修复记录.md)。

2026-10-26 V4：已交付Excel/CSV、HTML和Markdown提示词三条公开HTTP链路、三个独立接入脚本、4912/5242执行器及453任务提示词。平台配置由用户亲自完成，按[V4生产工作流节点配置手册](workflow_agent_v4/V4生产工作流节点配置手册.md)逐项绑定。删除原9802/5519/2950，不新增count回环；PLPL是indicator_release_frequency的变量别名，原生产后半链不因别名改写。工程验证及待验收边界见[V4实施与验证记录](workflow_agent_v4/V4实施与验证记录.md)。

V4使用POST `/v4/excel/jobs`、`/v4/html/jobs`、`/v4/markdown/jobs`和通用GET `/v4/jobs/{job_id}`；HTML/Markdown输入markdownMD5，Python统一输出ExcelData。Markdown提示词仍由平台453执行。本机服务重启后加载新接口，原模型/服务配置与既有资产沿用，V3接口保持兼容。下文V3节点配置为历史复现说明，当前生产接入以V4手册为准。

最新字段约定：按用户要求彻底撤销`artifact_kind`，HTTP共16个String字段，平台agent丢弃report_ref后为15个String字段。以`pageTitle + fileType`定位唯一资产，保留版本/hash和当前来源检查；使用最新五份Python节点代码并重启服务，已配置平台旧字段时同步删除。

阅读源码可从 [源码学习计划与逐文件导读](workflow_agent_test_v3/源码学习计划与逐文件导读.md) 开始：先认识整体调用关系，再按8次学习路线阅读各目录与文件。

2026-10-20阶段状态：**用户已确认 V3 本机框架生成代码、返回 DC 测试工作流并动态执行输出的整体流程测试通过，Demo 目标达成。** 历史实现介绍见 [V3 整体流程与技术实现说明](workflow_agent_test_v3/V3整体流程与技术实现说明.md)；历史操作见 [本机启动指南](workflow_agent_test_v3/V3本机启动与联调指南.md)、[节点代码](workflow_agent_test_v3/工作流节点代码/README.md)，证据见 [实施记录](workflow_agent_test_v3/V3实施与验证记录.md)。第二版交接基线为 0.2.2，V3验收基线为0.3.0及后续诊断补丁。

V1的上街区XLSX案例已完成真实生成、本地运行、资产复用及用户确认的平台测试；用户随后反馈仍有漏提取，因此历史67条结果不代表全量覆盖。V2已实现目录与命名、程序定位、模型内容预检；0.2.1新增可持续维护的任务表/参考Sheet程序读取，并按用户要求将完整性检查改为质量提示。Skills/Tools仍只预留目录和接口。

V2 历史验证见[实施与验证记录](docs/V2实施与验证记录.md)。V3 本机 API 通信及用户真实 DC 测试闭环均已通过；服务器迁移及更多真实 HTML/Markdown 验收仍为后续工作。

0.2.2增加可用候选保留、择优回退、有限完整性改进和完整质量报告。后续修复失败不再直接丢弃较早的可用代码；尚有模型语义问题的版本明确标记usable_with_warnings。新返回字段、报告位置及恢复方法见[候选保留与质量报告](docs/V2候选保留与质量报告.md)。

## 快速运行

V4 本机服务启动（使用用户已安装 FastAPI/Uvicorn 的 dc 环境，先在原窗口停止旧服务）：

```powershell
conda activate dc
cd D:\EDBPythonCode\dc_workflow\agent
python run_service.py check-config
python run_service.py serve
```

独立服务配置位于 `runtime/service/service.env`，现有令牌继续使用。将其中 `DC_API_TOKEN` 和实际已联通基址填入 [三个V4接入脚本](workflow_agent_v4/工作流节点代码/README.md)，按[V4逐节点手册](workflow_agent_v4/V4生产工作流节点配置手册.md)配置4912、5242和453。原 `.env` 的模型设置继续沿用；后台总预算360秒、生成预算300秒，每次接入主动等待115秒，平台接入节点超时120秒并配置异常重试3次。V3回环方案继续暂停。

以下是保留的本地 CLI 使用方式：

```powershell
conda activate dc
cd D:\EDBPythonCode\dc_workflow\agent
python run_agent.py check-env
python run_agent.py inspect --example example --source-path runtime/source_cache/example.xlsx
python run_agent.py run --example example --source-path runtime/source_cache/example.xlsx
```

无参数启动只显示帮助，不加载模型配置或请求网络。`inspect`读取全部原始内容，不调用模型；`run`先按精确来源查资产，命中则返回已有版本。

只验证模型内容判断、不生成资产时：

```powershell
python run_agent.py preflight --example example --source-path runtime/source_cache/example.xlsx
```

`preflight`会把本次真实文件的检查内容发送到已配置模型。`check-model`则只发送固定JSON小请求，不读取业务文件；它不能证明长生成一定成功。

任意本地任务沿用原有任务JSON适配：

```powershell
python run_agent.py run --task D:\path\task.json
python run_agent.py run --task-id TASK_ID --retry --rebuild --resume-candidate
```

恢复候选时重新核对真实源SHA256、预检和基本校验；成功模型操作可在相同任务、相同上下文下复用。任务表、选中参考内容或Skill/profile变化时重新形成设计，再验证旧候选。已有资产时需`--rebuild`才能进入生成/恢复；机械失败候选不覆盖资产，仍有模型语义意见的基本可用Python会明确标记质量状态。

## 任务表与生成参考

新生成/显式重建时，程序读取`rule/todo.xlsx`的当前文件快照，以原始pageTitle精确匹配E列。C列表头必须为DataSourcePathCode，E列表头必须为pageTitle；任务Sheet名称、数据行数和路径代码均不写死，多个符合表头约定的Sheet共同参加匹配。

| 匹配结果 | 行为 |
|---|---|
| 0行 | 返回精确错误码`todoFile_no_pageTitle`，不下载源文件或调用模型 |
| 多于1行 | 返回精确错误码`pageTitle_redundancy`，包括不同fileType的同标题任务 |
| 1行，有对应参考Sheet | 携带当前任务行及选中Sheet继续 |
| 1行，无对应参考Sheet | 仅携带任务信息继续，不增加缺失标志或提示 |

Excel/CSV和PDF、图片、DOC等Markdown链路按C列代码从`IndicatorName_Excel_Md.xlsx`选Sheet；HTML/HTM/SHTML从`rule_html.xlsx`选Sheet。使用Sheet名的精确文本匹配；路径代码应维护为文本，保留前导零和长编码。两个参考格式可能无表头，第1行也会读取。只传选中Sheet，不把整本工作簿交给模型。

程序每次实际生成读取新快照并计算SHA256；维护任务和参考资料无需改代码或重启进程。文件无法读取、损坏或todo表头不符单独报告REFERENCE_*错误，与没有匹配任务/Sheet区分。缓存命中和已有请求结果的幂等重放保持原行为，不因资料更新自动重建；修改资料后需新任务加`--rebuild`，或对原任务显式`--retry --rebuild`。

task_info及可选references贯穿设计、生成、修复和审查；与真实文件冲突时核验源位置，以源事实为准，在design/review.reference_conflicts记录依据。名称和正则参考不作为生产Python输出白名单。每个新资产保存generation_context.json及其指纹，便于回查实际使用的任务行和参考版本。

维护方法与完整契约见[任务参考与宽松校验说明](docs/V2任务参考与宽松校验.md)。

## 资产目录和程序查找

| fileType | 真实正文位置 | 输入来源 |
|---|---|---|
| xls / xlsx / csv | `code/excel/<fileType>/<pageTitle替换后>.py` | fileMd5 |
| html / htm / shtml | `code/html/<fileType>/<pageTitle替换后>.py` | markdownMD5对应Markdown |
| md / markdown / pdf / png / jpg / jpeg / doc / docx | `code/md/<fileType>/<pageTitle替换后>.md` | markdownMD5对应Markdown |

文件名只将pageTitle中的反斜杠替换为下划线。具体类型子目录使同一pageTitle的XLS/XLSX等不同资产分开保存；同类型下的名称碰撞、非法名称明确报错。TXT/JSON和未登记类型继续不支持。

`ArtifactStore.lookup()`执行数据库精确查询、业务路径计算、身份/版本及SHA256检查，没有模型搜文件或相似标题选择。磁盘上存在但未登记的同名文件不能直接使用。生产契约继续由原候选验证关卡检查。

各类型目录下，`.staging/`保存候选，`.versions/`保存不可变历史正文；设计、验证报告与模型操作记录继续保存在runtime。旧V1已登记资产在首次复用时自动复制到code，保持原版本ID与正文hash；原文件和历史manifest不改写、不删除，也不重新调用模型。

返回资产带有`layout_version=2`和`content_root=code`；`content_path`相对于配置的code根，`validation_path`仍相对于runtime。历史任务在当前资产已经更新后重放，会指向其不可变版本。不要再把所有content_path一律拼到runtime。

## 生成前模型内容校验

应用服务在缓存未命中或明确重建时，先完成程序任务匹配与可选参考读取，再下载一次真实bytes、完整读取全部Sheet/Markdown，调用模型做粗略判断，随后加载Skill并进入设计、生成。

- `has_data`：允许生成。
- `uncertain`：按用户要求允许生成。
- `no_data`：终止该任务，返回`status=failed`、精确`error.code=FilenoData`、`stage=preflight`、原因和报告引用。

零、负数、数值字符串有效；缺单位/日期、GZK未覆盖不能直接当文件无数据。模型超时、截断、schema错误以及下载/格式错误分别保留，不伪装成FilenoData。预检只判断源文件，不读取GZK、任务表或参考内容，不把参考中的指标名当成输入有数据的依据。

缓存命中不下载当前源、不调用任何模型，返回`preflight.status=not_run`；此时ready说明资产可服务。原任务重试可按源SHA256、模型和判定规范指纹复用成功的内容判断，不根据pageTitle单独缓存有无数据。

## 基本正确性与完整性

代码实际执行、非空有效输出、四字段接口、源数值/单位/日期证据和错误语义仍是基本关卡。Markdown提示词保留17章节和GZK精确匹配五字段接口，平台提取执行仍为not_run。

漏提取和相较旧候选少数据格位作为warning，可以在小预算内尝试改进，未改好仍发布保留的可用候选；设计/审查少覆盖section本身不反复触发检查。Review.rejection_kind=coverage表示纯遗漏，correctness表示错误输出或混合问题，原始意见均保存。实际运行、四字段、源数值和逐条真实来源证据失败的候选不能发布；只有模型语义意见未解决时，允许基本可用Python以usable_with_warnings交付，不宣称语义全部通过。ready不表示数据已经提全。

默认总修复4轮，其中纯完整性改进最多2轮，连续2轮无进展提前停止；总时限仍有效。diagnostics直接返回完整报告的绝对路径：runtime/tasks/任务ID/quality_report.md和quality_report.json；问题总账为runtime/diagnostics/quality_issues.jsonl。缓存命中沿用原资产报告，不重复记账。

审查可以引用已选参考的`file:sheet!A1:K36`等地址/范围，程序核验文件和Sheet身份、范围边界及实际提供的内容，不误判为虚构源位置。业务记录的evidence仍只能引用真实输入源，不能以参考库值替代源数据。

## 当前工程结构

```text
agent/
  run_agent.py                  本地入口
  run_service.py                V3 HTTP 服务启动/配置入口
  pyproject.toml / .env.example
  src/dc_agent/
    config.py                   工程根及运行配置
    bootstrap.py                CLI 与独立 worker 的公共应用装配
    cli.py                      兼容入口，实际实现位于entrypoints
    domain/                     任务、候选、报告、错误与结果契约
    ports/                      现有边界及预留Skills/Tools接口
    application/                普通应用服务、独立模型内容预检
    integration/                V3 持久队列、worker、不可变代码交付契约
    assets/                     类型目录、业务命名、迁移和发布
    orchestration/              LangGraph生成/验证/审查/修正
    generation/                 结构化模型操作与现有外部Skill适配
    inspection/                 原始bytes和单元格/Markdown事实读取
    references/                 todo任务与可选名称/正则Sheet程序读取
    validation/                 基本正确性、覆盖建议和受限修复检查
    infrastructure/
      model/                    Chat/Responses传输、终态与诊断
      source.py                 文件下载和显式参考资料
      storage.py                SQLite任务、事件、租约和资产指针
      execution.py / _runner.py 独立候选运行
      locking.py / files.py     跨进程发布协调和文件记录
    entrypoints/                本地CLI、HTTP API、服务启动器
    profiles/                   source_rules_v2任务/参考/宽松完整性适配
  skills/ / tools/              README与扩展边界；新注册器尚未实现
  code/                        分类后的当前文件、候选和版本
  rule/                        持续维护的todo和两个参考工作簿
  docs/                        V2实施方案和验证记录
  workflow_agent_test_v3/       V3设计、启动、两个平台脚本和验证记录
  tests/ / example/
  runtime/                     数据库、源缓存、任务与正式证据
```

现有Skill继续从`DC_SKILL_ROOT`只读加载，source_rules_v2替代原source_gzk_v1的“名称/正则工作簿暂不提供”前提，不修改原始Skill。Skills/Tools目录不意味着已接入新注册器或模型原生工具调用。Python解析器仍保持`main(params) → code/msg/ExcelData`；提示词仍保持GZK五字段out_data契约，目标提取模型不在外部执行。

## 配置与执行环境

使用已有Conda dc环境及依赖，不需要重新安装。模型仍由用户现有`.env`配置，V2没有改动该文件。

新增可选配置：

```dotenv
DC_CODE_DIR=code
DC_RULE_DIR=rule
DC_TODO_FILENAME=todo.xlsx
DC_INDICATOR_REFERENCE_FILENAME=IndicatorName_Excel_Md.xlsx
DC_HTML_REFERENCE_FILENAME=rule_html.xlsx
DC_MAX_RULE_BYTES=100000000
DC_MODEL_PREFLIGHT_MAX_TOKENS=800
DC_PREFLIGHT_TIMEOUT=90
```

不设置DC_CODE_DIR时，code默认是runtime的同级目录；相对显式路径按工程根解析。一个code目录绑定一个runtime数据库；不同数据库不要共用同一code目录。预检输出预算仍与全局DC_MODEL_MAX_TOKENS取较小值，并受任务总时间限制。

默认使用dc解释器的独立本地子进程，Docker保留可选。候选不继承模型凭据；本地子进程不等于完整沙箱。现有SSE终态、有限网络重试、模型操作日志、覆盖建议、正则修复和基本语义关卡继续生效。

## 验证与后续边界

0.2.2全量254项通过（64.19秒）、Ruff及60文件格式检查通过。真实案例旧回复回放保留candidate-0的67条并标记未解决语义问题，零新增模型调用。0.2.1的8条任务路由、真实模型新生成及平台历史证据分别保留，详见[验证记录](docs/V2实施与验证记录.md)。

V3 已提供本机 HTTP 通信与后台 worker，任务断线可查询，服务重启时明确标记进行中任务中断；平台真实 Demo 全流程已由用户确认测试通过。服务器部署、任意步骤自动恢复和手动版本切换 CLI 未纳入本轮。发布中断恢复、不可变历史版本和旧 V1 证据保留。未来 Skills/Tools、更多来源及 HTML/Markdown 真实验收另行推进。

## 首次真实模型运行的超时排查

2026-10-15任务4240b2fe-88a9-4c9d-b457-25a02af78733的design已生成成功，约66.9秒后进入generate_candidate；该阶段约180.5秒后失败。原配置90秒、一次重试，与两次90秒等待高度吻合。旧代码丢失具体异常类别，因此不能追溯证明必为ReadTimeout，但不能据旧提示认定接口完全不通。

已将该环境原90秒等待调整为240秒，并保留用户模型、地址、密钥和max_tokens设置。用户当时max_tokens=120000，这只是输出上限，不代表一定生成这么多；对当前资产生成可先考虑12000—16000，并根据截断情况再调整，不能单靠放大上限保证完整输出。

该次仅调整读取超时，未重新调用真实模型。随后用户重试收到HTTP 524，进一步排查结果如下。

### 后续HTTP 524与模型流式修复

用户再次运行时收到HTTP 524，已确认单纯增大本地等待不能改变该结果。检查发现旧代码的HTTPX client.stream只分段读取HTTP响应，请求体没有设置模型API的stream参数，实际上仍可能等待模型完整输出。

现已实现真正的模型SSE请求、跨字节/行边界拼接、心跳处理、忽略非最终推理字段、结束标记校验、总时间与大小限制。提供商若缓冲流或首包本身太慢，仍可能出现524；客户端不能保证绕过提供商的所有网关限制。

成功及失败调用写入model_journal中的 `*.metrics.json`，仅记录操作、尝试次数、HTTP状态、是否实际收到SSE、首字节时间、响应大小、总耗时和数值用量，不保存密钥或隐藏推理。

后续增加了requested_model、requested_max_tokens、实际finish_reason、DONE、事件数、正文bytes、有限格式的提供商错误码及逐次attempts。旧记录没有这些字段时不能倒推当时的结束原因。

真实小请求已验证HTTP200 + SSE，首字节24.656秒、总用量89token；没有发送源Excel或GZK。原任务后续三次生成均HTTP200并完整接收SSE，分别181.063、179.172、158.657秒，未再出现524。

这轮候选因要求不存在的fileServiceUrl参数而未通过运行验证。其进程在下载契约/具体错误反馈补丁前启动，最终VALIDATION_FAILED；未发布资产。补丁后另作一次关闭自动修正的真实验证，详见 [tests/验证记录.md](tests/验证记录.md)。设计缓存可以复用，新生成契约改变了生成缓存键，旧错误候选不会按相同缓存键直接复用。

当时补丁后真实生成147.563秒完整返回，解析得到68条记录；下载参数问题已解决。两条源值“0.02：37.56：62.42”和“提高1.2个百分点”以整段文本进入数值字段，运行校验拦截，当时未发布资产、状态为review_required/VALIDATION_FAILED；进一步补齐数值契约和具体源ref诊断后，该轮仅做本地回归。以上是历史排查，后续本地闭环及平台测试通过结论见文档开头和验证记录。

协议参考：[Chat Completions的stream参数](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[Cloudflare 524语义](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/)。状态码本身不能证明具体提供商内部的排队或模型负载原因。

### 后续短响应MODEL_OUTPUT_INCOMPLETE排查

任务30bdab36-fb66-4a7c-a53b-d8cbd581c69a在design阶段6.172秒结束，仅报告9个输出token、1043响应bytes。此前统一错误没有记录具体finish_reason，因此不能追溯确定是length、其他结束原因还是缺少标记，也不能将其认定为600秒超时或必然是token上限不足。

当前配置的最小JSON请求实测stop/DONE正常；同一任务的design重新检查55.532秒、1881输出token、stop/DONE正常且通过schema/section覆盖校验，成功设计已写入该任务日志。后续真实候选结果见 [tests/验证记录.md](tests/验证记录.md)。

继续原任务使用 `python run_agent.py run --task-id 30bdab36-fb66-4a7c-a53b-d8cbd581c69a --retry`；直接再次传--example会创建新任务，未发布成功资产时不会跨任务复用操作日志。

如果已有候选、只需继续验证与修正，可以运行：

```powershell
python run_agent.py run --task-id 30bdab36-fb66-4a7c-a53b-d8cbd581c69a --retry --resume-candidate
```

此选项选取该任务编号最大的candidate-N.json及已保存设计，重新读取真实源文件、核对原始bytes的SHA256，再执行当前机械校验和语义审查。文件变化则报RESUME_SOURCE_CHANGED，应不带该选项重新设计；没有保存候选则报RESUME_UNAVAILABLE。不会复用旧批准结论或绕过验证；修正额度按本次运行计算，新候选从下一编号保存，旧候选与模型成功日志保留。它只支持按task-id重试，正常运行无需添加。

同一源文件的已验证数据格位会记录在coverage_baseline.json中。0.2.1起，修正时消失的格位仅提供质量提示，可以在excluded_source_refs解释，也允许已有有效输出直接交付，不再要求逐项解释后才发布。该对照不把历史输出当作最终正确答案，也不证明首次生成完整。上一轮语义问题清单会在机械检查失败后继续保留；操作日志指纹包含指令、上下文及schema，避免规范或参考更新后复用旧回复。

2026-10-16补充：直接执行`python run_agent.py`会显示帮助和命令示例，退出码为0，不加载模型配置或自动发起请求。它仍是命令行工具，执行任务需要显式传入run等子命令。

当候选因runtime:title日期检查失败时，框架会尝试一次受限的本地正则转义修复。只处理Python AST中re调用的已知年月/正向百分点语法，且原始标题或源格位必须证明“原模式不匹配、消除一层重复转义后匹配”。不会全局替换反斜杠或修改任意路径、字符串和未知正则。修复提案必须通过真实main/追踪入口执行、全部机械检查，并保留修复前所有记录的名称/数值/单位；日期可按运行时来源修正，允许恢复之前漏掉的有效记录。采用后保存新的candidate-N.json与local-repair-N.json，原候选及报告保留，仍必须经过语义审查才可发布。不能证明有效则沿用正常模型修正流程，不把样例日期写死。

### APINebula协议适配与新的524证据

30bd任务重新生成时，即使stream=true，Chat Completions仍在125.766秒未收到任何SSE数据后返回524。因此此前几次流式成功只能证明当时请求完成，不能宣称外部网关超时已被彻底修复。

用户补充使用APINebula中转。其[快速开始](https://docs.apinebula.ai/docs/quickstart/)给出通用Chat请求，而[Codex接入说明](https://docs.apinebula.ai/docs/cli/codex/)明确配置Responses；[令牌分组说明](https://docs.apinebula.ai/docs/model-groups/token-groups/)要求核对模型/分组/接口类型。不能据这些文档断言本次Key的后台路由细节，需实测。

新增独立Responses接收器，保留Chat路径。当前Key和模型的真实Responses小请求通过：2.781秒收到首个事件、48.328秒response.completed、29输出token，JSON有效。仅据实测将本地.env新增/设置为API_STYLE=responses、REASONING_EFFORT=low及阶段预算4000/16000/4000；模型、地址、密钥及原全局上限保留。框架通用默认值仍为Chat，模板不强绑定APINebula。

Responses支持completed/incomplete/failed、拒绝/工具、终态一致性、非流式及usage映射；缺少终态或实际断流不发布半截结果。协议以[官方Responses事件说明](https://developers.openai.com/api/reference/resources/responses/streaming-events)为依据。原任务生成的实际结果见测试验证记录。

原长生成切Responses后2.281秒收到事件，但127.312秒RemoteProtocolError中断，仅两个事件、没有正文或completed。协议切换不能保证中转站的长请求完成。随后将模型回复中的逐条证据清单移为本地动态生成，缩小回复体积，保持同一模型和严格验证；该方案实跑情况以验证记录为准。

首次runtime证据实跑在123.313秒完成，3061输出token，代码7536字符，本地生产/验证两次执行一致并生成67条记录。被拦截的是D20整段描述性值；另查到日期正则双重转义导致空日期。已补充正向百分点窄规则、指定title的日期一致性检查，区分累计等业务口径与实际年月标签；这些规范更新改变profile指纹，最后闭环会重新设计，旧设计日志保留。
