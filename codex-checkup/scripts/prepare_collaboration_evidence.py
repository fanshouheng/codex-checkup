from __future__ import annotations

import argparse
from pathlib import Path

from codex_health.collaboration_evidence import build_collaboration_evidence, write_collaboration_evidence
from codex_health.common import env_codex_home


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="为 Codex 协作诊断生成私有、脱敏的样本与任务清单")
    parser.add_argument("--codex-home", type=Path, default=env_codex_home(), help="Codex 用户目录")
    parser.add_argument("--days", type=int, default=30, help="读取最近天数（1-3650）")
    parser.add_argument("--max-sessions", type=int, default=300, help="最多读取的会话文件数（1-5000）")
    parser.add_argument(
        "--max-samples",
        "--max-incidents",
        dest="max_samples",
        type=int,
        default=12,
        help="最多保留的对照样本数（1-30）；旧参数 --max-incidents 仍可用",
    )
    parser.add_argument(
        "--max-task-samples",
        type=int,
        default=100,
        help="最多保留的脱敏任务开场数（1-500），用于识别重复流程和 Skill 候选",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.cwd() / ".codex-health-private" / "collaboration-evidence.json",
        help="私有证据包输出路径",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not 1 <= args.days <= 3650:
        raise SystemExit("--days 必须在 1 到 3650 之间")
    if not 1 <= args.max_sessions <= 5000:
        raise SystemExit("--max-sessions 必须在 1 到 5000 之间")
    if not 1 <= args.max_samples <= 30:
        raise SystemExit("--max-samples 必须在 1 到 30 之间")
    if not 1 <= args.max_task_samples <= 500:
        raise SystemExit("--max-task-samples 必须在 1 到 500 之间")
    payload = build_collaboration_evidence(
        args.codex_home.expanduser().resolve(strict=False),
        days=args.days,
        max_sessions=args.max_sessions,
        max_samples=args.max_samples,
        max_task_samples=args.max_task_samples,
    )
    output = write_collaboration_evidence(args.output.expanduser().resolve(strict=False), payload)
    print(f"evidence_json={output}")
    print(f"sample_count={payload['sample_count']}")
    print(f"task_inventory_count={len(payload['task_inventory'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
