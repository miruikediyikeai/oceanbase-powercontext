"""Exercise real HTTP services, restart both processes, and record repeatable evidence."""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import httpx

from notes import stable_card_id

ROOT = Path(__file__).resolve().parent


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class Services:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.pc_port, self.app_port = free_port(), free_port()
        self.pc_url = f"http://127.0.0.1:{self.pc_port}"
        self.url = f"http://127.0.0.1:{self.app_port}"
        self.processes = []
        self.logs = []

    def launch(self, command, log_name, env=None):
        log = (self.directory / log_name).open("ab")
        self.logs.append(log)
        process = subprocess.Popen([sys.executable, *command], cwd=ROOT, env=env, stdout=log, stderr=log)
        self.processes.append(process)
        return process

    def wait_ready(self, process, url):
        deadline = time.monotonic() + 90
        with httpx.Client(trust_env=False) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"服务启动失败，请查看 {self.directory} 中的日志。")
                try:
                    if client.get(url, timeout=1).status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                time.sleep(0.2)
        raise RuntimeError(f"服务启动超时，请查看 {self.directory} 中的日志。")

    def start(self):
        pc = self.launch(
            ["serve_powercontext.py", "--data", str(self.directory / "pc"), "--port", str(self.pc_port)],
            "powercontext.log",
        )
        self.wait_ready(pc, self.pc_url + "/health/live")
        env = {**os.environ, "POWERCONTEXT_URL": self.pc_url, "RECALL_LEARNER": "verification"}
        env.pop("POWERCONTEXT_TOKEN", None)
        app = self.launch(
            ["-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(self.app_port), "--log-level", "warning"],
            "app.log",
            env,
        )
        self.wait_ready(app, self.url + "/")

    def stop(self):
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in self.logs:
            log.close()
        self.processes.clear()
        self.logs.clear()


def run_verification(directory):
    services = Services(directory)
    checks = []
    started = datetime.now(UTC)
    try:
        services.start()
        with httpx.Client(base_url=services.url, timeout=30, trust_env=False) as client:

            def call(method, path, body=None, status=200):
                response = client.request(method, path, json=body)
                if response.status_code != status:
                    raise AssertionError(f"{method} {path}: {response.status_code} {response.text}")
                return response.json()

            sample = json.loads((ROOT / "samples/database-basics.json").read_text(encoding="utf-8"))
            sample["cards"][0]["answer"] += " Unicode readback: cafe\u0301."
            assert call("GET", "/api/workspace")["stats"]["total"] == 0
            imported = call("POST", "/api/import", sample)
            assert imported["added"] == 6
            assert call("POST", "/api/import", sample)["added"] == 0
            checks.append("sample_import_and_repeat_without_duplicates")
            changed = json.loads(json.dumps(sample))
            changed["cards"][0]["answer"] = "changed answer"
            call("POST", "/api/import", changed, 409)
            invalid = {"cards": [{**sample["cards"][0], "id": "invalid"}, {**sample["cards"][0], "id": "invalid"}]}
            call("POST", "/api/import", invalid, 422)
            checks.append("conflicting_content_and_duplicate_identity_rejected")
            before = call("GET", "/api/workspace")
            card = before["plan"][0]
            payload = {
                "card_id": card["card"]["id"],
                "rating": "again",
                "entry_version_id": card["citation"]["entry_version_id"],
            }
            result = call("POST", "/api/review", payload)
            assert result["card"]["repetitions"] == 1
            assert result["card"]["lapses"] == 1
            call("POST", "/api/review", payload, 409)
            checks.append("review_written_and_stale_repeat_rejected")
            call("PUT", "/api/preferences", {"name": "验证学习者", "daily_limit": 2, "entry_version_id": None})
            call("PUT", "/api/preferences", {"name": "过期修改", "daily_limit": 3, "entry_version_id": None}, 409)
            saved = call("GET", "/api/workspace")
            assert saved["stats"] == {"total": 6, "due": 5, "weak": 1, "reviews": 1}
            assert len(saved["plan"]) == 2
            assert all(row["card"]["id"] != card["card"]["id"] for row in saved["plan"])
            checks.append("preferences_change_plan_and_not_due_card_is_excluded")
            # Read the cited version directly from PowerContext, bypassing the app.
            with httpx.Client(base_url=services.pc_url, timeout=30, trust_env=False) as pc:
                citation = result["receipt"]["entry"]["citation"]
                exact = pc.post("/v1/memory/entries/get", json={"scope_id": saved["scope_id"], "citation": citation})
                assert exact.status_code == 200, exact.text
                assert json.loads(exact.json()["text"])["lapses"] == 1
            checks.append("exact_citation_read_back_from_real_powercontext_http")
            services.stop()
            services.start()
            restored = call("GET", "/api/workspace")
            assert restored["scope_id"] == saved["scope_id"]
            assert restored["preferences"] == saved["preferences"]
            assert restored["cards"] == saved["cards"]
            assert restored["stats"] == saved["stats"]
            checks.append("both_processes_restarted_same_scope_preferences_progress_and_citations_restored")
            # Notes-to-cards: segmentation, hand-fill path, stable identity, exact history.
            note = (
                "# 事务\n\n"
                "原子性确保事务整体提交或回滚，不会出现只完成一半的状态。\n\n"
                "## 隔离级别\n\n"
                "可串行化是最严格的隔离级别，能够避免幻读等并发异常现象。\n"
            )
            segments = call("POST", "/api/notes/segments", {"note": note, "topic": "数据库", "source": "验证笔记"})
            assert [segment["heading"] for segment in segments["segments"]] == ["事务", "隔离级别"]
            checks.append("note_segmentation_respects_headings")
            without_model = call(
                "POST",
                "/api/notes/candidates",
                {"segments": segments["segments"], "topic": "数据库", "source": "验证笔记"},
                400,
            )
            assert "RECALL_LLM" in without_model["detail"]
            checks.append("candidate_generation_reports_missing_model_config")
            question = "什么是事务的原子性？"
            answer = "事务要么全部提交，要么全部回滚。"
            note_card = {
                "id": stable_card_id("数据库", question, answer, "验证笔记"),
                "topic": "数据库",
                "question": question,
                "answer": answer,
                "source": "验证笔记",
            }
            assert call("POST", "/api/import", {"cards": [note_card]})["added"] == 1
            assert call("POST", "/api/import", {"cards": [note_card]})["added"] == 0
            checks.append("note_card_import_is_idempotent_via_stable_identity")
            current = call("GET", "/api/workspace")
            note_record = next(record for record in current["cards"] if record["card"]["id"] == note_card["id"])
            call(
                "POST",
                "/api/review",
                {
                    "card_id": note_card["id"],
                    "rating": "hard",
                    "entry_version_id": note_record["citation"]["entry_version_id"],
                },
            )
            timeline = call("GET", f"/api/cards/{note_card['id']}/history")["timeline"]
            assert [item["event"]["kind"] for item in timeline] == ["import", "review"]
            assert timeline[1]["event"]["rating"] == "hard"
            assert timeline[0]["entry_version_id"] != timeline[1]["entry_version_id"]
            checks.append("card_history_reads_exact_versions_not_current_state")
            weekly = call("GET", "/api/weekly")
            assert weekly["summary"]["range"]["timezone"] == "UTC"
            assert weekly["summary"]["totals"]["reviews"] >= 1
            assert weekly["summary"]["rule"]
            checks.append("weekly_summary_uses_real_revision_timeline")
            final = call("GET", "/api/workspace")
            # A separate learner must not inherit this learner's cards.
            with httpx.Client(base_url=services.pc_url, timeout=30, trust_env=False) as pc:
                other = pc.post(
                    "/v1/scopes",
                    json={
                        "title": "独立学习者",
                        "summary": "验证学习空间隔离",
                        "idempotency_key": "qoder-recall-v1:other",
                    },
                ).json()
                assert other["scope_id"] != saved["scope_id"]
                assert pc.post("/v1/memory/entries/list", json={"scope_id": other["scope_id"]}).json()["entries"] == []
            checks.append("separate_learner_scope_has_no_inherited_cards")
            services.processes[0].terminate()
            services.processes[0].wait(timeout=15)
            call("GET", "/api/workspace", status=503)
            checks.append("backend_unavailable_is_reported_without_fabricated_memory")
            return {
                "status": "passed",
                "started_at": started.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "python": sys.version.split()[0],
                "powercontext_version": version("powercontext"),
                "execution": "real localhost HTTP services, SQLite backend owned by PowerContext; no mocks",
                "checks": checks,
                "stats": final["stats"],
                "scope_id": final["scope_id"],
                "review_citation": citation,
                "saved_review": json.loads(exact.json()["text"]),
                "usage_effects": "Synthetic verification only; no real-user efficiency claim.",
            }
    finally:
        services.stop()


def main():
    parser = argparse.ArgumentParser(description="运行真实 PowerContext HTTP 验证并导出证据")
    parser.add_argument("--output", type=Path, default=Path(".data/verification.json"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="verify-", dir=args.output.parent.resolve()) as directory:
        try:
            report = run_verification(directory)
        except Exception:
            for log in Path(directory).glob("*.log"):
                print(f"{log.name}:\n{log.read_text(encoding='utf-8', errors='replace')[-6000:]}", file=sys.stderr)
            raise
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"status": report["status"], "checks": len(report["checks"]), "output": str(args.output)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
