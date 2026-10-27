# HTTP 422 联调排查

日期：2026-10-20。对应用户真实任务 fileMd5：C02BBD1DEB3A82CCA96B66D873969E18，资料位于 agent/example。当前结论：平台已经请求到本机 API，但任务提交参数校验失败；旧版没有记录具体失败字段，不能从已有访问日志还原唯一根因。

后续状态：用户已确认 V3 整体流程测试通过。本文件保留当时的排查过程；未另行确认的 422 具体字段归因不作猜测。当前整体说明见 [V3 技术说明](V3整体流程与技术实现说明.md)。

## 1. 已确定的事实

用户本机 Uvicorn 收到平台的 `POST /v3/excel/jobs` 并返回 422，平台脚本收到相同状态码。当前请求地址可以访问服务，应继续使用此次测试的 API_BASE_URL。若未改过交付脚本的默认值，就是 `http://service-3.example.invalid`。

访问日志中的 service-4.example.invalid、service-5.example.invalid 是平台请求来源 IP，不是本机 API 地址；0.0.0.0 是服务监听设置，不是工作流调用地址。无需在平台运行 PowerShell，可继续用同一真实数据任务联调。

已用实际 `example/result.json` 的五个业务字段与 `example/GZKJG.json` 的 21 条规则，通过实际接入脚本的参数整理和隔离 FastAPI TestClient 提交，得到 **HTTP 202**。样例 fileType 为 xlsx，指标代码均为整数，现有接口接受并将代码规范为字符串。fileMd5.json 本身只有文件 ID，不含 fileType。

因此原始样例合法；但它不能证明平台节点实际提交的六个字段与本地样例完全一致。当前旧日志没有请求参数或字段级错误，不能直接断定是哪一项映射错误。调查证据见 runtime/diagnostics/v3-422-investigation.json；它没有调用正在运行的用户服务或模型。

## 2. 最需要核对的映射

| 接入节点参数 | 应绑定的变量 | 本次样例 |
|---|---|---|
| fileMd5 | fileMd5 | C02BBD1DEB3A82CCA96B66D873969E18 |
| fileType | fileType | xlsx |
| pageTitle | pageTitle | 2229 提供的本次来源路径 |
| title | title | 本次实际标题 |
| publictime | publictime | 本次实际发布日期，允许空字符串 |
| GZKJG | GZK.GZKJG | 21 条对象数组或该数组的 JSON 字符串 |

旧接入脚本对 fileType 只检查字符串，缺映射时会发送空字符串，服务随后因只支持 xls/xlsx/csv 而返回 422。这是已复现的候选原因，不代表本次真实请求已确诊。另一个候选是规则代码实际变成浮点数等不支持的类型；本地样例整数代码没有此问题，不预先放宽或修改规则。

## 3. 已完成的诊断修正

- 接入脚本提前校验并规范 fileType，缺失或不支持时直接报字段及允许值，不猜成 xlsx。
- 服务端区分 request_schema 与 task_contract 两个校验阶段，返回字段位置、错误类别、输入类型及 diagnostic_id。
- 同一份安全诊断保存到 runtime/diagnostics/http-input-errors/诊断编号.json，终端显示阶段、字段和编号。
- 接入脚本显示这些白名单诊断字段，不复制完整响应、原始入参或令牌；遇到旧服务的无字段错误时提示更新并重启服务。

例如下面的报错仅用于说明格式，不是本次真实请求的已确认根因：

```text
DC_AGENT_HTTP_ERROR: HTTP 422；请求已到服务端，但入参校验失败;
phase=request_schema; fileType=literal_error(input_type=str);
diagnostic_id=...
```

若实际显示 `GZKJG[0].rule_indicator_code=value_error(input_type=float)`，则再核对该行规则代码的真实类型，不修改正确的源文件或业务解析器。

## 4. 用户更新与复测

1. 本机运行服务的 PowerShell 窗口按 Ctrl+C 停止，再执行 `python run_service.py serve`。当前服务没有自动重载，必须重启才会使用新的错误处理。
2. 重新复制 [外部框架接入.py](工作流节点代码/外部框架接入.py) 到平台，保留此次能连通的 API_BASE_URL，重新填入原 API_TOKEN。不要把占位令牌留在平台。
3. 按上表核对输入映射，然后仍用同一真实任务测试。若输入正确，POST 将返回 202；若仍失败，平台错误或本机终端将指出具体字段。将新的字段级错误提供给开发侧继续定位。

本轮无需重新配置 agent 变量或修改动态执行器 4912。无需提供平台 PowerShell，也无需更换已能访问本机的网络地址。

## 5. 验证边界

修改后 HTTP/节点/真实回环回归 117 项通过，新增安全字段诊断测试 16 项通过，Ruff 与 80 文件格式检查通过。证据分别在 runtime/diagnostics/v3-422-regression.xml、v3-422-diagnostics-tests.xml。测试不请求真实公司网络或模型，不代表用户真实任务的全部参数已经通过。原模型 .env、规则、样例、解析器和服务令牌保留，正在运行的用户服务由用户自行重启。
