"""采集数据库使用记录。

执行模块真实使用的 SQL，把版本、表结构、资料统计、导入批次与检索查询的原始结果
写入 `evidence/database/`，用于核对「工程资料确实保存在 seekdb／OceanBase 中，
且查询结果被用于构造上下文」。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

MINIMAX_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MINIMAX_ROOT))

from sddctx.config import Settings  # noqa: E402
from sddctx.retrieval import build_context, tokenize  # noqa: E402
from sddctx.store import SeekDBStore  # noqa: E402

TASK = (
    "为 OrderRepository.list_orders 增加按订单状态筛选的能力，"
    "支持状态枚举与状态字符串（忽略大小写），非法状态抛出 ValueError，"
    "无匹配时返回空列表。规格见 SPEC-ORD-003。"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="采集数据库使用记录")
    parser.add_argument("--evidence-dir", type=Path, default=None)
    args = parser.parse_args()

    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
    evidence_dir = (args.evidence_dir or MINIMAX_ROOT / "evidence" / "database" / stamp).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings.from_env()
    tokens = tokenize(TASK)
    report: dict[str, object] = {
        "captured_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "target": {
            "host": settings.database.host,
            "port": settings.database.port,
            "database": settings.database.database,
        },
        "task": TASK,
        "tokens": tokens,
    }

    with SeekDBStore(settings.database) as store:
        report["version"] = store._query("SELECT VERSION() AS version, DATABASE() AS db")
        report["counts"] = store.counts()
        report["tables"] = store._query("SHOW TABLES")
        report["import_batches"] = store.import_batches()
        report["spec_rules"] = [
            {"spec_id": rule.spec_id, "rule_id": rule.rule_id, "title": rule.rule_title,
             "source_file": rule.source_file, "content_hash": rule.content_hash}
            for rule in store.list_spec_rules()
        ]
        report["code_symbols"] = [
            {"symbol": symbol.symbol, "kind": symbol.kind, "file": symbol.file, "line": symbol.line}
            for symbol in store.list_code_symbols()
        ]
        report["test_cases"] = [
            {"name": test.name, "file": test.file, "targets": list(test.targets)}
            for test in store.list_test_cases()
        ]

        spec_hits = store.search_spec_rules(tokens, limit=6)
        code_hits = store.search_code_symbols(tokens, limit=6, names=["OrderRepository", "list_orders"])
        report["retrieval"] = {
            "spec_rules": [
                {"rule_id": rule.rule_id, "score": score, "source_file": rule.source_file}
                for rule, score in spec_hits
            ],
            "code_symbols": [
                {"symbol": symbol.symbol, "file": symbol.file, "score": score}
                for symbol, score in code_hits
            ],
        }

        bundle = build_context(store, task_id="T-001", task=TASK)
        report["context_sources"] = {
            "specs": [f"{rule.spec_id}::{rule.rule_id}" for rule in bundle.specs],
            "code": [symbol.qualified for symbol in bundle.code],
            "target_tests": [test.qualified for test in bundle.target_tests],
            "regression_tests": [test.qualified for test in bundle.regression_tests],
        }

    (evidence_dir / "database-usage.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print(f"evidence: {evidence_dir / 'database-usage.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
