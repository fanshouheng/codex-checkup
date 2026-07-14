from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import safe_project_label, short_hash
from .session_audit import CONTINUE_RE, CORRECTION_RE, UUID_RE, _content_text, _session_files


SCOPE_RE = re.compile(
    r"(?:只改|仅修改|其他.{0,6}(?:不动|不要改|别改)|别改|不要改|不要把.{0,8}改|超出范围|范围.{0,6}(?:跑偏|不对)|overreach|out of scope|don't change|do not change)",
    re.IGNORECASE,
)
EVIDENCE_RE = re.compile(
    r"(?:不要瞎编|别瞎编|有依据吗|证据呢|你确定|看源码|读源码|实际.{0,8}不是|凭什么|编造|hallucin|evidence|source code|are you sure)",
    re.IGNORECASE,
)
VERIFICATION_RE = re.compile(
    r"(?:你.{0,8}(?:测试|验证|检查|截图).{0,6}(?:吗|没有|没)|(?:没有|没).{0,6}(?:测试|验证|检查)|测试了吗|验证了吗|did you (?:test|verify|check)|you (?:didn't|did not) (?:test|verify|check))",
    re.IGNORECASE,
)
AUTONOMY_RE = re.compile(
    r"(?:怎么停了|不要停|别停|直接做完|继续做完|不用.{0,8}确认|不需要.{0,8}确认|别问了|不要再问|没做完|还没完成|why did you stop|finish it|don't stop|do not stop)",
    re.IGNORECASE,
)
SUCCESS_RE = re.compile(
    r"^\s*(?:这次对了|就是这样|这样就对了|做得好|很好|非常好|perfect|exactly|that's right|looks good)(?:[，,。.!！]|$)",
    re.IGNORECASE,
)
NON_CORRECTION_REQUEST_RE = re.compile(r"(?:哪些|哪里|有没有|是否|怎么判断).{0,40}不对", re.IGNORECASE)

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization)\b(\s*[:=]\s*)([\"']?)[^\s,;\"']{8,}([\"']?)"
    ),
)

INCIDENT_PRIORITY = {
    "scope_control": 6,
    "evidence_discipline": 5,
    "verification_gap": 4,
    "autonomy_calibration": 3,
    "correction": 2,
    "success_pattern": 1,
}


@dataclass(frozen=True)
class Message:
    role: str
    text: str


@dataclass(frozen=True)
class CandidateIncident:
    kind: str
    session_ref: str
    project: str
    message_index: int
    messages: tuple[Message, ...]
    signal_offset: int


def _skip_message(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith(("# AGENTS.md instructions", "<environment_context>", "<permissions instructions>"))


def _read_messages(path: Path) -> tuple[str, str, list[Message]]:
    match = UUID_RE.search(path.name)
    session_id = match.group(0) if match else path.name
    cwd = ""
    messages: list[Message] = []
    fallback_user: list[tuple[int, Message]] = []
    saw_event_user = False

    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return session_id, cwd, messages
    with handle:
        for line in handle:
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue
            record_type = obj.get("type")
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            if record_type in {"session_meta", "turn_context"}:
                value = payload.get("cwd")
                if isinstance(value, str) and value:
                    cwd = value
            if record_type == "event_msg" and payload.get("type") == "user_message":
                text = payload.get("message")
                if isinstance(text, str) and text.strip() and not _skip_message(text):
                    saw_event_user = True
                    messages.append(Message("user", text.strip()))
                continue
            if record_type != "response_item" or payload.get("type") != "message":
                continue
            role = payload.get("role")
            text = _content_text(payload.get("content")).strip()
            if not text or _skip_message(text):
                continue
            if role == "assistant":
                messages.append(Message("assistant", text))
            elif role == "user":
                fallback_user.append((len(messages), Message("user", text)))

    if not saw_event_user and fallback_user:
        for index, message in reversed(fallback_user):
            messages.insert(index, message)
    return session_id, cwd, messages


def _classify_user_message(text: str) -> str | None:
    compact = " ".join(text.split())
    if SCOPE_RE.search(compact):
        return "scope_control"
    if EVIDENCE_RE.search(compact):
        return "evidence_discipline"
    if VERIFICATION_RE.search(compact):
        return "verification_gap"
    if AUTONOMY_RE.search(compact):
        return "autonomy_calibration"
    if CORRECTION_RE.search(compact) and not NON_CORRECTION_REQUEST_RE.search(compact):
        return "correction"
    if SUCCESS_RE.search(compact):
        return "success_pattern"
    return None


def _continuation_indices(messages: list[Message]) -> set[int]:
    user_indices = [index for index, message in enumerate(messages) if message.role == "user"]
    result: set[int] = set()
    previous_was_continue = False
    for index in user_indices:
        current = bool(CONTINUE_RE.fullmatch(" ".join(messages[index].text.split())))
        if current and previous_was_continue:
            result.add(index)
        previous_was_continue = current
    return result


def _redact(text: str, codex_home: Path, cwd: str, max_chars: int = 650) -> str:
    value = " ".join(text.split())
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 4:
            value = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)
        else:
            value = pattern.sub("[REDACTED]", value)

    replacements = [
        (str(codex_home.resolve(strict=False)), "$CODEX_HOME"),
        (str(Path.home().resolve(strict=False)), "$HOME"),
    ]
    if cwd:
        replacements.insert(0, (str(Path(cwd).expanduser().resolve(strict=False)), "$PROJECT"))
    for source, replacement in replacements:
        value = re.sub(re.escape(source), replacement, value, flags=re.IGNORECASE)
        value = re.sub(re.escape(source.replace("\\", "/")), replacement, value, flags=re.IGNORECASE)
    if len(value) > max_chars:
        value = value[: max_chars - 14].rstrip() + " ...[truncated]"
    return value


