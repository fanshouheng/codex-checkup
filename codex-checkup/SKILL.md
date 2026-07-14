---
name: codex-checkup
description: 对个人 Codex 工作台做本地、只读、证据驱动的全景体检，审核历史协作、配置、AGENTS.md、Skills、MCP 和可识别项目状态，找出协作弯路、能力漏用、规则冲突、失效配置、未闭环或疑似遗忘项目，并恢复经用户确认后可执行的任务顺序。用户提到 Codex 体检、全面体检、全景体检、使用诊断、聊天复盘、协作返工、配置优化、Skill 清理、AGENTS.md 冲突、项目进度恢复、忘记做到哪里、项目路线复盘或下一步任务排序时，都应使用本 Skill。
---

# Codex 全景体检

把体检当成一次证据审计，而不是凭感觉给建议。先测量，再判断；把事实、推断和建议分开。

运行环境：Python 3.11+，支持 Windows、macOS 和 Linux。默认只读取本地 Codex 数据并在当前项目写入脱敏报告。

## 安全边界

- 默认只读。不要修改、删除或上传 Codex 配置、聊天、Skills、插件缓存或项目文件。
- 只在当前工作目录写入体检报告。
- 报告不得包含聊天原文、密钥值、完整环境变量、完整用户名路径或完整配置内容。
- 配置内部结构和本地会话格式可能随版本变化。无法确认时降低置信度并明确说明覆盖缺口。
- 优化动作先形成建议。只有用户明确要求修复后，才进入备份、差异预览、用户确认、修改、复测流程。

## 体检流程

1. 读取 [references/audit-contract.md](references/audit-contract.md)，严格使用其中的三个引擎、证据等级、项目状态和统一输出结构。
2. 确认范围：默认检查最近 30 天、当前项目和当前用户的 Codex 目录。用户指定日期、项目或模块时按其范围执行；不要未经授权扫描整块磁盘。
3. 解析本 Skill 的实际目录，先运行基础扫描：

```powershell
python <skill-directory>/scripts/run_audit.py --project <current-project> --days 30
```

4. 阅读生成的 `report.md` 和 `report.json`。把它们作为确定性证据，不要用脚本未采集到的内容补齐配置、Skill 和项目结论。
5. 用户明确要求审核聊天、协作、返工、“全面体检”或“全景体检”时，继续深度协作诊断。若用户只说“检查 Codex”且没有提聊天，先说明下一步会读取少量脱敏聊天片段并征得同意。
6. 生成私有证据包：

```powershell
python <skill-directory>/scripts/prepare_collaboration_evidence.py --days 30 --max-samples 12
```

7. 明确告诉用户：私有证据包同时包含顺利完成样本和摩擦样本，均为短小、尽力脱敏的聊天片段；读取后这些片段会进入当前 Codex 上下文，但不会写入可分享报告。然后读取 [references/collaboration-rubric.md](references/collaboration-rubric.md) 和 `.codex-health-private/collaboration-evidence.json`。
8. 分别运行交互协作、Codex 工作台和项目恢复判断。项目只从会话工作目录、最近项目记录和用户指定目录发现；状态必须符合契约，证据不足时使用 `unknown`。
9. 形成观察结论后读取 [references/codex-practice-network.md](references/codex-practice-network.md)，把每项建议路由到最匹配的 `PRAxxx` 节点。区分官方规范、官方 X 新能力、具名实践者经验、普通社区线索和反例；不得把传播量当作正确性证据。
10. 按契约生成 `health-check.md`。每个结论记录证据等级、置信度、覆盖范围、主要归因、实践节点、建议依据、放置位置、审批边界和验证方法。先讲最值得处理的 3 项，不要用问题数量制造焦虑。

## 三个引擎

- **交互协作引擎**：比较顺利与摩擦样本，诊断返工、范围、自治、验证、降级交付、任务切换和 Skill 使用情况。
- **Codex 工作台引擎**：检查配置、用户/项目/嵌套 AGENTS.md、Skills、MCP 的质量、作用域、重复、冲突和实际遵守情况。
- **项目恢复引擎**：恢复审计范围内项目的目标、状态、未完成项、阻塞、依赖和下一步，不评价项目商业价值。

产品契约见 [references/audit-contract.md](references/audit-contract.md)，Codex 使用建议见 [references/codex-practice-network.md](references/codex-practice-network.md)，详细判断规则见 [references/checks.md](references/checks.md)，协作语义诊断见 [references/collaboration-rubric.md](references/collaboration-rubric.md)，隐私约束见 [references/privacy.md](references/privacy.md)，报告解释方式见 [references/reporting.md](references/reporting.md)。

## 输出要求

回复用户并写入 `health-check.md` 时交付四份结果：

1. **协作诊断**：保留哪些成功模式，哪些协作弯路需要改变，Skill 是否有确认使用、可能漏用或范围内未观察。
2. **配置诊断**：配置关系、AGENTS.md 作用域与冲突、Skills/MCP 的重复、失效和风险。
3. **项目恢复地图**：每个可识别项目的证据状态、已完成、未完成、阻塞和下一步。
4. **建议行动顺序**：按用户目标、阻塞关系、收尾成本、疑似遗忘和系统性影响排序，并说明理由和完成条件。

最后说明审计范围、`A/B/C/D/U` 证据分布、建议来源等级和无法判断项。每项优化建议至少列出一个 `PRAxxx`；没有匹配实践时标记为“本地推导”。涉及写配置、删 Skill、归档聊天、关闭项目或修改项目时，先征得用户同意。

## 复测

用户批准并完成优化后，用相同的 `--days`、`--project` 和模块范围再次运行。只比较同口径指标，并记录：

- 哪个证据发生变化
- 哪项能力被保留
- 哪些问题仍未解决
- 是否出现新的副作用
