---
name: codex-checkup
description: 对个人 Codex 工作台做本地、证据驱动的全景体检，并在用户批准后继续整改与同口径复测。用人能直接看懂的方式审核历史协作、配置、AGENTS.md、Skills、MCP 和可识别项目状态，重点指出哪些协作流程反复绕路、哪里重复返工、哪些人工流程适合沉淀为 Skill，以及下一次具体应该怎么做。用户提到 Codex 体检、全面体检、全景体检、使用诊断、聊天复盘、协作返工、重复浪费时间、哪些适合做成 Skill、配置优化、Skill 清理、AGENTS.md 冲突、项目进度恢复、忘记做到哪里、项目路线复盘、下一步任务排序、按体检结果优化、继续整改、修复第几项或复测优化效果时，都应使用本 Skill。
---

# Codex 全景体检

把体检当成一次证据审计，而不是凭感觉给建议。先测量，再判断；把事实、推断和建议分开。

运行环境：Python 3.11+，支持 Windows、macOS 和 Linux。默认只读取本地 Codex 数据并在当前项目写入脱敏报告。

## 安全边界

- 默认只读。不要修改、删除或上传 Codex 配置、聊天、Skills、插件缓存或项目文件。
- 只在当前工作目录写入体检报告。
- 报告不得包含聊天原文、密钥值、完整环境变量、完整用户名路径或完整配置内容。
- 配置内部结构和本地会话格式可能随版本变化。无法确认时降低置信度并明确说明覆盖缺口。
- 优化动作先形成建议。只有用户明确要求修复后才进入整改；用户点名的可逆、限定范围修改可以直接执行，高影响动作仍按下文边界单独确认。

## 模式路由

先判断用户要进入哪个阶段，避免每次都从头体检：

- **体检模式**：用户要求检查、诊断、复盘或盘点时，执行完整或限定范围的只读体检。
- **整改模式**：用户说“开始优化”“按报告处理”“修复第 N 项”或指定某类问题时，读取现有 `health-check.md` 和整改状态，直接继续对应行动。
- **复测模式**：用户说“重新体检”“验证优化效果”或整改批次完成时，沿用基线报告的时间、项目和模块范围复测。

整改或复测时读取 [references/remediation.md](references/remediation.md)。只有找不到可用基线、基线范围不明或用户要求刷新时，才重新执行完整体检。

## 体检流程

1. 读取 [references/audit-contract.md](references/audit-contract.md)，严格使用其中的三个引擎、证据等级、项目状态和统一输出结构。
2. 确认范围：默认检查最近 30 天、当前项目和当前用户的 Codex 目录。用户指定日期、项目或模块时按其范围执行；不要未经授权扫描整块磁盘。
3. 解析本 Skill 的实际目录，先运行基础扫描：

```powershell
python <skill-directory>/scripts/run_audit.py --project <current-project> --days 30
```

4. 阅读生成的 `report.md` 和 `report.json`。把它们作为确定性证据，不要用脚本未采集到的内容补齐配置、Skill 和项目结论。会话模块为 `partial` 时不得生成 `D` 级结论，也不得声称某个 Skill 在完整范围内未使用。
5. 用户明确要求审核聊天、协作、返工、“全面体检”或“全景体检”时，继续深度协作诊断。若用户只说“检查 Codex”且没有提聊天，先说明下一步会读取少量脱敏聊天片段并征得同意。
6. 生成私有证据包：

```powershell
python <skill-directory>/scripts/prepare_collaboration_evidence.py --days 30 --max-samples 12 --max-task-samples 100
```

