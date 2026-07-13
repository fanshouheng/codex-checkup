from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .common import find_high_risk_text, path_alias, relative_markdown_links
from .model import Finding, ModuleResult


@dataclass
class SkillRecord:
    directory: Path
    skill_file: Path
    name: str
    description: str
    line_count: int


def _frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration:
        return {}

    values: dict[str, str] = {}
    index = 1
    while index < end:
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", lines[index])
        if not match:
            index += 1
            continue
        key, raw = match.groups()
        raw = raw.strip()
        if raw in {"|", ">"}:
            parts: list[str] = []
            index += 1
            while index < end and (not lines[index].strip() or lines[index][:1].isspace()):
                if lines[index].strip():
                    parts.append(lines[index].strip())
                index += 1
            values[key] = " ".join(parts)
            continue
        values[key] = raw.strip("\"'")
        index += 1
    return values


def _skill_roots(codex_home: Path, project: Path) -> list[Path]:
    candidates = [
        Path.home() / ".agents" / "skills",
        codex_home / "skills",
        project / ".agents" / "skills",
        project / ".codex" / "skills",
    ]
    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        key = str(candidate.resolve(strict=False)).lower()
        if key in seen:
            continue
        seen.add(key)
        roots.append(candidate)
    return roots


def _discover_records(roots: list[Path]) -> list[SkillRecord]:
    records: list[SkillRecord] = []
    seen_real: set[str] = set()
    for root in roots:
        try:
            skill_files = list(root.rglob("SKILL.md"))
        except OSError:
            continue
        for skill_file in skill_files:
            if any(part in {".git", "node_modules", "__pycache__"} for part in skill_file.parts):
                continue
            directory = skill_file.parent
            real_key = str(skill_file.resolve(strict=False)).lower()
            if real_key in seen_real:
                continue
            seen_real.add(real_key)
            try:
                text = skill_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta = _frontmatter(text)
            records.append(
                SkillRecord(
                    directory=directory,
                    skill_file=skill_file,
                    name=meta.get("name", "").strip(),
                    description=meta.get("description", "").strip(),
                    line_count=len(text.splitlines()),
                )
            )
    return records


def _has_trigger_cue(description: str) -> bool:
    return bool(
        re.search(
            r"\b(?:use|trigger|invoke|when|whenever|asks?|mentions?)\b|(?:使用|触发|当用户|用户提到|适用于|场景)",
            description,
            re.IGNORECASE,
        )
    )


def _security_hits(record: SkillRecord) -> list[str]:
    hits: set[str] = set()
    candidates = [record.skill_file]
    scripts = record.directory / "scripts"
    if scripts.is_dir():
        for path in scripts.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".sh", ".ps1", ".py", ".js", ".mjs", ".cmd", ".bat"}:
                candidates.append(path)
    for path in candidates:
        try:
            if path.stat().st_size > 512_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits.update(find_high_risk_text(text))
    return sorted(hits)


