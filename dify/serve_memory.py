"""Run a real PowerContext 1.2.0 instance; no application-side memory database."""
import argparse
import os
from pathlib import Path

import uvicorn
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import ExternalSkillsConfig, InferenceConfig, RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    AccessControlConfig, BearerAuthConfig, HttpConfig, McpConfig,
    MetricsConfig, ServerSettings, TracingConfig,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path(".data/memory"))
    parser.add_argument("--port", type=int, default=8811)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    directory = args.data.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    token = os.getenv("POWERCONTEXT_TOKEN", "")
    settings = ServerSettings(
        workspace=directory,
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{(directory / 'memory.db').as_posix()}"),
        http=HttpConfig(host=args.host, port=args.port),
        auth=BearerAuthConfig(enabled=bool(token), token=token or None),
        access=AccessControlConfig(mode="disabled"),
        inference=InferenceConfig(),
        runtime=RuntimeConfig(schedule_seconds=None, experience_schedule_seconds=None),
        external_skills=ExternalSkillsConfig(), mcp=McpConfig(enabled=False),
        metrics=MetricsConfig(enabled=False), tracing=TracingConfig(enabled=False),
    )
    uvicorn.run(create_server_app(settings=settings, scheduler_path=directory / "scheduler.db"),
                host=args.host, port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
