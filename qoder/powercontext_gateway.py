"""HTTP boundary for PowerContext 1.2.0. No local replacement for memory."""

import asyncio
import json
import os

import httpx
from fastapi import HTTPException


class PowerContextGateway:
    def __init__(self, base_url: str | None = None, learner: str | None = None):
        self.base_url = (base_url or os.getenv("POWERCONTEXT_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.learner = learner or os.getenv("RECALL_LEARNER", "demo")
        token = os.getenv("POWERCONTEXT_TOKEN")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=30, trust_env=False)
        self.scope_id = None
        # One local app owns one learner; serialize read/modify/write operations.
        self.lock = asyncio.Lock()

    async def post(self, path: str, payload: dict) -> dict:
        try:
            response = await self.client.post(path, json=payload)
        except httpx.TransportError as error:
            raise HTTPException(503, "PowerContext 连接失败；写入结果可能未知，请刷新核对后再操作。") from error
        if response.status_code >= 400:
            code = 409 if response.status_code == 409 else 502
            message = (
                "记忆已变化，请刷新后重试。"
                if code == 409
                else (f"PowerContext 返回 HTTP {response.status_code}，请检查服务版本、权限与服务日志。")
            )
            raise HTTPException(code, message)
        return response.json()

    async def scope(self) -> str:
        if self.scope_id is None:
            result = await self.post(
                "/v1/scopes",
                {
                    "title": f"拾忆 · {self.learner}",
                    "summary": "拾忆复习教练：知识卡片、学习进度与复习偏好。",
                    "idempotency_key": f"qoder-recall-v1:{self.learner}",
                },
            )
            self.scope_id = result["scope_id"]
        return self.scope_id

    async def entries(self) -> list[dict]:
        result = await self.post("/v1/memory/entries/list", {"scope_id": await self.scope()})
        return result["entries"]

    async def save(self, kind: str, value, previous: dict | None, reason: str, revision: int | None) -> dict:
        text = json.dumps(value.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        if len(text.encode("utf-8")) > 8192:
            raise HTTPException(422, "单条知识卡片超过 PowerContext 的 8192 字节限制。")
        payload = {"scope_id": await self.scope(), "kind": kind, "text": text, "reason": reason}
        if previous:
            payload["citation"] = previous["citation"]
            return await self.post("/v1/memory/entries/revise", payload)
        if revision is not None:
            payload["expected_revision"] = revision
        return await self.post("/v1/memory/remember", payload)
