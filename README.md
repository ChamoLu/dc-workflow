# dc-workflow

DataCopilot 工作流、外部资产生成 Agent、平台接入节点及相关说明资料。

本仓库是原项目的脱敏发布副本。运行前请配置自己的模型服务、文件下载服务和 Agent 接入地址；`*.example.invalid` 地址仅作占位，无法连接真实服务。

| 路径 | 内容 |
| --- | --- |
| `agent/` | Agent 源码、测试、生成资产、规则资料及 V3/V4 接入节点 |
| `workflow/` | 工作流结构、导出数据、节点脚本、规则资产和说明文档 |
| `excel_to_json.py` | Excel 转 JSON 辅助工具 |
| `PROJECT_MEMORY.md`、`PROJECT_STATUS_AND_GOALS.md` | 项目背景、约定和进展记录 |

## 本地配置

进入 `agent/`，参照 [Agent 使用说明](agent/README.md) 安装依赖，然后复制配置模板：

```powershell
Copy-Item .env.example .env
```

在本地 `.env` 填写 `DC_MODEL_BASE_URL`、`DC_MODEL_NAME`、`DC_MODEL_API_KEY`、`DC_DOWNLOAD_BASE_URL`，并根据环境设置 `DC_SKILL_ROOT`。HTTP 服务配置参见 `agent/service.env.example`；可用 `python run_service.py init-config` 在运行目录生成独立服务令牌。

平台接入脚本中的 `API_BASE_URL`、`API_TOKEN` 需要在部署环境填写，真实配置不应写回版本库。不同 `service-N.example.invalid` 代表原项目中的不同内部服务地址；端口与主机已一起隐藏，部署时填写完整服务地址。

## 发布与凭据保护

原始 `.env`、运行目录、任务日志、数据库、缓存及原始界面截图没有纳入本仓库。内部主机地址和个人用户目录已替换，模型账户的调用量与计费明细已移除。源码、JSON 和工作簿 XML 均完成发布前检查。

Git 忽略规则只负责阻止未跟踪文件被加入。已经提交的凭据仍会保留在历史中，因此提交前还需运行检查：

```powershell
python tools/check_public_files.py --staged
python tools/check_public_files.py --history
```

可在当前仓库启用随附的提交及推送检查钩子：

```powershell
git config core.hooksPath .githooks
```

检查脚本使用 Python 标准库，检测私密配置文件、已知密钥格式和内部地址，并扫描工作簿 XML。它不能识别所有自定义凭据；更新配置和导出文件时仍应核对差异。如果真实密钥曾被上传到其他仓库或分享给他人，应在服务提供方撤销并重新生成。
