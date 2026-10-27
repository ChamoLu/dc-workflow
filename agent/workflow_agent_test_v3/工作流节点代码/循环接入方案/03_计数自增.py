# 采集数据源：本次工作流的 pageTitle；通用计数节点，不解析业务数据。
# 数据来源编码、数据源路径代码、路径、目标表：由实际交付解析器记录。
# 本节点只在每轮接入正常返回后执行；状态判断及次数上限直接配置在条件节点。


def main(params):
    count = params.get("count") if isinstance(params, dict) else None
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("count必须为非负整型，请检查初始化及计数节点输入映射")
    return {"count": count + 1}
