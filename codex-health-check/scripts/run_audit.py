from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from codex_health import __version__
from codex_health.common import env_codex_home, path_alias
from codex_health.config_audit import audit_config
from codex_health.model import ModuleResult
from codex_health.project_audit import audit_project
from codex_health.report import write_report
from codex_health.session_audit import SessionDataset, audit_sessions
from codex_health.skill_audit import audit_skills


VALID_MODULES = ("config", "skills", "sessions", "project")


def parse_modules(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return list(VALID_MODULES)
    modules = [item.strip().lower() for item in value.split(",") if item.strip()]
    unknown = [item for item in modules if item not in VALID_MODULES]
    if unknown:
        raise argparse.ArgumentTypeError(f"未知模块：{', '.join(unknown)}")
    if not modules:
        raise argparse.ArgumentTypeError("至少选择一个模块")
    return list(dict.fromkeys(modules))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地、只读的 Codex 全面体检")
    parser.add_argument("--codex-home", type=Path, default=env_codex_home(), help="Codex 用户目录")
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="要审核的当前项目")
    parser.add_argument("--days", type=int, default=30, help="聊天统计天数（1-3650）")
    parser.add_argument("--max-sessions", type=int, default=500, help="最多读取的会话文件数（1-5000）")
    parser.add_argument("--modules", type=parse_modules, default=list(VALID_MODULES), help="all 或逗号分隔模块")
    parser.add_argument("--output", type=Path, help="报告输出目录")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def failed_module(name: str, error: Exception) -> ModuleResult:
    result = ModuleResult(name=name, status="failed")
    result.notes.append(f"模块执行失败：{type(error).__name__}。为保护隐私，报告未写入异常内容。")
    return result


def main() -> int:
    args = build_parser().parse_args()
    if not 1 <= args.days <= 3650:
        raise SystemExit("--days 必须在 1 到 3650 之间")
    if not 1 <= args.max_sessions <= 5000:
        raise SystemExit("--max-sessions 必须在 1 到 5000 之间")

    codex_home = args.codex_home.expanduser().resolve(strict=False)
    project = args.project.expanduser().resolve(strict=False)
    output = args.output
    if output is None:
        output = project / f"codex-health-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    else:
        output = output.expanduser().resolve(strict=False)

    modules: list[ModuleResult] = []
    sessions = SessionDataset()

    if "config" in args.modules:
        try:
            modules.append(audit_config(codex_home, project))
        except Exception as error:  # module isolation is part of report coverage
            modules.append(failed_module("config", error))

    if "skills" in args.modules:
        try:
            modules.append(audit_skills(codex_home, project))
        except Exception as error:
            modules.append(failed_module("skills", error))

    if "sessions" in args.modules or "project" in args.modules:
        try:
            session_result, sessions = audit_sessions(codex_home, args.days, args.max_sessions)
        except Exception as error:
            session_result = failed_module("sessions", error)
        if "sessions" in args.modules:
            modules.append(session_result)

    if "project" in args.modules:
        try:
            modules.append(audit_project(project, sessions))
        except Exception as error:
            modules.append(failed_module("project", error))

    metadata = {
        "tool_version": __version__,
        "days": args.days,
        "max_sessions": args.max_sessions,
        "project": path_alias(project, codex_home, project),
        "codex_home": path_alias(codex_home, codex_home, project),
        "modules": args.modules,
        "read_only_sources": True,
        "raw_chat_in_report": False,
    }
    markdown_path, json_path = write_report(output, metadata, modules)
    print(f"report_md={markdown_path}")
    print(f"report_json={json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
