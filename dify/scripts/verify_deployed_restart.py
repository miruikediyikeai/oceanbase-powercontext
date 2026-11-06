"""Restart this project's two companion containers and verify real Dify memory reuse.

Run only against the dedicated local stack in compose.dify.yaml. Other containers
and volumes are preserved. The randomly generated passport is never exported.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

from dotenv import load_dotenv
import httpx

ROOT = Path(__file__).resolve().parents[1]


def run(output, wsl_distro):
    load_dotenv(ROOT / ".env")
    passport = "restart-" + secrets.token_hex(16)
    api = os.environ["DIFY_API_URL"].rstrip("/")
    adapter = "http://127.0.0.1:8812"
    rounds = []
    with httpx.Client(timeout=150, trust_env=False) as client:
        def turn(move, returning=False):
            response = client.post(api + "/workflows/run",
                headers={"Authorization": "Bearer " + os.environ["DIFY_APP_KEY"]},
                json={"inputs": {"passport": passport, "move": move}, "response_mode": "blocking",
                      "user": "restart-" + secrets.token_hex(8) if returning else "restart-first"})
            response.raise_for_status()
            data = response.json()["data"]
            assert data["status"] == "succeeded", data.get("error")
            rounds.append({"input": move, "workflow_run_id": data["id"],
                           "status": data["status"], "outputs": data["outputs"]})
            return data["outputs"]["text"]

        def load():
            response = client.post(adapter + "/memory/load", json={"passport": passport},
                headers={"Authorization": "Bearer " + os.environ["CABINET_TOKEN"]})
            response.raise_for_status()
            return response.json()

        assert load()["state"] is None
        assert "已寄存" in turn("寄存 会打嗝的月亮 | 每周五变成奶酪")
        assert "咕叽" in turn("约定 每次见面先说咕叽")
        assert "夜班结案收据" in turn("结案")
        before = load()
        prefix = ["wsl", "-d", wsl_distro, "-u", "root", "--"] if wsl_distro else []
        compose = prefix + ["docker", "compose", "-f", "compose.yaml", "-f", "compose.dify.yaml"]
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        subprocess.run(compose + ["restart", "memory", "adapter"], cwd=ROOT, check=True,
                       timeout=90, **options)
        deadline = time.monotonic() + 90
        while True:
            try:
                if client.get(adapter + "/health", timeout=3).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Companion services did not recover after restart")
            time.sleep(0.5)
        after = load()
        assert after == before, "State/version/scope/citation changed across restart"
        # Read directly from the real PC server, from the private Compose network.
        readback = (
            "import httpx,json,os; r=httpx.post('http://memory:8811/v1/memory/entries/list', "
            "json={'scope_id':" + repr(after["scope_id"]) + "}, "
            "headers={'Authorization':'Bearer '+os.environ['POWERCONTEXT_TOKEN']},trust_env=False); "
            "r.raise_for_status(); print(json.dumps(r.json(),ensure_ascii=False))"
        )
        raw = subprocess.run(compose + ["exec", "-T", "adapter", "python", "-c", readback],
                             cwd=ROOT, check=True, capture_output=True, timeout=30, **options)
        direct = json.loads(raw.stdout)
        entry = next(x for x in direct["entries"] if x["kind"] == "cabinet_state")
        assert json.loads(entry["text"])["state"] == before["state"]
        assert entry["citation"] == before["citation"]
        text = turn("开门", returning=True)
        assert all(word in text for word in ("咕叽", "会打嗝的月亮", "上次结案"))
    evidence = {"status": "passed", "executed_at": datetime.now(timezone.utc).isoformat(),
                "execution": "actual Dify API + restart of deployed PowerContext/adapter containers",
                "before_restart": before, "after_restart": after,
                "powercontext_direct_readback": direct, "rounds": rounds,
                "checks": ["empty_initial_scope", "three_real_dify_turns_saved_memory",
                           "both_containers_restarted", "identical_state_version_scope_citation",
                           "direct_powercontext_api_matches_saved_state",
                           "new_dify_user_reuses_old_vow_and_souvenir_after_restart"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PASS: deployed restart persistence; {len(rounds)} real Dify runs; evidence: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/deployed-restart.json")
    parser.add_argument("--wsl-distro", help="On Windows, invoke Docker in this WSL distribution")
    args = parser.parse_args()
    run(args.output, args.wsl_distro)
