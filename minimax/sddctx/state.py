"""任务状态存取（PowerContext HTTP Memory API）。

状态先在本地工作副本中维护，再写入 PowerContext；模块重启后从 PowerContext
读回，进程之间不依赖任何本地内存状态。
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sddctx.config import PowerContextSettings
from sddctx.models import TaskState

STATE_MARKER = "sddctx.task_state"
STATE_KIND = "working_note"


def _encode(state: TaskState) -> str:
    payload = state.to_dict()
    payload["updated_at"] = payload["updated_at"] or datetime.now(UTC).astimezone().isoformat(
        timespec="seconds"
    )
    marker = f"{STATE_MARKER} task_id={state.task_id} stage={state.stage}"
    return f"{marker}\n{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"


def _decode(text: str) -> dict[str, Any] | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                return None
            if isinstance(payload, dict) and payload.get("task_id"):
                return payload
            return None
    return None


class PowerContextStateStore:
    """通过 PowerContext 读写任务状态。"""

    def __init__(self, settings: PowerContextSettings) -> None:
        self._settings = settings
        self._scope_id: str | None = settings.scope_id

    def scope_id(self) -> str:
        """取得（必要时创建）本模块使用的 scope。"""
        if self._scope_id:
            return self._scope_id
        scope_id = asyncio.run(self._resolve_scope_id())
        self._scope_id = scope_id
        return scope_id

    async def _resolve_scope_id(self) -> str:
        from powercontext.http import CreateScopeRequest

        async with self._client() as client:
            if self._settings.scope_id:
                return self._settings.scope_id
            try:
                scope = await client.create_scope(
                    CreateScopeRequest(
                        title=self._settings.scope_title,
                        summary="规有所依 SDD 上下文模块的任务状态",
                        idempotency_key="sddctx-task-state",
                    )
                )
            except Exception:
                default_scope = await client.get_default_scope()
                return default_scope.scope_id
            else:
                return scope.scope_id

    def _client(self):
        from powercontext.client import PowerContextClient

        return PowerContextClient(
            self._settings.base_url,
            token=self._settings.api_token,
            timeout=30.0,
        )

    def save(self, state: TaskState) -> dict[str, Any]:
        """把任务状态写入 PowerContext。"""
        from powercontext.http import RememberMemoryRequest

        state.updated_at = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
        scope_id = self.scope_id()
        text = _encode(state)

        async def _run() -> Any:
            async with self._client() as client:
                return await client.remember_memory(
                    RememberMemoryRequest(
                        scope_id=scope_id,
                        kind=STATE_KIND,
                        text=text,
                        reason=f"sddctx task state {state.task_id} stage={state.stage}",
                    )
                )

        response = asyncio.run(_run())
        citation = None
        if response.entry is not None:
            citation = response.entry.citation
        return {
            "scope_id": scope_id,
            "memory": response.memory.model_dump() if response.memory else None,
            "citation": citation.model_dump() if citation is not None else None,
        }

    def load(self, task_id: str) -> TaskState | None:
        """按任务编号读回最新的任务状态。"""
        from powercontext.http import ListMemoryEntriesRequest

        scope_id = self.scope_id()

        async def _run() -> list[Any]:
            async with self._client() as client:
                page = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=scope_id))
                return list(page.entries)

        entries = asyncio.run(_run())
        candidates: list[TaskState] = []
        for entry in entries:
            payload = _decode(entry.text)
            if payload is None or payload.get("task_id") != task_id:
                continue
            candidates.append(TaskState.from_dict(payload))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.updated_at)
        return candidates[-1]

    def list_tasks(self) -> list[str]:
        """列出 PowerContext 中记录过的任务编号。"""
        from powercontext.http import ListMemoryEntriesRequest

        scope_id = self.scope_id()

        async def _run() -> list[Any]:
            async with self._client() as client:
                page = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=scope_id))
                return list(page.entries)

        entries = asyncio.run(_run())
        task_ids: list[str] = []
        for entry in entries:
            payload = _decode(entry.text)
            if payload and payload.get("task_id") and payload["task_id"] not in task_ids:
                task_ids.append(payload["task_id"])
        return task_ids


class LocalStateStore:
    """任务状态的工作副本。"""

    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir / "state"
        self._dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, task_id: str) -> Path:
        return self._dir / f"{task_id}.json"

    def save(self, state: TaskState) -> Path:
        path = self.path_for(state.task_id)
        state.updated_at = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
        path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def load(self, task_id: str) -> TaskState | None:
        path = self.path_for(task_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return TaskState.from_dict(payload)
