import asyncio
from datetime import UTC, datetime

from domain import Card
from history import _event_from_change, weekly_summary

NOW = datetime(2026, 11, 2, 8, tzinfo=UTC)


def card(**overrides):
    defaults = dict(
        id="note-abc",
        topic="数据库",
        question="什么是事务？",
        answer="一组操作的集合。",
        source="笔记",
        repetitions=0,
        lapses=0,
        streak=0,
        interval_days=0,
        due_at=NOW,
        last_reviewed_at=None,
        last_rating=None,
    )
    defaults.update(overrides)
    return Card(**defaults)


def test_import_event_on_first_appearance():
    fresh = card()
    event = _event_from_change(None, fresh)
    assert event["kind"] == "import"


def test_review_event_tracks_lapses_delta():
    before = card()
    after = card(repetitions=1, lapses=1, last_reviewed_at=NOW, last_rating="again")
    event = _event_from_change(before, after)
    assert event["kind"] == "review"
    assert event["rating"] == "again"
    assert event["lapses_delta"] == 1


def test_review_without_new_timestamp_is_not_an_event():
    before = card(last_reviewed_at=NOW, repetitions=1)
    after = card(last_reviewed_at=NOW, repetitions=1, lapses=1)
    assert _event_from_change(before, after) is None


class FakeGateway:
    def __init__(self, revisions):
        self.revisions = {
            revision: {
                "content": {
                    "manifest": {
                        "entries": [
                            {
                                "entry_id": item["entry_id"],
                                "entry_version_id": item["entry_version_id"],
                                "state": "active",
                            }
                            for item in citations
                        ]
                    }
                }
            }
            for revision, citations in revisions.items()
        }

    async def get(self, path):
        revision = int(path.rsplit("/", 1)[1])
        return self.revisions[revision]

    async def post(self, path, payload):
        citation = payload["citation"]
        return {"text": self.entries[citation["entry_version_id"]], "kind": "recall_card_v1"}


async def _weekly_summary_only_counts_reviews_inside_window():
    outside = (NOW.replace(year=2020)).isoformat()
    gateway = FakeGateway(
        revisions={
            1: [{"entry_id": "e1", "entry_version_id": "v1"}],
            2: [{"entry_id": "e1", "entry_version_id": "v2"}],
            3: [{"entry_id": "e1", "entry_version_id": "v3"}],
        }
    )
    gateway.entries = {
        "v1": card().model_dump_json(),
        "v2": card(repetitions=1, last_reviewed_at=NOW, last_rating="good").model_dump_json(),
        "v3": card(repetitions=2, lapses=1, last_reviewed_at=NOW, last_rating="again").model_dump_json(),
    }
    # Force the "before" snapshot of v2 to look like an out-of-window review.
    gateway.entries["v2"] = card(
        repetitions=1, last_reviewed_at=datetime.fromisoformat(outside), last_rating="good"
    ).model_dump_json()
    summary = await weekly_summary(gateway, "scp_x", head_revision=3)
    assert summary["totals"]["reviews"] == 1
    assert summary["totals"]["lapses"] == 1
    assert summary["range"]["days"] == 7


def test_weekly_summary_only_counts_reviews_inside_window():
    asyncio.run(_weekly_summary_only_counts_reviews_inside_window())
