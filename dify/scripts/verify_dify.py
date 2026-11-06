"""Verify the published app through an actual Dify engine. Never simulates Dify."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import secrets

from dotenv import load_dotenv
import httpx

ROOT = Path(__file__).resolve().parents[1]


def run(output, passport):
    load_dotenv(ROOT / ".env")
    key = os.getenv("DIFY_APP_KEY", "")
    if not key:
        raise SystemExit("请先导入、配置并发布 DSL，再在 .env 中填写 DIFY_APP_KEY。")
    url = os.getenv("DIFY_API_URL", "http://127.0.0.1:5001/v1").rstrip("/")
    rounds = []
    moves = [("开门", "三步玩一局"), ("寄存 会打嗝的月亮 | 每周五变成奶酪", "已寄存"),
             ("约定 每次见面先说咕叽", "咕叽"), ("认领 会打嗝的月亮 | 我从没见过它", "驳回"),
             ("认领 会打嗝的月亮 | 奶酪就是证据", "核对通过"), ("结案", "夜班结案收据"),
             ("开门", "上次结案")]
    with httpx.Client(timeout=150, trust_env=False) as client:
        for i, (move, expected) in enumerate(moves):
            # Different Dify API users emulate a new conversation while keeping the same passport.
            user = "verify-" + hashlib.sha256(passport.encode()).hexdigest()[:16] + ("-return" if i == 6 else "-first")
            response = client.post(url + "/workflows/run", headers={"Authorization": "Bearer " + key},
                                   json={"inputs": {"passport": passport, "move": move},
                                         "response_mode": "blocking", "user": user})
            response.raise_for_status()
            data = response.json()["data"]
            assert data["status"] == "succeeded", data.get("error")
            assert expected in data["outputs"]["text"], data["outputs"]
            assert json.loads(data["outputs"]["memory_citation"])["entry_version_id"]
            if i == 6:
                assert "咕叽" in data["outputs"]["text"] and "会打嗝的月亮" in data["outputs"]["text"]
            rounds.append({"input": move, "workflow_run_id": data["id"], "status": data["status"],
                           "outputs": data["outputs"], "total_steps": data.get("total_steps")})
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence = {"status": "passed", "execution": "actual published Dify Workflow service API",
                "executed_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                "rounds": rounds, "checks": ["all_workflow_runs_succeeded", "cross_dify_user_memory_reuse",
                                               "real_powercontext_citations_in_outputs"]}
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PASS: {len(rounds)} Dify runs; evidence: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/dify-verification.json")
    parser.add_argument("--passport", default="dify-" + secrets.token_hex(16))
    args = parser.parse_args()
    run(args.output, args.passport)
