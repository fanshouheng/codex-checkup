from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import read_jsonl, safe_project_label
from .model import Finding, ModuleResult


CORRECTION_RE = re.compile(
    r"(?:不是这样|不对(?:$|[，。,.!！])|你(?:理解|搞|弄)错|我说的是|别改|不要.{0,8}改|又错|还是不对|跑偏|搞错了|that's not what|not what i asked|you misunderstood|still wrong|don't change|do not change|revert that)",
    re.IGNORECASE,
)
CONTINUE_RE = re.compile(
    r"^(?:继续|继续吧|接着|对|是|好的|好|可以|开始吧|ok|okay|yes|go ahead|proceed|continue)[。.!！ ]*$",
    re.IGNORECASE,
)
FAILURE_RE = re.compile(
    r"(?:exit code[:=]?\s*[1-9]|command failed|script failed|traceback \(most recent call last\)|error:|exception:|失败|报错)",
    re.IGNORECASE,
)
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SKILL_FILE_RE = re.compile(r"([A-Za-z0-9][A-Za-z0-9_-]{1,63})[\\/]+SKILL\.md\b", re.I)


@dataclass
class SessionStats:
    session_id: str
    cwd: str = ""
    last_activity_at: str = ""
    user_messages: int = 0
    assistant_messages: int = 0
    correction_signals: int = 0
    continuation_messages: int = 0
    short_messages: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    failed_tool_results: int = 0
    known_events: int = 0
    confirmed_skill_refs: set[str] = field(default_factory=set)


@dataclass
class SessionDataset:
    sessions: list[SessionStats] = field(default_factory=list)
    files_available: int = 0
    files_considered: int = 0
    files_parsed: int = 0
    files_unparsed: int = 0
    truncated: bool = False


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text") or block.get("input_text") or block.get("output_text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _payload_output(payload: dict[str, Any]) -> str:
    for key in ("output", "content", "result"):
        value = payload.get(key)
        if isinstance(value, str):
            return value[:2000]
        if isinstance(value, list):
            return _content_text(value)[:2000]
        if isinstance(value, dict):
            for nested_key in ("text", "output", "message", "error"):
                nested = value.get(nested_key)
                if isinstance(nested, str):
                    return nested[:2000]
    return ""


def _extract_session(path: Path) -> SessionStats | None:
    match = UUID_RE.search(path.name)
    file_stat = path.stat()
    stats = SessionStats(
        session_id=match.group(0) if match else f"file-{file_stat.st_size}",
        last_activity_at=datetime.fromtimestamp(file_stat.st_mtime, timezone.utc).isoformat(),
    )
    event_user_messages: list[str] = []
    fallback_user_messages: list[str] = []
    saw_object = False

    for obj in read_jsonl(path):
        saw_object = True
        record_type = obj.get("type")
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            payload = {}

        if record_type == "session_meta":
            stats.known_events += 1
            session_id = payload.get("id")
            cwd = payload.get("cwd")
            if isinstance(session_id, str) and session_id:
                stats.session_id = session_id
            if isinstance(cwd, str) and cwd:
                stats.cwd = cwd
            continue

        if record_type == "turn_context":
            stats.known_events += 1
            cwd = payload.get("cwd")
            if isinstance(cwd, str) and cwd:
                stats.cwd = cwd
            continue

        if record_type == "event_msg" and payload.get("type") == "user_message":
            stats.known_events += 1
            text = payload.get("message")
            if isinstance(text, str) and text.strip():
                event_user_messages.append(text.strip())
            continue

        if record_type != "response_item":
            continue

        item_type = payload.get("type")
        if item_type == "message":
            stats.known_events += 1
            role = payload.get("role")
            text = _content_text(payload.get("content"))
            if role == "assistant" and text.strip():
                stats.assistant_messages += 1
            elif role == "user" and text.strip() and not text.lstrip().startswith("# AGENTS.md instructions"):
                fallback_user_messages.append(text.strip())
        elif item_type in {"function_call", "custom_tool_call", "local_shell_call", "computer_tool_call"}:
            stats.known_events += 1
            stats.tool_calls += 1
            serialized = json.dumps(payload, ensure_ascii=False, default=str)
            stats.confirmed_skill_refs.update(match.group(1).lower() for match in SKILL_FILE_RE.finditer(serialized))
        elif item_type in {"function_call_output", "custom_tool_call_output", "local_shell_call_output", "computer_tool_call_output"}:
            stats.known_events += 1
            stats.tool_results += 1
            if FAILURE_RE.search(_payload_output(payload)):
                stats.failed_tool_results += 1

    if not saw_object:
        return None

    messages = event_user_messages or fallback_user_messages
    stats.user_messages = len(messages)
    for text in messages:
        compact = " ".join(text.split())
        if CORRECTION_RE.search(compact):
            stats.correction_signals += 1
        if CONTINUE_RE.fullmatch(compact):
            stats.continuation_messages += 1
        if len(compact) <= 12 and not re.search(r"[/\\.:@]", compact):
            stats.short_messages += 1
    return stats


def _session_file_selection(codex_home: Path, days: int, max_sessions: int) -> tuple[list[Path], int]:
    cutoff = time.time() - days * 86400
    candidates: list[Path] = []
    roots = [codex_home / "sessions", codex_home / "archived_sessions"]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.jsonl"):
            try:
                if path.stat().st_mtime >= cutoff:
                    candidates.append(path)
            except OSError:
                continue
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[:max_sessions], len(candidates)


def _session_files(codex_home: Path, days: int, max_sessions: int) -> list[Path]:
    files, _ = _session_file_selection(codex_home, days, max_sessions)
    return files


