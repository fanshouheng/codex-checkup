# Codex 全面体检 Skill

这是一个本地优先、默认只读的 Codex 使用诊断 Skill。它从配置、指令、Skills、聊天协作和项目执行五个角度收集证据，再输出分优先级、带置信度的优化建议。

它刻意不做两件事：不上传聊天内容，不自动修改用户环境。体检报告只保留统计量、脱敏路径和规则命中信息。

## 首版能力

- 审核 `config.toml` 的权限组合、过时字段、内联敏感值、MCP 与失效项目路径
- 审核全局和项目级 Skills 的元数据、重复项、断链资源、体积和高风险指令
- 统计最近聊天中的返工信号、连续确认、长会话和工具失败
- 跨项目汇总目标对齐、工具可靠性、知识沉淀和 Git 恢复点，只把多证据项目列入方向复盘
- 结合当前项目的 Git、AGENTS.md 和聊天样本给出流程建议
- 同时生成便于阅读的 Markdown 和便于后续自动化的 JSON 报告

## 安装

克隆仓库后，把标准 Skill 子目录安装到用户级 Skill 目录：

```powershell
git clone <repository-url> .\spcodex
Copy-Item -Recurse .\spcodex\codex-health-check "$HOME/.agents/skills/codex-health-check"
```

开发阶段也可以直接在本仓库验证：

```powershell
python .\codex-health-check\scripts\run_audit.py --project . --days 30
```

要求 Python 3.11 或更高版本，不需要安装第三方 Python 包。

## 使用

安装后可以直接对 Codex 说：

```text
帮我全面体检最近 30 天的 Codex 使用，重点看聊天返工、配置风险和当前项目流程。
```

也可以只运行某些模块：

```powershell
python .\codex-health-check\scripts\run_audit.py --modules config,skills --output .\audit-output
```

可用模块：`config`、`skills`、`sessions`、`portfolio`、`project`。

## 报告原则

- `P0`：需要立即处理的敏感信息或破坏性风险
- `P1`：很可能造成安全、返工或交付问题
- `P2`：值得安排的流程和维护问题
- `P3`：低风险观察项

报告不会生成一个看似精确的总分。不同模块的覆盖率、证据质量和风险性质不同，把它们压成单一分数会掩盖真正的问题。

## 当前边界

- Codex 本地会话文件不是稳定公共接口，解析器采用探测式兼容并在报告中显示覆盖率。
- 插件管理接口仍可能变化。首版审核可发现的本地 Skill 与配置，不把插件缓存目录当作“已启用”的证据。
- 聊天指标是协作信号，不是用户表现评分，也不能单独证明项目路线错误。
