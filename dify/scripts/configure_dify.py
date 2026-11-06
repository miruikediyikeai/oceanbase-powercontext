"""Import the exact delivered DSL through the real Dify console API and publish it.

Only use --initialize for a dedicated local installation owned by this project.
Credentials stay in ignored .env; the report contains no secrets.
"""
import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv, set_key
import httpx

ROOT = Path(__file__).resolve().parents[1]


def configure(base_url, cabinet_url, initialize, report):
    load_dotenv(ROOT / ".env")
    email = os.environ["DIFY_ADMIN_EMAIL"]
    password = os.environ["DIFY_ADMIN_PASSWORD"]
    cabinet_token = os.environ["CABINET_TOKEN"]
    checks = []
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=90, trust_env=False) as client:
        deadline = time.monotonic() + 180
        while True:
            try:
                ready = client.get("/console/api/setup", timeout=3)
                if ready.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                raise SystemExit("Dify API did not become ready in 180 seconds; inspect docker compose logs api.")
            time.sleep(0.5)
        def call(method, path, body=None):
            response = client.request(method, "/console/api" + path, json=body)
            if response.status_code >= 400:
                raise RuntimeError(f"Dify console {path}: HTTP {response.status_code}: "
                                   + str(response.json().get("message", "request rejected")))
            return response.json()
        setup = call("GET", "/setup")
        if setup["step"] != "finished":
            if not initialize:
                raise SystemExit("Dify not initialized. Use --initialize only on this project's fresh local stack.")
            call("POST", "/setup", {"email": email, "name": "Cabinet Judge", "password": password})
            checks.append("initialized_dedicated_local_dify")
        login = call("POST", "/login", {"email": email, "password": password, "remember_me": False})
        if login.get("result") != "success":
            raise RuntimeError("Dify login rejected")
        client.headers["Authorization"] = "Bearer " + login["data"]["access_token"]
        checks.append("authenticated_real_console")
        result = call("POST", "/apps/imports", {
            "mode": "yaml-content", "yaml_content": (ROOT / "dsl/strange-cabinet.yml").read_text(encoding="utf-8")})
        if result["status"] == "pending":
            result = call("POST", "/apps/imports/" + result["id"] + "/confirm")
        if result["status"] not in ("completed", "completed-with-warnings") or not result.get("app_id"):
            raise RuntimeError("DSL import failed: " + str(result.get("error")))
        app_id = result["app_id"]
        checks.append("imported_delivered_dsl_via_console")
        draft = call("GET", f"/apps/{app_id}/workflows/draft")
        for variable in draft["environment_variables"]:
            if variable["name"] == "cabinet_url":
                variable["value"] = cabinet_url.rstrip("/")
            elif variable["name"] == "cabinet_token":
                variable["value"] = cabinet_token
        call("POST", f"/apps/{app_id}/workflows/draft", {
            "graph": draft["graph"], "features": draft["features"], "hash": draft["hash"],
            "environment_variables": draft["environment_variables"],
            "conversation_variables": draft.get("conversation_variables", [])})
        checks.append("bound_service_address_and_secret_in_real_draft")
        publish = call("POST", f"/apps/{app_id}/workflows/publish", {
            "marked_name": "cabinet-v1", "marked_comment": "Reproducible Dify + PowerContext verification"})
        checks.append("published_real_workflow")
        api_key = call("POST", f"/apps/{app_id}/api-keys")["token"]
        set_key(str(ROOT / ".env"), "DIFY_APP_KEY", api_key)
        set_key(str(ROOT / ".env"), "DIFY_API_URL", base_url.rstrip("/") + "/v1")
        set_key(str(ROOT / ".env"), "DIFY_APP_ID", app_id)
        evidence = {"status": "passed", "execution": "actual Dify 1.9.1 console HTTP API",
                    "executed_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    "app_id": app_id, "import_status": result["status"],
                    "dsl_sha256": hashlib.sha256((ROOT / "dsl/strange-cabinet.yml").read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
                    "dsl_hash_encoding": "UTF-8 with LF (same text sent to import API)",
                    "dsl_version": result["imported_dsl_version"], "published": publish["result"],
                    "node_count": len(draft["graph"]["nodes"]), "checks": checks}
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"PASS: DSL imported and published; app_id={app_id}; report={report}")
        print("Application API key saved to ignored .env; restart adapter to enable /play.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5001")
    parser.add_argument("--cabinet-url", default="http://adapter:8812")
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--report", type=Path, default=ROOT / ".runtime/dify-import.json")
    args = parser.parse_args()
    configure(args.base_url, args.cabinet_url, args.initialize, args.report)
