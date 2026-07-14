from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import PRIORITY_ORDER, Finding, ModuleResult


DOMAIN_LABELS = {
    "config": "环境与配置",
    "skills": "指令与 Skills",
    "sessions": "聊天协作",
    "portfolio": "跨项目组合",
    "project": "项目执行",
}

PRACTICE_BY_RULE: dict[str, tuple[list[str], str]] = {
    "CFG001": (["PRA018"], "official-confirmed"),
    "CFG002": (["PRA018"], "official-confirmed"),
    "CFG003": (["PRA018"], "official-confirmed"),
    "CFG004": (["PRA008"], "official-confirmed"),
    "CFG005": (["PRA008"], "local-only"),
    "CFG006": (["PRA016", "PRA018"], "official-plus-practice"),
    "SKL001": (["PRA010", "PRA011"], "official-confirmed"),
    "SKL002": (["PRA010"], "official-confirmed"),
    "SKL003": (["PRA011"], "official-confirmed"),
    "SKL004": (["PRA011"], "local-only"),
    "SKL005": (["PRA010"], "official-confirmed"),
    "SKL006": (["PRA010", "PRA012"], "official-confirmed"),
    "SKL007": (["PRA018"], "official-confirmed"),
    "SES001": (["PRA001", "PRA015"], "local-only"),
    "SES002": (["PRA001", "PRA003"], "official-plus-practice"),
    "SES003": (["PRA004"], "official-confirmed"),
    "SES004": (["PRA016"], "practice-supported"),
    "PRJ001": (["PRA007"], "official-confirmed"),
    "PRJ002": (["PRA007"], "official-confirmed"),
    "PRJ003": (["PRA008", "PRA009"], "local-only"),
    "PRJ004": (["PRA008"], "official-confirmed"),
    "PRJ005": (["PRA003", "PRA020"], "local-only"),
    "PRJ006": (["PRA018"], "official-confirmed"),
    "PRJ007": (["PRA008"], "official-confirmed"),
    "POR001": (["PRA001", "PRA015"], "local-only"),
    "POR002": (["PRA016"], "practice-supported"),
    "POR003": (["PRA008", "PRA009"], "local-only"),
    "POR004": (["PRA007"], "official-confirmed"),
    "POR005": (["PRA003", "PRA020"], "local-only"),
}


def _engine_for(module_name: str) -> str:
    if module_name == "sessions":
        return "collaboration"
    if module_name in {"config", "skills"}:
        return "workbench"
    return "projects"


def _finding_payload(item: Finding, module: ModuleResult) -> dict[str, Any]:
    data = item.to_dict()
    heuristic = item.rule_id.startswith(("SES", "POR")) or item.rule_id in {"PRJ003", "PRJ005"}
    practice_refs, basis = PRACTICE_BY_RULE.get(item.rule_id, (["local-only"], "local-only"))
    placement = {
        "config": "config",
        "skills": "Skill",
        "sessions": "prompt、AGENTS.md 或验证流程",
        "portfolio": "项目任务或项目 AGENTS.md",
        "project": "项目任务、测试或项目 AGENTS.md",
    }.get(module.name, "manual-review")
    data.update(
        {
            "finding_id": item.rule_id,
            "engine": _engine_for(module.name),
            "evidence_grade": "C" if heuristic else "A",
            "evidence_refs": [item.rule_id],
            "coverage": {"module": module.name, "status": module.status},
            "practice_refs": practice_refs,
            "recommendation_basis": basis,
            "placement": placement,
            "approval_required": item.requires_approval,
            "verification": f"修复后以相同范围重新运行 {module.name} 模块，并比较规则 {item.rule_id} 是否仍触发。",
        }
    )
    return data


def build_payload(metadata: dict[str, Any], modules: list[ModuleResult]) -> dict[str, Any]:
    module_payloads: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for module in modules:
        module_data = module.to_dict()
        module_findings = [_finding_payload(finding, module) for finding in module.findings]
        module_data["findings"] = module_findings
        module_payloads.append(module_data)
        findings.extend(module_findings)
    findings.sort(key=lambda item: (PRIORITY_ORDER[item["priority"]], item["domain"], item["rule_id"]))
    counts = Counter(item["priority"] for item in findings)
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
        "modules": module_payloads,
        "findings": findings,
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
                f"- 证据等级：`{item['evidence_grade']}` / 实践节点：{', '.join(item['practice_refs'])} / 建议依据：`{item['recommendation_basis']}`",
                f"- 证据：{item['evidence']}",
                f"- 影响：{item['impact']}",
                f"- 建议：{item['recommendation']}",
                f"- 边界：{approval}",
                f"- 复测：{item['verification']}",
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
        "# Codex 全景体检报告",
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
