from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "codex-checkup"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from codex_health.config_audit import audit_config
from codex_health.collaboration_evidence import _redact, build_collaboration_evidence
from codex_health.model import Finding, ModuleResult
from codex_health.portfolio_audit import _group_projects, audit_portfolio
from codex_health.project_audit import audit_project
from codex_health.report import build_payload, render_markdown
from codex_health.session_audit import SessionDataset, SessionStats, audit_sessions
from codex_health.skill_audit import _has_trigger_cue, audit_skills


class AuditTest(unittest.TestCase):
    def test_practice_network_has_sources_routes_and_skill_integration(self):
        network = (SKILL_ROOT / "references" / "codex-practice-network.md").read_text(encoding="utf-8")
        contract = (SKILL_ROOT / "references" / "audit-contract.md").read_text(encoding="utf-8")
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        for index in range(1, 21):
            self.assertIn(f"PRA{index:03d}", network)
        for source in ("O01", "O08", "X01", "X03", "X11"):
            self.assertIn(source, network)
        self.assertIn("https://x.com/", network)
        self.assertIn("official-confirmed", network)
        self.assertIn("高传播万能模板反例", network)
        self.assertIn("references/codex-practice-network.md", skill)
        self.assertIn("practice_refs", contract)
        self.assertIn("recommendation_basis", contract)

    def test_skill_defines_remediation_resume_and_retest_handoff(self):
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        contract = (SKILL_ROOT / "references" / "audit-contract.md").read_text(encoding="utf-8")
        remediation = (SKILL_ROOT / "references" / "remediation.md").read_text(encoding="utf-8")
        metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")

        for phrase in ("继续整改", "修复第几项", "复测优化效果"):
            self.assertIn(phrase, skill.split("---", 2)[1])
        for phrase in ("体检模式", "整改模式", "复测模式", "不要在报告链接处结束回复"):
            self.assertIn(phrase, skill)
        for phrase in (
            ".codex-health-private/remediation-state.json",
            '"status": "pending"',
            '"retest_outcome": "not_run"',
            "开始第 1 项",
            "只优化配置",
            "只恢复未完成项目",
        ):
            self.assertIn(phrase, remediation)
        self.assertIn("整改执行", contract)
        self.assertIn("继续整改入口", metadata)

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
        self.assertIn("Codex 全景体检", text)
        self.assertIn("$codex-checkup", text)

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

    def test_session_coverage_is_partial_for_unparsed_or_truncated_files(self):
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp)
            session_dir = codex_home / "sessions"
            session_dir.mkdir()
            for index in range(2):
                row = {
                    "type": "session_meta",
                    "payload": {
                        "id": f"019f0000-0000-7000-8000-00000000001{index}",
                        "cwd": str(codex_home / f"project-{index}"),
                    },
                }
                (session_dir / f"rollout-019f0000-0000-7000-8000-00000000001{index}.jsonl").write_text(
                    json.dumps(row), encoding="utf-8"
                )
            (session_dir / "broken.jsonl").write_text("{not-json", encoding="utf-8")

            unparsed_result, unparsed_data = audit_sessions(codex_home, days=30, max_sessions=10)
            self.assertEqual("partial", unparsed_result.status)
            self.assertEqual(3, unparsed_data.files_available)
            self.assertEqual(1, unparsed_data.files_unparsed)

            truncated_result, truncated_data = audit_sessions(codex_home, days=30, max_sessions=1)
            self.assertEqual("partial", truncated_result.status)
            self.assertTrue(truncated_data.truncated)
            self.assertEqual(3, truncated_result.summary["session_files_available"])

    def test_session_records_confirmed_skill_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp)
            session_dir = codex_home / "sessions"
            session_dir.mkdir()
            rows = [
                {
                    "type": "session_meta",
                    "payload": {"id": "019f0000-0000-7000-8000-000000000020", "cwd": str(codex_home)},
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "shell_command",
                        "arguments": json.dumps(
                            {"command": str(codex_home / "skills" / "codex-checkup" / "SKILL.md")}
                        ),
                    },
                },
            ]
            path = session_dir / "rollout-019f0000-0000-7000-8000-000000000020.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            result, _ = audit_sessions(codex_home, days=30, max_sessions=10)
            self.assertEqual(["codex-checkup"], result.summary["confirmed_skill_refs"])

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
                        "type": "function_call",
                        "name": "shell_command",
                        "call_id": "verify-1",
                        "arguments": json.dumps({"command": "python -m unittest"}),
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "verify-1",
                        "output": "Exit code: 0\nRan 4 tests - OK",
                    },
                },
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

    def test_unverified_assistant_completion_is_not_a_success_sample(self):
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp) / "codex"
            session_dir = codex_home / "sessions"
            session_dir.mkdir(parents=True)
            rows = [
                {
                    "type": "session_meta",
                    "payload": {"id": "019f0000-0000-7000-8000-000000000030", "cwd": str(Path(temp) / "p")},
                },
                {"type": "event_msg", "payload": {"type": "user_message", "message": "请修复并测试"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "shell_command",
                        "call_id": "verify-failed",
                        "arguments": json.dumps({"command": "python -m unittest"}),
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "verify-failed",
                        "output": "Exit code: 1\n1 failed, 3 passed",
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "已完成，测试和构建均通过。"}],
                    },
                },
            ]
            path = session_dir / "rollout-019f0000-0000-7000-8000-000000000030.jsonl"
            path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

            payload = build_collaboration_evidence(codex_home, days=30, max_sessions=10, max_samples=5)
            self.assertNotIn("successful_completion", {item["type"] for item in payload["samples"]})

    def test_private_evidence_redacts_pem_jwt_bearer_and_database_url(self):
        fixture = (
            "-----BEGIN OPENSSH PRIVATE KEY----- AAAAB3NzaFixture -----END OPENSSH PRIVATE KEY----- "
            "Bearer abcdefghijklmnopqrstuvwxyz123456 "
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaXh0dXJlIn0.signature123456 "
            "postgresql://admin:super-secret@example.invalid/db"
        )
        redacted = _redact(fixture, Path.home() / ".codex", "")
        self.assertNotIn("OPENSSH PRIVATE KEY", redacted)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", redacted)
        self.assertNotIn("eyJhbGci", redacted)
        self.assertNotIn("super-secret", redacted)

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

    def test_portfolio_groups_subdirectories_by_git_root(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            api = project / "api"
            web = project / "web"
            api.mkdir(parents=True)
            web.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            dataset = SessionDataset(
                sessions=[SessionStats("api", cwd=str(api)), SessionStats("web", cwd=str(web))]
            )
            result = audit_portfolio(dataset)
            self.assertEqual(1, result.summary["projects_seen"])

    def test_portfolio_caches_project_normalization_for_repeated_cwd(self):
        cwd = str(Path("repeated-project"))
        sessions = [SessionStats("first", cwd=cwd), SessionStats("second", cwd=cwd)]

        with patch(
            "codex_health.portfolio_audit.canonical_project_path",
            side_effect=lambda path: path.resolve(strict=False),
        ) as normalize:
            projects = _group_projects(sessions)

        self.assertEqual(1, len(projects))
        self.assertEqual(1, normalize.call_count)

    def test_portfolio_is_partial_when_project_directory_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            available = Path(temp) / "available"
            available.mkdir()
            missing = Path(temp) / "missing"
            dataset = SessionDataset(
                sessions=[SessionStats("available", cwd=str(available)), SessionStats("missing", cwd=str(missing))]
            )

            result = audit_portfolio(dataset)

            self.assertEqual("partial", result.status)
            self.assertEqual(2, result.summary["projects_seen"])
            self.assertEqual(1, result.summary["projects_evaluated"])
            self.assertEqual(1, result.summary["projects_unavailable"])
            self.assertEqual(0, result.summary["projects_skipped_by_limit"])
            self.assertNotIn(str(missing), " ".join(result.notes))

    def test_portfolio_reports_existing_projects_skipped_by_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first"
            second = Path(temp) / "second"
            first.mkdir()
            second.mkdir()
            dataset = SessionDataset(
                sessions=[SessionStats("first", cwd=str(first)), SessionStats("second", cwd=str(second))]
            )

            result = audit_portfolio(dataset, max_filesystem_projects=1)

            self.assertEqual("partial", result.status)
            self.assertEqual(1, result.summary["projects_evaluated"])
            self.assertEqual(0, result.summary["projects_unavailable"])
            self.assertEqual(1, result.summary["projects_skipped_by_limit"])

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

    def test_skill_security_scan_ignores_pattern_definitions_but_flags_real_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "skills"
            skill_dir = root / "security-check"
            scripts = skill_dir / "scripts"
            scripts.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: security-check\ndescription: 当用户要求安全检查时使用。\n---\n",
                encoding="utf-8",
            )
            detector = scripts / "detector.py"
            detector.write_text(
                'PATTERN = re.compile(r"\\b(?:cat|read|open)\\b.*(?:\\.env|private_key)")\n',
                encoding="utf-8",
            )

            clean = audit_skills(Path(temp) / "codex", Path(temp) / "project", roots=[root])
            self.assertNotIn("SKL007", {item.rule_id for item in clean.findings})

            detector.write_text('with open(".env") as handle:\n    value = handle.read()\n', encoding="utf-8")
            risky = audit_skills(Path(temp) / "codex", Path(temp) / "project", roots=[root])
            self.assertIn("SKL007", {item.rule_id for item in risky.findings})

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
        required = {
            "finding_id",
            "engine",
            "evidence_grade",
            "coverage",
            "practice_refs",
            "recommendation_basis",
            "placement",
            "approval_required",
            "verification",
        }
        self.assertTrue(required.issubset(payload["findings"][0]))

    def test_code_project_without_git_is_actionable(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            (project / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
            result = audit_project(project, SessionDataset())
            self.assertIn("PRJ001", {item.rule_id for item in result.findings})

    def test_project_inventory_covers_user_project_and_nested_agents(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            codex_home = base / "codex"
            project = base / "project"
            nested = project / "packages" / "api"
            codex_home.mkdir()
            nested.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            (codex_home / "AGENTS.md").write_text("全局规则", encoding="utf-8")
            (project / "AGENTS.md").write_text("运行 pytest\n完成前验证测试", encoding="utf-8")
            (nested / "AGENTS.md").write_text("仅适用于 API", encoding="utf-8")
            (project / "TODO.md").write_text("TODO: add release test", encoding="utf-8")

            result = audit_project(project, SessionDataset(), codex_home)
            scopes = {item["scope"] for item in result.summary["agents_inventory"]}
            self.assertEqual({"user", "project", "nested"}, scopes)
            self.assertIn("TODO.md", result.summary["recovery_facts"]["plan_files"])
            self.assertEqual("in_progress", result.summary["recovery_facts"]["evidence_state"])

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
