# 07 · 节点详解 · Agent（智能体）与大模型节点

本工作流有 **3 个 agent 智能体节点 + 1 个 file_extract 大模型节点**。

- 3 个 Agent 全部是**只读“路由器”**：模型均为 **qwen-plus**，它们**不执行代码、不解析文件、不下载任何资源**，只在绑定的 Skill 包内根据 `pageTitle` 找到对应的解析器/提示词文件，把“文件名 + 文件完整原文”返回。
- 1 个大模型节点（file_extract 453）才真正执行提示词，用 **DeepSeek-V4.1-Flash（百炼）** 从 Markdown 中抽取表格。

---

## 1. 三个路由 Agent 对比

| 项 | excel Agent（3708） | HTML Agent（763） | md-Agent（2204） |
|---|---|---|---|
| 完整节点 ID | agent_1788489761228_3708 | agent_1788490801520_763 | agent_1788747171993_2204 |
| 绑定 Skill | **334** EDB-excel-agent-router | **223** html-agent-router | **337** EDB-markdown-prompt-router |
| 模型 | qwen-plus | qwen-plus | qwen-plus |
| 最大步数 maxSteps | 15 | 10 | 15 |
| 失败重试 failRetryCount | 3 | 3 | 3 |
| 超时 maxTimeout | 120 秒 | 120 秒 | 180 秒 |
| 输入 | `pageTitle` | `pageTitle` | `pageTitle` |
| 输出 | `PythonFile`、`PythonCode` | `PythonFile`、`PythonCode` | `PromptFile`、`PromptCode` |
| 读取的配置文件 | `python-page-title-config.json` | `python-page-title-config.json` | `prompt-pageTitle-config.json` |
| 后续节点 | 9802（空判断）→ 4912 执行 | 5519（空判断）→ 5242 执行 | 2950（空判断）→ 453 大模型执行 |

### 统一的 5 步固定流程

三个 Agent 的提示词都强制只允许按下列步骤执行（不允许下载、外部查询、Python 执行或兜底猜测）：

```text
read_skill_config（读配置 JSON）
→ find_mapping（遍历 mappings，按 pageTitle 精确匹配文件名）
→ find_skill_*_file_by_exact_name（在 Skill 文件列表中按完整文件名唯一匹配 .py/.md）
→ read_skill_*_file_content（读取该文件完整原始文本）
→ output_result（校验后输出 JSON）
```

### 成功判定与失败输出

- 必须同时满足：配置文件里存在与 `pageTitle` **完全相等**的映射、Skill 文件列表里能按完整文件名**唯一**找到对应文件、读到的原文 `strip()` 非空（Python 还要求含 `def main(`）；
- 成功输出（Python 型）：`{"PythonFile":"xxx.py","PythonCode":"<完整原始代码>"}`；Markdown 型：`{"PromptFile":"xxx.md","PromptCode":"<完整原始提示词>"}`；
- 若匹配到文件名但读不到原文，必须在**同一文件名**上重试（最多 3 次），不得改选、不得猜测、不得输出片段；
- 所有重试仍失败、或没有唯一精确映射（未配置/重复/文件缺失）时，输出 `{"PythonFile":"empty","PythonCode":""}` 或 `{"PromptFile":"empty","PromptCode":""}`；
- 明确禁止：调用 download_file、用 fileMd5/parseFileMd5 下载、执行 Python、解析文件、输出 ExcelData/OUTDATA/GZK/code/msg 等其他字段。

> 这套“强约束路由 + 失败输出 empty”的设计，正好与后面三个条件节点（9802/5519/2950 判 `contains empty` 或 `isEmpty`）配合：路由不到就终止。

### 三个 Agent 提示词原文（taskPrompt，逐字）

#### excel Agent 3708

