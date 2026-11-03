"""PowerContext 服务重启演示。

流程：读取任务状态 → 停止本地 PowerContext 服务 → 用同一数据目录重新启动服务 →
再次读取任务状态，验证任务状态确实保存在服务之外的持久化存储中，重启后仍可复用。

证据写入 `evidence/powercontext/<时间戳>/restart-demo.json`。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

MINIMAX_ROOT = Path(__file__).resolve().parent.parent
TASK_ID = "T-001"


def _port_open(host: str, port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex((host, port)) == 0


def _read_state(env: dict[str, str]) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "sddctx", "state", "show", "--task-id", TASK_ID],
        cwd=MINIMAX_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    payload: dict[str, object] = {"exit_code": completed.returncode}
    try:
        payload["state"] = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload["stdout"] = completed.stdout
    return payload


def _stop_service() -> list[int]:
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" |"
        "Where-Object { $_.CommandLine -like '*start_powercontext.py*' } |"
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }"
    )
    completed = subprocess.run(
        [shutil.which("powershell") or "powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return [int(line.strip()) for line in completed.stdout.split() if line.strip().isdigit()]


def _start_service(data_dir: Path, port: int) -> subprocess.Popen[bytes]:
    log = (MINIMAX_ROOT / ".data" / "powercontext-server.log").open("ab")
    return subprocess.Popen(
        [sys.executable, "scripts/start_powercontext.py", "--port", str(port), "--data", str(data_dir)],
        cwd=MINIMAX_ROOT,
        stdout=log,
        stderr=log,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="PowerContext 服务重启演示")
    parser.add_argument("--port", type=int, default=8811)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--data", type=Path, default=MINIMAX_ROOT / ".data" / "powercontext")
    parser.add_argument("--evidence-dir", type=Path, default=None)
    args = parser.parse_args()

    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
    evidence_dir = (args.evidence_dir or MINIMAX_ROOT / "evidence" / "powercontext" / stamp).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(MINIMAX_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"

    before = _read_state(env)
    stopped = _stop_service()
    for _ in range(30):
        if not _port_open(args.host, args.port):
            break
        time.sleep(0.5)
    stopped_ok = not _port_open(args.host, args.port)

    process = _start_service(args.data.resolve(), args.port)
    started_ok = False
    for _ in range(120):
        if _port_open(args.host, args.port):
            started_ok = True
            break
        time.sleep(0.5)

    after = _read_state(env) if started_ok else {"exit_code": -1, "error": "服务未在预期时间内启动"}

    before_state = before.get("state") if isinstance(before.get("state"), dict) else {}
    after_state = after.get("state") if isinstance(after.get("state"), dict) else {}
    demo = {
        "generated_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "task_id": TASK_ID,
        "data_dir": args.data.resolve().as_posix(),
        "port": args.port,
        "stopped_pids": stopped,
        "stopped_ok": stopped_ok,
        "restarted_ok": started_ok,
        "restarted_pid": process.pid,
        "before_restart": before,
        "after_restart": after,
        "state_found_after_restart": bool(after_state.get("found")),
        "same_task": before_state.get("task_id") == after_state.get("task_id") == TASK_ID,
        "specs_preserved": before_state.get("specs") == after_state.get("specs"),
        "code_preserved": before_state.get("code") == after_state.get("code"),
        "target_tests_preserved": before_state.get("target_tests") == after_state.get("target_tests"),
    }
    (evidence_dir / "restart-demo.json").write_text(
        json.dumps(demo, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(demo, ensure_ascii=False, indent=2))
    return 0 if demo["state_found_after_restart"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
