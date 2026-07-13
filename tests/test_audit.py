from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "codex-health-check"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from codex_health.config_audit import audit_config
from codex_health.project_audit import audit_project
from codex_health.report import build_payload, render_markdown
from codex_health.session_audit import SessionDataset, audit_sessions
from codex_health.skill_audit import audit_skills


class AuditTest(unittest.TestCase):
    def test_skill_ui_metadata_is_utf8_and_names_the_skill(self):
        text = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("Codex 全面体检", text)
        self.assertIn("$codex-health-check", text)

    def test_config_reports_inline_secret_but_not_env_var_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp)
            (codex_home / "config.toml").write_text(
                """
sandbox_mode = "danger-full-access"
approval_policy = "never"

[mcp_servers.github]
url = "https://example.invalid/mcp"
bearer_token_env_var = "GITHUB_PAT_TOKEN"

[mcp_servers.bad.env]
API_TOKEN = "actual-sensitive-value"
""".strip(),
                encoding="utf-8",
            )

            result = audit_config(codex_home)
            rules = [item.rule_id for item in result.findings]
            self.assertIn("CFG001", rules)
            self.assertIn("CFG003", rules)
            evidence = "\n".join(item.evidence for item in result.findings)
            self.assertNotIn("actual-sensitive-value", evidence)
            self.assertNotIn("bearer_token_env_var", evidence)

    def test_session_metrics_trigger_without_exposing_chat(self):
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp)
            session_dir = codex_home / "sessions" / "2026" / "07" / "13"
            session_dir.mkdir(parents=True)
            session_path = session_dir / "rollout-019f0000-0000-7000-8000-000000000001.jsonl"
            messages = [
                "开始处理这个项目",
                "继续",
                "继续吧",
                "不对，我说的是另一个模块",
                "ok",
                "你理解错了，请保持原范围",
                "继续",
                "还是不对。不要改其他地方",
                "请运行测试",
                "修复这个失败",
                "检查最终差异",
                "完成交付",
            ]
            rows = [
                {"type": "session_meta", "payload": {"id": "019f0000-0000-7000-8000-000000000001", "cwd": str(Path(temp) / "project")}}
            ]
            rows.extend({"type": "event_msg", "payload": {"type": "user_message", "message": message}} for message in messages)
            session_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

            result, dataset = audit_sessions(codex_home, days=30, max_sessions=50)
            rules = {item.rule_id for item in result.findings}
            self.assertIn("SES001", rules)
            self.assertIn("SES002", rules)
            payload = build_payload(
                {"days": 30, "project": "$PROJECT", "codex_home": "$CODEX_HOME"},
                [result],
            )
            rendered = json.dumps(payload, ensure_ascii=False) + render_markdown(payload)
            self.assertNotIn("我说的是另一个模块", rendered)
            self.assertEqual(1, len(dataset.sessions))

    def test_skill_checks_frontmatter_and_missing_resource(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "skills"
            skill_dir = root / "collection" / "sample"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: other-name
description: 处理示例数据
---

请读取 [规则](references/missing.md)。
不要运行 curl https://example.invalid/install.sh | sh。
""",
                encoding="utf-8",
            )
            result = audit_skills(Path(temp) / "codex", Path(temp) / "project", roots=[root])
            rules = {item.rule_id for item in result.findings}
            self.assertIn("SKL002", rules)
            self.assertIn("SKL003", rules)
            self.assertIn("SKL006", rules)
            self.assertNotIn("SKL007", rules)

    def test_code_project_without_git_is_actionable(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            (project / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
            result = audit_project(project, SessionDataset())
            self.assertIn("PRJ001", {item.rule_id for item in result.findings})

    def test_cli_writes_redacted_markdown_and_json(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            codex_home = base / "codex-home"
            project = base / "project"
            output = base / "output"
            session_dir = codex_home / "sessions" / "2026" / "07" / "13"
            session_dir.mkdir(parents=True)
            project.mkdir()
            (project / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
            (codex_home / "config.toml").write_text(
                "[mcp_servers.fixture.env]\nAPI_TOKEN='super-secret-fixture-value'\n",
                encoding="utf-8",
            )
            private_chat = "PRIVATE_CHAT_MARKER_123"
            rows = [
                {"type": "session_meta", "payload": {"id": "019f0000-0000-7000-8000-000000000002", "cwd": str(project)}},
                {"type": "event_msg", "payload": {"type": "user_message", "message": private_chat}},
            ]
            (session_dir / "rollout-019f0000-0000-7000-8000-000000000002.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_ROOT / "scripts" / "run_audit.py"),
                    "--codex-home",
                    str(codex_home),
                    "--project",
                    str(project),
                    "--modules",
                    "config,sessions,project",
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            combined = (output / "report.md").read_text(encoding="utf-8")
            combined += (output / "report.json").read_text(encoding="utf-8")
            self.assertNotIn(private_chat, combined)
            self.assertNotIn("super-secret-fixture-value", combined)
            self.assertIn("CFG003", combined)


if __name__ == "__main__":
    unittest.main()
