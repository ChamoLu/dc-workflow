# 上街区 Excel 平台执行失败修复记录

记录日期：2026-10-26（工作区日期；登记时间为 UTC 2026-10-27 05:51:50）。用户已完成工作流配置。最初 HTML 缺 `markdownMD5` 的问题由用户确认是测试入口选择错误，现已解决，相关接入代码推测性改动已撤销。

## 本次原因

用户任务 `69d111f1-dc6c-4eb1-be80-a05c3013d766` 已正确经过 Excel 外部接入节点，随后在4912平台执行器的 `platform_result_validate` 阶段失败。执行器检出 `ExcelData[28].indicator_data` 不是数值。

复现结果表明，该任务命中了历史解析资产 `ee451d7c-e1ac-441d-86ef-eddc0de80645`，SHA256 `fe48754ab9ffa82733b52f8a46166afd1a7db728966cdba9db1ccc33979c46bb`。该代码沿用3月文件的固定行号。6月文件增加“城镇居民人均可支配收入（元）”，后续区块相对下移一行，导致：

- 下标28（第29条）读取6月 `主要经济指标!C27`，其值为表头“累计”。随后还误读了D27、B48、C66、D66等表头。
- 6月C20居民收入 `28716.29` 被套用了历史该行CPI的 `%` 单位。
- 旧代码复现得到62条，既存在表头字符串，也存在有效记录遗漏。

因此修复对象是该来源的业务解析资产。4912数值约束正确；三个接入脚本、两个平台执行器及453提示词均与撤销 `artifact_kind` 后的交付清单逐字节一致，工作流快照未改动。

## 已完成修复

正式代码位于 [上街区统计月报解析器](../code/excel/xlsx/上街区人民政府_政务公开_政府信息公开_法定主动公开内容_统计信息_统计月报.py)。代码按真实区块标题及“指标 / 累计 / 比去年同期±%”表头定位，不依赖固定数据行号。名称、单位与日期按原始源单元格及规则核对，保留真实括号业务口径；新增收入行按原文取“元”，CPI百分点说明保留数值及“个百分点”。复合产业结构比例不拆造为单值记录。

`main(params)`、`fileMd5` 下载方式和 `code/msg/ExcelData` 接口保持不变，生产记录仍只有四个核心字段。`_dc_validate` 另外提供逐条来源证据供审查；生产不读取GZK、规则工作簿或本地文件。

规则定位为 `rule/todo.xlsx` Sheet1第5行，路径代码 `2085711403154546690`；名称参考使用精确同名Sheet。两个实际工作簿全部Sheet、表题、表头、合并区域、单位、日期及有效数据区均已检查。单位取值逻辑、单位位置、截止日期位置补充在[修复设计](../runtime/diagnostics/v4-result-69d111f1/design.md)，没有改写规则工作簿。

使用 `ArtifactStore` 登记新版本，保证当前业务文件、所有者记录、数据库当前指针与不可变版本一致：

| 项目 | 当前值 |
|---|---|
| 新资产版本 | `e92ee28d-5b30-4a90-9568-d75dcf9736ca` |
| 新代码SHA256 | `7ea489dca4b54422f1d2be0406f8ba4e83f4137c3f7e6dc582fe39387492b39f` |
| 内部修复任务 | `ab152a6d-85f7-4178-a124-b6b76f7e6cef` |
| 质量状态 | `usable_with_warnings`：已人工核对两份实际源，未调用外部模型审查，未来期间仍须原始文件回归 |
| 旧任务及旧版本 | 保持原记录与原SHA256，可供追溯，不改写成新代码 |
| 登记证据 | [release.json](../runtime/diagnostics/v4-result-69d111f1/release.json) |

## 验证证据

| 实际输入 | 原始字节 | 下载接口mock | 现有4912执行器 | 输出 |
|---|---:|---|---|---:|
| 2026年3月 | 19067 | 通过 | 通过 | 67条 |
| 2026年6月 | 19282 | 通过 | 通过 | 70条 |

逐格回归独立核对所有有效数值单元格、零值、原始精度、四字段、严格JSON、表头排除、名称层级、括号单位及运行时 `title` 月末日期。结果与证据分别见 [March-validation.json](../runtime/diagnostics/v4-result-69d111f1/March-validation.json)、[June-validation.json](../runtime/diagnostics/v4-result-69d111f1/June-validation.json)、[regression.json](../runtime/diagnostics/v4-result-69d111f1/regression.json)。本地GZK匹配只作诊断，不能将其数量视为真实平台最终 `out_data` 数。

两份源文件的原始字节与来源校验值已作为[正式回归夹具](../tests/fixtures/shangjie/README.md)保留，新增4项语义回归测试。完整回归 **1003项通过，118.91秒，0失败/错误/跳过**，报告为 [v4-shangjie-full.xml](../runtime/diagnostics/v4-shangjie-full.xml)。Ruff检查通过，112个Python文件格式检查通过，范围包括现有源码、测试、V3/V4节点脚本和当前上街区业务代码。真实DC平台复测由用户完成，本地回归不能记作平台已通过。

## 复测方式

本次只更新登记资产，服务代码没有变化，无须为本次资产修复重启服务或替换节点脚本。在正确的生产测试入口，用原6月业务输入重新发起一次完整测试，确认返回的新 `artifact_version` 为 `e92ee28d-5b30-4a90-9568-d75dcf9736ca`，然后检查4912及后续筛选结果。

不要续查旧 `job_id=69d111f1-dc6c-4eb1-be80-a05c3013d766` 来判断修复是否生效；旧任务始终固定其旧版本。默认无 `workflow_run_id` 的900秒幂等窗口在登记时已过期。若现场额外绑定了 `workflow_run_id`，应由真正的新测试执行提供其新运行标识，不能通过伪造MD5或随机修改业务参数绕开幂等。

本次保留了原`.env`、服务令牌、三本规则工作簿、其他业务资产及旧不可变版本。29个保护项中仅当前上街区业务代码及其所有者记录按预期变化，其他27项一致；见[保护复核](../runtime/diagnostics/v4-result-69d111f1/protected-after.json)。登记新增了对应不可变版本、manifest和内部修复报告，不会覆盖历史证据。

最终复核见[final-verification.json](../runtime/diagnostics/v4-result-69d111f1/final-verification.json)。本任务临时测试目录及指定缓存共15个路径已核实不存在，清单见[cleanup.json](../runtime/diagnostics/v4-result-69d111f1/cleanup.json)；正式原始文件、候选代码、报告和历史证据保留。
