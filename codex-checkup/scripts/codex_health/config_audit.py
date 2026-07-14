from __future__ import annotations

import shlex
import tomllib
from pathlib import Path
from typing import Any

from .common import contains_inline_secret, flatten_mapping
from .model import Finding, ModuleResult


SHELL_COMMANDS = {"sh", "bash", "zsh", "fish", "cmd", "cmd.exe", "powershell", "pwsh"}


def _approval_mode(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "granular"
    return "unset"


def _command_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        return Path(shlex.split(value, posix=False)[0]).name.lower()
    except (ValueError, IndexError):
        return Path(value.split()[0]).name.lower()


def _parse_config(path: Path) -> dict[str, Any] | None:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return None


def _audit_layer(result: ModuleResult, data: dict[str, Any], layer: str) -> dict[str, Any]:
    sandbox = str(data.get("sandbox_mode", "unset"))
    approval = _approval_mode(data.get("approval_policy"))
    if sandbox == "danger-full-access" and approval == "never":
        result.findings.append(
            Finding(
                "CFG001",
                "配置",
                "P1",
                "高",
                "文件权限与审批边界同时放宽",
                f"{layer}：sandbox_mode=danger-full-access，approval_policy=never。",
                "命令可访问更大范围，且不会在关键动作前停下来让用户复核。",
                "确认这是否只用于隔离自动化环境；交互开发优先缩小权限或恢复按需审批。",
                True,
            )
        )
    elif approval == "never":
        result.findings.append(
            Finding(
                "CFG002",
                "配置",
                "P2",
                "高",
                "审批策略为 never",
                f"{layer}：approval_policy=never。",
                "交互任务中的高影响动作不会获得人工复核机会。",
                "确认使用场景；自动化可保留，日常交互建议使用按需审批或更窄权限。",
                True,
            )
        )

    secret_keys = [key for key, value in flatten_mapping(data) if contains_inline_secret(key, value)]
    if secret_keys:
        result.findings.append(
            Finding(
                "CFG003",
                "安全与隐私",
                "P0",
                "高",
                "配置中疑似直接保存敏感值",
                f"{layer}检测到 {len(secret_keys)} 个疑似敏感键；报告不会显示键值。键路径：{', '.join(sorted(secret_keys)[:5])}。",
                "配置文件可能被备份、提交或读取到模型上下文中。",
                "撤销已暴露凭据，改用环境变量引用或系统凭据存储，并检查 Git 历史。",
                True,
            )
        )

    deprecated = []
    if "experimental_instructions_file" in data:
        deprecated.append("experimental_instructions_file")
    if "instructions" in data:
        deprecated.append("instructions")
    if deprecated:
        result.findings.append(
            Finding(
                "CFG004",
                "配置",
                "P2",
                "高",
                "存在过时或保留的指令字段",
                f"{layer}命中字段：{', '.join(deprecated)}。",
                "指令可能没有按预期加载，或在版本升级后失效。",
                "将持久项目规则放入 AGENTS.md；需要替换内置指令时使用当前官方字段。",
                True,
            )
        )

    mcp_servers = data.get("mcp_servers", {})
    shell_mcp: list[str] = []
    enabled_count = 0
    if isinstance(mcp_servers, dict):
        for name, server in mcp_servers.items():
            if not isinstance(server, dict):
                continue
            if server.get("enabled", True) is not False:
                enabled_count += 1
            if _command_name(server.get("command")) in SHELL_COMMANDS:
                shell_mcp.append(str(name))
    if shell_mcp:
        result.findings.append(
            Finding(
                "CFG006",
                "安全与隐私",
                "P1",
                "中",
                "MCP 通过通用 shell 启动",
                f"{layer}有 {len(shell_mcp)} 个 MCP 使用 shell 命令：{', '.join(sorted(shell_mcp)[:5])}。",
                "shell 会扩大命令拼接、环境展开和隐藏附加动作的空间。",
                "核对启动参数，能直接执行固定程序时改为直接 command + args。",
                True,
            )
        )

    return {
        "sandbox_mode": sandbox,
        "approval_policy": approval,
        "mcp_servers": len(mcp_servers) if isinstance(mcp_servers, dict) else 0,
        "mcp_enabled_or_unspecified": enabled_count,
    }


def _project_trust(data: dict[str, Any], project: Path | None) -> str:
    if project is None:
        return "not_checked"
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        return "unknown"
    resolved = project.resolve(strict=False)
    for path_text, settings in projects.items():
        if not isinstance(path_text, str) or not isinstance(settings, dict):
            continue
        try:
            if Path(path_text).expanduser().resolve(strict=False) == resolved:
                return str(settings.get("trust_level", "unspecified"))
        except OSError:
            continue
    return "not_listed"


def audit_config(codex_home: Path, project: Path | None = None) -> ModuleResult:
    result = ModuleResult(name="config")
    user_path = codex_home / "config.toml"
    project_path = project / ".codex" / "config.toml" if project is not None else None
    paths = [("用户级配置", user_path)]
    if project_path is not None and project_path.is_file():
        paths.append(("项目级配置", project_path))

    existing = [(label, path) for label, path in paths if path.is_file()]
    if not existing:
        result.status = "unavailable"
        result.notes.append("未找到用户级或项目级 config.toml；该模块无法判断当前配置。")
        result.summary = {"config_present": False, "sources": []}
        return result

    parsed: list[tuple[str, dict[str, Any]]] = []
    failed_labels: list[str] = []
    for label, path in existing:
        data = _parse_config(path)
        if data is None:
            failed_labels.append(label)
            result.findings.append(
                Finding(
                    "CFG000",
                    "配置",
                    "P1",
                    "高",
                    "Codex 配置无法解析",
                    f"{label}存在，但标准 TOML 解析失败。",
                    "Codex 可能忽略部分设置或无法按预期启动。",
                    "先用 TOML 诊断工具定位语法错误；修复前备份原文件。",
                    True,
                )
            )
        else:
            parsed.append((label, data))

    if failed_labels:
        result.status = "partial" if parsed else "failed"
        result.notes.append(f"{len(failed_labels)} 个配置来源解析失败。")

    result.summary = {
        "config_present": True,
        "parseable": bool(parsed),
        "sources": [label for label, _ in existing],
        "parsed_sources": [label for label, _ in parsed],
    }
    layer_summaries: dict[str, dict[str, Any]] = {}
    for label, data in parsed:
        layer_summaries[label] = _audit_layer(result, data, label)
    result.summary["layers"] = layer_summaries

    user_data = next((data for label, data in parsed if label == "用户级配置"), {})
    user_summary = layer_summaries.get("用户级配置", {})
    result.summary["sandbox_mode"] = user_summary.get("sandbox_mode", "unset")
    result.summary["approval_policy"] = user_summary.get("approval_policy", "unset")
    result.summary["mcp_servers"] = sum(item["mcp_servers"] for item in layer_summaries.values())
    result.summary["mcp_enabled_or_unspecified"] = sum(
        item["mcp_enabled_or_unspecified"] for item in layer_summaries.values()
    )

    projects = user_data.get("projects", {})
    missing_projects = 0
    if isinstance(projects, dict):
        for path_text in projects:
            if isinstance(path_text, str) and not Path(path_text).expanduser().is_dir():
                missing_projects += 1
    result.summary["project_stanzas"] = len(projects) if isinstance(projects, dict) else 0
    result.summary["missing_project_dirs"] = missing_projects
    result.summary["current_project_trust"] = _project_trust(user_data, project)
    if missing_projects:
        result.findings.append(
            Finding(
                "CFG005",
                "配置",
                "P2",
                "高",
                "配置中存在失效项目目录",
                f"{missing_projects} 个项目配置指向不存在的目录；路径已隐藏。",
                "旧项目配置会增加维护噪声，并可能让信任或项目级设置难以核对。",
                "逐项确认后删除失效 stanza；修改前备份 config.toml。",
                True,
            )
        )

    profile_count = len(list(codex_home.glob("*.config.toml")))
    requirements_present = (codex_home / "requirements.toml").is_file()
    result.summary["profile_files"] = profile_count
    result.summary["requirements_present"] = requirements_present
    if profile_count:
        if result.status == "complete":
            result.status = "partial"
        result.notes.append(f"发现 {profile_count} 个 profile 配置文件；脚本无法确认当前运行时选择了哪一个。")
    if requirements_present:
        if result.status == "complete":
            result.status = "partial"
        result.notes.append("发现 requirements.toml；管理员约束只做存在性记录，未解析为有效运行时配置。")
    if project_path is not None and project_path.is_file() and result.summary["current_project_trust"] != "trusted":
        if result.status == "complete":
            result.status = "partial"
        result.notes.append("发现项目级 config.toml，但未确认当前项目处于 trusted 状态。")

    return result
