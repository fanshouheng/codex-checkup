from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .common import safe_project_label
from .model import Finding, ModuleResult
from .project_audit import _looks_like_code_project, _run_git
from .session_audit import SessionDataset, SessionStats


@dataclass
class ProjectSignals:
    path: Path
    label: str
    sessions: int = 0
    user_messages: int = 0
    corrections: int = 0
    tool_results: int = 0
    failed_tool_results: int = 0
    long_sessions: int = 0

    @property
    def correction_rate(self) -> float:
        return self.corrections / self.user_messages if self.user_messages else 0.0

    @property
    def tool_failure_rate(self) -> float:
        return self.failed_tool_results / self.tool_results if self.tool_results else 0.0


def _project_key(cwd: str) -> tuple[str, Path]:
    path = Path(cwd).expanduser().resolve(strict=False)
    return str(path).casefold(), path


def _group_projects(sessions: list[SessionStats]) -> list[ProjectSignals]:
    grouped: dict[str, ProjectSignals] = {}
    for session in sessions:
        if not session.cwd:
            continue
        key, path = _project_key(session.cwd)
        signals = grouped.setdefault(key, ProjectSignals(path=path, label=safe_project_label(str(path))))
        signals.sessions += 1
        signals.user_messages += session.user_messages
        signals.corrections += session.correction_signals
        signals.tool_results += session.tool_results
        signals.failed_tool_results += session.failed_tool_results
        if session.user_messages >= 40:
            signals.long_sessions += 1
    return sorted(grouped.values(), key=lambda item: (-item.sessions, item.label))


def _summary_row(signals: ProjectSignals, risk_families: list[str]) -> dict[str, object]:
    return {
        "project": signals.label,
        "sessions": signals.sessions,
        "user_messages": signals.user_messages,
        "correction_rate": round(signals.correction_rate, 4),
        "tool_results": signals.tool_results,
        "tool_failure_signal_rate": round(signals.tool_failure_rate, 4),
        "long_sessions": signals.long_sessions,
        "risk_families": risk_families,
    }


def audit_portfolio(dataset: SessionDataset, max_filesystem_projects: int = 50) -> ModuleResult:
    result = ModuleResult(name="portfolio")
    projects = _group_projects(dataset.sessions)
    if not projects:
        result.status = "unavailable"
        result.summary = {"projects_seen": 0, "projects_evaluated": 0, "projects_needing_direction_review": 0}
        result.notes.append("会话样本中没有可关联的项目目录。")
        return result

    evaluated = 0
    flagged = 0
    rows: list[dict[str, object]] = []
    for signals in projects:
        risk_families: list[str] = []

        if signals.user_messages >= 20 and signals.corrections >= 3 and signals.correction_rate >= 0.12:
            risk_families.append("alignment")
            result.findings.append(
                Finding(
                    "POR001",
                    "跨项目组合",
                    "P1",
                    "中",
                    "项目目标对齐返工集中",
                    f"{signals.label}：{signals.user_messages} 条用户消息中有 {signals.corrections} 个返工信号（{signals.correction_rate:.1%}）。",
                    "目标、边界或验收方式可能在实现过程中反复变化。",
                    "抽查该项目返工最高的会话，把问题分别归因为需求、实现、验证或方案选择，再决定改提示还是改路线。",
                    False,
                )
            )

        if signals.tool_results >= 20 and signals.failed_tool_results >= 5 and signals.tool_failure_rate >= 0.20:
            risk_families.append("tool-reliability")
            result.findings.append(
                Finding(
                    "POR002",
                    "跨项目组合",
                    "P1",
                    "低",
                    "项目工具失败信号集中",
                    f"{signals.label}：{signals.tool_results} 个工具结果中有 {signals.failed_tool_results} 个失败信号（{signals.tool_failure_rate:.1%}）。",
                    "环境假设、命令顺序或依赖状态可能持续消耗调试轮次。",
                    "抽样失败结果，优先修复重复出现的环境前置条件和验证命令；关键词统计不能代替日志复盘。",
                    False,
                )
            )

        check_filesystem = evaluated < max_filesystem_projects and signals.path.is_dir()
        if check_filesystem:
            evaluated += 1
            agents_present = (signals.path / "AGENTS.md").is_file()
            if signals.sessions >= 5 and (signals.corrections >= 3 or signals.long_sessions >= 2) and not agents_present:
                risk_families.append("knowledge-persistence")
                result.findings.append(
                    Finding(
                        "POR003",
                        "跨项目组合",
                        "P2",
                        "中",
                        "重复项目知识没有沉淀",
                        f"{signals.label}：{signals.sessions} 个会话、{signals.corrections} 个返工信号、{signals.long_sessions} 个超长会话，项目根目录没有 AGENTS.md。",
                        "新会话可能重复解释构建方式、约束和已经纠正过的问题。",
                        "只沉淀重复出现的规则、验证命令和项目边界，不要把临时任务写进持久指令。",
                        True,
                    )
                )

            if signals.sessions >= 2 and _looks_like_code_project(signals.path):
                git_code, _ = _run_git(signals.path, "rev-parse", "--show-toplevel")
                if git_code != 0:
                    risk_families.append("recovery-point")
                    result.findings.append(
                        Finding(
                            "POR004",
                            "跨项目组合",
                            "P1",
                            "高",
                            "活跃代码项目缺少 Git 恢复点",
                            f"{signals.label}：最近样本中有 {signals.sessions} 个会话，目录具有代码特征但不在 Git 工作树中。",
                            "跨会话修改无法稳定审阅、选择性提交或回退。",
                            "检查敏感文件和忽略规则后初始化 Git，并提交一个最小可用基线。",
                            True,
                        )
                    )

        if len(risk_families) >= 2:
            flagged += 1
            readable = {
                "alignment": "目标对齐",
                "tool-reliability": "工具可靠性",
                "knowledge-persistence": "知识沉淀",
                "recovery-point": "版本恢复点",
            }
            labels = "、".join(readable[item] for item in risk_families)
            result.findings.append(
                Finding(
                    "POR005",
                    "跨项目组合",
                    "P1",
                    "中",
                    "项目需要方向复盘",
                    f"{signals.label} 同时命中 {len(risk_families)} 个独立证据族：{labels}。这不等于技术路线已被证实错误。",
                    "继续追加实现前，应该先确认问题来自路线、环境、需求还是执行纪律。",
                    "暂停扩展功能，写清当前目标、硬约束、已选方案和被否决方案；用一个最小验证任务检验最不确定的假设。",
                    False,
                )
            )

        rows.append(_summary_row(signals, risk_families))

    result.summary = {
        "projects_seen": len(projects),
        "projects_evaluated": evaluated,
        "projects_needing_direction_review": flagged,
        "filesystem_project_limit": max_filesystem_projects,
        "projects": rows[:25],
    }
    if len(projects) > max_filesystem_projects:
        result.status = "partial"
        result.notes.append(
            f"聊天统计覆盖 {len(projects)} 个项目；为控制本地遍历成本，只对最活跃的 {max_filesystem_projects} 个现存目录执行文件系统检查。"
        )
    return result
