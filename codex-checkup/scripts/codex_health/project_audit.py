from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .common import find_high_risk_text
from .model import Finding, ModuleResult
from .session_audit import SessionDataset, SessionStats


PROJECT_MARKERS = {
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "CMakeLists.txt",
    ".sln",
}
SOURCE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java", ".cs", ".cpp", ".c", ".h"}
SKIP_DIRS = {
    ".git",
    ".codex-health-private",
    ".pytest_cache",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
}
PLAN_NAMES = {"TODO.MD", "PLAN.MD", "TASKS.MD", "ROADMAP.MD", "BACKLOG.MD"}
TEXT_SUFFIXES = {".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java", ".cs"}
TODO_RE = re.compile(r"(?:\bTODO\b|\bFIXME\b|\bXXX\b|待办|未完成|下一步)", re.I)
COMMAND_RE = re.compile(
    r"(?:```(?:bash|sh|powershell|shell|cmd)|\b(?:python|pytest|npm|pnpm|yarn|cargo|go|dotnet|mvn|gradle)\b)",
    re.I,
)
VALIDATION_RE = re.compile(r"(?:测试|验证|验收|完成条件|test|verify|validation|build|lint)", re.I)


def _keep_directory(name: str) -> bool:
    return name not in SKIP_DIRS and not name.startswith("codex-health-report-")


def _run_git(project: Path, *args: str) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return completed.returncode, completed.stdout.strip()


def _looks_like_code_project(project: Path) -> bool:
    try:
        names = {item.name for item in project.iterdir()}
    except OSError:
        return False
    if names & PROJECT_MARKERS:
        return True
    source_count = 0
    files_seen = 0
    for current, dirnames, filenames in os.walk(project):
        dirnames[:] = [name for name in dirnames if _keep_directory(name)]
        for filename in filenames:
            files_seen += 1
            if Path(filename).suffix.lower() in SOURCE_SUFFIXES:
                source_count += 1
                if source_count >= 3:
                    return True
            if files_seen >= 5000:
                return False
    return False


