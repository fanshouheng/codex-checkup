from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable


SECRET_KEY_RE = re.compile(
    r"(?:^|[_-])(api[_-]?key|token|secret|password|passwd|credential|private[_-]?key)(?:$|[_-])",
    re.IGNORECASE,
)

HIGH_RISK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "远程脚本直接交给 shell 执行",
        re.compile(r"\b(?:curl|wget)\b[^\r\n|;]*\|\s*(?:sh|bash|zsh|powershell)\b", re.I),
    ),
    (
        "宽泛破坏性删除",
        re.compile(r"(?:\brm\s+-[a-z]*r[a-z]*f\s+(?:/|~|\$HOME|\*)|\bRemove-Item\b[^\r\n]*-Recurse[^\r\n]*(?:C:\\\\|\$HOME|\*))", re.I),
    ),
    (
        "读取常见密钥文件",
        re.compile(r"\b(?:cat|type|Get-Content|read|open)\b[^\r\n]*(?:\.env|id_rsa|id_ed25519|credentials|private[_-]?key)", re.I),
    ),
)
NEGATION_RE = re.compile(r"\b(?:do not|don't|never|must not|avoid)\b|(?:不要|不得|禁止|避免)", re.I)


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:8]


def path_alias(path: Path, codex_home: Path | None = None, project: Path | None = None) -> str:
    resolved = path.expanduser().resolve(strict=False)
    pairs = [
        (project, "$PROJECT"),
        (codex_home, "$CODEX_HOME"),
        (Path.home(), "$HOME"),
    ]
    for root, label in pairs:
        if root is None:
            continue
        root_resolved = root.expanduser().resolve(strict=False)
        try:
            relative = resolved.relative_to(root_resolved)
        except ValueError:
            continue
        if not relative.parts:
            return label
        return f"{label}/{relative.as_posix()}"
    return f"{resolved.name}#{short_hash(str(resolved))}"


def flatten_mapping(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if not isinstance(value, dict):
        yield prefix, value
        return
    for key, child in value.items():
        child_prefix = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(child, dict):
            yield from flatten_mapping(child, child_prefix)
        else:
            yield child_prefix, child


def contains_inline_secret(key_path: str, value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    key = key_path.rsplit(".", 1)[-1]
    if not SECRET_KEY_RE.search(key):
        return False
    if key.lower().endswith(("_env_var", "env_var")):
        return False
    upper = value.strip().upper()
    if upper.startswith("${") or upper.startswith("ENV:") or upper.endswith("_ENV_VAR"):
        return False
    if re.fullmatch(r"[A-Z][A-Z0-9_]{5,}", value.strip()):
        return False
    return len(value.strip()) >= 8


def safe_project_label(path_text: str) -> str:
    path = Path(path_text)
    name = path.name or "project"
    return f"{name}#{short_hash(path_text)}"


def find_high_risk_text(text: str) -> list[str]:
    hits: set[str] = set()
    for line in text.splitlines():
        if NEGATION_RE.search(line):
            continue
        for label, pattern in HIGH_RISK_PATTERNS:
            if pattern.search(line):
                hits.add(label)
    return sorted(hits)


def relative_markdown_links(text: str) -> list[str]:
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
    result: list[str] = []
    for link in links:
        target = link.split("#", 1)[0].strip()
        if not target or "://" in target or target.startswith(("#", "mailto:")):
            continue
        result.append(target.replace("%20", " "))
    return result


def env_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
