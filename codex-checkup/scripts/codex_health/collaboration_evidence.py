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
COMPLETION_RE = re.compile(
    r"(?:已完成|完成并|已经.{0,12}完成|已提交|已经提交|已生成|已经生成|已写入|已交付|处理完成|修复完成|工作区干净|\bcompleted\b|\bdone\b|\bdelivered\b)",
    re.IGNORECASE,
)
VERIFIED_RE = re.compile(
    r"(?:测试|验证|确认|构建|lint|test|工作区干净|启动成功|截图|预览|通过|已提交|git|build|verified|passed)",
    re.IGNORECASE,
)
INCOMPLETE_RE = re.compile(
    r"(?:尚未|还未|未完成|未(?:安装|上传|导出|提交|发布|执行|交付)|没有真正.{0,8}(?:提交|完成|导出)|仍需|还需要你|需要你提供|等待你|not yet|still need|pending user)",
    re.IGNORECASE,
)
SUCCESS_DISQUALIFIER_RE = re.compile(
    r"(?:不对|不是这样|还是不|跑偏|搞错|理解错|太.{0,12}(?:了|腔|像)|重新(?:写|做|改)|改一下|再改|不符合|不是我.{0,8}(?:要|的)|wrong|redo)",
    re.IGNORECASE,
)

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
    basis: str


def _skip_message(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith(
        ("# AGENTS.md instructions", "<environment_context>", "<permissions instructions>", "<heartbeat>")
    )


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
        candidates.append(
            CandidateIncident(
                kind,
                session_ref,
                project,
                index,
                context,
                index - start,
                "explicit_user_feedback",
            )
        )
    return candidates


def _successful_session_candidate(path: Path, codex_home: Path) -> CandidateIncident | None:
    session_id, cwd, messages = _read_messages(path)
    if not messages:
        return None
    continuation_indices = _continuation_indices(messages)
    for index, message in enumerate(messages):
        if message.role != "user":
            continue
        has_prior_assistant = any(item.role == "assistant" for item in messages[:index])
        if has_prior_assistant and SUCCESS_DISQUALIFIER_RE.search(" ".join(message.text.split())):
            return None
        kind = "autonomy_calibration" if index in continuation_indices else _classify_user_message(message.text)
        if kind and kind != "success_pattern":
            return None

    assistant_indices = [index for index, message in enumerate(messages) if message.role == "assistant"]
    user_indices = [index for index, message in enumerate(messages) if message.role == "user"]
    if not assistant_indices or not user_indices:
        return None
    final_user_index = user_indices[-1]
    tail_assistant_indices = [index for index in assistant_indices if index > final_user_index]
    if not tail_assistant_indices:
        return None
    final_assistant_index = tail_assistant_indices[-1]
    completion_text = " ".join(messages[index].text for index in tail_assistant_indices[-2:])
    if not COMPLETION_RE.search(completion_text) or INCOMPLETE_RE.search(completion_text):
        return None

    selected_indices = {user_indices[0], final_assistant_index}
    if len(user_indices) > 1:
        selected_indices.add(user_indices[-1])
    if len(tail_assistant_indices) > 1:
        selected_indices.add(tail_assistant_indices[-2])
    ordered = sorted(selected_indices)
    context = tuple(
        Message(messages[index].role, _redact(messages[index].text, codex_home, cwd))
        for index in ordered
    )
    signal_offset = ordered.index(final_assistant_index)
    basis = "completion_and_verification" if VERIFIED_RE.search(completion_text) else "completion_without_followup_correction"
    return CandidateIncident(
        "successful_completion",
        short_hash(session_id),
        safe_project_label(cwd) if cwd else "unknown",
        final_assistant_index,
        context,
        signal_offset,
        basis,
    )


def _select_with_kind_limit(candidates: list[CandidateIncident], limit: int) -> list[CandidateIncident]:
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
        if len(selected) >= limit:
            break
    return selected


def _diverse_successes(candidates: list[CandidateIncident], limit: int) -> list[CandidateIncident]:
    selected: list[CandidateIncident] = []
    used_projects: set[str] = set()
    for require_new_project in (True, False):
        for candidate in candidates:
            if candidate in selected:
                continue
            if require_new_project and candidate.project in used_projects:
                continue
            selected.append(candidate)
            used_projects.add(candidate.project)
            if len(selected) >= limit:
                return selected
    return selected


def build_collaboration_evidence(
    codex_home: Path,
    days: int = 30,
    max_sessions: int = 300,
    max_samples: int = 12,
) -> dict[str, Any]:
    files = _session_files(codex_home, days, max_sessions)
    friction_candidates: list[CandidateIncident] = []
    success_candidates: list[CandidateIncident] = []
    for path in files:
        session_candidates = _session_candidates(path, codex_home)
        friction_candidates.extend(item for item in session_candidates if item.kind != "success_pattern")
        success_candidates.extend(item for item in session_candidates if item.kind == "success_pattern")
        successful_session = _successful_session_candidate(path, codex_home)
        if successful_session is not None:
            success_candidates.append(successful_session)

    friction_candidates.sort(key=lambda item: -INCIDENT_PRIORITY[item.kind])
    success_candidates.sort(key=lambda item: item.basis != "completion_and_verification")
    success_quota = min(len(success_candidates), max(1, max_samples // 3))
    friction_quota = max_samples - success_quota
    selected_friction = _select_with_kind_limit(friction_candidates, friction_quota)
    remaining = max_samples - len(selected_friction)
    selected_success = _diverse_successes(success_candidates, remaining)
    selected = selected_friction + selected_success
    if len(selected) < max_samples:
        remaining_friction = [item for item in friction_candidates if item not in selected_friction]
        selected.extend(_select_with_kind_limit(remaining_friction, max_samples - len(selected)))

    counts = Counter(item.kind for item in selected)
    class_counts = Counter(
        "successful" if item.kind in {"successful_completion", "success_pattern"} else "friction"
        for item in selected
    )
    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "private": True,
        "notice": "Contains short redacted chat excerpts. Reading this file places those excerpts in the current Codex context.",
        "scope": {
            "days": days,
            "session_files_considered": len(files),
            "max_sessions": max_sessions,
            "max_samples": max_samples,
        },
        "sample_count": len(selected),
        "sample_class_counts": dict(sorted(class_counts.items())),
        "sample_type_counts": dict(sorted(counts.items())),
        "samples": [
            {
                "sample_class": "successful" if item.kind in {"successful_completion", "success_pattern"} else "friction",
                "type": item.kind,
                "basis": item.basis,
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