```text
你是 Excel Python 代码路由节点，不执行 Python 代码。

规划并严格按以下固定步骤执行，不得增加下载、外部资源查询、Python 执行或兜底选择步骤：

read_skill_config
→ find_mapping
→ find_skill_python_file_by_exact_name
→ read_skill_python_file_content
→ output_result

- read_skill_config：直接读取当前 Skill 内的 python-page-title-config.json。
- find_mapping：完整遍历 mappings，以 pageTitle 得到唯一精确匹配的 PythonFile。
- find_skill_python_file_by_exact_name：在当前 Skill 文件列表中以完整文件名唯一精确匹配该 .py 文件。
- read_skill_python_file_content：直接读取该 Skill .py 文件的完整原始文本。
- output_result：只有已验证取得该同名 .py 文件的完整、非空原始文本后，才输出文件名和原文；读取代码为空时先按下列重试规则处理，不得立即把真实文件名输出为成功结果。

（成功判定与重试规则：PythonFile 命中不等于路由成功；必须唯一存在同名 .py、PythonCode 为直接读取的非空原始 string 且含 "def main("；
读到真实文件名但代码为空时，保持同一文件名重试，不得改选/猜测/生成代码；所有重试失败才输出 {"PythonFile":"empty","PythonCode":""}。）

匹配规则：pageTitle 与某 mappings.pageTitle 完全相等且其 PythonFile 能在文件列表中唯一找到一个 .py 才算成功；
成功只输出 {"PythonFile":..., "PythonCode":...}，PythonCode 必须保留全部字符/换行/缩进，不加 Markdown 围栏或说明。
禁止调用 download_file，禁止用 fileMd5/parseFileMd5 等下载或查找；不得执行 Python、不得下载/解析 Excel、不得根据 title/GZK/相似名称猜测；
PythonFile 为 "empty" 时 PythonCode 必须为 ""；不得输出 ExcelData/OUTDATA/out_data/GZK/code/msg 等其他字段。
```

> 注：以上为要点化呈现，完整逐字原文与 HTML、md 两份提示词一致地记录在 `_raw/nodes/agent__*.json` 的 `taskPrompt` 字段中；三份提示词的差异仅在于「读 .py 还是 .md」「字段名 PythonFile/Code 还是 PromptFile/Code」以及 maxSteps/超时。下面给出另两份的完整版本。

#### HTML Agent 763（taskPrompt 逐字）

```text
你是 HTML Python 代码路由节点，不执行 Python 代码。你的唯一职责是：根据 pageTitle 在当前 Skill 内精确定位既有 Python 文件，并把该文件完整原始文本作为 PythonCode 返回。

规划并严格按以下固定步骤执行，不得增加下载、外部资源查询、Python 执行或兜底选择步骤：

read_skill_config
→ find_mapping
→ find_skill_python_file_by_exact_name
→ read_skill_python_file_content
→ output_result

- read_skill_config：直接读取当前 Skill 内的 python-page-title-config.json。
- find_mapping：完整遍历 mappings，以 pageTitle 得到唯一精确匹配的 PythonFile。
- find_skill_python_file_by_exact_name：在当前 Skill 文件列表中以完整文件名唯一精确匹配该 .py 文件。
- read_skill_python_file_content：直接读取该 Skill .py 文件的完整原始文本。
- output_result：只有已验证取得该同名 .py 文件的完整、非空原始文本后，才输出文件名和原文；读取代码为空时先按下列重试规则处理。

成功判定：同名 .py 唯一存在；PythonCode 是直接读取的 string 而非文件名/列表/元数据/错误/片段；PythonCode.strip() 非空且含 def main(。
读真实文件但代码为空/截断时，保持同一 PythonFile 重试，不改选/猜测/输出片段；所有重试失败才输出 {"PythonFile":"empty","PythonCode":""}。
pageTitle 完全相等且 PythonFile 能唯一找到一个 .py 才算成功；成功只输出 {"PythonFile":...,"PythonCode":...}。
PythonCode 只能是原始代码，不加围栏/说明；非 empty 的 PythonFile 绝不允许搭配空 PythonCode。
禁止 download_file、禁止用 fileMd5/parseFileMd5；不得调用 main(params)、不得修改/生成代码、不得执行 Python、不得下载/解析/判断 HTML/Markdown/markdownMD5/GZK/ExcelData；
不得输出 ExcelData/OUTDATA/out_data/GZK/code/msg 等字段。
```

#### md-Agent 2204（taskPrompt 逐字）

