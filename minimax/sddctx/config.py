"""运行配置：全部来源为环境变量，便于评审时替换为其它实例。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
MINIMAX_ROOT = PACKAGE_ROOT.parent


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _env_optional(name: str) -> str | None:
    value = os.environ.get(name)
    return None if value is None or value == "" else value


@dataclass(frozen=True)
class DatabaseSettings:
    """seekdb / OceanBase 连接参数（MySQL 协议）。"""

    host: str
    port: int
    user: str
    password: str
    database: str

    @classmethod
    def from_env(cls) -> DatabaseSettings:
        return cls(
            host=_env("SDDCTX_DB_HOST", "127.0.0.1"),
            port=int(_env("SDDCTX_DB_PORT", "10000")),
            user=_env("SDDCTX_DB_USER", "root"),
            password=_env("SDDCTX_DB_PASSWORD", ""),
            database=_env("SDDCTX_DB_NAME", "sdd_context"),
        )


@dataclass(frozen=True)
class PowerContextSettings:
    """PowerContext HTTP Memory API 连接参数。"""

    base_url: str
    api_token: str | None
    scope_title: str
    scope_id: str | None

    @classmethod
    def from_env(cls) -> PowerContextSettings:
        return cls(
            base_url=_env("SDDCTX_POWERCONTEXT_URL", "http://127.0.0.1:8811"),
            api_token=_env_optional("SDDCTX_POWERCONTEXT_TOKEN"),
            scope_title=_env("SDDCTX_POWERCONTEXT_SCOPE_TITLE", "sdd-context"),
            scope_id=_env_optional("SDDCTX_POWERCONTEXT_SCOPE"),
        )


@dataclass(frozen=True)
class MiniMaxSettings:
    """MiniMax 开放接口参数。"""

    base_url: str
    api_key: str | None
    model: str

    @classmethod
    def from_env(cls) -> MiniMaxSettings:
        return cls(
            base_url=_env("MINIMAX_BASE_URL", "https://api.minimaxi.com"),
            api_key=_env_optional("MINIMAX_API_KEY"),
            model=_env("MINIMAX_MODEL", "MiniMax-M2"),
        )


@dataclass(frozen=True)
class Settings:
    """模块的完整运行配置。"""

    project_dir: Path
    data_dir: Path
    database: DatabaseSettings
    powercontext: PowerContextSettings
    minimax: MiniMaxSettings

    @classmethod
    def from_env(cls, project_dir: Path | None = None) -> Settings:
        resolved_project = Path(
            project_dir
            if project_dir is not None
            else _env("SDDCTX_PROJECT_DIR", str(MINIMAX_ROOT / "sample_project"))
        ).resolve()
        data_dir = Path(_env("SDDCTX_DATA_DIR", str(MINIMAX_ROOT / ".data"))).resolve()
        return cls(
            project_dir=resolved_project,
            data_dir=data_dir,
            database=DatabaseSettings.from_env(),
            powercontext=PowerContextSettings.from_env(),
            minimax=MiniMaxSettings.from_env(),
        )
