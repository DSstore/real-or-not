"""Dashboard numbers and text, with no Qt dependency so they are easy to test.

Input is the list of stored round documents for one player (see backend.storage). A metric
with no samples is None and is shown as a dash, never as zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

NO_VALUE = "-"
# These views understand only Reach Garden rounds. The store also keeps scam quiz rounds, whose documents have a
# different shape, so every read for these views must ask for this game by name.
GARDEN_GAME = "reach_garden"


def _mean(values: Iterable[float | None]) -> float | None:
    usable = [v for v in values if v is not None and math.isfinite(v)]
    return sum(usable) / len(usable) if usable else None


@dataclass(frozen=True)
class Summary:
    rounds: int
    targets_attempted: int
    targets_completed: int
    accuracy: float | None
    best_streak: int
    total_seconds: float
    average_reaction: float | None
    average_movement: float | None
    average_stability: float | None
    average_efficiency: float | None
    last_played: int | None  # UTC milliseconds


@dataclass(frozen=True)
class Trend:
    """Oldest round first; None marks a round with no value for that metric."""

    accuracy: list[float | None]
    reaction: list[float | None]


def summarize(documents: list[dict]) -> Summary:
    attempted = sum(d["targetsAttempted"] for d in documents)
    completed = sum(d["targetsCompleted"] for d in documents)
    return Summary(
        rounds=len(documents),
        targets_attempted=attempted,
        targets_completed=completed,
        accuracy=completed / attempted if attempted else None,
        best_streak=max((d["bestStreak"] for d in documents), default=0),
        total_seconds=sum(d["duration"] for d in documents),
        average_reaction=_mean(d["averageReactionTime"] for d in documents),
        average_movement=_mean(d["averageMovementTime"] for d in documents),
        average_stability=_mean(d["averageHoldStability"] for d in documents),
        average_efficiency=_mean(d["pathEfficiency"] for d in documents),
        last_played=max((d["endedAt"] for d in documents), default=None),
    )


def trend(documents: list[dict]) -> Trend:
    ordered = sorted(documents, key=lambda d: (d["endedAt"], d["session_id"]))
    return Trend(accuracy=[d["accuracy"] for d in ordered], reaction=[d["averageReactionTime"] for d in ordered])


def percent(value: float | None) -> str:
    return NO_VALUE if value is None else f"{value * 100:.0f}%"


def seconds(value: float | None) -> str:
    return NO_VALUE if value is None else f"{value:.2f} s"


def duration(value: float | None) -> str:
    """Whole minutes and seconds, e.g. 3 min 05 s."""
    if value is None:
        return NO_VALUE
    total = int(round(value))
    return f"{total // 60} min {total % 60:02d} s" if total >= 60 else f"{total} s"


def when(milliseconds: int | None) -> str:
    """Local date and time of a UTC-millisecond timestamp."""
    if milliseconds is None:
        return NO_VALUE
    return datetime.fromtimestamp(milliseconds / 1000).astimezone().strftime("%Y-%m-%d %H:%M")


LEVEL_PREFIX = "level_"
MIN_LEVEL, MAX_LEVEL, DEFAULT_LEVEL = 1, 5, 3


def level_of(document: dict) -> int | None:
    """Difficulty level a round was played at, from its ``difficulty`` label.

    ``level_1`` to ``level_5`` are adaptive levels. ``default`` (rounds saved before adaptive difficulty)
    used the original rules, which are level 3. Anything else is unknown (None)."""
    label = document.get("difficulty")
    if label == "default":
        return DEFAULT_LEVEL
    if isinstance(label, str) and label.startswith(LEVEL_PREFIX):
        digits = label[len(LEVEL_PREFIX):]
        if digits.isascii() and digits.isdigit() and MIN_LEVEL <= int(digits) <= MAX_LEVEL:
            return int(digits)
    return None


def level_text(document: dict) -> str:
    level = level_of(document)
    return NO_VALUE if level is None else str(level)


def mixed_levels(documents: list[dict]) -> bool:
    """True if the rounds were played at more than one known difficulty level."""
    return len({level for level in map(level_of, documents) if level is not None}) > 1


def table_row(document: dict) -> list[str]:
    """Cells for one round, in the dashboard's column order."""
    return [
        when(document["endedAt"]),
        document["hand"],
        level_text(document),
        f"{document['targetsCompleted']}/{document['targetsAttempted']}",
        percent(document["accuracy"]),
        seconds(document["averageReactionTime"]),
        seconds(document["averageMovementTime"]),
        percent(document["averageHoldStability"]),
        percent(document["pathEfficiency"]),
        duration(document["duration"]),
    ]


TABLE_HEADERS = ["Played", "Hand", "Level", "Watered", "Accuracy", "Reaction", "Movement", "Stability", "Efficiency", "Time"]
