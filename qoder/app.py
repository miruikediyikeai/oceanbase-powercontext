"""拾忆: a local, single-learner study workspace backed by PowerContext."""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from domain import CARD_KIND, MAX_CARDS, PREFERENCES_KIND, Card, CardInput, Preferences, make_plan, review, utc_now
from history import card_history, weekly_summary
from notes import CandidateRequest, SegmentInput, generate_candidates, llm_config, split_note
from powercontext_gateway import PowerContextGateway

ROOT = Path(__file__).resolve().parent


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cards: list[CardInput] = Field(min_length=1, max_length=MAX_CARDS)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_id: str
    rating: Literal["again", "hard", "good"]
    entry_version_id: str


class PreferencesRequest(Preferences):
    entry_version_id: str | None = None


def decode(entries: list[dict]) -> tuple[list[dict], Preferences, dict | None]:
    cards = []
    preferences = Preferences()
    preference_entry = None
    identifiers = set()
    for entry in entries:
        try:
            if entry["kind"] == CARD_KIND:
                card = Card.model_validate_json(entry["text"])
                if card.id in identifiers:
                    raise ValueError("duplicate card identity")
                identifiers.add(card.id)
                cards.append({"card": card, "citation": entry["citation"], "version": entry["version"]})
            elif entry["kind"] == PREFERENCES_KIND:
                if preference_entry is not None:
                    raise ValueError("duplicate preferences")
                preferences = Preferences.model_validate_json(entry["text"])
                preference_entry = entry
        except (ValidationError, ValueError) as error:
            raise HTTPException(409, "学习记忆的格式或身份存在冲突，请检查原始记录；不会覆盖它们。") from error
    return cards, preferences, preference_entry


def check_version(entry: dict | None, expected: str | None):
    actual = entry["citation"]["entry_version_id"] if entry else None
    if actual != expected:
        raise HTTPException(409, "页面中的学习进度已过期，请刷新后重试。")


def create_app(gateway: PowerContextGateway | None = None) -> FastAPI:
    pc = gateway or PowerContextGateway()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await pc.client.aclose()

    app = FastAPI(title="拾忆 · Recall", version="0.1.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "static" / "index.html")

    @app.get("/api/workspace")
    async def workspace():
        async with pc.lock:
            entries = await pc.entries()
            records, preferences, preference_entry = decode(entries)
            now = utc_now()
            return {
                "learner": pc.learner,
                "scope_id": await pc.scope(),
                "preferences": preferences,
                "preferences_version": preference_entry["citation"]["entry_version_id"] if preference_entry else None,
                "cards": records,
                "plan": make_plan(records, preferences, now),
                "stats": {
                    "total": len(records),
                    "due": sum(record["card"].due_at <= now for record in records),
                    "weak": sum(record["card"].last_rating in ("again", "hard") for record in records),
                    "reviews": sum(record["card"].repetitions for record in records),
                },
                "memory": entries,
                "llm_configured": llm_config() is not None,
                "server_time": now,
            }

    @app.get("/api/sample")
    async def sample():
        return json.loads((ROOT / "samples" / "database-basics.json").read_text(encoding="utf-8"))

    @app.post("/api/import")
    async def import_cards(body: ImportRequest):
        async with pc.lock:
            entries = await pc.entries()
            records, _, _ = decode(entries)
            existing = {record["card"].id: record["card"] for record in records}
            incoming = {card.id: card for card in body.cards}
            if len(incoming) != len(body.cards):
                raise HTTPException(422, "导入文件中的卡片 ID 重复。")
            if len(set(existing) | set(incoming)) > MAX_CARDS:
                raise HTTPException(422, f"当前单人原型最多保存 {MAX_CARDS} 张卡片。")
            for card in body.cards:
                if card.id in existing:
                    original = CardInput.model_validate(
                        existing[card.id].model_dump(include=set(CardInput.model_fields))
                    )
                    if original != card:
                        raise HTTPException(409, f"卡片 {card.id} 内容与已存记录冲突，请使用新 ID。")
            # Write one at a time. A failed batch can be reimported: existing identities are skipped.
            added = 0
            revision = max((entry["citation"]["memory_ref"]["revision"] for entry in entries), default=None)
            for card in body.cards:
                if card.id in existing:
                    continue
                result = await pc.save(CARD_KIND, Card(**card.model_dump()), None, "用户导入知识卡片", revision)
                revision = result["memory"]["revision"]
                added += 1
            return {"added": added, "skipped": len(body.cards) - added, "scope_id": await pc.scope()}

    @app.post("/api/review")
    async def record_review(body: ReviewRequest):
        async with pc.lock:
            entries = await pc.entries()
            records, _, _ = decode(entries)
            record = next((record for record in records if record["card"].id == body.card_id), None)
            if record is None:
                raise HTTPException(404, "知识卡片不存在。")
            check_version(record, body.entry_version_id)
            updated = review(record["card"], body.rating, utc_now())
            result = await pc.save(CARD_KIND, updated, record, f"用户自评：{body.rating}", None)
            return {"card": updated, "receipt": result}

    @app.put("/api/preferences")
    async def update_preferences(body: PreferencesRequest):
        async with pc.lock:
            entries = await pc.entries()
            _, _, previous = decode(entries)
            check_version(previous, body.entry_version_id)
            preferences = Preferences.model_validate(body.model_dump(exclude={"entry_version_id"}))
            revision = max((entry["citation"]["memory_ref"]["revision"] for entry in entries), default=None)
            result = await pc.save(PREFERENCES_KIND, preferences, previous, "用户设置复习偏好", revision)
            return {"preferences": preferences, "receipt": result}

    @app.post("/api/notes/segments")
    async def split_notes(body: SegmentInput):
        return {"segments": [segment.model_dump() for segment in split_note(body.note)]}

    @app.post("/api/notes/candidates")
    async def note_candidates(body: CandidateRequest):
        # Candidates are response-only. Nothing is written until the user confirms the import.
        try:
            candidates = await generate_candidates(body)
        except RuntimeError as error:
            raise HTTPException(400 if llm_config() is None else 502, str(error)) from error
        return {"candidates": [candidate.model_dump() for candidate in candidates]}

    @app.get("/api/cards/{card_id}/history")
    async def get_card_history(card_id: str):
        async with pc.lock:
            entries = await pc.entries()
            records, _, _ = decode(entries)
            record = next((record for record in records if record["card"].id == card_id), None)
            if record is None:
                raise HTTPException(404, "知识卡片不存在。")
            head = record["citation"]["memory_ref"]["revision"]
            timeline = await card_history(pc, await pc.scope(), record["citation"]["entry_id"], head)
            return {"card_id": card_id, "timeline": timeline, "head_revision": head}

    @app.get("/api/weekly")
    async def get_weekly():
        async with pc.lock:
            entries = await pc.entries()
            revision = max((entry["citation"]["memory_ref"]["revision"] for entry in entries), default=None)
            if revision is None:
                return {"summary": None, "empty": True}
            summary = await weekly_summary(pc, await pc.scope(), revision)
            empty = summary["totals"]["reviews"] == 0 and summary["totals"]["new_cards"] == 0
            return {"summary": summary, "empty": empty}

    return app


app = create_app()
