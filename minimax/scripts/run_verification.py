"""端到端验证样例。

按顺序执行：导入工程资料 → 检索上下文 → 写入任务状态 → 修改前跑目标测试 →
MiniMax 完成代码改动 → 修改后跑目标测试与回归测试 → 重启后从 PowerContext 读回状态。

每一步的命令、退出码与输出都写入 `evidence/verification/<时间戳>/` 下，
便于逐条复核。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

MINIMAX_ROOT = Path(__file__).resolve().parent.parent
TASK_ID = "T-001"
TASK_TEXT = (
    "为 OrderRepository.list_orders 增加按订单状态筛选的能力，"
    "支持状态枚举与状态字符串（忽略大小写），非法状态抛出 ValueError，"
    "无匹配时返回空列表。规格见 SPEC-ORD-003。"
)


def _run(command: list[str], cwd: Path, env: dict[str, str]) -> dict[str, object]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return {
        "command": " ".join(command),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _collect_artifacts(evidence_dir: Path) -> None:
    """把 pytest 原始日志与模型请求/响应归档到证据目录。"""
    test_logs = MINIMAX_ROOT / ".data" / "test-logs"
    if test_logs.exists():
        target = evidence_dir / "pytest-logs"
        target.mkdir(exist_ok=True)
        for path in sorted(test_logs.glob("*.log")):
            shutil.copy2(path, target / path.name)

    agent_dir = MINIMAX_ROOT / ".data" / "agent" / TASK_ID
    if agent_dir.exists():
        target = evidence_dir / "minimax"
        target.mkdir(exist_ok=True)
        for path in sorted(agent_dir.iterdir()):
            if path.is_file():
                shutil.copy2(path, target / path.name)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行端到端验证样例")
    parser.add_argument("--skip-agent", action="store_true", help="跳过 MiniMax 代码改动步骤")
    parser.add_argument(
        "--agent-output",
        type=Path,
        default=None,
        help="模型回复文件；提供时由该文件提供改动，不调用 MiniMax 开放接口",
    )
    parser.add_argument("--evidence-dir", type=Path, default=None)
    args = parser.parse_args()

    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
    evidence_dir = (args.evidence_dir or MINIMAX_ROOT / "evidence" / "verification" / stamp).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(MINIMAX_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"

    python = sys.executable
    steps: list[dict[str, object]] = []

    def record(name: str, result: dict[str, object]) -> None:
        steps.append({"step": name, **result})
        (evidence_dir / f"{len(steps):02d}-{name}.log").write_text(
            f"$ {result['command']}\n\n{result['stdout']}\n{result['stderr']}", encoding="utf-8"
        )
        print(f"[{name}] exit={result['exit_code']}")

    record("doctor", _run([python, "-m", "sddctx", "doctor"], MINIMAX_ROOT, env))
    record("import", _run([python, "-m", "sddctx", "import"], MINIMAX_ROOT, env))
    record(
        "context",
        _run([python, "-m", "sddctx", "context", "--task-id", TASK_ID, "--task", TASK_TEXT], MINIMAX_ROOT, env),
    )
    record(
        "state-save-context",
        _run([python, "-m", "sddctx", "state", "save", "--task-id", TASK_ID], MINIMAX_ROOT, env),
    )
    record(
        "tests-before",
        _run(
            [python, "-m", "sddctx", "verify", "--task-id", TASK_ID, "--label", "before"],
            MINIMAX_ROOT,
            env,
        ),
    )

    if not args.skip_agent:
        agent_command = [python, "-m", "sddctx", "agent", "--task-id", TASK_ID]
        if args.agent_output:
            agent_command += ["--from-file", str(args.agent_output.resolve())]
        record("minimax-agent", _run(agent_command, MINIMAX_ROOT, env))

    record(
        "tests-after",
        _run(
            [python, "-m", "sddctx", "verify", "--task-id", TASK_ID, "--label", "after"],
            MINIMAX_ROOT,
            env,
        ),
    )
    record(
        "state-read-back",
        _run([python, "-m", "sddctx", "state", "show", "--task-id", TASK_ID], MINIMAX_ROOT, env),
    )

    _collect_artifacts(evidence_dir)

    summary = {
        "task_id": TASK_ID,
        "task": TASK_TEXT,
        "generated_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "evidence_dir": evidence_dir.as_posix(),
        "steps": [
            {key: step[key] for key in ("step", "command", "exit_code")} for step in steps
        ],
    }
    (evidence_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
