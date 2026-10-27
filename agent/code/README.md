# 生成资产目录

- Excel/CSV：`excel/<fileType>/<pageTitle反斜杠替换为下划线>.py`。
- HTML对应Markdown解析器：`html/<fileType>/<同名规则>.py`。
- 图片/文档等转换Markdown的提示词：`md/<fileType>/<同名规则>.md`。

各具体类型目录下 `.staging/` 存候选，`.versions/` 存不可变版本。应用按精确来源键查询登记信息，再核对业务文件名、身份和SHA256，不使用模型搜索文件。

`.owners/`、`.runtime.json`、`.publication.lock` 和短暂的 `.publication.json` 是框架维护的登记/发布恢复记录。请通过验证和发布流程更新资产，直接改写当前文件会被完整性校验拦截。

首次复用V1已登记资产会自动复制到新布局并保留原版本和正文hash；原始历史文件和证据暂不删除。一个code目录只能绑定一个runtime任务数据库。
