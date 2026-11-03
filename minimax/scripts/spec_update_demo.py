"""规格变更演示。

流程：导入当前规格 → 检索上下文 → 修改一条规格并重新导入 → 再次检索上下文，
对比两次输出，说明模块使用的是数据库中的最新规格内容，而不是固定答案。

演示在 `.data/spec-update-demo/` 下操作示例工程的副本，最后会把数据库恢复成
示例工程当前的内容。
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
TASK_TEXT = "为 OrderRepository.list_orders 增加按订单状态筛选的能力，规格见 SPEC-ORD-003。"

NEW_RULE = """
## SPEC-ORD-003-R5 筛选的作用范围

`status` 筛选只作用于 `list_orders`，不影响 `find_by_id` 的查询结果。
"""


def _run(command: list[str], env: dict[str, str], project: Path | None = None) -> dict[str, object]:
    args = list(command)
    if project is not None:
        # 全局选项要放在子命令之前：python -m sddctx --project <dir> import
        args = [args[0], *args[1:3], "--project", str(project), *args[3:]]
    completed = subprocess.run(
        args,
        cwd=MINIMAX_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return {
        "command": " ".join(args),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="规格变更演示")
    parser.add_argument("--evidence-dir", type=Path, default=None)
    args = parser.parse_args()

    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
    evidence_dir = (args.evidence_dir or MINIMAX_ROOT / "evidence" / "spec-update" / stamp).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(MINIMAX_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    # 演示使用独立库，演示用的新增规格不会留在正式工程资料里
    env["SDDCTX_DB_NAME"] = "sdd_context_spec_demo"

    python = sys.executable
    source_project = MINIMAX_ROOT / "sample_project"
    scratch = MINIMAX_ROOT / ".data" / "spec-update-demo" / "project"
    if scratch.exists():
        shutil.rmtree(scratch)
    shutil.copytree(source_project, scratch, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"))

    steps: list[dict[str, object]] = []

    def record(name: str, result: dict[str, object]) -> None:
        steps.append({"step": name, **result})
        (evidence_dir / f"{len(steps):02d}-{name}.log").write_text(
            f"$ {result['command']}\n\n{result['stdout']}\n{result['stderr']}", encoding="utf-8"
        )
        print(f"[{name}] exit={result['exit_code']}")

    record("import-baseline", _run([python, "-m", "sddctx", "import"], env))
    record(
        "context-before",
        _run(
            [python, "-m", "sddctx", "context", "--task-id", "T-SPEC-BASE", "--task", TASK_TEXT],
            env,
        ),
    )

    spec_path = scratch / "specs" / "SPEC-ORD-003-order-status-filter.md"
    spec_path.write_text(spec_path.read_text(encoding="utf-8").rstrip() + "\n" + NEW_RULE, encoding="utf-8")
    record("import-updated", _run([python, "-m", "sddctx", "import"], env, project=scratch))
    record(
        "context-after",
        _run(
            [python, "-m", "sddctx", "context", "--task-id", "T-SPEC-UPDATE", "--task", TASK_TEXT],
            env,
        ),
    )

    record("import-restore", _run([python, "-m", "sddctx", "import"], env))

    before = json.loads(
        (MINIMAX_ROOT / ".data" / "context" / "T-SPEC-BASE.json").read_text(encoding="utf-8")
    )
    after = json.loads(
        (MINIMAX_ROOT / ".data" / "context" / "T-SPEC-UPDATE.json").read_text(encoding="utf-8")
    )
    before_ids = [rule["rule_id"] for rule in before["specs"]]
    after_ids = [rule["rule_id"] for rule in after["specs"]]
    comparison = {
        "before_rule_ids": before_ids,
        "after_rule_ids": after_ids,
        "added_rule_ids": sorted(set(after_ids) - set(before_ids)),
        "updated": sorted(set(after_ids) - set(before_ids)) != [],
    }
    (evidence_dir / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {
        "generated_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "evidence_dir": evidence_dir.as_posix(),
        "comparison": comparison,
        "steps": [{key: step[key] for key in ("step", "command", "exit_code")} for step in steps],
    }
    (evidence_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
