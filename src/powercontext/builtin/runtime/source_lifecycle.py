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

"""Propagate source availability across a scope-local evidence DAG."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select, text

from powercontext.artifacts.models import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory.errors import InvalidMemoryCitationError, MemoryEntryNotFoundError
from powercontext.builtin.persistence.sqlite.topic_memory_index import (
    _DELETE_CHUNK_FTS_SQL,
    _DELETE_TOPIC_FTS_SQL,
    _INSERT_CHUNK_FTS_SQL,
    _INSERT_TOPIC_FTS_SQL,
)
from powercontext.builtin.persistence.tables import (
    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
)
from powercontext.builtin.records import BaseValueNotFoundError
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources.models import SourceRef

_TOPIC_COLUMNS = (
    "scope_id",
    "family",
    "artifact_id",
    "revision",
    "title",
    "summary",
    "searchable_text",
    "source_count",
)
_CHUNK_COLUMNS = (
    "scope_id",
    "family",
    "artifact_id",
    "revision",
    "chunk_ordinal",
    "start_offset",
    "end_offset",
    "chunk_text",
    "searchable_text",
    "policy_version",
)


class SourceLifecycleError(ValueError):
    """A lifecycle registration, event, or resolution failed."""


class SourceLifecycleEvent(BaseModel):
    """One source availability change addressed to a registered lifecycle revision."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    revision: int
    sequence: int
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("lifecycle text must contain non-whitespace content")  # noqa: TRY003
        return value

    @field_validator("revision", "sequence")
    @classmethod
    def require_positive_int(cls, value: int) -> int:
        if type(value) is not int or value < 1:
            raise ValueError("lifecycle revision and sequence must be positive integers")  # noqa: TRY003
        return value

    @field_validator("available")
    @classmethod
    def require_bool(cls, value: bool) -> bool:
        if type(value) is not bool:
            raise ValueError("lifecycle availability must be a boolean")  # noqa: TRY003
        return value