7. 明确告诉用户：私有证据包包含顺利/摩擦短片段，以及最多 100 个会话的脱敏任务开场清单；读取后它们会进入当前 Codex 上下文，但不会写入可分享报告。然后读取 [references/collaboration-rubric.md](references/collaboration-rubric.md) 和 `.codex-health-private/collaboration-evidence.json`。
8. 分别运行交互协作、Codex 工作台和项目恢复判断。先读取 `exact_repeat_groups` 定位完全重复任务，再用 `task_inventory` 聚类语义相近的任务和人工步骤，用顺利/摩擦样本解释哪些环节造成返工。只把 `confirmed_skill_refs` 中的名称写成确认使用；“可能漏用”必须同时有可解析任务语义和匹配的 Skill 描述。按 `agents_inventory` 读取用户级、项目级和嵌套 `AGENTS.md`，比较作用域、重复、冲突和实际执行要求。项目只从会话工作目录、最近项目记录和用户指定目录发现；使用 `recovery_facts` 中的 Git、计划、TODO 和最近活动证据，证据不足时保持 `unknown`，不得用工作区干净推断已完成。
9. 形成观察结论后读取 [references/codex-practice-network.md](references/codex-practice-network.md)，把每项建议路由到最匹配的 `PRAxxx` 节点。区分官方规范、官方 X 新能力、具名实践者经验、普通社区线索和反例；不得把传播量当作正确性证据。
10. 读取 [references/human-report.md](references/human-report.md)，生成面向人的 `health-check.md` 和机器/审计用的 `health-check-evidence.md`。主报告先讲具体协作流程、重复成本、理想做法和 Skill 候选；证据等级、规则编号、模块状态、实践节点、覆盖路径和完整项目清单全部下沉到证据附录。
11. 运行 `python <skill-directory>/scripts/validate_human_report.py health-check.md`。校验失败时按错误重写主报告，不能把不合格报告直接交给用户。
12. 不要在报告链接处结束回复。明确推荐最先处理的一项，并给出“开始第 1 项 / 只优化协作 / 只优化配置 / 只恢复项目 / 暂不修改”五个后续入口。用户在最初请求中已经明确要求“体检并优化”时，生成报告后直接进入整改预检，不再要求用户重复表达同一意图。

## 三个引擎

- **交互协作引擎**：比较顺利与摩擦样本，诊断返工、范围、自治、验证、降级交付、任务切换和 Skill 使用情况。
- **Codex 工作台引擎**：检查配置、用户/项目/嵌套 AGENTS.md、Skills、MCP 的质量、作用域、重复、冲突和实际遵守情况。
- **项目恢复引擎**：恢复审计范围内项目的目标、状态、未完成项、阻塞、依赖和下一步，不评价项目商业价值。

产品契约见 [references/audit-contract.md](references/audit-contract.md)，人话报告格式见 [references/human-report.md](references/human-report.md)，整改闭环见 [references/remediation.md](references/remediation.md)，Codex 使用建议见 [references/codex-practice-network.md](references/codex-practice-network.md)，详细判断规则见 [references/checks.md](references/checks.md)，协作语义诊断见 [references/collaboration-rubric.md](references/collaboration-rubric.md)，隐私约束见 [references/privacy.md](references/privacy.md)，报告解释方式见 [references/reporting.md](references/reporting.md)。

## 输出要求

回复用户并写入 `health-check.md` 时，用人话交付四类结果：

1. **协作流程**：哪里反复绕路、出现过几次、为什么会返工、下一次改成什么流程。
2. **Skill 机会与工作台问题**：哪些重复人工流程适合 Skill，哪些更应放进 AGENTS.md、自动检查或配置；只列真正影响工作的配置问题。
3. **项目恢复**：优先列出需要继续、验证、解除阻塞或决定去留的项目，不展开冗长的状态不明清单。
4. **下一步行动**：按用户目标、阻塞关系、收尾成本、疑似遗忘和系统性影响排序，并说明完成条件。

`health-check-evidence.md` 记录审计范围、`A/B/C/D/U`、建议来源、完整项目表和无法判断项。每项优化建议在附录中至少列出一个 `PRAxxx`；没有匹配实践时标记为“本地推导”。涉及写配置、删 Skill、归档聊天、关闭项目或修改项目时，先征得用户同意。

## 整改闭环

用户批准优化后，不要只重复建议。读取 `health-check.md`，按 [references/remediation.md](references/remediation.md) 创建或恢复 `.codex-health-private/remediation-state.json`，一次推进一个边界清楚的行动：核验证据、说明改动、执行、验证、记录状态，再推荐下一项。

用户明确说“修复第 N 项”或点名行动时，视为批准该行动的可逆、限定范围修改。删除、覆盖用户数据、修改凭据/权限、清理全局配置、初始化或提交 Git、推送、归档或关闭项目仍需单独确认。不要把“建议优化全部”解释为这些高影响动作的批量授权。

## 复测

用户批准并完成优化后，用相同的 `--days`、`--project` 和模块范围再次运行。只比较同口径指标，并记录：

- 哪个证据发生变化
- 哪项能力被保留
- 哪些问题仍未解决
- 是否出现新的副作用

只有动作验证通过时才标记 `verified`；只有同口径复测确认原发现消失或降级时才标记 `resolved`。复测后继续给出仍存在、新增和无法比较的问题，不用健康分数掩盖差异。
