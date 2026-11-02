"""Deterministic study rules; PowerContext owns all durable study records."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from unicodedata import normalize

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

MAX_CARDS = 100
CARD_KIND = "recall_card_v1"
PREFERENCES_KIND = "recall_preferences_v1"


def utc_now() -> datetime:
    return datetime.now(UTC)


class CardInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    topic: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=300)
    answer: str = Field(min_length=1, max_length=700)
    source: str = Field(min_length=1, max_length=200)

    @field_validator("topic", "question", "answer", "source", mode="before")
    @classmethod
    def normalize_text(cls, value):
        # Match PowerContext normalization so a reimport retains the same identity.
        return normalize("NFC", value) if isinstance(value, str) else value


class Card(CardInput):
    schema_version: Literal[1] = 1
    repetitions: int = Field(default=0, ge=0)
    lapses: int = Field(default=0, ge=0)
    streak: int = Field(default=0, ge=0)
    interval_days: int = Field(default=0, ge=0, le=60)
    due_at: AwareDatetime = Field(default_factory=utc_now)
    last_reviewed_at: AwareDatetime | None = None
    last_rating: Literal["again", "hard", "good"] | None = None


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    name: str = Field(default="学习者", min_length=1, max_length=32)
    daily_limit: int = Field(default=6, ge=1, le=20)


def review(card: Card, rating: Literal["again", "hard", "good"], now: datetime) -> Card:
    """Use explicit self-ratings, not an unverified claim of automatic grading."""
    if rating == "again":
        interval, streak = 0, 0
        due = now + timedelta(minutes=10)
    elif rating == "hard":
        interval, streak = 1, 0
        due = now + timedelta(days=1)
    else:
        streak = card.streak + 1
        interval = min(60, max(1, card.interval_days * 2))
        due = now + timedelta(days=interval)
    return card.model_copy(
        update={
            "repetitions": card.repetitions + 1,
            "lapses": card.lapses + (rating == "again"),
            "streak": streak,
            "interval_days": interval,
            "due_at": due,
            "last_reviewed_at": now,
            "last_rating": rating,
        }
    )


def make_plan(records: list[dict], preferences: Preferences, now: datetime) -> list[dict]:
    eligible = [record for record in records if record["card"].due_at <= now]
    eligible.sort(
        key=lambda record: (
            record["card"].last_rating != "again",
            record["card"].repetitions != 0,
            record["card"].due_at,
            record["card"].id,
        )
    )
    result = []
    for record in eligible[: preferences.daily_limit]:
        card = record["card"]
        reason = (
            "上次未掌握，优先巩固"
            if card.last_rating == "again"
            else ("还未复习，从这里开始" if card.repetitions == 0 else "已到复习时间，巩固记忆")
        )
        result.append({**record, "reason": reason})
    return result
