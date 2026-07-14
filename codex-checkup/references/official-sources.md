# 官方依据

核验日期：2026-07-14。

- [Using Goals in Codex](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex#how-to-write-a-goal)
- [Codex customization: Skills](https://learn.chatgpt.com/docs/customization/overview#skills)
- [Custom instructions with AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md#how-codex-discovers-guidance)
- [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents#why-subagent-workflows-help)
- [Git worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees)
- [Agent approvals and version control](https://learn.chatgpt.com/docs/agent-approvals-security#version-control)
- [Skills operational best practices](https://developers.openai.com/cookbook/examples/skills_in_api#operational-best-practices)
- [Codex configuration reference](https://developers.openai.com/codex/config-reference)

本 Skill 依赖的稳定原则：目标应定义结果、验证、约束、边界、迭代和阻塞条件；Skill 使用 `SKILL.md` 加可选 scripts/references/assets；AGENTS.md 按全局、项目和嵌套目录组成指令链；独立写任务可用 worktree 隔离；会话与插件相关接口可能继续演进，因此本地文件解析结果必须报告覆盖状态，不能伪装成稳定公共 schema。

官方规范与 X/社区实践的关系、来源日期和适用边界见 [codex-practice-network.md](codex-practice-network.md)。