def _project_summaries(sessions: list[SessionStats]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for session in sessions:
        if not session.cwd:
            continue
        row = grouped.setdefault(
            session.cwd,
            {"project": safe_project_label(session.cwd), "sessions": 0, "user_messages": 0, "correction_signals": 0},
        )
        row["sessions"] += 1
        row["user_messages"] += session.user_messages
        row["correction_signals"] += session.correction_signals
    rows = list(grouped.values())
    for row in rows:
        messages = row["user_messages"]
        row["correction_rate"] = round(row["correction_signals"] / messages, 4) if messages else 0.0
    rows.sort(key=lambda item: (-item["sessions"], item["project"]))
    return rows[:20]


def audit_sessions(codex_home: Path, days: int, max_sessions: int) -> tuple[ModuleResult, SessionDataset]:
    result = ModuleResult(name="sessions")
    dataset = SessionDataset()
    files, files_available = _session_file_selection(codex_home, days, max_sessions)
    dataset.files_available = files_available
    dataset.files_considered = len(files)
    dataset.truncated = files_available > len(files)
    if not files:
        result.status = "unavailable"
        result.summary = {"days": days, "session_files": 0}
        result.notes.append("指定时间范围内未找到本地 Codex 会话文件。")
        return result, dataset

    for path in files:
        try:
            stats = _extract_session(path)
        except OSError:
            stats = None
        if stats is not None:
            dataset.files_parsed += 1
            dataset.sessions.append(stats)
        else:
            dataset.files_unparsed += 1

    known_sessions = [item for item in dataset.sessions if item.known_events > 0]
    if not known_sessions:
        result.status = "partial"
        result.notes.append("会话文件可读取，但未识别到支持的事件结构；本地格式可能已变化。")
    elif dataset.files_unparsed or len(known_sessions) < len(dataset.sessions):
        result.status = "partial"
        result.notes.append("部分会话未识别到支持的事件结构。")
    if dataset.truncated:
        result.status = "partial"
        result.notes.append(f"时间范围内有 {files_available} 个会话文件，仅按上限读取最近 {len(files)} 个。")

    total_user = sum(item.user_messages for item in known_sessions)
    corrections = sum(item.correction_signals for item in known_sessions)
    continuations = sum(item.continuation_messages for item in known_sessions)
    long_sessions = sum(1 for item in known_sessions if item.user_messages >= 40)
    tool_results = sum(item.tool_results for item in known_sessions)
    failed_results = sum(item.failed_tool_results for item in known_sessions)
    correction_rate = corrections / total_user if total_user else 0.0
    continuation_rate = continuations / total_user if total_user else 0.0
    long_rate = long_sessions / len(known_sessions) if known_sessions else 0.0
    failure_rate = failed_results / tool_results if tool_results else 0.0

    result.summary = {
        "days": days,
        "session_files": len(files),
        "session_files_available": files_available,
        "session_files_considered": len(files),
        "session_files_parsed": dataset.files_parsed,
        "session_files_unparsed": dataset.files_unparsed,
        "session_files_truncated": dataset.truncated,
        "sessions_parsed": len(known_sessions),
        "user_messages": total_user,
        "correction_signals": corrections,
        "correction_rate": round(correction_rate, 4),
        "continuation_messages": continuations,
        "continuation_rate": round(continuation_rate, 4),
        "long_sessions": long_sessions,
        "tool_results": tool_results,
        "failed_tool_results": failed_results,
        "tool_failure_signal_rate": round(failure_rate, 4),
        "confirmed_skill_refs": sorted(
            {name for session in known_sessions for name in session.confirmed_skill_refs}
        ),
        "projects": _project_summaries(known_sessions),
    }

    if total_user >= 10 and correction_rate >= 0.15:
        result.findings.append(
            Finding(
                "SES001",
                "聊天协作",
                "P1",
                "中",
                "聊天中返工信号偏高",
                f"{total_user} 条用户消息中识别到 {corrections} 条高置信返工信号（{correction_rate:.1%}）。未读取到报告中的聊天原文。",
                "目标、边界或产出标准可能在多轮之后才对齐，增加重复工作。",
                "抽查返工最多的项目，区分需求表达、模型误判和验证缺失，再把重复纠正沉淀为项目规则。",
                False,
            )
        )
    if total_user >= 12 and continuation_rate >= 0.25:
        result.findings.append(
            Finding(
                "SES002",
                "聊天协作",
                "P2",
                "中",
                "连续确认消息占比较高",
                f"{total_user} 条用户消息中有 {continuations} 条仅表示继续或确认（{continuation_rate:.1%}）。",
                "Codex 可能频繁停在中间步骤，用户需要重复推动。",
                "任务开头给出授权范围、完成条件和仅在实质分歧时提问的规则。",
                False,
            )
        )
    if len(known_sessions) >= 5 and long_rate > 0.20:
        result.findings.append(
            Finding(
                "SES003",
                "聊天协作",
                "P2",
                "中",
                "超长会话比例偏高",
                f"{len(known_sessions)} 个会话中有 {long_sessions} 个包含至少 40 条用户消息（{long_rate:.1%}）。",
                "新目标可能与旧上下文混合，复盘和定位决策成本上升。",
                "同一目标保持连续；出现独立新目标或技术方向切换时创建新会话并写明交接结论。",
                False,
            )
        )
    if tool_results >= 10 and failure_rate >= 0.20:
        result.findings.append(
            Finding(
                "SES004",
                "聊天协作",
                "P1",
                "低",
                "工具失败信号偏高",
                f"{tool_results} 个可识别工具结果中有 {failed_results} 个包含失败信号（{failure_rate:.1%}）。",
                "环境假设、命令选择或验证顺序可能导致重复尝试。",
                "从具体项目日志抽样复核失败原因；优先修复重复出现的环境与命令问题。",
                False,
            )
        )

    return result, dataset
