from __future__ import annotations

import argparse
import re
from pathlib import Path


REQUIRED_SECTIONS = (
    "先说结果",
    "反复绕路的协作流程",
    "哪些重复工作值得做成 Skill",
    "目前做得好的地方",
    "配置和规则里真正需要处理的事",
    "需要继续或收尾的项目",
    "接下来先做什么",
)

FORBIDDEN_PATTERNS = (
    ("内部模块状态", re.compile(r"\b(?:complete|partial|unavailable|failed)\b")),
    ("内部证据等级", re.compile(r"(?:`[ABCDU]`|\b[ABCDU]=\d)")),
    ("实践节点编号", re.compile(r"\bPRA\d{3}\b")),
    ("规则编号", re.compile(r"\b(?:CFG|SKL|SES|PRJ|POR)\d{3}\b")),
    ("内部字段名", re.compile(r"\b(?:finding_id|recommendation_basis|evidence_grade)\b")),
    ("内部项目占位符", re.compile(r"\$PROJECT\b")),
)


def validate_report(text: str, max_chars: int = 6000) -> list[str]:
    errors: list[str] = []
    if len(text) > max_chars:
        errors.append(f"主报告过长：{len(text)} 字符，建议不超过 {max_chars}。")

    headings = re.findall(r"(?m)^##\s+(.+?)\s*$", text)
    if headings and headings[0].strip() == "审计范围与覆盖":
        errors.append("主报告不能以“审计范围与覆盖”开头。")
    for section in REQUIRED_SECTIONS:
        if not any(section in heading for heading in headings):
            errors.append(f"缺少用户可见章节：{section}")

    for label, pattern in FORBIDDEN_PATTERNS:
        if pattern.search(text):
            errors.append(f"主报告包含{label}，应移到 health-check-evidence.md。")

    if "health-check-evidence.md" not in text:
        errors.append("主报告没有链接 health-check-evidence.md。")
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校验 Codex Checkup 人话主报告")
    parser.add_argument("report", type=Path, help="health-check.md 路径")
    parser.add_argument("--max-chars", type=int, default=6000, help="主报告最大字符数")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        text = args.report.read_text(encoding="utf-8")
    except OSError as error:
        print(f"report_read_error={type(error).__name__}")
        return 2
    errors = validate_report(text, max_chars=args.max_chars)
    if errors:
        print("human_report_valid=false")
        for error in errors:
            print(f"- {error}")
        return 1
    print("human_report_valid=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
