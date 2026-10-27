# Conda dc：Python 依赖准备

整理日期：2026-10-15。对应《外部智能体框架设计指导书》V0.1。

本文件与 requirements-bootstrap.txt 是开发前的安装建议，不是已经测试通过的依赖锁文件。本轮未安装任何库；用户在 dc 环境自行安装。首次开发时再检查解析结果、锁定精确版本并做最小真实运行验证。

## 安装

在能正常使用 Conda 的终端中执行：

```powershell
conda activate dc
python --version
python -c "import sys; print(sys.executable)"
python -m pip install -r "D:\EDBPythonCode\dc_workflow\agent\requirements-bootstrap.txt"
python -m pip check
```

确认解释器属于 dc。LangGraph 当前要求 Python >=3.10；初期优先考虑 Python 3.11/3.12，但不要仅因这份建议就重建或降级已建立的环境。其他版本先交给依赖解析及后续测试核对兼容性。[LangGraph 包元数据](https://pypi.org/project/langgraph/)。

## 首批依赖

| 分组 | 安装名 | 用途 |
|---|---|---|
| 编排 | langgraph | 生成、验证、修正流程 |
| 编排 | langgraph-checkpoint-sqlite | 本地持久化检查点，需单独安装的后端包 |
| 契约 | pydantic、pydantic-settings | 任务/结果结构、配置读取 |
| 配置 | PyYAML | profile 与参考资料绑定的 YAML 配置 |
| 数据库 | SQLAlchemy、alembic | 元数据访问与数据库迁移 |
| 下载 | httpx | 框架文件服务客户端 |
| 下载兼容 | requests | 兼容现有解析器采用的下载方式 |
| Excel | openpyxl | XLSX 原始工作簿读取 |
| Excel | xlrd | 原始 XLS bytes、ctype 等类型检查，不用于 XLSX |
| Markdown | markdown-it-py | 辅助结构检查，仍保留原文及转换异常 |
| 测试 | pytest、pytest-asyncio、pytest-cov | 单元/集成测试、异步测试、覆盖率 |
| 下载模拟 | respx | HTTPX 请求模拟 |
| 下载模拟 | requests-mock | requests 下载模拟，适配已有解析器 |
| 代码质量 | ruff | 静态检查与格式检查 |

间接依赖由 pip 解析，不手动罗列全部 langchain-core、aiosqlite 等传递依赖。不同包的版本号不能直接理解为相同兼容系列。

## 后续按需安装

| 触发条件 | 可能需要的包 |
|---|---|
| 确认具体模型接口 | 对应提供商的 SDK 或 LangChain 适配包，例如 langchain-openai、langchain-anthropic；按选定接法安装，不全部预装 |
| 开始实现工作流 HTTP 通信 | fastapi、uvicorn |
| 通过 Python SDK 控制 Docker | docker；它不包含 Docker Engine，后者需独立安装和验证 |
| 迁移 PostgreSQL | psycopg、langgraph-checkpoint-postgres |
| 实际需要 DataFrame 分析 | pandas；普通 XLSX/XLS 检查不依赖它 |
| 需要渲染原始 PDF 或图像证据 | pypdf、pdfplumber、Pillow 等，按真实输入选用，不预设已支持全部格式 |

暂不需要安装完整 langchain、deepagents、多个 Agent 框架、redis/celery、向量数据库、torch 或 transformers。后续出现实际需求再加入。

标准库 sqlite3、csv、json、decimal、logging、pathlib、hashlib、subprocess 不需要 pip 安装。

## 版本与安装结果

requirements-bootstrap.txt 约束主要架构依赖的大版本，不宣称是一组已联合实测的版本。pip 安装时解析可满足约束的依赖；pip check 只检查包依赖关系，不能证明项目行为正确。

安装成功后可记录当前包清单，便于后续核对：

```powershell
python -m pip freeze > "D:\EDBPythonCode\dc_workflow\agent\requirements-installed.txt"
```

该文件是当前 pip 环境快照，不能替代 Conda 环境导出，也不能自动作为跨平台可复现的正式锁文件。正式锁定在本地真实样本测试通过后完成。

包说明参考：[SQLite 检查点后端](https://pypi.org/project/langgraph-checkpoint-sqlite/)、[openpyxl](https://pypi.org/project/openpyxl/)、[xlrd](https://pypi.org/project/xlrd/)、[HTTPX](https://pypi.org/project/httpx/)。
