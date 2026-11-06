"""Real HTTP integration test. Also usable as an independent reproducibility harness."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from importlib.metadata import version

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class Services:
    def __init__(self, directory):
        self.directory = directory
        self.pc_port, self.port = free_port(), free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.pc_url = f"http://127.0.0.1:{self.pc_port}"
        self.token = secrets.token_urlsafe(32)
        self.running = []

    def launch(self, arguments, name, env):
        log = (self.directory / (name + ".log")).open("ab")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = subprocess.Popen([sys.executable, *arguments], cwd=ROOT, env=env,
                                   stdout=log, stderr=log, **options)
        self.running.append((process, log))
        return process

    def ready(self, process, url):
        with httpx.Client(trust_env=False) as client:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Service exited; see {self.directory}")
                try:
                    if client.get(url, timeout=1).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
        raise RuntimeError("Service startup timed out")

    def start(self):
        env = dict(os.environ)
        for name in ("POWERCONTEXT_TOKEN", "NARRATOR_API_KEY", "NARRATOR_BASE_URL", "NARRATOR_MODEL"):
            env.pop(name, None)
        process = self.launch(["serve_memory.py", "--data", str(self.directory / "pc"),
                               "--port", str(self.pc_port)], "powercontext", env)
        self.ready(process, self.pc_url + "/health/live")
        env.update(POWERCONTEXT_URL=self.pc_url, CABINET_TOKEN=self.token)
        process = self.launch(["-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port",
                               str(self.port), "--workers", "1", "--log-level", "warning"], "adapter", env)
        self.ready(process, self.url + "/health")

    def stop(self):
        for process, log in reversed(self.running):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(5)
            log.close()
        self.running.clear()


def run(directory):
    directory.mkdir(parents=True, exist_ok=True)
    services = Services(directory)
    passport = "verify-" + secrets.token_hex(12)
    document = yaml.safe_load((ROOT / "dsl/strange-cabinet.yml").read_text(encoding="utf-8"))
    code = next(n["data"]["code"] for n in document["workflow"]["graph"]["nodes"] if n["id"] == "game")
    namespace = {}
    exec(compile(code, "dify-code-node", "exec"), namespace)
    assert code == (ROOT / "game.py").read_text(encoding="utf-8")
    rounds, checks = [], []
    client = httpx.Client(base_url=services.url, headers={"Authorization": "Bearer " + services.token},
                          timeout=45, trust_env=False)
    def post(path, body, expected=200):
        response = client.post(path, json=body)
        assert response.status_code == expected, (path, response.status_code, response.text)
        return response.json()
    def load(key=passport):
        return post("/memory/load", {"passport": key})
    def turn(move):
        before = load()
        result = namespace["main"](json.dumps(before), move, passport, secrets.token_hex(12))
        payload = json.loads(result["payload"])
        saved = post("/memory/commit", payload)
        narrated = post("/narrate", {"text": saved["text"]})
        rounds.append({"input": move, "previous_version": before["version"], "version": saved["version"],
                       "citation": saved["citation"], "output": narrated["text"]})
        return payload, saved
    try:
        services.start()
        assert load()["state"] is None
        turn("开门")
        turn("寄存 会打嗝的月亮 | 每周五变成奶酪")
        turn("寄存 拒绝加班的影子 | 下班后替我去月球钓鱼")
        turn("约定 每次见面先说咕叽")
        _, rejected = turn("认领 会打嗝的月亮 | 这只是一个普通气球")
        assert "驳回" in rejected["text"] and load()["state"]["score"] == 4
        _, claimed = turn("认领 会打嗝的月亮 | 奶酪是它的周五皮肤")
        assert "核对通过" in claimed["text"] and load()["state"]["score"] == 7
        _, duplicate = turn("认领 会打嗝的月亮 | 奶酪")
        assert "已经认领" in duplicate["text"] and load()["state"]["score"] == 7
        checks.append("claim_uses_persisted_origin_wrong_proof_rejected_duplicate_cannot_score")
        payload, saved = turn("看看")
        replay = post("/memory/commit", payload)
        assert replay["replayed"] and replay["version"] == saved["version"]
        stale = {**payload, "request_id": "stale-request"}
        post("/memory/commit", stale, 409)
        checks.append("same_request_replay_is_idempotent_stale_different_request_conflicts")
        assert load("another-" + secrets.token_hex(8))["state"] is None
        checks.append("separate_passport_has_separate_scope")
        _, receipt = turn("结案")
        assert "夜班结案收据" in receipt["text"]
        before_restart = load()
        services.stop()
        services.start()
        restored = load()
        assert restored == before_restart
        with httpx.Client(timeout=30, trust_env=False) as memory_client:
            raw = memory_client.post(services.pc_url + "/v1/memory/entries/list",
                                     json={"scope_id": restored["scope_id"]})
            raw.raise_for_status()
            powercontext_readback = raw.json()
        _, returned = turn("开门")
        assert all(word in returned["text"] for word in ("咕叽", "会打嗝的月亮", "负一楼常驻编外人员"))
        checks.append("both_processes_restart_same_powercontext_citation_and_state_restored_old_vow_and_souvenir_reused")
        post("/memory/load", {"passport": "bad"}, 422)
        assert client.post("/memory/load", json={"passport": passport},
                           headers={"Authorization": "Bearer wrong"}).status_code == 401
        checks.append("invalid_passport_and_missing_authorization_rejected")
        assert post("/narrate", {"text": "already saved"})["mode"] == "rules"
        checks.append("no_model_key_explicitly_reports_rules_mode")
        # Stop PC only; an apparently healthy adapter must not invent memory.
        services.running[0][0].terminate()
        services.running[0][0].wait(15)
        post("/memory/load", {"passport": passport}, 503)
        checks.append("powercontext_unavailable_returns_503_without_fabricating_memory")
        evidence = {"status": "passed", "executed_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    "python": sys.version, "powercontext": version("powercontext"),
                    "execution": "real PowerContext HTTP and adapter HTTP; gameplay executes exact DSL code; this script does not start the Dify engine",
                    "checks": checks, "scope_id": before_restart["scope_id"],
                    "initial_state": None, "before_restart": before_restart,
                    "after_restart": restored, "powercontext_direct_readback": powercontext_readback,
                    "restored_citation": restored["citation"], "rounds": rounds}
        (directory / "verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": "passed", "checks": len(checks), "rounds": len(rounds),
                          "evidence": str(directory / "verification.json")}, ensure_ascii=False))
        return evidence
    finally:
        client.close()
        services.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/verification")
    run(parser.parse_args().output.resolve())
