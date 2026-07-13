from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import Finding, ModuleResult, sort_findings


DOMAIN_LABELS = {
    "config": "环境与配置",
    "skills": "指令与 Skills",
    "sessions": "聊天协作",
    "project": "项目执行",
}


def build_payload(metadata: dict[str, Any], modules: list[ModuleResult]) -> dict[str, Any]:
    findings = sort_findings([finding for module in modules for finding in module.findings])
    counts = Counter(item.priority for item in findings)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata,
        "coverage": [
            {"module": module.name, "label": DOMAIN_LABELS.get(module.name, module.name), "status": module.status}
            for module in modules
        ],
        "summary": {
            "finding_count": len(findings),
            "priority_counts": {priority: counts.get(priority, 0) for priority in ("P0", "P1", "P2", "P3")},
        },
        "modules": [module.to_dict() for module in modules],
        "findings": [finding.to_dict() for finding in findings],
    }


def _coverage_table(payload: dict[str, Any]) -> list[str]:
    lines = ["| 模块 | 状态 |", "| --- | --- |"]
    for row in payload["coverage"]:
        lines.append(f"| {row['label']} | `{row['status']}` |")
    return lines


def _finding_lines(findings: list[dict[str, Any]]) -> list[str]:
    if not findings:
        return ["本次口径下没有触发问题规则。这个结论不代表未覆盖模块没有风险。"]
    lines: list[str] = []
    for index, item in enumerate(findings, start=1):
        approval = "需要用户批准后修改。" if item["requires_approval"] else "可先继续只读复核。"
        lines.extend(
            [
                f"### {index}. [{item['priority']}] {item['title']}",
                "",
                f"- 领域：{item['domain']} / 规则 `{item['rule_id']}` / 置信度：{item['confidence']}",
                f"- 证据：{item['evidence']}",
                f"- 影响：{item['impact']}",
                f"- 建议：{item['recommendation']}",
                f"- 边界：{approval}",
                "",
            ]
        )
    return lines


def _retained_lines(payload: dict[str, Any]) -> list[str]:
    findings = payload["findings"]
    finding_domains = {item["domain"] for item in findings if item["priority"] in {"P0", "P1"}}
    lines: list[str] = []
    config_module = next((item for item in payload["modules"] if item["name"] == "config"), None)
    if config_module and config_module["status"] == "complete" and "配置" not in finding_domains:
        lines.append("- 配置可被标准 TOML 解析，未发现高优先级配置组合问题。")
    skills_module = next((item for item in payload["modules"] if item["name"] == "skills"), None)
    if skills_module and skills_module["summary"].get("skills_found", 0) and "Skills" not in finding_domains:
        lines.append("- 已发现的 Skills 未触发高优先级结构问题，现有工作流可继续保留。")
    session_module = next((item for item in payload["modules"] if item["name"] == "sessions"), None)
    if session_module and session_module["summary"].get("user_messages", 0) >= 10 and "聊天协作" not in finding_domains:
        lines.append("- 当前聊天样本没有触发高返工或高失败阈值，不建议为了优化而过度拆分流程。")
    if not lines:
        lines.append("- 本次高优先级问题跨多个领域，先完成复核再判断哪些现有能力应原样保留。")
    return lines


def _top_categories(findings: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    top: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in findings:
        key = (item["domain"], item["title"])
        if key in seen:
            continue
        seen.add(key)
        top.append(item)
        if len(top) >= limit:
            break
    return top


def render_markdown(payload: dict[str, Any]) -> str:
    counts = payload["summary"]["priority_counts"]
    findings = payload["findings"]
    top = _top_categories(findings)
    lines = [
        "# Codex 全面体检报告",
        "",
        f"生成时间：`{payload['generated_at']}`",
        f"范围：最近 {payload['metadata']['days']} 天；项目 `{payload['metadata']['project']}`；默认只读。",
        "",
        "## 总体判断",
        "",
    ]
    if top:
        lines.append(
            f"共发现 {len(findings)} 项：P0 {counts['P0']}、P1 {counts['P1']}、P2 {counts['P2']}、P3 {counts['P3']}。"
            f"当前应先处理：{'；'.join(item['title'] for item in top)}。"
        )
    else:
        lines.append("已完成的检查域没有触发问题规则；请结合覆盖状态理解该结论。")

    lines.extend(["", "## 覆盖状态", ""])
    lines.extend(_coverage_table(payload))
    lines.extend(["", "## 优先处理", ""])
    if top:
        for item in top:
            lines.append(f"- **[{item['priority']}] {item['title']}**：{item['recommendation']}（置信度：{item['confidence']}）")
    else:
        lines.append("- 暂无。")

    lines.extend(["", "## 全部发现", ""])
    lines.extend(_finding_lines(findings))
    lines.extend(["## 建议保留", ""])
    lines.extend(_retained_lines(payload))
    lines.extend(["", "## 覆盖缺口", ""])
    gaps = []
    for module in payload["modules"]:
        if module["status"] != "complete":
            note = "；".join(module["notes"]) if module["notes"] else "该模块未完整执行。"
            gaps.append(f"- {DOMAIN_LABELS.get(module['name'], module['name'])}：`{module['status']}`。{note}")
    if gaps:
        lines.extend(gaps)
    else:
        lines.append("- 已选择的模块均完成；本地会话格式仍属于版本相关诊断来源。")

    lines.extend(
        [
            "",
            "## 下一步",
            "",
            "1. 先人工复核 P0/P1 的证据是否符合实际使用意图。",
            "2. 每次只改一组相关设置或流程，保留备份和能力边界。",
            "3. 用相同天数、项目和模块重新体检，只比较同口径指标。",
            "",
            "> 本报告不包含聊天原文、密钥值或完整用户路径，也不会自动修改 Codex 环境。",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(output_dir: Path, metadata: dict[str, Any], modules: list[ModuleResult]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_payload(metadata, modules)
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(payload), encoding="utf-8")
    return markdown_path, json_path