def canonical_project_path(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    if not resolved.is_dir():
        return resolved
    code, root = _run_git(resolved, "rev-parse", "--show-toplevel")
    if code == 0 and root:
        return Path(root).resolve(strict=False)
    return resolved


def project_recovery_facts(project: Path, max_files: int = 250) -> dict[str, object]:
    facts: dict[str, object] = {
        "git_branch": "unknown",
        "git_changed_entries": 0,
        "git_untracked_entries": 0,
        "plan_files": [],
        "todo_markers": 0,
        "files_considered": 0,
        "files_scanned": 0,
        "scan_truncated": False,
        "evidence_state": "unknown",
    }
    git_code, _ = _run_git(project, "rev-parse", "--show-toplevel")
    if git_code == 0:
        _, branch = _run_git(project, "branch", "--show-current")
        status_code, status_text = _run_git(project, "status", "--porcelain=v1", "--untracked-files=all")
        status_lines = status_text.splitlines() if status_code == 0 and status_text else []
        facts["git_branch"] = branch or "detached"
        facts["git_untracked_entries"] = sum(1 for line in status_lines if line.startswith("??"))
        facts["git_changed_entries"] = len(status_lines) - int(facts["git_untracked_entries"])
        if status_lines:
            facts["evidence_state"] = "in_progress"

    plan_files: list[str] = []
    todo_markers = 0
    files_considered = 0
    files_scanned = 0
    for current, dirnames, filenames in os.walk(project):
        relative_dir = Path(current).resolve(strict=False).relative_to(project.resolve(strict=False))
        dirnames[:] = [name for name in dirnames if _keep_directory(name)]
        if len(relative_dir.parts) >= 3:
            dirnames[:] = []
        for filename in filenames:
            if files_considered >= max_files:
                facts["scan_truncated"] = True
                break
            files_considered += 1
            path = Path(current) / filename
            if filename.upper() in PLAN_NAMES:
                plan_files.append(path.relative_to(project).as_posix())
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                if path.stat().st_size > 256_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            files_scanned += 1
            todo_markers += len(TODO_RE.findall(text))
        if facts["scan_truncated"]:
            break
    facts["plan_files"] = sorted(plan_files)[:20]
    facts["todo_markers"] = todo_markers
    facts["files_considered"] = files_considered
    facts["files_scanned"] = files_scanned
    return facts


def agents_inventory(project: Path, codex_home: Path | None, max_files: int = 50) -> tuple[list[dict[str, object]], bool]:
    paths: list[tuple[str, Path, str]] = []
    truncated = False
    if codex_home is not None:
        user_agents = codex_home / "AGENTS.md"
        if user_agents.is_file():
            paths.append(("user", user_agents, "$CODEX_HOME/AGENTS.md"))
    for current, dirnames, filenames in os.walk(project):
        dirnames[:] = [name for name in dirnames if _keep_directory(name)]
        if "AGENTS.md" not in filenames:
            continue
        path = Path(current) / "AGENTS.md"
        relative = path.relative_to(project).as_posix()
        scope = "project" if relative == "AGENTS.md" else "nested"
        paths.append((scope, path, relative))
        if len(paths) >= max_files:
            truncated = True
            break

    records: list[dict[str, object]] = []
    for scope, path, label in paths[:max_files]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            records.append(
                {
                    "scope": scope,
                    "path": label,
                    "readable": True,
                    "nonblank_lines": sum(1 for line in text.splitlines() if line.strip()),
                    "has_commands": bool(COMMAND_RE.search(text)),
                    "has_validation": bool(VALIDATION_RE.search(text)),
                }
            )
        except OSError:
            records.append({"scope": scope, "path": label, "readable": False})
    return records, truncated


def _same_project(session: SessionStats, project: Path) -> bool:
    if not session.cwd:
        return False
    try:
        session_path = canonical_project_path(Path(session.cwd))
        project_path = canonical_project_path(project)
        return session_path == project_path
    except OSError:
        return False


def audit_project(project: Path, sessions: SessionDataset, codex_home: Path | None = None) -> ModuleResult:
    result = ModuleResult(name="project")
    if not project.is_dir():
        result.status = "unavailable"
        result.summary = {"project_present": False}
        result.notes.append("指定项目目录不存在。")
        return result

    code_project = _looks_like_code_project(project)
    git_code, git_root = _run_git(project, "rev-parse", "--show-toplevel")
    has_git = git_code == 0 and bool(git_root)
    result.summary = {"project_present": True, "looks_like_code_project": code_project, "git": has_git}

    if code_project and not has_git:
        result.findings.append(
            Finding(
                "PRJ001",
                "项目执行",
                "P1",
                "高",
                "代码项目没有 Git 恢复点",
                "当前目录具有代码项目特征，但不在 Git 工作树中。",
                "Codex 批量修改后缺少可审阅差异、选择性提交和可靠回退。",
                "初始化 Git，先提交最小基线；之后按目的选择性暂存和提交。",
                True,
            )
        )

    if has_git:
        status_code, status_text = _run_git(project, "status", "--porcelain=v1", "--untracked-files=all")
        status_lines = status_text.splitlines() if status_code == 0 and status_text else []
        untracked = sum(1 for line in status_lines if line.startswith("??"))
        changed = len(status_lines) - untracked
        commit_code, commit_text = _run_git(project, "rev-list", "--count", "HEAD")
        commit_count = int(commit_text) if commit_code == 0 and commit_text.isdigit() else 0
        result.summary.update(
            {"git_commits": commit_count, "git_changed_entries": changed, "git_untracked_entries": untracked}
        )
        if commit_count == 0:
            result.findings.append(
                Finding(
                    "PRJ002",
                    "项目执行",
                    "P2",
                    "高",
                    "Git 仓库尚无基线提交",
                    "仓库已初始化，但当前分支没有可计数提交。",
                    "无法可靠比较 Codex 改动前后的状态。",
                    "检查敏感文件和 .gitignore 后，选择性提交当前可用基线。",
                    True,
                )
            )
        if untracked > 20 or changed > 30:
            result.findings.append(
                Finding(
                    "PRJ002",
                    "项目执行",
                    "P2",
                    "高",
                    "工作树积累了较多未归档变化",
                    f"Git 显示 {changed} 个已跟踪变化、{untracked} 个未跟踪条目。",
                    "不同目的的改动可能混在一起，审阅、回退和定位回归更困难。",
                    "先按功能分组审阅，补齐忽略规则，再选择性提交；不要直接 git add .。",
                    True,
                )
            )

    project_sessions = [item for item in sessions.sessions if _same_project(item, project)]
    user_messages = sum(item.user_messages for item in project_sessions)
    corrections = sum(item.correction_signals for item in project_sessions)
    correction_rate = corrections / user_messages if user_messages else 0.0
    result.summary.update(
        {
            "matching_sessions": len(project_sessions),
            "matching_user_messages": user_messages,
            "matching_correction_signals": corrections,
            "matching_correction_rate": round(correction_rate, 4),
        }
    )

    result.summary["recovery_facts"] = project_recovery_facts(project)
    inventory, inventory_truncated = agents_inventory(project, codex_home)
    result.summary["agents_inventory"] = inventory
    result.summary["agents_inventory_truncated"] = inventory_truncated
    if inventory_truncated:
        result.status = "partial"
        result.notes.append("AGENTS.md 文件超过清单上限，未完整读取全部嵌套指令。")

    agents_path = project / "AGENTS.md"
    if not agents_path.is_file() and len(project_sessions) >= 5 and corrections >= 2:
        result.findings.append(
            Finding(
                "PRJ003",
                "项目执行",
                "P2",
                "中",
                "重复使用的项目缺少持久指导",
                f"当前项目匹配 {len(project_sessions)} 个会话和 {corrections} 个返工信号，但根目录没有 AGENTS.md。",
                "相同的项目边界、验证命令或纠正意见可能在新会话中重复解释。",
                "只沉淀已经重复出现的规则、构建命令和验收方式；不要把临时需求全部写入 AGENTS.md。",
                True,
            )
        )
    if agents_path.is_file():
        try:
            agents_text = agents_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            agents_text = ""
        nonblank_lines = sum(1 for line in agents_text.splitlines() if line.strip())
        result.summary["agents_nonblank_lines"] = nonblank_lines
        if nonblank_lines > 300:
            result.findings.append(
                Finding(
                    "PRJ004",
                    "项目执行",
                    "P2",
                    "高",
                    "项目级 AGENTS.md 过长",
                    f"AGENTS.md 有 {nonblank_lines} 行非空内容。",
                    "每次任务都要加载大量规则，关键约束更难被识别。",
                    "删除重复与失效规则；把目录专属要求移动到更近的嵌套 AGENTS.md。",
                    True,
                )
            )
        missing_quality = []
        if not COMMAND_RE.search(agents_text):
            missing_quality.append("可执行命令")
        if not VALIDATION_RE.search(agents_text):
            missing_quality.append("验证或完成标准")
        if missing_quality:
            result.findings.append(
                Finding(
                    "PRJ007",
                    "项目执行",
                    "P2",
                    "中",
                    "项目级 AGENTS.md 缺少执行信息",
                    f"根目录 AGENTS.md 未识别到：{'、'.join(missing_quality)}。",
                    "Codex 仍需要在每次任务中猜测怎样构建、测试或判断完成。",
                    "补充当前项目真实可运行的命令和最小完成条件；不要写无法执行的泛化要求。",
                    True,
                )
            )
        risky = find_high_risk_text(agents_text)
        if risky:
            result.findings.append(
                Finding(
                    "PRJ006",
                    "安全与隐私",
                    "P1",
                    "中",
                    "项目指令中存在高风险执行模式",
                    f"AGENTS.md 命中：{', '.join(risky)}。未输出命令原文。",
                    "这些指令会在后续任务中持续影响 Codex 行为。",
                    "人工审核命中位置，替换为明确目标路径、最小权限和用户确认。",
                    True,
                )
            )

    if len(project_sessions) >= 3 and user_messages >= 15 and correction_rate >= 0.20:
        result.findings.append(
            Finding(
                "PRJ005",
                "项目执行",
                "P1",
                "中",
                "当前项目的返工信号集中",
                f"{len(project_sessions)} 个匹配会话、{user_messages} 条用户消息中有 {corrections} 个返工信号（{correction_rate:.1%}）。",
                "项目知识、技术边界、验收标准或当前方案可能没有稳定下来。",
                "抽查返工最高的 3 个会话，分别标记为需求不清、实现误判、验证缺失或方案方向问题，再决定是否调整技术路线。",
                False,
            )
        )

    return result
