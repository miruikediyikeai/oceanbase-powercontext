"""Verify the published app through an actual Dify engine. Never simulates Dify."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import secrets
import time

from dotenv import load_dotenv
import httpx

ROOT = Path(__file__).resolve().parents[1]


def run(output, passport, console_traces=False, web_url=None):
    load_dotenv(ROOT / ".env")
    key = os.getenv("DIFY_APP_KEY", "")
    if not key:
        raise SystemExit("请先导入、配置并发布 DSL，再在 .env 中填写 DIFY_APP_KEY。")
    url = os.getenv("DIFY_API_URL", "http://127.0.0.1:5001/v1").rstrip("/")
    rounds = []
    traces = []
    moves = [("开门", "三步玩一局"), ("寄存 会打嗝的月亮 | 每周五变成奶酪", "已寄存"),
             ("约定 每次见面先说咕叽", "咕叽"), ("认领 会打嗝的月亮 | 我从没见过它", "驳回"),
             ("认领 会打嗝的月亮 | 奶酪就是证据", "核对通过"), ("结案", "夜班结案收据"),
             ("开门", "上次结案")]
    with httpx.Client(timeout=150, trust_env=False) as client:
        console_headers = None
        if console_traces:
            response = client.post(url.removesuffix("/v1") + "/console/api/login", json={
                "email": os.environ["DIFY_ADMIN_EMAIL"], "password": os.environ["DIFY_ADMIN_PASSWORD"],
                "remember_me": False})
            response.raise_for_status()
            console_headers = {"Authorization": "Bearer " + response.json()["data"]["access_token"]}
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
        if web_url:
            page = client.get(web_url.rstrip("/") + "/")
            page.raise_for_status()
            assert "怪奇失物招领处" in page.text and "text/html" in page.headers["content-type"]
            assert key not in page.text and os.environ["CABINET_TOKEN"] not in page.text
            response = client.post(web_url.rstrip("/") + "/play", json={"passport": passport, "move": "开门"})
            response.raise_for_status()
            data = response.json()
            assert all(word in data["outputs"]["text"] for word in ("咕叽", "会打嗝的月亮", "上次结案"))
            rounds.append({"input": "开门", "entry": "companion /play -> real Dify",
                           "workflow_run_id": data["workflow_run_id"], "status": "succeeded", "outputs": data["outputs"]})
        if console_headers:
            for turn in rounds:
                path = url.removesuffix("/v1") + "/console/api/apps/" + os.environ["DIFY_APP_ID"] + \
                       "/workflow-runs/" + turn["workflow_run_id"] + "/node-executions"
                deadline = time.monotonic() + 30
                while True:
                    response = client.get(path, headers=console_headers)
                    response.raise_for_status()
                    nodes = response.json()["data"]
                    if len(nodes) == 9 or time.monotonic() >= deadline:
                        break
                    time.sleep(0.5)
                assert len(nodes) == 9 and all(n["status"] == "succeeded" for n in nodes), \
                    [(n["node_id"], n["status"], n.get("error")) for n in nodes]
                # Explicit allowlist: raw inputs/process_data can contain passport and Bearer credentials.
                traces.append({"workflow_run_id": turn["workflow_run_id"], "nodes": [
                    {k: n.get(k) for k in ("node_id", "node_type", "title", "status", "elapsed_time", "error")}
                    for n in nodes]})
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence = {"status": "passed", "execution": "actual published Dify Workflow service API",
                "executed_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                "rounds": rounds, "node_traces": traces,
                "checks": ["all_workflow_runs_succeeded", "cross_dify_user_memory_reuse",
                           "real_powercontext_citations_in_outputs"] +
                          (["all_nine_real_nodes_succeeded_each_run"] if traces else []) +
                          (["companion_html_returned_without_service_keys", "companion_web_api_calls_real_dify"] if web_url else [])}
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PASS: {len(rounds)} Dify runs; evidence: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/dify-verification.json")
    parser.add_argument("--passport", default="dify-" + secrets.token_hex(16))
    parser.add_argument("--console-traces", action="store_true", help="Read sanitized node traces using local admin credentials")
    parser.add_argument("--web-url", help="Also exercise the companion /play entry point")
    args = parser.parse_args()
    run(args.output, args.passport, args.console_traces, args.web_url)
