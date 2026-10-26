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

"""Incremental context packs: chunked payloads and prefix deltas."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_PACK_KEYS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_KEYS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})


class ContextPackError(ValueError):
    """A context pack or delta failed protocol or chunk validation."""


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _loads(data: bytes) -> Any:
    try:
        return json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextPackError("context pack is not UTF-8 JSON") from error


def _require_object(value: Any, keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContextPackError("context pack fields do not match the schema")
    return value


def _b64(item: bytes) -> str:
    return base64.b64encode(item).decode("ascii")


def _unb64_list(value: Any) -> tuple[bytes, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContextPackError("context pack items must be base64 strings")
    try:
        return tuple(base64.b64decode(item, validate=True) for item in value)
    except binascii.Error as error:
        raise ContextPackError("context pack item is not valid base64") from error


def _digest(schema: str, items: list[str]) -> str:
    canonical = _canonical({"schema": schema, "items": items})
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _fit_byte_count(unsigned: dict[str, Any]) -> bytes:
    guess = 0
    encoded = b""
    for _ in range(16):
        encoded = _canonical({**unsigned, "byte_count": guess})
        if len(encoded) == guess:
            return encoded
        guess = len(encoded)
    for candidate in range(max(0, len(encoded) - 8), len(encoded) + 9):
        encoded = _canonical({**unsigned, "byte_count": candidate})
        if len(encoded) == candidate:
            return encoded
    raise ContextPackError("context pack byte count did not converge")


def _utf8_chunks(content: str, chunk_bytes: int) -> tuple[bytes, ...]:
    if type(chunk_bytes) is not int or chunk_bytes <= 0:
        raise ContextPackError("chunk size must be a positive integer")
    data = content.encode("utf-8")
    items: list[bytes] = []
    offset = 0
    while offset < len(data):
        end = min(offset + chunk_bytes, len(data))
        chunk = data[offset:end]
        while chunk:
            try:
                chunk.decode("utf-8")
            except UnicodeDecodeError:
                chunk = chunk[:-1]
                continue
            break
        if not chunk:
            raise ContextPackError("chunk size cannot hold a valid UTF-8 boundary")
        items.append(chunk)
        offset += len(chunk)
    return tuple(items)


@dataclass(frozen=True, slots=True)
class ContextPack:
    """A complete, self-describing context payload."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...] | list[bytes]) -> ContextPack:
        materialized = tuple(items)
        if any(not isinstance(item, (bytes, bytearray)) for item in materialized):
            raise ContextPackError("context pack items must be bytes")
        raw_items = tuple(bytes(item) for item in materialized)
        encoded = [_b64(item) for item in raw_items]
        digest = _digest(PACK_SCHEMA, encoded)
        wire = _fit_byte_count({"schema": PACK_SCHEMA, "items": encoded, "digest": digest})
        return cls(raw_items, digest, len(wire))

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = 512) -> ContextPack:
        if not isinstance(content, str):
            raise ContextPackError("context pack content must be text")
        return cls.build(_utf8_chunks(content, chunk_bytes))

    def to_bytes(self) -> bytes:
        encoded = [_b64(item) for item in self.items]
        return _fit_byte_count({"schema": PACK_SCHEMA, "items": encoded, "digest": self.digest})

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        if not isinstance(data, (bytes, bytearray)):
            raise ContextPackError("context pack bytes are required")
        raw = bytes(data)
        payload = _require_object(_loads(raw), _PACK_KEYS)
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("context pack schema is not supported")
        items = _unb64_list(payload["items"])
        encoded = [_b64(item) for item in items]
        digest = payload["digest"]
        if not isinstance(digest, str) or digest != _digest(PACK_SCHEMA, encoded):
            raise ContextPackError("context pack digest does not match")
        byte_count = payload["byte_count"]
        if type(byte_count) is not int or byte_count != len(raw):
            raise ContextPackError("context pack byte count does not match")
        return cls(items, digest, byte_count)


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """Prefix reuse plus the suffix needed to rebuild a target pack."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("delta endpoints must be context packs")
        prefix = 0
        for left, right in zip(base.items, target.items, strict=False):
            if left != right:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        payload = {
            "schema": DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": [_b64(item) for item in self.appended],
        }
        return _canonical(payload)

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        if not isinstance(data, (bytes, bytearray)):
            raise ContextPackError("context pack delta bytes are required")
        payload = _require_object(_loads(bytes(data)), _DELTA_KEYS)
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("context pack delta schema is not supported")
        prefix = payload["prefix_items"]
        if type(prefix) is not int or prefix < 0:
            raise ContextPackError("context pack prefix must be a non-negative integer")
        for key in ("base_digest", "target_digest"):
            if not isinstance(payload[key], str) or not payload[key].startswith("sha256:"):
                raise ContextPackError("context pack digest is malformed")
        appended = _unb64_list(payload["appended"])
        return cls(payload["base_digest"], payload["target_digest"], prefix, appended)


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Rebuild the target pack from a verified base and delta."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("delta application requires a pack and a delta")
    if base.digest != delta.base_digest:
        raise ContextPackError("context pack delta base digest does not match")
    if type(delta.prefix_items) is not int or delta.prefix_items < 0 or delta.prefix_items > len(base.items):
        raise ContextPackError("context pack prefix is outside the base")
    rebuilt = ContextPack.build(base.items[: delta.prefix_items] + tuple(delta.appended))
    if rebuilt.digest != delta.target_digest:
        raise ContextPackError("context pack delta target digest does not match")
    return rebuilt
