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
from codex_health.collaboration_evidence import build_collaboration_evidence
from codex_health.model import Finding, ModuleResult
from codex_health.portfolio_audit import audit_portfolio
from codex_health.project_audit import audit_project
from codex_health.report import build_payload, render_markdown
from codex_health.session_audit import SessionDataset, SessionStats, audit_sessions
from codex_health.skill_audit import _has_trigger_cue, audit_skills


class AuditTest(unittest.TestCase):
    def test_audit_contract_defines_engines_evidence_states_and_output(self):
        contract = (SKILL_ROOT / "references" / "audit-contract.md").read_text(encoding="utf-8")
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        for heading in ("交互协作引擎", "Codex 工作台引擎", "项目恢复引擎"):
            self.assertIn(heading, contract)
        for grade in ("`A`", "`B`", "`C`", "`D`", "`U`"):
            self.assertIn(grade, contract)
        for state in (
            "`unknown`",
            "`planned`",
            "`in_progress`",
            "`verification_pending`",
            "`blocked`",
            "`stale`",
            "`completed`",
            "`archived`",
        ):
            self.assertIn(state, contract)
        for section in ("协作诊断", "配置诊断", "项目恢复地图", "建议行动顺序"):
            self.assertIn(section, contract)
        self.assertIn("references/audit-contract.md", skill)

    def test_skill_ui_metadata_is_utf8_and_names_the_skill(self):
        text = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("Codex 全面体检", text)
        self.assertIn("$codex-health-check", text)

    def test_chinese_used_for_phrase_is_a_trigger_cue(self):
        self.assertTrue(_has_trigger_cue("用于开始复杂项目前的方案调研，也用于技术选型请求。"))

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

    def test_config_audits_project_layer_and_marks_unresolved_profiles_partial(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            codex_home = base / "codex"
            project = base / "project"
            (project / ".codex").mkdir(parents=True)
            codex_home.mkdir()
            (codex_home / "config.toml").write_text("sandbox_mode='workspace-write'\n", encoding="utf-8")
            (codex_home / "team.config.toml").write_text("model='fixture'\n", encoding="utf-8")
            (project / ".codex" / "config.toml").write_text(
                "sandbox_mode='danger-full-access'\napproval_policy='never'\n",
                encoding="utf-8",
            )

            result = audit_config(codex_home, project)
            self.assertEqual(["用户级配置", "项目级配置"], result.summary["parsed_sources"])
            self.assertEqual("partial", result.status)
            risky = [item for item in result.findings if item.rule_id == "CFG001"]
            self.assertEqual(1, len(risky))
            self.assertIn("项目级配置", risky[0].evidence)

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

    def test_collaboration_evidence_keeps_context_and_redacts_private_values(self):
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp) / "codex-home"
            project = Path(temp) / "private-project"
            session_dir = codex_home / "sessions" / "2026" / "07" / "14"
            session_dir.mkdir(parents=True)
            project.mkdir()
            rows = [{"type": "session_meta", "payload": {"id": "019f0000-0000-7000-8000-000000000099", "cwd": str(project)}}]

            def add_user(text: str) -> None:
                rows.append({"type": "event_msg", "payload": {"type": "user_message", "message": text}})

            def add_assistant(text: str) -> None:
                rows.append(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": text}],
                        },
                    }
                )

            add_assistant("你准备做什么产品？")
            add_user("我要检查哪些项目路线不对，哪些流程不对")
            add_assistant("请继续说明发布方式。")
            add_user("发布时不要被看出来是这个项目改的")
            add_user("请处理接口，token=super-secret-token-value")
            add_assistant(f"我已经重写全部模块，文件在 {project}。")
            add_user("我说的是只改接口，其他不要改，api_key=another-secret-value")
            add_assistant("已经调整。")
            add_user("你测试了吗？")
            add_assistant("还没有运行测试。")
            add_user("继续")
            add_assistant("我先停在这里等确认。")
            add_user("继续")
            add_assistant("测试已通过。")
            add_user("这次对了")
            add_user("# AGENTS.md instructions\nAPI_TOKEN=must-not-appear")
            session_path = session_dir / "rollout-019f0000-0000-7000-8000-000000000099.jsonl"
            session_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

            smooth_project = Path(temp) / "smooth-project"
            smooth_project.mkdir()
            smooth_rows = [
                {"type": "session_meta", "payload": {"id": "019f0000-0000-7000-8000-000000000100", "cwd": str(smooth_project)}},
                {"type": "event_msg", "payload": {"type": "user_message", "message": "请修复导出按钮并完成验证"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "已完成导出按钮修复，测试和构建均通过，工作区干净。"}],
                    },
                },
            ]
            (session_dir / "rollout-019f0000-0000-7000-8000-000000000100.jsonl").write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in smooth_rows),
                encoding="utf-8",
            )

            incomplete_rows = [
                {"type": "session_meta", "payload": {"id": "019f0000-0000-7000-8000-000000000101", "cwd": str(smooth_project)}},
                {"type": "event_msg", "payload": {"type": "user_message", "message": "请导出最终 MP4"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "时间线已完成，但尚未导出 MP4。"}],
                    },
                },
            ]
            (session_dir / "rollout-019f0000-0000-7000-8000-000000000101.jsonl").write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in incomplete_rows),
                encoding="utf-8",
            )

            skipped_action_rows = [
                {"type": "session_meta", "payload": {"id": "019f0000-0000-7000-8000-000000000102", "cwd": str(smooth_project)}},
                {"type": "event_msg", "payload": {"type": "user_message", "message": "请检查并上传这个 Skill"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "已完成只读核对，但未上传 Skill。"}],
                    },
                },
            ]
            (session_dir / "rollout-019f0000-0000-7000-8000-000000000102.jsonl").write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in skipped_action_rows),
                encoding="utf-8",
            )

            trailing_request_rows = [
                {"type": "session_meta", "payload": {"id": "019f0000-0000-7000-8000-000000000103", "cwd": str(smooth_project)}},
                {"type": "event_msg", "payload": {"type": "user_message", "message": "请整理季度总结"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "季度总结已完成。"}],
                    },
                },
                {"type": "event_msg", "payload": {"type": "user_message", "message": "还要生成 Word 文件"}},
            ]
            (session_dir / "rollout-019f0000-0000-7000-8000-000000000103.jsonl").write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in trailing_request_rows),
                encoding="utf-8",
            )

            payload = build_collaboration_evidence(codex_home, days=30, max_sessions=20, max_samples=12)
            kinds = {item["type"] for item in payload["samples"]}
            self.assertTrue({"scope_control", "verification_gap", "autonomy_calibration", "success_pattern"}.issubset(kinds))
            self.assertIn("successful_completion", kinds)
            self.assertGreaterEqual(payload["sample_class_counts"]["successful"], 2)
            self.assertGreaterEqual(payload["sample_class_counts"]["friction"], 1)
            self.assertTrue(all(sum(1 for message in item["context"] if message["is_signal"]) == 1 for item in payload["samples"]))
            rendered = json.dumps(payload, ensure_ascii=False)
            self.assertTrue(payload["private"])
            self.assertIn("$PROJECT", rendered)
            self.assertNotIn(str(project), rendered)
            self.assertNotIn("super-secret-token-value", rendered)
            self.assertNotIn("another-secret-value", rendered)
            self.assertNotIn("must-not-appear", rendered)
            self.assertNotIn("时间线已完成，但尚未导出 MP4。", rendered)
            self.assertNotIn("已完成只读核对，但未上传 Skill。", rendered)
            self.assertNotIn("还要生成 Word 文件", rendered)
            signal_texts = [
                message["text"]
                for item in payload["samples"]
                for message in item["context"]
                if message["is_signal"]
            ]
            self.assertNotIn("我要检查哪些项目路线不对，哪些流程不对", signal_texts)
            self.assertNotIn("发布时不要被看出来是这个项目改的", signal_texts)

    def test_portfolio_requires_multiple_evidence_families_for_route_review(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "private-project"
            project.mkdir()
            (project / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
            sessions = []
            for index in range(5):
                sessions.append(
                    SessionStats(
                        session_id=f"session-{index}",
                        cwd=str(project),
                        user_messages=10,
                        correction_signals=2,
                        tool_results=10,
                        failed_tool_results=3,
                    )
                )
            result = audit_portfolio(SessionDataset(sessions=sessions))
            rules = {item.rule_id for item in result.findings}
            self.assertTrue({"POR001", "POR002", "POR003", "POR004", "POR005"}.issubset(rules))
            self.assertEqual(1, result.summary["projects_needing_direction_review"])
            route = next(item for item in result.findings if item.rule_id == "POR005")
            self.assertNotIn(str(project), route.evidence)
            self.assertIn("不等于技术路线已被证实错误", route.evidence)

    def test_portfolio_does_not_judge_route_from_one_small_session(self):
        session = SessionStats(
            session_id="small",
            cwd=str(Path("sample-project")),
            user_messages=5,
            correction_signals=2,
        )
        result = audit_portfolio(SessionDataset(sessions=[session]))
        self.assertNotIn("POR005", {item.rule_id for item in result.findings})
        self.assertEqual(0, result.summary["projects_needing_direction_review"])

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

```markdown
[文档示例](FORMS.md)
[地址占位](URL)
```
""",
                encoding="utf-8",
            )
            result = audit_skills(Path(temp) / "codex", Path(temp) / "project", roots=[root])
            rules = {item.rule_id for item in result.findings}
            self.assertIn("SKL002", rules)
            self.assertIn("SKL003", rules)
            self.assertIn("SKL006", rules)
            self.assertNotIn("SKL007", rules)
            broken = next(item for item in result.findings if item.rule_id == "SKL006")
            self.assertIn("1 个断链资源", broken.evidence)

    def test_report_priorities_show_distinct_problem_categories(self):
        duplicate = [
            Finding("A1", "Skills", "P1", "高", "同一问题", "证据一", "影响", "建议一"),
            Finding("A2", "Skills", "P1", "高", "同一问题", "证据二", "影响", "建议二"),
            Finding("B1", "配置", "P2", "高", "另一问题", "证据三", "影响", "建议三"),
        ]
        module = ModuleResult(name="skills", findings=duplicate)
        payload = build_payload(
            {"days": 30, "project": "$PROJECT", "codex_home": "$CODEX_HOME"},
            [module],
        )
        markdown = render_markdown(payload)
        priority_section = markdown.split("## 优先处理", 1)[1].split("## 全部发现", 1)[0]
        self.assertEqual(1, priority_section.count("同一问题"))
        self.assertIn("另一问题", priority_section)

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
                    "config,sessions,portfolio,project",
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
            self.assertIn('"module": "portfolio"', combined)


if __name__ == "__main__":
    unittest.main()
