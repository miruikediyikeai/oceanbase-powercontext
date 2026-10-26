# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Cross-call embedding coalescing and a bounded vector cache."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Iterable

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel


class CoalescingEmbeddingModel:
    """Share in-flight embedding work and reuse successful vectors."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must be non-negative")
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_ms = coalesce_ms
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._inflight: dict[str, tuple[asyncio.Task[EmbeddingResult], tuple[str, ...]]] = {}
        self._waiters: list[tuple[tuple[str, ...], asyncio.Future[EmbeddingResult]]] = []
        self._lock = asyncio.Lock()
        self._window: asyncio.Task[None] | None = None

    @property
    def profile(self):
        return self._model.profile

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        materialized = tuple(texts)
        if not materialized:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[EmbeddingResult] = loop.create_future()
        async with self._lock:
            self._waiters.append((materialized, waiter))
            if self._window is None or self._window.done():
                self._window = asyncio.create_task(self._close_window())
        return await asyncio.shield(waiter)

    async def _close_window(self) -> None:
        try:
            await asyncio.sleep(self._coalesce_ms / 1000)
            async with self._lock:
                waiters = self._waiters
                self._waiters = []
                self._window = None
                plans = self._plan(waiters)
            if plans:
                await asyncio.gather(*(self._finish(*plan) for plan in plans), return_exceptions=True)
        except Exception as error:
            async with self._lock:
                pending = self._waiters
                self._waiters = []
                self._window = None
            for _texts, future in pending:
                if not future.done():
                    future.set_exception(error)
            raise

    def _plan(
        self, waiters: list[tuple[tuple[str, ...], asyncio.Future[EmbeddingResult]]]
    ) -> list[tuple[asyncio.Future[EmbeddingResult], list[tuple[str, object]], list[asyncio.Task[EmbeddingResult]]]]:
        active = [(texts, future) for texts, future in waiters if not future.cancelled()]
        needed: list[str] = []
        seen: set[str] = set()
        for texts, _future in active:
            for text in dict.fromkeys(texts):
                if text in seen:
                    continue
                seen.add(text)
                if text in self._cache:
                    self._cache.move_to_end(text)
                elif text not in self._inflight:
                    needed.append(text)
        for offset in range(0, len(needed), self._max_batch_size):
            batch = tuple(needed[offset : offset + self._max_batch_size])
            task = asyncio.create_task(self._invoke(batch))
            for text in batch:
                self._inflight[text] = (task, batch)
        plans: list[
            tuple[asyncio.Future[EmbeddingResult], list[tuple[str, object]], list[asyncio.Task[EmbeddingResult]]]
        ] = []
        for texts, future in active:
            steps: list[tuple[str, object]] = []
            used: list[asyncio.Task[EmbeddingResult]] = []
            for text in texts:
                cached = self._cache.get(text)
                if cached is not None and text not in self._inflight:
                    steps.append(("cache", cached))
                    continue
                task, batch = self._inflight[text]
                steps.append(("batch", task, batch.index(text)))
                if all(task is not item for item in used):
                    used.append(task)
            plans.append((future, steps, used))
        return plans

    async def _invoke(self, batch: tuple[str, ...]) -> EmbeddingResult:
        try:
            result = await self._model.embed(batch)
            if len(result.vectors) != len(batch):
                raise ValueError("embedding vector count does not match the batch")
        except BaseException:
            async with self._lock:
                self._release(batch)
            raise
        async with self._lock:
            self._release(batch)
            if self._cache_size > 0:
                for text, vector in zip(batch, result.vectors, strict=True):
                    self._cache[text] = tuple(vector)
                    self._cache.move_to_end(text)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
        return result

    def _release(self, batch: tuple[str, ...]) -> None:
        for text in batch:
            current = self._inflight.get(text)
            if current is not None and current[1] == batch:
                self._inflight.pop(text, None)

    async def _finish(
        self,
        future: asyncio.Future[EmbeddingResult],
        steps: list[tuple[str, object]],
        used: list[asyncio.Task[EmbeddingResult]],
    ) -> None:
        try:
            completed = {task: await asyncio.shield(task) for task in used}
            vectors: list[EmbeddingVector] = []
            for step in steps:
                if step[0] == "cache":
                    vectors.append(step[1])  # type: ignore[arg-type]
                    continue
                task = step[1]
                index = step[2]
                vectors.append(completed[task].vectors[index])  # type: ignore[index]
            if not future.done():
                future.set_result(
                    EmbeddingResult(
                        vectors=tuple(vectors),
                        usage=_usage(completed[item] for item in used),
                    )
                )
        except Exception as error:
            if not future.done():
                future.set_exception(error)


def _usage(results: Iterable[EmbeddingResult]) -> InferenceUsage:
    rows = tuple(result.usage for result in results)
    if not rows:
        return InferenceUsage(requests=0)

    def total(name: str) -> int | None:
        values = [getattr(row, name) for row in rows]
        if all(value is None for value in values):
            return None
        return sum(value or 0 for value in values)

    return InferenceUsage(
        requests=sum(row.requests for row in rows),
        input_tokens=total("input_tokens"),
        output_tokens=total("output_tokens"),
    )
