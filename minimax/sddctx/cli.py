"""命令行入口。

    python -m sddctx import
    python -m sddctx context --task-id T-001 --task "..."
    python -m sddctx state save|show --task-id T-001
    python -m sddctx agent --task-id T-001
    python -m sddctx verify --task-id T-001
    python -m sddctx doctor
    python -m sddctx stats
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sddctx.agent import MiniMaxAgent
from sddctx.code import (
    collect_source_files,
    collect_test_files,
    extract_symbols,
    extract_tests,
    symbol_fingerprint,
)
from sddctx.config import Settings
from sddctx.models import TaskState
from sddctx.retrieval import build_context
from sddctx.specs import load_specs
from sddctx.state import LocalStateStore, PowerContextStateStore
from sddctx.store import SeekDBStore


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _settings(args: argparse.Namespace) -> Settings:
    project_dir = Path(args.project).resolve() if getattr(args, "project", None) else None
    return Settings.from_env(project_dir)


def _context_path(settings: Settings, task_id: str) -> Path:
    directory = settings.data_dir / "context"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{task_id}.json"


# -- import -----------------------------------------------------------------


def _collect_materials(project_dir: Path) -> tuple[list[Any], list[Any], list[Any]]:
    """解析工程目录下的规格规则、代码符号与测试用例。"""
    spec_rules: list[Any] = []
    for document in load_specs(project_dir / "specs", project_dir):
        spec_rules.extend(document.to_rules())

    symbols: list[Any] = []
    for path in collect_source_files(project_dir):
        symbols.extend(extract_symbols(path, project_dir))
    known = {symbol.symbol for symbol in symbols} | {
        symbol.symbol.split(".", 1)[-1] for symbol in symbols
    }

    tests: list[Any] = []
    for path in collect_test_files(project_dir):
        tests.extend(extract_tests(path, project_dir, known))
    return spec_rules, symbols, tests


def _import_records(
    spec_changes: list[tuple[str, str]],
    spec_rules: list[Any],
    code_changes: list[tuple[str, str]],
    symbols: list[Any],
    test_changes: list[tuple[str, str]],
    tests: list[Any],
    removed: dict[str, list[str]],
) -> list[tuple[str, str, str, str]]:
    """把本次导入的逐条变更整理成批次记录。"""
    records: list[tuple[str, str, str, str]] = []
    for (key, change), rule in zip(spec_changes, spec_rules, strict=True):
        records.append(("spec_rule", key, rule.content_hash, change))
    for (key, change), symbol in zip(code_changes, symbols, strict=True):
        records.append(("code_symbol", key, symbol_fingerprint(symbol), change))
    for (key, change), test in zip(test_changes, tests, strict=True):
        records.append(("test_case", key, _test_hash(test), change))
    for kind, keys in removed.items():
        for key in keys:
            records.append((kind, key, "", "removed"))
    return records


def command_import(args: argparse.Namespace) -> int:
    settings = _settings(args)
    project_dir = settings.project_dir
    if not project_dir.exists():
        print(f"工程目录不存在：{project_dir}", file=sys.stderr)
        return 2

    batch_id = args.batch_id or f"batch-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    spec_rules, symbols, tests = _collect_materials(project_dir)

    with SeekDBStore(settings.database) as store:
        store.ensure_schema()
        spec_changes = store.upsert_spec_rules(spec_rules)
        code_changes = store.upsert_code_symbols(symbols)
        test_changes = store.upsert_test_cases(tests)
        removed = {
            "spec_rule": store.purge_stale_spec_rules(spec_rules),
            "code_symbol": store.purge_stale_code_symbols(symbols),
            "test_case": store.purge_stale_test_cases(tests),
        }
        records = _import_records(
            spec_changes, spec_rules, code_changes, symbols, test_changes, tests, removed
        )

        store.record_batch(
            batch_id,
            project_dir.as_posix(),
            len(spec_rules),
            len(symbols),
            len(tests),
            records,
        )
        counts = store.counts()

    summary = {
        "batch_id": batch_id,
        "source_dir": project_dir.as_posix(),
        "specs": len(spec_rules),
        "code_symbols": len(symbols),
        "test_cases": len(tests),
        "removed": removed,
        "changed": sorted(
            {change for _key, change in spec_changes + code_changes + test_changes if change != "unchanged"}
            | ({"removed"} if any(removed.values()) else set())
        ),
        "counts": counts,
        "at": _now(),
    }
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "import-latest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _test_hash(test: Any) -> str:
    digest = hashlib.sha256()
    digest.update(f"{test.file}\x1f{test.name}\x1f{test.body}".encode())
    return digest.hexdigest()


# -- context ----------------------------------------------------------------


def command_context(args: argparse.Namespace) -> int:
    settings = _settings(args)
    local_state = LocalStateStore(settings.data_dir)
    power_state = PowerContextStateStore(settings.powercontext)

    state = local_state.load(args.task_id) or TaskState(task_id=args.task_id)
    state.task_id = args.task_id
    state.title = args.title or state.title
    state.description = args.task or state.description

    with SeekDBStore(settings.database) as store:
        bundle = build_context(store, task_id=args.task_id, task=state.description)

    path = _context_path(settings, args.task_id)
    path.write_text(json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path = path.with_suffix(".md")
    markdown_path.write_text(bundle.to_markdown(), encoding="utf-8")

    state.specs = [f"{rule.spec_id}::{rule.rule_id}" for rule in bundle.specs]
    state.code = [symbol.qualified for symbol in bundle.code]
    state.target_tests = [test.qualified for test in bundle.target_tests]
    state.regression_tests = [test.qualified for test in bundle.regression_tests]
    state.context_path = path.as_posix()
    state.stage = "context_ready"
    state.done = [
        f"导入并检索工程资料（{len(bundle.specs)} 条规格规则、"
        f"{len(bundle.code)} 个代码符号、{len(bundle.target_tests)} 条目标测试）"
    ]
    state.todo = ["按规格实现代码改动", "运行目标测试与回归测试", "记录验证结果"]
    state.updated_at = _now()

    local_state.save(state)
    saved = power_state.save(state)

    print(json.dumps(
        {
            "task_id": state.task_id,
            "specs": state.specs,
            "code": state.code,
            "target_tests": state.target_tests,
            "regression_tests": state.regression_tests,
            "context": path.as_posix(),
            "context_markdown": markdown_path.as_posix(),
            "powercontext": saved,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


# -- state ------------------------------------------------------------------


def command_state_save(args: argparse.Namespace) -> int:
    settings = _settings(args)
    local_state = LocalStateStore(settings.data_dir)
    power_state = PowerContextStateStore(settings.powercontext)
    state = local_state.load(args.task_id)
    if state is None:
        print(f"本地没有任务 {args.task_id} 的状态，请先执行 context 命令。", file=sys.stderr)
        return 2
    if args.stage:
        state.stage = args.stage
    saved = power_state.save(state)
    print(json.dumps({"task_id": state.task_id, "stage": state.stage, "powercontext": saved},
                     ensure_ascii=False, indent=2))
    return 0


def command_state_show(args: argparse.Namespace) -> int:
    settings = _settings(args)
    power_state = PowerContextStateStore(settings.powercontext)
    state = power_state.load(args.task_id)
    if state is None:
        print(json.dumps({"task_id": args.task_id, "found": False}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"found": True, "source": "powercontext", **state.to_dict()},
                     ensure_ascii=False, indent=2))
    return 0


# -- agent ------------------------------------------------------------------


def command_agent(args: argparse.Namespace) -> int:
    settings = _settings(args)
    local_state = LocalStateStore(settings.data_dir)
    power_state = PowerContextStateStore(settings.powercontext)
    state = local_state.load(args.task_id)
    if state is None:
        print(f"本地没有任务 {args.task_id} 的状态，请先执行 context 命令。", file=sys.stderr)
        return 2

    context_file = _context_path(settings, args.task_id)
    if not context_file.exists():
        print(f"缺少上下文文件 {context_file}，请先执行 context 命令。", file=sys.stderr)
        return 2
    payload = json.loads(context_file.read_text(encoding="utf-8"))

    from sddctx.models import CodeSymbol, SpecRule, TestCase
    from sddctx.retrieval import ContextBundle

    bundle = ContextBundle(
        task_id=payload["task_id"],
        task=payload["task"],
        generated_at=payload.get("generated_at", ""),
        specs=[SpecRule(**{k: v for k, v in item.items() if k != "score"}) for item in payload["specs"]],
        code=[CodeSymbol(**{k: v for k, v in item.items() if k != "score"}) for item in payload["code"]],
        target_tests=[TestCase(**{k: v for k, v in item.items() if k != "score"}) for item in payload["target_tests"]],
        regression_tests=[
            TestCase(**{k: v for k, v in item.items() if k != "score"}) for item in payload["regression_tests"]
        ],
        tokens=payload.get("tokens", []),
        notes=payload.get("notes", []),
    )

    files = args.files or _candidate_files(bundle, settings.project_dir)
    agent = MiniMaxAgent(settings.minimax, settings.project_dir, settings.data_dir)

    model_output = None
    output_source = "minimax-http"
    if getattr(args, "from_file", None):
        output_file = Path(args.from_file).resolve()
        if not output_file.exists():
            print(f"模型输出文件不存在：{output_file}", file=sys.stderr)
            return 2
        model_output = output_file.read_text(encoding="utf-8")
        output_source = "assistant-session"

    result = agent.run(bundle, files, args.task_id, model_output=model_output, output_source=output_source)

    state.stage = "code_modified" if result.applied else "agent_failed"
    state.done.append(
        f"MiniMax（{result.model}）产出代码改动并应用：{', '.join(result.changed_files) or '无'}"
    )
    if result.error:
        state.todo.append(f"处理 MiniMax 应用失败：{result.error}")
    state.updated_at = _now()
    local_state.save(state)
    power_state.save(state)

    print(json.dumps(
        {
            "task_id": result.task_id,
            "model": result.model,
            "applied": result.applied,
            "changed_files": result.changed_files,
            "error": result.error,
            "request": result.request_path,
            "response": result.response_path,
            "diff": result.diff_path,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if result.applied else 1


def _candidate_files(bundle: Any, project_dir: Path) -> list[str]:
    """挑出交给模型阅读的源文件。"""
    files: list[str] = []
    for symbol in bundle.code:
        if symbol.file not in files:
            files.append(symbol.file)
    return [f for f in files if (project_dir / f).exists()]


# -- verify -----------------------------------------------------------------


def command_verify(args: argparse.Namespace) -> int:
    settings = _settings(args)
    local_state = LocalStateStore(settings.data_dir)
    power_state = PowerContextStateStore(settings.powercontext)
    state = local_state.load(args.task_id)
    if state is None:
        print(f"本地没有任务 {args.task_id} 的状态，请先执行 context 命令。", file=sys.stderr)
        return 2

    context_file = _context_path(settings, args.task_id)
    payload = json.loads(context_file.read_text(encoding="utf-8"))
    target_cases = payload["target_tests"]
    target_files = sorted({item["file"] for item in target_cases})
    all_test_files = sorted({item["file"] for item in payload["target_tests"] + payload["regression_tests"]})
    regression_files = [f for f in all_test_files if f not in target_files]

    results: list[dict[str, Any]] = []
    logs_dir = settings.data_dir / "test-logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    plans: list[tuple[str, list[str]]] = []
    if target_files and args.only in ("both", "target"):
        node_ids = [f"{item['file']}::{item['name']}" for item in target_cases]
        plans.append(("target", node_ids))
    if regression_files and args.only in ("both", "regression"):
        plans.append(("regression", regression_files))

    for label, selectors in plans:
        command = [sys.executable, "-m", "pytest", *selectors, "-q"]
        completed = subprocess.run(
            command,
            cwd=settings.project_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_pytest_env(settings),
        )
        log_path = logs_dir / f"{args.task_id}-{args.label}-{label}.log"
        log_path.write_text(
            f"$ {' '.join(command)}\n\n{completed.stdout}\n{completed.stderr}", encoding="utf-8"
        )
        results.append(
            {
                "kind": label,
                "command": " ".join(command),
                "exit_code": completed.returncode,
                "log": log_path.as_posix(),
                "summary": _summary(completed.stdout),
            }
        )

    state.verification = results
    passed = all(item["exit_code"] == 0 for item in results)
    state.stage = "verified" if passed else "verification_failed"
    state.done.append(f"运行目标测试与回归测试（{args.label}）")
    state.updated_at = _now()
    local_state.save(state)
    power_state.save(state)

    print(json.dumps({"task_id": state.task_id, "stage": state.stage, "verification": results},
                     ensure_ascii=False, indent=2))
    return 0 if state.stage == "verified" else 1


def _pytest_env(settings: Settings) -> dict[str, str]:
    """pytest 子进程环境：保证能 import 到示例工程的 src 目录。"""
    env = dict(os.environ)
    src = (settings.project_dir / "src").as_posix()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else src
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _summary(stdout: str) -> str:
    for line in reversed(stdout.strip().splitlines()):
        if line.strip():
            return line.strip()
    return ""


# -- doctor / stats ---------------------------------------------------------


def command_doctor(args: argparse.Namespace) -> int:
    settings = _settings(args)
    report: dict[str, Any] = {"project_dir": settings.project_dir.as_posix()}

    try:
        with SeekDBStore(settings.database) as store:
            store.ensure_schema()
            report["database"] = {"ok": True, "counts": store.counts()}
    except Exception as error:
        report["database"] = {"ok": False, "error": str(error)}

    try:
        power_state = PowerContextStateStore(settings.powercontext)
        report["powercontext"] = {
            "ok": True,
            "base_url": settings.powercontext.base_url,
            "scope_id": power_state.scope_id(),
            "tasks": power_state.list_tasks(),
        }
    except Exception as error:
        report["powercontext"] = {"ok": False, "error": str(error)}

    report["minimax"] = {
        "configured": bool(settings.minimax.api_key),
        "base_url": settings.minimax.base_url,
        "model": settings.minimax.model,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def command_stats(args: argparse.Namespace) -> int:
    settings = _settings(args)
    with SeekDBStore(settings.database) as store:
        print(json.dumps(store.counts(), ensure_ascii=False, indent=2))
    return 0


# -- 入口 -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sddctx", description="规有所依 SDD 上下文模块")
    parser.add_argument("--project", help="示例业务工程目录，默认 sample_project")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import", help="把规格、代码与测试导入数据库")
    import_parser.add_argument("--batch-id", help="导入批次编号")
    import_parser.set_defaults(handler=command_import)

    context_parser = subparsers.add_parser("context", help="按任务检索上下文")
    context_parser.add_argument("--task-id", required=True)
    context_parser.add_argument("--task", required=True, help="开发任务描述")
    context_parser.add_argument("--title", help="任务标题")
    context_parser.set_defaults(handler=command_context)

    state_parser = subparsers.add_parser("state", help="任务状态读写")
    state_sub = state_parser.add_subparsers(dest="state_command", required=True)
    state_save = state_sub.add_parser("save", help="把本地状态写入 PowerContext")
    state_save.add_argument("--task-id", required=True)
    state_save.add_argument("--stage", help="覆盖阶段标记")
    state_save.set_defaults(handler=command_state_save)
    state_show = state_sub.add_parser("show", help="从 PowerContext 读回任务状态")
    state_show.add_argument("--task-id", required=True)
    state_show.set_defaults(handler=command_state_show)

    agent_parser = subparsers.add_parser("agent", help="用 MiniMax 完成代码改动")
    agent_parser.add_argument("--task-id", required=True)
    agent_parser.add_argument("--files", nargs="*", help="交给模型阅读的源文件")
    agent_parser.add_argument(
        "--from-file",
        default=None,
        help="直接使用该文件中的模型回复（助手会话产出改动时使用），不调用 MiniMax 开放接口",
    )
    agent_parser.set_defaults(handler=command_agent)

    verify_parser = subparsers.add_parser("verify", help="运行目标测试与回归测试")
    verify_parser.add_argument("--task-id", required=True)
    verify_parser.add_argument("--label", default="after", help="本轮测试记录的标签，例如 before / after")
    verify_parser.add_argument(
        "--only", default="both", choices=["both", "target", "regression"], help="只跑某类测试"
    )
    verify_parser.set_defaults(handler=command_verify)

    doctor_parser = subparsers.add_parser("doctor", help="检查数据库、PowerContext 与 MiniMax 配置")
    doctor_parser.set_defaults(handler=command_doctor)

    stats_parser = subparsers.add_parser("stats", help="查看数据库中的资料统计")
    stats_parser.set_defaults(handler=command_stats)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