def audit_skills(codex_home: Path, project: Path, roots: list[Path] | None = None) -> ModuleResult:
    result = ModuleResult(name="skills")
    selected_roots = roots if roots is not None else _skill_roots(codex_home, project)
    records = _discover_records(selected_roots)
    result.summary = {"roots_found": len(selected_roots), "skills_found": len(records)}
    if not selected_roots:
        result.status = "unavailable"
        result.notes.append("未找到用户级或项目级 Skill 目录。")
        return result

    if not records:
        result.status = "partial"
        result.notes.append("发现 Skill 目录，但未找到可读取的 SKILL.md。")
        return result

    names: dict[str, list[SkillRecord]] = {}
    descriptions: dict[str, list[SkillRecord]] = {}
    for record in records:
        label = path_alias(record.directory, codex_home, project)
        if not record.name or not record.description:
            missing = []
            if not record.name:
                missing.append("name")
            if not record.description:
                missing.append("description")
            result.findings.append(
                Finding(
                    "SKL001",
                    "Skills",
                    "P1",
                    "高",
                    "Skill 元数据不完整",
                    f"{label} 缺少 {', '.join(missing)}。",
                    "Codex 可能无法发现、触发或正确解释这个 Skill。",
                    "补齐标准 frontmatter，并验证 name 与目录、description 与触发场景一致。",
                    True,
                )
            )
        if record.name and record.name.lower() != record.directory.name.lower():
            result.findings.append(
                Finding(
                    "SKL002",
                    "Skills",
                    "P2",
                    "高",
                    "Skill 名称与目录不一致",
                    f"{label} 的 name={record.name}。",
                    "安装、引用和维护时容易把两个身份混在一起。",
                    "确认公开名称后统一目录名和 frontmatter；修改前检查调用方式。",
                    True,
                )
            )
        if record.description and not _has_trigger_cue(record.description):
            result.findings.append(
                Finding(
                    "SKL003",
                    "Skills",
                    "P2",
                    "中",
                    "Skill 描述没有明确触发场景",
                    f"{label} 的 description 只描述能力，未识别到触发语义。",
                    "用户没有显式点名 Skill 时，Codex 更可能漏触发。",
                    "在 description 中加入用户会说的任务场景，同时保留能力边界。",
                    True,
                )
            )
        if record.line_count > 500:
            result.findings.append(
                Finding(
                    "SKL005",
                    "Skills",
                    "P2",
                    "高",
                    "SKILL.md 体积过大",
                    f"{label}/SKILL.md 共 {record.line_count} 行。",
                    "Skill 触发后会加载过多内容，关键工作流也更难维护。",
                    "保留主流程和路由，把领域资料移到 references，并按需读取。",
                    True,
                )
            )

        try:
            text = record.skill_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        missing_links = []
        for target in relative_markdown_links(text):
            if not (record.directory / target).resolve(strict=False).exists():
                missing_links.append(target)
        if missing_links:
            result.findings.append(
                Finding(
                    "SKL006",
                    "Skills",
                    "P1",
                    "高",
                    "Skill 引用的本地资源不存在",
                    f"{label} 有 {len(missing_links)} 个断链资源；示例：{', '.join(missing_links[:3])}。",
                    "工作流执行到该步骤时会中断，或被迫临时猜测替代方案。",
                    "修复相对路径或补齐资源，并运行一次真实调用验证。",
                    True,
                )
            )

        high_risk = _security_hits(record)
        if high_risk:
            result.findings.append(
                Finding(
                    "SKL007",
                    "安全与隐私",
                    "P1",
                    "中",
                    "Skill 中存在高风险执行模式",
                    f"{label} 命中：{', '.join(high_risk)}。未输出命令原文。",
                    "一旦 Skill 被触发，命令可能扩大供应链、删除或凭据暴露风险。",
                    "在运行前逐行人工审核；改为固定程序、明确路径、最小权限和显式确认。",
                    True,
                )
            )

        if record.name:
            names.setdefault(record.name.lower(), []).append(record)
        if record.description:
            descriptions.setdefault(record.description.casefold(), []).append(record)

    duplicate_groups = [group for group in names.values() if len(group) > 1]
    duplicate_description_groups = [group for group in descriptions.values() if len(group) > 1]
    result.summary["duplicate_name_groups"] = len(duplicate_groups)
    result.summary["duplicate_description_groups"] = len(duplicate_description_groups)
    for group in duplicate_groups:
        labels = [path_alias(item.directory, codex_home, project) for item in group]
        result.findings.append(
            Finding(
                "SKL004",
                "Skills",
                "P2",
                "高",
                "发现同名 Skill",
                f"name={group[0].name} 出现在 {len(group)} 个位置：{', '.join(labels)}。",
                "不同作用域可能覆盖或混淆调用，维护时也可能只更新其中一份。",
                "先确认实际加载路径和使用频率，再选择保留来源；不要直接批量删除。",
                True,
            )
        )
    for group in duplicate_description_groups:
        if len({item.name.lower() for item in group if item.name}) <= 1:
            continue
        labels = [path_alias(item.directory, codex_home, project) for item in group]
        result.findings.append(
            Finding(
                "SKL004",
                "Skills",
                "P3",
                "中",
                "多个 Skill 使用完全相同的描述",
                f"{len(group)} 个 Skill 描述相同：{', '.join(labels)}。",
                "触发边界可能重叠，Codex 难以稳定选择正确工作流。",
                "为每个 Skill 写清独有场景和不适用边界，再做触发测试。",
                True,
            )
        )

    return result
