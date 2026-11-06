"""Generate local-only secrets without printing them. Never overwrites an existing .env."""
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / ".env"
    if target.exists():
        raise SystemExit(".env already exists; preserve it and configure missing values manually.")
    keys = ["CABINET_TOKEN", "POWERCONTEXT_TOKEN", "DIFY_SECRET_KEY", "DIFY_DB_PASSWORD", "DIFY_REDIS_PASSWORD",
            "DIFY_SANDBOX_KEY", "DIFY_PLUGIN_KEY", "DIFY_PLUGIN_INNER_KEY"]
    lines = [name + "=" + secrets.token_hex(32) for name in keys]
    lines += ["DIFY_ADMIN_EMAIL=cabinet-admin@example.com",
              "DIFY_ADMIN_PASSWORD=Cabinet9" + secrets.token_hex(16),
              "DIFY_API_URL=http://127.0.0.1:5001/v1", "DIFY_APP_KEY=",
              "POWERCONTEXT_URL=http://127.0.0.1:8811"]
    with target.open("x", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
    target.chmod(0o600)
    print("Created ignored .env with random local credentials; no credentials printed.")


if __name__ == "__main__":
    main()
