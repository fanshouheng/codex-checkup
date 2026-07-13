# 官方依据

核验日期：2026-07-13。

- [Codex customization: Skills and AGENTS.md](https://developers.openai.com/codex/concepts/customization)
- [Codex configuration reference](https://developers.openai.com/codex/config-reference)
- [Codex App Server API overview](https://learn.chatgpt.com/docs/app-server#api-overview)

本 Skill 依赖的稳定原则：Skill 使用 `SKILL.md` 加可选 scripts/references/assets；用户配置位于 `~/.codex/config.toml`，可信项目可使用 `.codex/config.toml`；AGENTS.md 用于持久项目指导；会话与插件相关接口可能继续演进，因此本地文件解析结果必须报告覆盖状态，不能伪装成稳定公共 schema。
