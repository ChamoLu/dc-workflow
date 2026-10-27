# Tools 扩展预留

本目录暂不实现工具注册、动态脚本加载或模型工具调用。已在 `dc_agent.ports.ToolRegistry` 预留 `describe(kind)` 和 `invoke(tool_id, args, context)` 接口。

目前应用直接调用已有文件获取、事实检查、模型预检和候选验证服务。未来有明确工具需求时，在此添加工具说明、登记清单及必要资源，实现经白名单控制的调用适配；不自动扫描目录并执行脚本。
