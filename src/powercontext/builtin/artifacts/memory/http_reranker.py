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

"""Score Memory candidates through a batched HTTP reranking service."""

from __future__ import annotations

import math
from collections.abc import Sequence

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


class _Score(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int
    score: float

    @field_validator("score")
    @classmethod
    def require_finite_score(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("score must be finite")  # noqa: TRY003
        return value


class _ScoreResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scores: list[_Score] = Field(min_length=1)


class HTTPMemoryReranker:
    """Rerank a candidate pool by scores from one offline HTTP service."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        if len(candidates) < 1:
            raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
        if limit < 1 or limit > len(candidates):
            raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003

        scored: list[tuple[float, int]] = []
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            ranks = tuple(range(start + 1, start + 1 + len(batch)))
            response = await self._client.post(
                self._endpoint,
                json={
                    "query": query,
                    "candidates": [
                        {"rank": rank, "text": candidate.text} for rank, candidate in zip(ranks, batch, strict=True)
                    ],
                },
            )
            requests += 1
            response.raise_for_status()
            scored.extend(_validated_scores(response, ranks))

        ordered = sorted(scored, key=lambda item: (-item[0], item[1]))
        selected = tuple(rank for _score, rank in ordered[:limit])
        return MemoryRerankDecision(
            selected_ranks=selected,
            usage=InferenceUsage(requests=requests),
            discarded_rank_count=len(candidates) - len(selected),
        )


def _validated_scores(response: httpx.Response, expected_ranks: Sequence[int]) -> list[tuple[float, int]]:
    try:
        payload = response.json()
        parsed = _ScoreResponse.model_validate(payload)
    except (ValidationError, ValueError) as error:
        raise ValueError("rerank response schema is invalid") from error  # noqa: TRY003
    ranks = [item.rank for item in parsed.scores]
    if sorted(ranks) != sorted(expected_ranks) or len(ranks) != len(set(ranks)):
        raise ValueError("rerank response must cover each batch rank once")  # noqa: TRY003
    return [(item.score, item.rank) for item in parsed.scores]
