"""Record allowlisted container/version evidence without exporting environment secrets."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def record(output, wsl_distro):
    prefix = ["wsl", "-d", wsl_distro, "-u", "root", "--"] if wsl_distro else []
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

    def command(*args):
        result = subprocess.run(prefix + list(args), cwd=ROOT, check=True, capture_output=True,
                                timeout=30, **options)
        return result.stdout.decode("utf-8").strip()

    compose = ["docker", "compose", "-f", "compose.yaml", "-f", "compose.dify.yaml"]
    # No `compose config` or full `docker inspect`: those expose real credentials.
    raw = command(*compose, "ps", "--format", "json")
    rows = json.loads(raw) if raw.startswith("[") else [json.loads(line) for line in raw.splitlines()]
    containers = [{k: row.get(k) for k in ("Service", "Image", "State", "Health", "Status")}
                  for row in rows]
    assert len(containers) == 9 and all(row["State"] == "running" for row in containers)
    assert all(row["Health"] in ("", "healthy", None) for row in containers)
    images = []
    for name in sorted({row["Image"] for row in containers}):
        detail = json.loads(command("docker", "image", "inspect", name, "--format",
            '{"id":{{json .Id}},"digests":{{json .RepoDigests}},"os":{{json .Os}},"architecture":{{json .Architecture}}}'))
        images.append({"image": name, **detail})
    packages = json.loads(command(*compose, "exec", "-T", "adapter", "python", "-c",
        "import sys,json; from importlib.metadata import version; "
        "print(json.dumps({'python':sys.version,'packages':{n:version(n) for n in "
        "['powercontext','fastapi','httpx','uvicorn','rpds-py']}}))"))
    dependency_check = command(*compose, "exec", "-T", "adapter", "python", "-m", "pip", "check")
    evidence = {"status": "passed", "executed_at": datetime.now(timezone.utc).isoformat(),
                "docker": command("docker", "--version"),
                "compose": command("docker", "compose", "version", "--short"),
                "containers": containers, "images": images, "runtime": packages,
                "dependency_check": dependency_check,
                "dsl_sha256": hashlib.sha256((ROOT / "dsl/strange-cabinet.yml").read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
                "dsl_hash_encoding": "UTF-8 with LF (same text sent to import API)"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PASS: {len(containers)} deployed containers; evidence: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/deployment.json")
    parser.add_argument("--wsl-distro", help="On Windows, invoke Docker in this WSL distribution")
    args = parser.parse_args()
    record(args.output, args.wsl_distro)
