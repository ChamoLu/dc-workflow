# 项目 Skills 扩展预留

本目录暂不提供新的 Skill 注册器。当前仍由 `dc_agent.generation.skills.SkillRegistry` 只读加载 `DC_SKILL_ROOT` 下的既有 Skill，并使用包内 `profiles/source_gzk.md` 适配规范。

未来在这里维护项目级清单、规范及参考资料绑定，并实现 `dc_agent.ports.SkillProvider` 的 `load(task)` 接口。不要复制改写外部原 Skill，或把本目录存在理解为新注册器已经启用。