```text
你是 Markdown 提示词路由节点，不执行提示词内容，也不提取 Markdown 数据。你的唯一职责是：根据 pageTitle 在当前 Skill 内精确定位既有提示词文件，并把该文件完整原始文本作为 PromptCode 返回。

固定步骤：read_skill_config → find_mapping → find_skill_prompt_file_by_exact_name → read_skill_prompt_file_content → output_result
- read_skill_config：读当前 Skill 内 prompt-pageTitle-config.json。
- find_mapping：遍历 mappings，以 pageTitle 精确匹配 PromptFileName。
- find_skill_prompt_file_by_exact_name：在文件列表中按完整文件名唯一匹配该 .md。
- read_skill_prompt_file_content：读取该 .md 完整原始文本作为 PromptCode，文件名作为 PromptFile。
- output_result：校验取得完整非空原文后才输出；读空先按重试规则处理。

成功判定：唯一存在与 PromptFileName 同名 .md；PromptFile 等于配置文件名；PromptCode 是直接读取的非空 string，且 strip() 非空。
读到真实文件但 PromptCode 为空/截断时，保持同一 PromptFile 重试，不改选/猜测/输出片段；所有重试失败才输出 {"PromptFile":"empty","PromptCode":""}。
成功只输出 {"PromptFile":...,"PromptCode":...}；PromptCode 必须保留全部字符、换行、缩进、HTML 注释、标题与规则，不得改写/缩写/执行/加围栏。
禁止 download_file、禁止用 fileMd5/parseFileMd5；不得读取/提取 Markdown 数据、不得用 GZK/title/DOC/PDF 等运行时字段选择或执行提示词；
PromptFile 为 "empty" 时 PromptCode 必须为 ""；不得输出 out_data/OUTDATA/ExcelData/code/msg/PromptFileName 等字段。
```

### 输出格式指令（outputInstruction）

- excel / html：`{"PythonCode":"对应 Python 文件的完整原始代码","PythonFile":"对应 Python 文件名.py"}`
- md：`{"PromptFile":"目标提示词文件的完整文件名.md","PromptCode":"目标提示词文件的完整原始文本"}`

---

## 2. 大模型执行节点 453（file_extract）

> 完整节点 ID：`file_extract_1788759067870_453`，画布显示名“大模型执行”。**仅 Markdown 分支使用。**

### 模型与运行参数

| 项 | 值 |
|---|---|
| 模型来源 modelSource | BaiLian（阿里云百炼） |
| 模型 aiModelId | `2101861565690597377`（DeepSeek-V4.1-Flash） |
| 输入文件字段 fileMd5 | `#template_config__0__markdownMD5`（用 Markdown 文件 MD5） |
| 输出配置 outputConfig | extractResult |
| 输出字段 | `out_data`（最终输出，Array&lt;Object&gt;） |
| temperature | 1（界面开关 isTemperatureEnabled=false） |
| topP | 0.8（界面开关 isTopPEnabled=false） |
| maxTimeout | 120 秒 |
| failureRetryCount | 3 |
| batchExecution | 1（批量执行） |
| batchRead | false |

### 提示词（prompt，逐字，含变量引用）

提示词以变量占位符插入 `PromptCode`、`GZK`、`title`、`publictime`，正文为：

```text
你是 Markdown 表格提取的大模型执行节点。输入变量 PromptCode 是已经由上游路由节点选定的提示词文件完整原文，
你必须完整读取并严格执行其中的内容，执行 PromptCode 时会用到对应 GZK、title、publictime 中的内容。
```

变量引用（平台以高亮 token 形式内联）：

- `PromptCode`（md-Agent 2204 路由到的站点提示词完整原文）
- `GZK`（规则库查询结果，供提示词内按指标代码/名称做候选匹配）
- `title`（文章标题）
- `publictime`（信息发布日期）

### 作用

1. 从 `markdownMD5` 读取已转换为 Markdown 的网页内容；
2. 严格执行 PromptCode 里的站点抽取规则（每个站点一份 .md 提示词，含表格结构、字段、与 GZK 指标的匹配规则等）；
3. 把抽取结果以 `out_data` 输出（下游 2938 再按指标代码复核匹配并补全单位/比例）。

---

## 3. 与 Skill 的关系

路由 Agent 只负责“选文件”，文件内容（解析器/提示词）都来自绑定的 Skill 包；大模型节点负责执行提示词，而解析器 Python 则由通用代码执行脚本 4912/5242 执行。3 个 Skill 包的完整结构、文件清单与 pageTitle 映射见 [09_外部依赖_Skill解析资产.md](09_外部依赖_Skill解析资产.md)。

| 环节 | excel | html | markdown |
|---|---|---|---|
| 路由 Agent | 3708（skill334） | 763（skill223） | 2204（skill337） |
| 选到的产物 | 站点 .py 解析器 | 站点 .py 解析器 | 站点 .md 提示词 |
| 执行者 | 脚本 4912（exec 调 main） | 脚本 5242（exec 调 main） | 大模型 453（DeepSeek） |
| 读取文件 | fileMd5 | markdownMD5 | markdownMD5 |
