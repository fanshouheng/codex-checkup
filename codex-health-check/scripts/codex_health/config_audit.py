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


def audit_config(codex_home: Path) -> ModuleResult:
    result = ModuleResult(name="config")
    config_path = codex_home / "config.toml"
    if not config_path.is_file():
        result.status = "unavailable"
        result.notes.append("未找到用户级 config.toml；该模块无法判断当前配置。")
        result.summary = {"config_present": False}
        return result

    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        result.status = "failed"
        result.summary = {"config_present": True, "parseable": False}
        result.findings.append(
            Finding(
                "CFG000",
                "配置",
                "P1",
                "高",
                "Codex 配置无法解析",
                "config.toml 存在，但标准 TOML 解析失败。",
                "Codex 可能忽略部分设置或无法按预期启动。",
                "先用 TOML 诊断工具定位语法错误；修复前备份原文件。",
                True,
            )
        )
        return result

    sandbox = str(data.get("sandbox_mode", "unset"))
    approval = _approval_mode(data.get("approval_policy"))
    result.summary = {
        "config_present": True,
        "parseable": True,
        "sandbox_mode": sandbox,
        "approval_policy": approval,
    }

    if sandbox == "danger-full-access" and approval == "never":
        result.findings.append(
            Finding(
                "CFG001",
                "配置",
                "P1",
                "高",
                "文件权限与审批边界同时放宽",
                "sandbox_mode=danger-full-access，approval_policy=never。",
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
                "approval_policy=never。",
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
                f"检测到 {len(secret_keys)} 个疑似敏感键；报告不会显示键值。键路径：{', '.join(sorted(secret_keys)[:5])}。",
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
                f"命中字段：{', '.join(deprecated)}。",
                "指令可能没有按预期加载，或在版本升级后失效。",
                "将持久项目规则放入 AGENTS.md；需要替换内置指令时使用当前官方字段。",
                True,
            )
        )

    projects = data.get("projects", {})
    missing_projects = 0
    if isinstance(projects, dict):
        for path_text in projects:
            if isinstance(path_text, str) and not Path(path_text).expanduser().is_dir():
                missing_projects += 1
    result.summary["project_stanzas"] = len(projects) if isinstance(projects, dict) else 0
    result.summary["missing_project_dirs"] = missing_projects
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
    result.summary["mcp_servers"] = len(mcp_servers) if isinstance(mcp_servers, dict) else 0
    result.summary["mcp_enabled_or_unspecified"] = enabled_count
    if shell_mcp:
        result.findings.append(
            Finding(
                "CFG006",
                "安全与隐私",
                "P1",
                "中",
                "MCP 通过通用 shell 启动",
                f"{len(shell_mcp)} 个 MCP 使用 shell 命令：{', '.join(sorted(shell_mcp)[:5])}。",
                "shell 会扩大命令拼接、环境展开和隐藏附加动作的空间。",
                "核对启动参数，能直接执行固定程序时改为直接 command + args。",
                True,
            )
        )

    return result
