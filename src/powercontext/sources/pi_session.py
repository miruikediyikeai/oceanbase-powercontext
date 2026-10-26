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

"""Capture the visible events on one selected branch of a Pi v3 session."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
_VISIBLE_MESSAGE_ROLES = frozenset({"bashExecution", "toolResult"})


class PiSessionInput(BaseModel):
    """One offline Pi JSONL session and the leaf that selects its branch."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event, including the original record used to render it."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """Captured text for one Pi event. Identity ignores later edits to the record."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Return visible events from the selected leaf back to the root, root first."""

    records = _load_records(value.content)
    header, nodes = records[0], records[1:]
    if header.get("type") != "session" or header.get("version") != 3:
        raise ValueError("Pi session header must be type session version 3")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or session_id == "" or session_id != session_id.strip():
        raise ValueError("Pi session id must be a non-empty string")  # noqa: TRY003

    by_id: dict[str, dict[str, JsonValue]] = {}
    order: list[str] = []
    for record in nodes:
        event_id = record.get("id")
        if not isinstance(event_id, str) or event_id == "" or event_id in by_id:
            raise ValueError("Pi event ids must be unique non-empty strings")  # noqa: TRY003
        if "parentId" not in record:
            raise ValueError("Pi event is missing parentId")  # noqa: TRY003
        parent_id = record.get("parentId")
        if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in by_id):
            raise ValueError("Pi event parent must be null or an earlier node")  # noqa: TRY003
        by_id[event_id] = record
        order.append(event_id)
    if not order:
        raise ValueError("Pi session does not contain an event leaf")  # noqa: TRY003

    leaf_id = order[-1] if value.leaf_id is None else value.leaf_id
    if leaf_id not in by_id:
        raise ValueError("Pi session leaf was not found")  # noqa: TRY003

    lineage: list[dict[str, JsonValue]] = []
    seen: set[str] = set()
    current: str | None = leaf_id
    while current is not None:
        if current in seen:
            raise ValueError("Pi session branch contains a cycle")  # noqa: TRY003
        seen.add(current)
        record = by_id[current]
        lineage.append(record)
        parent = record.get("parentId")
        current = parent if isinstance(parent, str) else None
    lineage.reverse()

    events: list[PiEventInput] = []
    for record in lineage:
        rendered = _render_event(record)
        if rendered is None:
            continue
        role, body = rendered
        event_id = record["id"]
        assert isinstance(event_id, str)
        parent = record.get("parentId")
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=event_id,
                parent_id=parent if isinstance(parent, str) else None,
                event_type=str(record.get("type")),
                role=role,
                content=f"{role}: {body}",
                record=record,
            )
        )
    return tuple(events)


class PiEventSourceAdapter:
    """Resolve and read one captured Pi event without consulting the session file."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = _event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose the full event text. Memory chunking must not trim this Source."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PiEventSourceAdapter(),
    projections=(PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    """Import new visible events on one branch and keep a checksum of what was stored."""

    name = PI_SESSION_CONNECTOR_NAME
    version = "1"
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        raw = self.path.read_bytes()
        events = parse_pi_session(PiSessionInput(content=raw, leaf_id=self.leaf_id))
        imported = _checkpoint_events(session.checkpoint)
        current = {record["id"]: _record_digest(record) for record in _event_records(raw)}
        for event_id, digest in imported.items():
            if event_id in current and current[event_id] != digest:
                raise ValueError("imported Pi event was rewritten")  # noqa: TRY003

        updated = dict(imported)
        for event in events:
            digest = _record_digest(event.record)
            previous = updated.get(event.event_id)
            if previous is not None:
                if previous != digest:
                    raise ValueError("imported Pi event was rewritten")  # noqa: TRY003
                continue
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
            updated[event.event_id] = digest
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint={"events": updated})


def _load_records(content: bytes) -> list[dict[str, JsonValue]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session must be strict UTF-8") from error  # noqa: TRY003
    records: list[dict[str, JsonValue]] = []
    for line in text.splitlines():
        if line.strip() == "":
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("Pi session line is not JSON") from error  # noqa: TRY003
        if not isinstance(value, dict):
            raise ValueError("Pi session records must be JSON objects")  # noqa: TRY003
        records.append(value)
    if not records:
        raise ValueError("Pi session is empty")  # noqa: TRY003
    return records


def _event_records(content: bytes) -> list[dict[str, JsonValue]]:
    return _load_records(content)[1:]


def _render_event(record: dict[str, JsonValue]) -> tuple[str, str] | None:
    event_type = record.get("type")
    if event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        if not isinstance(summary, str) or summary == "":
            return None
        return str(event_type), summary
    if event_type == "custom_message":
        body = _text_from_content(record.get("content"))
        if body == "":
            return None
        return "custom", body
    if event_type != "message":
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if not isinstance(role, str) or role == "":
        return None
    if role == "bashExecution":
        command = message.get("command")
        output = message.get("output")
        command_text = command if isinstance(command, str) else ""
        output_text = output if isinstance(output, str) else ""
        return role, f"$ {command_text}\n{output_text}"
    if role == "toolResult":
        tool_name = message.get("toolName")
        tool_call_id = message.get("toolCallId")
        name = tool_name if isinstance(tool_name, str) else ""
        call_id = tool_call_id if isinstance(tool_call_id, str) else ""
        return role, f"[tool_result {name} {call_id}] {_text_from_content(message.get('content'))}"
    if role in _VISIBLE_MESSAGE_ROLES:
        return None
    body = _text_from_content(message.get("content"))
    if body == "":
        return None
    return role, body


def _text_from_content(content: JsonValue | None) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif block_type == "toolCall":
            name = block.get("name") if isinstance(block.get("name"), str) else ""
            call_id = block.get("id") if isinstance(block.get("id"), str) else ""
            parts.append(f"[tool_call {name} {call_id}] {_compact_json(block.get('arguments', {}))}")
    return "\n".join(parts)


def _compact_json(value: JsonValue | None) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _event_source_id(session_id: str, event_id: str) -> str:
    source_id = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("Pi event source id must not exceed 256 characters")  # noqa: TRY003
    return source_id


def _record_digest(record: dict[str, JsonValue]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _checkpoint_events(checkpoint: JsonValue | None) -> dict[str, str]:
    if checkpoint is None:
        return {}
    if not isinstance(checkpoint, dict):
        raise ValueError("Pi session checkpoint is invalid")  # noqa: TRY003
    events = checkpoint.get("events", {})
    if not isinstance(events, dict):
        raise ValueError("Pi session checkpoint is invalid")  # noqa: TRY003
    loaded: dict[str, str] = {}
    for event_id, digest in events.items():
        if not isinstance(event_id, str) or not isinstance(digest, str):
            raise ValueError("Pi session checkpoint is invalid")  # noqa: TRY003
        loaded[event_id] = digest
    return loaded