def _session_candidates(path: Path, codex_home: Path) -> list[CandidateIncident]:
    session_id, cwd, messages = _read_messages(path)
    if not messages:
        return []
    session_ref = short_hash(session_id)
    project = safe_project_label(cwd) if cwd else "unknown"
    continuation_indices = _continuation_indices(messages)
    candidates: list[CandidateIncident] = []
    for index, message in enumerate(messages):
        if message.role != "user":
            continue
        if not any(item.role == "assistant" for item in messages[:index]):
            continue
        kind = "autonomy_calibration" if index in continuation_indices else _classify_user_message(message.text)
        if kind is None:
            continue
        start = max(0, index - 2)
        end = min(len(messages), index + 3)
        context = tuple(
            Message(item.role, _redact(item.text, codex_home, cwd))
            for item in messages[start:end]
        )
        candidates.append(CandidateIncident(kind, session_ref, project, index, context, index - start))
    return candidates


def build_collaboration_evidence(
    codex_home: Path,
    days: int = 30,
    max_sessions: int = 300,
    max_incidents: int = 12,
) -> dict[str, Any]:
    files = _session_files(codex_home, days, max_sessions)
    candidates: list[CandidateIncident] = []
    for path in files:
        candidates.extend(_session_candidates(path, codex_home))

    candidates.sort(key=lambda item: -INCIDENT_PRIORITY[item.kind])
    selected: list[CandidateIncident] = []
    per_kind: dict[str, int] = defaultdict(int)
    seen: set[tuple[str, str, int]] = set()
    for candidate in candidates:
        key = (candidate.session_ref, candidate.kind, candidate.message_index)
        if key in seen or per_kind[candidate.kind] >= 3:
            continue
        seen.add(key)
        per_kind[candidate.kind] += 1
        selected.append(candidate)
        if len(selected) >= max_incidents:
            break

    counts = Counter(item.kind for item in selected)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "private": True,
        "notice": "Contains short redacted chat excerpts. Reading this file places those excerpts in the current Codex context.",
        "scope": {
            "days": days,
            "session_files_considered": len(files),
            "max_sessions": max_sessions,
            "max_incidents": max_incidents,
        },
        "incident_count": len(selected),
        "incident_type_counts": dict(sorted(counts.items())),
        "incidents": [
            {
                "type": item.kind,
                "project": item.project,
                "session_ref": item.session_ref,
                "context": [
                    {"role": message.role, "text": message.text, "is_signal": index == item.signal_offset}
                    for index, message in enumerate(item.messages)
                ],
            }
            for item in selected
        ],
    }


def write_collaboration_evidence(output: Path, payload: dict[str, Any]) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output
