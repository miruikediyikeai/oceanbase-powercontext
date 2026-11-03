"""启动本地 PowerContext 服务，供上下文模块读写任务状态。

用法：

    python scripts/start_powercontext.py --port 8811 --data .data/powercontext

服务使用文件型 SQLite，进程重启后数据仍在，任务状态可以被后续进程读回。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn


def build_app(data_dir: Path) -> object:
    """按数据目录构造 PowerContext 服务。"""
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

    data_dir.mkdir(parents=True, exist_ok=True)
    database = data_dir / "memory.db"
    settings = ServerSettings(
        workspace=data_dir,
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database.as_posix()}"),
        http=HttpConfig(host="127.0.0.1"),
        auth=BearerAuthConfig(enabled=False),
        access=AccessControlConfig(mode="disabled"),
        inference=InferenceConfig(),
        runtime=RuntimeConfig(schedule_seconds=None, experience_schedule_seconds=None),
        external_skills=ExternalSkillsConfig(),
        mcp=McpConfig(enabled=False),
        metrics=MetricsConfig(enabled=False),
        tracing=TracingConfig(enabled=False),
    )
    return create_server_app(settings=settings, scheduler_path=data_dir / "scheduler.db")


def main() -> int:
    parser = argparse.ArgumentParser(description="启动本地 PowerContext 服务")
    parser.add_argument("--data", type=Path, default=Path(".data/powercontext"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8811)
    args = parser.parse_args()

    data_dir = args.data.resolve()
    app = build_app(data_dir)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
