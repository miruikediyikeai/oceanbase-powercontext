"""Start an actual, isolated PowerContext service for the local study workspace."""

import argparse
from pathlib import Path

import uvicorn
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import ExternalSkillsConfig, InferenceConfig, RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    AccessControlConfig,
    BearerAuthConfig,
    HttpConfig,
    McpConfig,
    MetricsConfig,
    ServerSettings,
    TracingConfig,
)


def main():
    parser = argparse.ArgumentParser(description="启动拾忆使用的本地 PowerContext 服务")
    parser.add_argument("--data", type=Path, default=Path(".data/powercontext"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    directory = args.data.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    settings = ServerSettings(
        workspace=directory,
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{(directory / 'memory.db').as_posix()}"),
        http=HttpConfig(host="127.0.0.1", port=args.port),
        auth=BearerAuthConfig(enabled=False),
        access=AccessControlConfig(mode="disabled"),
        inference=InferenceConfig(),
        runtime=RuntimeConfig(schedule_seconds=None, experience_schedule_seconds=None),
        external_skills=ExternalSkillsConfig(),
        mcp=McpConfig(enabled=False),
        metrics=MetricsConfig(enabled=False),
        tracing=TracingConfig(enabled=False),
    )
    app = create_server_app(settings=settings, scheduler_path=directory / "scheduler.db")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
