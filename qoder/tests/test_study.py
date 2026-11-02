from datetime import UTC, datetime, timedelta

import pytest

from domain import Card, Preferences, make_plan, review

NOW = datetime(2026, 11, 2, 8, tzinfo=UTC)


def card(**changes):
    return Card(id="sample", topic="测试", question="问题？", answer="答案。", source="自编", due_at=NOW, **changes)


def test_forgotten_card_is_due_in_ten_minutes_and_remains_a_weak_point():
    updated = review(card(repetitions=3, streak=2, interval_days=4), "again", NOW)
    assert updated.repetitions == 4
    assert updated.lapses == 1
    assert updated.streak == 0
    assert updated.due_at == NOW + timedelta(minutes=10)
    assert not make_plan([{"card": updated}], Preferences(), NOW)
    assert make_plan([{"card": updated}], Preferences(), NOW + timedelta(minutes=10))


@pytest.mark.parametrize("interval,expected", [(0, 1), (1, 2), (8, 16), (60, 60)])
def test_remembered_card_gets_a_bounded_longer_interval(interval, expected):
    updated = review(card(interval_days=interval), "good", NOW)
    assert updated.due_at == NOW + timedelta(days=expected)
    assert updated.streak == 1


def test_hard_card_resets_streak_and_is_due_tomorrow():
    updated = review(card(streak=3, interval_days=8), "hard", NOW)
    assert updated.due_at == NOW + timedelta(days=1)
    assert updated.streak == 0


def test_plan_prioritizes_due_weak_points_and_respects_saved_limit():
    weak = review(card(), "again", NOW - timedelta(minutes=20))
    new = card().model_copy(update={"id": "new"})
    later = card().model_copy(update={"id": "later", "due_at": NOW + timedelta(days=1)})
    plan = make_plan([{"card": new}, {"card": later}, {"card": weak}], Preferences(daily_limit=1), NOW)
    assert len(plan) == 1
    assert plan[0]["card"] == weak
    assert "上次未掌握" in plan[0]["reason"]
