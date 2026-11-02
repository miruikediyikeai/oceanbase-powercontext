"""Review history and weekly summary read from real PowerContext revisions.

The Memory artifact keeps an immutable snapshot of entry version citations at
every revision. A card's timeline is rebuilt from those exact citations, never
from today's interpretation of current state.
"""

from datetime import UTC, timedelta
from typing import Any

from fastapi import HTTPException

from domain import CARD_KIND, Card, utc_now

WEEK_DAYS = 7


class RevisionReader:
    """Walk Memory artifact revisions and read exact entry versions."""

    def __init__(self, gateway):
        self.gateway = gateway
        self._revision_cache: dict[int, dict[str, dict]] = {}

    async def _get_revision(self, scope_id: str, revision: int) -> dict[str, dict]:
        if revision not in self._revision_cache:
            result = await self.gateway.get(f"/v1/scopes/{scope_id}/artifacts/memory/memory/revisions/{revision}")
            manifest = result.get("content", {}).get("manifest", {})
            self._revision_cache[revision] = {
                item["entry_id"]: item for item in manifest.get("entries", []) if item.get("state") == "active"
            }
        return self._revision_cache[revision]

    async def _get_entry(self, scope_id: str, manifest_item: dict, revision: int) -> dict:
        citation = {
            "memory_ref": {"family": "memory", "artifact_id": "memory", "revision": revision},
            "entry_id": manifest_item["entry_id"],
            "entry_version_id": manifest_item["entry_version_id"],
        }
        return await self.gateway.post("/v1/memory/entries/get", {"scope_id": scope_id, "citation": citation})


def _event_from_change(before: Card | None, after: Card) -> dict[str, Any] | None:
    if before is None:
        if after.last_reviewed_at is None:
            return {
                "kind": "import",
                "at": after.due_at,  # creation time approximation: first due_at
                "card": after,
                "lapses_delta": 0,
            }
        return None
    if after.last_reviewed_at is None or after.last_reviewed_at == before.last_reviewed_at:
        return None
    return {
        "kind": "review",
        "at": after.last_reviewed_at,
        "rating": after.last_rating,
        "lapses_delta": after.lapses - before.lapses,
        "next_due_at": after.due_at,
    }


async def card_history(gateway, scope_id: str, entry_id: str, head_revision: int) -> list[dict[str, Any]]:
    reader = RevisionReader(gateway)
    snapshots: list[tuple[int, str, Card]] = []
    for revision in range(head_revision, 0, -1):
        manifest = await reader._get_revision(scope_id, revision)
        item = manifest.get(entry_id)
        if item is None:
            break
        entry = await reader._get_entry(scope_id, item, revision)
        card = Card.model_validate_json(entry["text"])
        snapshots.append((revision, item["entry_version_id"], card))
    if not snapshots:
        raise HTTPException(404, "未找到该卡片的记忆记录。")

    timeline = []
    for position in range(len(snapshots) - 1, -1, -1):
        revision, entry_version_id, card = snapshots[position]
        before = snapshots[position + 1][2] if position + 1 < len(snapshots) else None
        event = _event_from_change(before, card)
        timeline.append(
            {
                "revision": revision,
                "entry_version_id": entry_version_id,
                "card": card,
                "event": event,
            }
        )
    return timeline


async def weekly_summary(gateway, scope_id: str, head_revision: int) -> dict[str, Any]:
    reader = RevisionReader(gateway)
    now = utc_now()
    start = (now - timedelta(days=WEEK_DAYS - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = now

    # entry_id -> (last seen entry_version_id, last seen card at or before the window)
    seen: dict[str, tuple[str, Card | None]] = {}
    review_events: list[dict[str, Any]] = []
    import_events: list[dict[str, Any]] = []

    for revision in range(1, head_revision + 1):
        manifest = await reader._get_revision(scope_id, revision)
        for entry_id, item in manifest.items():
            previous = seen.get(entry_id)
            card = None
            changed = previous is None or previous[0] != item["entry_version_id"]
            if changed:
                entry = await reader._get_entry(scope_id, item, revision)
                if entry.get("kind") != CARD_KIND:
                    seen[entry_id] = (item["entry_version_id"], None)
                    continue
                card = Card.model_validate_json(entry["text"])
            elif previous[1] is not None:
                card = previous[1]
            if card is not None:
                seen[entry_id] = (item["entry_version_id"], card)
            if changed:
                before = previous[1] if previous else None
                event = _event_from_change(before, card)
                if event and start <= event["at"] <= end:
                    event["entry_id"] = entry_id
                    if event["kind"] == "review":
                        review_events.append(event)
                    else:
                        import_events.append(event)

    reviews = sorted(review_events, key=lambda event: event["at"])
    daily: dict[str, dict[str, int]] = {}
    for event in reviews:
        key = event["at"].astimezone(UTC).date().isoformat()
        bucket = daily.setdefault(key, {"reviews": 0, "lapses": 0})
        bucket["reviews"] += 1
        bucket["lapses"] += event["lapses_delta"]
    return {
        "range": {"start": start, "end": end, "days": WEEK_DAYS, "timezone": "UTC"},
        "rule": (
            "统计范围：最近 7 天（含今天），按 UTC 计算。每次复习按卡片实际复习时间计入当天；"
            "遗忘次数按每次复习前后遗忘计数的差值累加。"
        ),
        "totals": {
            "reviews": len(reviews),
            "lapses": sum(event["lapses_delta"] for event in reviews),
            "new_cards": len(import_events),
        },
        "daily": [{"date": key, **value} for key, value in sorted(daily.items())],
    }