class SourceLifecycleApplication:
    """Select the lifecycle service for one scope."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._runtime, scope_id)


class ScopedSourceLifecycle:
    """Register an evidence DAG and project availability into Memory and Topic Memory."""

    def __init__(self, runtime: Any, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = scope_id

    async def register_source(self, node_id: str, source_ref: SourceRef, revision: int) -> None:
        _require_node(node_id)
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("source lifecycle revision must be a positive integer")
        reference = source_ref.model_dump(mode="json")
        async with self._runtime._context(self.scope_id) as _context, self._runtime._locked(self.scope_id):
            await self._require_source(source_ref)
            document = await self._load()
            self._bind(
                document,
                node_id,
                {
                    "kind": "source",
                    "revision": revision,
                    "reference": reference,
                    "available": True,
                    "sequence": 0,
                    "event": None,
                },
            )
            await self._save(document)

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        *,
        parents: tuple[str, ...],
        policy: str,
    ) -> None:
        reference = citation.model_dump(mode="json")
        async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
            await self._require_memory(citation)
            document = await self._load()
            node = self._bind_derived(document, node_id, "memory", reference, parents, policy)
            if node["available"] is False:
                await self._sync_memory(context, {node_id: False}, document, "lifecycle registration")
            await self._save(document)

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        *,
        parents: tuple[str, ...],
        policy: str,
    ) -> None:
        reference = artifact_ref.model_dump(mode="json")
        async with self._runtime._context(self.scope_id) as _context, self._runtime._locked(self.scope_id):
            await self._require_topic(artifact_ref)
            document = await self._load()
            snapshot = await self._topic_snapshot(reference["artifact_id"])
            if snapshot is None:
                raise SourceLifecycleError("topic memory is not searchable")
            node = self._bind_derived(document, node_id, "topic", reference, parents, policy)
            node["index"] = snapshot
            if node["available"] is False:
                await self._set_topic(reference["artifact_id"], visible=False, snapshot=snapshot)
            await self._save(document)

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
            document = await self._load()
            node = document["nodes"].get(event.node_id)
            if node is None or node["kind"] != "source":
                raise SourceLifecycleError("lifecycle event does not address a registered source")
            if event.revision != node["revision"]:
                raise SourceLifecycleError("lifecycle event revision does not match the source")
            payload = event.model_dump(mode="json")
            if event.sequence < node["sequence"] or (
                event.sequence == node["sequence"] and payload != node["event"]
            ):
                raise SourceLifecycleError("lifecycle event sequence is not newer")
            if event.sequence == node["sequence"]:
                return _active_ids(document)
            previous = {item_id: item["available"] for item_id, item in document["nodes"].items()}
            node["available"] = event.available
            _recompute(document)
            desired = {item_id: item["available"] for item_id, item in document["nodes"].items()}
            memory_changes = {
                item_id: desired[item_id]
                for item_id, item in document["nodes"].items()
                if item["kind"] == "memory" and desired[item_id] != previous[item_id]
            }
            await self._sync_memory(context, memory_changes, document, event.reason)
            for item_id, item in document["nodes"].items():
                if item["kind"] != "topic" or desired[item_id] == previous[item_id]:
                    continue
                await self._set_topic(
                    item["reference"]["artifact_id"],
                    visible=desired[item_id],
                    snapshot=item.get("index"),
                )
            node["sequence"] = event.sequence
            node["event"] = payload
            await self._save(document)
            return _active_ids(document)

    async def resolve(self, node_id: str, *, purpose: str) -> SourceRef | MemoryCitation | ArtifactRef:
        if purpose not in {"generation", "audit"}:
            raise SourceLifecycleError("lifecycle purpose is not supported")
        async with self._runtime._context(self.scope_id), self._runtime._locked(self.scope_id):
            document = await self._load()
            node = document["nodes"].get(node_id)
            if node is None:
                raise SourceLifecycleError("lifecycle node is not registered")
            if purpose == "generation" and not node["available"]:
                raise SourceLifecycleError("lifecycle node is not available for generation")
            return _reference(node)

    async def _require_source(self, source_ref: SourceRef) -> None:
        try:
            await self._runtime._records().get_source(self.scope_id, source_ref.source_type, source_ref.source_id)
        except BaseValueNotFoundError as error:
            raise SourceLifecycleError("lifecycle source is not readable") from error

    async def _require_memory(self, citation: MemoryCitation) -> None:
        try:
            await self._runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=citation))
        except (MemoryEntryNotFoundError, InvalidMemoryCitationError, ArtifactNotFoundError) as error:
            raise SourceLifecycleError("lifecycle memory citation is not readable") from error

    async def _require_topic(self, artifact_ref: ArtifactRef) -> None:
        try:
            await self._runtime._records().get_artifact_revision(
                self.scope_id,
                artifact_ref.family,
                artifact_ref.artifact_id,
                artifact_ref.revision,
            )
        except BaseValueNotFoundError as error:
            raise SourceLifecycleError("lifecycle topic is not readable") from error

    def _bind(self, document: dict[str, Any], node_id: str, node: dict[str, Any]) -> None:
        current = document["nodes"].get(node_id)
        if current is None:
            node["order"] = len(document["nodes"])
            document["nodes"][node_id] = node
            return
        if _identity(current) != _identity(node):
            raise SourceLifecycleError("lifecycle node is already bound to different evidence")

    def _bind_derived(
        self,
        document: dict[str, Any],
        node_id: str,
        kind: str,
        reference: dict[str, Any],
        parents: tuple[str, ...],
        policy: str,
    ) -> dict[str, Any]:
        _require_node(node_id)
        if policy not in {"all", "any"}:
            raise SourceLifecycleError("lifecycle policy must be all or any")
        if not parents or len(set(parents)) != len(parents):
            raise SourceLifecycleError("lifecycle parents must be unique and nonempty")
        if node_id in parents or any(parent not in document["nodes"] for parent in parents):
            raise SourceLifecycleError("lifecycle parents must already be registered")
        if _reaches(document, parents, node_id):
            raise SourceLifecycleError("lifecycle dependencies must be acyclic")
        node = {
            "kind": kind,
            "reference": reference,
            "parents": list(parents),
            "policy": policy,
            "available": _supported(document, parents, policy),
        }
        self._bind(document, node_id, node)
        return document["nodes"][node_id]

    async def _sync_memory(
        self,
        context: Any,
        changes: dict[str, bool],
        document: dict[str, Any],
        reason: str,
    ) -> None:
        if not changes:
            return
        service = context.artifacts.memory
        current = await service.head(context.artifacts.memory_artifact_id)
        for desired, operation in ((False, service.forget), (True, service.reactivate)):
            selected = []
            versions = {item.entry_id: item for item in await service.entries(current)}
            states = {item.entry_id: item.state for item in current.content.manifest.entries}
            for node_id, available in changes.items():
                if available is not desired:
                    continue
                entry_id = document["nodes"][node_id]["reference"]["entry_id"]
                version = versions.get(entry_id)
                if version is None:
                    raise SourceLifecycleError("lifecycle memory entry is missing")
                target = "active" if desired else "inactive"
                if states[entry_id] != target:
                    selected.append(version)
            if selected:
                current = await operation(current, entries=tuple(selected), reason=reason)

    async def _topic_snapshot(self, artifact_id: str) -> dict[str, Any] | None:
        topics = TOPIC_MEMORY_ACTIVE_TOPICS_TABLE
        chunks = TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE
        async with self._database.transaction() as connection:
            topic = (
                await connection.execute(
                    select(topics).where(topics.c.scope_id == self.scope_id, topics.c.artifact_id == artifact_id)
                )
            ).mappings().one_or_none()
            if topic is None:
                return None
            rows = (
                await connection.execute(
                    select(chunks)
                    .where(chunks.c.scope_id == self.scope_id, chunks.c.artifact_id == artifact_id)
                    .order_by(chunks.c.chunk_ordinal)
                )
            ).mappings()
        return {"topic": _columns(topic, _TOPIC_COLUMNS), "chunks": [_columns(row, _CHUNK_COLUMNS) for row in rows]}

    async def _set_topic(self, artifact_id: str, *, visible: bool, snapshot: dict[str, Any] | None) -> None:
        """Hide or restore search rows without dropping the active Topic head."""

        identity = {"scope_id": self.scope_id, "artifact_id": artifact_id}
        async with self._database.transaction() as connection:
            indexed = await connection.scalar(
                text(
                    "SELECT count(*) FROM pc_topic_memory_topic_fts "
                    "WHERE scope_id = :scope_id AND artifact_id = :artifact_id"
                ),
                identity,
            )
            if visible == bool(indexed):
                return
            await connection.execute(_DELETE_TOPIC_FTS_SQL, identity)
            await connection.execute(_DELETE_CHUNK_FTS_SQL, identity)
            if not visible or snapshot is None:
                return
            topic = snapshot["topic"]
            await connection.execute(
                _INSERT_TOPIC_FTS_SQL,
                {
                    "scope_id": topic["scope_id"],
                    "artifact_id": topic["artifact_id"],
                    "revision": topic["revision"],
                    "title": topic["title"],
                    "summary": topic["summary"],
                    "searchable_text": topic["searchable_text"],
                },
            )
            for chunk in snapshot["chunks"]:
                await connection.execute(
                    _INSERT_CHUNK_FTS_SQL,
                    {
                        "scope_id": chunk["scope_id"],
                        "artifact_id": chunk["artifact_id"],
                        "revision": chunk["revision"],
                        "chunk_ordinal": chunk["chunk_ordinal"],
                        "start_offset": chunk["start_offset"],
                        "title": topic["title"],
                        "summary": topic["summary"],
                        "chunk_text": chunk["chunk_text"],
                        "searchable_text": chunk["searchable_text"],
                    },
                )

    @property
    def _database(self) -> Any:
        database = getattr(self._runtime._provider, "database", None)
        if database is None:
            raise SourceLifecycleError("lifecycle storage is unavailable")
        return database

    async def _load(self) -> dict[str, Any]:
        async with self._database.transaction() as connection:
            await connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS pc_source_lifecycle (scope_id TEXT PRIMARY KEY, document TEXT NOT NULL)"
            )
            raw = await connection.scalar(
                text("SELECT document FROM pc_source_lifecycle WHERE scope_id = :scope_id"),
                {"scope_id": self.scope_id},
            )
        if raw is None:
            return {"nodes": {}}
        loaded = json.loads(raw)
        if not isinstance(loaded, dict) or not isinstance(loaded.get("nodes"), dict):
            raise SourceLifecycleError("lifecycle record is unreadable")
        return loaded

    async def _save(self, document: dict[str, Any]) -> None:
        payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        async with self._database.transaction() as connection:
            await connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS pc_source_lifecycle (scope_id TEXT PRIMARY KEY, document TEXT NOT NULL)"
            )
            await connection.execute(
                text(
                    "INSERT INTO pc_source_lifecycle (scope_id, document) VALUES (:scope_id, :document) "
                    "ON CONFLICT(scope_id) DO UPDATE SET document = excluded.document"
                ),
                {"scope_id": self.scope_id, "document": payload},
            )


def _columns(row: Any, names: tuple[str, ...]) -> dict[str, Any]:
    return {name: row[name] for name in names}


def _require_node(node_id: str) -> None:
    if not isinstance(node_id, str) or not node_id.strip():
        raise SourceLifecycleError("lifecycle node id must contain non-whitespace content")


def _identity(node: dict[str, Any]) -> dict[str, Any]:
    keys = ("kind", "revision", "reference", "parents", "policy")
    return {key: node.get(key) for key in keys}


def _supported(document: dict[str, Any], parents: tuple[str, ...] | list[str], policy: str) -> bool:
    flags = [bool(document["nodes"][parent]["available"]) for parent in parents]
    return all(flags) if policy == "all" else any(flags)


def _reaches(document: dict[str, Any], parents: tuple[str, ...], node_id: str) -> bool:
    pending = list(parents)
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current == node_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        pending.extend(document["nodes"][current].get("parents") or ())
    return False


def _recompute(document: dict[str, Any]) -> None:
    for node in sorted(document["nodes"].values(), key=lambda item: item["order"]):
        if node["kind"] == "source":
            continue
        node["available"] = _supported(document, node["parents"], node["policy"])


def _active_ids(document: dict[str, Any]) -> list[str]:
    return sorted(node_id for node_id, node in document["nodes"].items() if node["available"])


def _reference(node: dict[str, Any]) -> SourceRef | MemoryCitation | ArtifactRef:
    if node["kind"] == "source":
        return SourceRef.model_validate(node["reference"])
    if node["kind"] == "memory":
        return MemoryCitation.model_validate(node["reference"])
    return ArtifactRef.model_validate(node["reference"])
