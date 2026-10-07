"""Scam quiz dashboard numbers and text, with no Qt dependency so they are easy to test.

Input is the list of stored quiz round documents for one player (see backend.storage). Each document holds one
player's round: scalar totals plus one ``responses`` entry per question, which the receiver filled with the question's
category, difficulty and right answer (``None`` for a question the bank did not know). A metric with no samples is
None and is shown as a dash, never as zero. Skipped questions (time ran out) count as asked and not correct.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.dashboard_model import NO_VALUE, duration, level_of, level_text, percent, seconds, when
from shared.protocol import QUIZ_GAME
from shared.questions import FINAL_CATEGORY

CATEGORY_LABELS = {
    "sms_phishing": "SMS scams",
    "phone_scam": "Phone call scams",
    "email_phishing": "Email scams",
    "fake_website": "Fake websites",
    "social_media_scam": "Social media scams",
    "ecommerce_scam": "Online shopping scams",
    "investment_scam": "Investment scams",
    "love_scam": "Love scams",
    "job_loan_scam": "Job and loan scams",
    "protect_personal_info": "Protecting personal info",
    "misinformation": "Fake news",
    "genai": "AI and deepfakes",
    "messaging_safety": "Messaging safety",
    "banking_payments": "Banking and payments",
    "device_security": "Device security",
    "digital_wellbeing": "Digital wellbeing",
    "singpass_services": "Singpass and government",
    FINAL_CATEGORY: "Digital skills (challenge)",
}
TABLE_HEADERS = ["Played", "Level", "Correct", "Accuracy", "Answer time", "Best streak", "Time"]


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category.replace("_", " ").capitalize())


def _mean(values: list[float]) -> float | None:
    usable = [v for v in values if math.isfinite(v)]
    return sum(usable) / len(usable) if usable else None


def _answer_times(document: dict) -> list[float]:
    """Seconds taken on each answered question; skipped questions have no time."""
    return [item["response_ms"] / 1000 for item in document["responses"] if item.get("response_ms") is not None]


def accuracy_of(document: dict) -> float | None:
    total = document["totalQuestions"]
    return document["correct"] / total if total else None


@dataclass(frozen=True)
class Summary:
    rounds: int
    questions: int
    correct: int
    skipped: int
    accuracy: float | None
    best_streak: int
    average_answer_time: float | None  # Over every answered question, not the mean of round averages.
    latest_level: int | None  # The level of the most recent round.
    last_played: int | None  # UTC milliseconds
    total_seconds: float
    challenge_asked: int
    challenge_correct: int

    @property
    def challenge_accuracy(self) -> float | None:
        return self.challenge_correct / self.challenge_asked if self.challenge_asked else None


@dataclass(frozen=True)
class Trend:
    """Oldest round first."""

    accuracy: list[float | None]
    answer_time: list[float | None]


@dataclass(frozen=True)
class CategoryScore:
    category: str
    asked: int
    correct: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.asked

    @property
    def label(self) -> str:
        return category_label(self.category)


def summarize(documents: list[dict]) -> Summary:
    ordered = sorted(documents, key=lambda d: (d["endedAt"], d["session_id"]))
    questions = sum(d["totalQuestions"] for d in documents)
    correct = sum(d["correct"] for d in documents)
    times = [t for d in documents for t in _answer_times(d)]
    challenge = [item for d in documents for item in d["responses"] if item.get("category") == FINAL_CATEGORY]
    return Summary(
        rounds=len(documents),
        questions=questions,
        correct=correct,
        skipped=sum(d["skipped"] for d in documents),
        accuracy=correct / questions if questions else None,
        best_streak=max((d["bestStreak"] for d in documents), default=0),
        average_answer_time=_mean(times),
        latest_level=level_of(ordered[-1]) if ordered else None,
        last_played=ordered[-1]["endedAt"] if ordered else None,
        total_seconds=sum(d["duration"] for d in documents),
        challenge_asked=len(challenge),
        challenge_correct=sum(1 for item in challenge if item.get("is_correct")),
    )


def trend(documents: list[dict]) -> Trend:
    ordered = sorted(documents, key=lambda d: (d["endedAt"], d["session_id"]))
    return Trend(accuracy=[accuracy_of(d) for d in ordered], answer_time=[d["averageResponseTime"] for d in ordered])


def category_scores(documents: list[dict]) -> list[CategoryScore]:
    """Per topic, weakest first (lowest accuracy, then the topic asked most often, then name).

    Questions the receiver could not look up (no category) are left out: they cannot be placed in a topic."""
    asked: dict[str, int] = {}
    right: dict[str, int] = {}
    for document in documents:
        for item in document["responses"]:
            category = item.get("category")
            if category is None:
                continue
            asked[category] = asked.get(category, 0) + 1
            right[category] = right.get(category, 0) + (1 if item.get("is_correct") else 0)
    scores = [CategoryScore(category, asked[category], right[category]) for category in asked]
    return sorted(scores, key=lambda s: (s.accuracy, -s.asked, s.category))


def weakest(scores: list[CategoryScore], minimum_asked: int = 2) -> CategoryScore | None:
    """The topic to practise next: the lowest accuracy among topics asked at least ``minimum_asked`` times.

    One unlucky question is not a weakness, so a topic needs a couple of answers before it is named. A topic
    answered perfectly is not a weakness either, so a pure-100% history has none."""
    candidates = [s for s in scores if s.asked >= minimum_asked and s.correct < s.asked]
    return candidates[0] if candidates else None  # `scores` is already weakest first


def table_row(document: dict) -> list[str]:
    """Cells for one round, in the dashboard's column order."""
    return [
        when(document["endedAt"]),
        level_text(document),
        f"{document['correct']}/{document['totalQuestions']}",
        percent(accuracy_of(document)),
        seconds(document["averageResponseTime"]),
        str(document["bestStreak"]),
        duration(document["duration"]),
    ]


__all__ = ["CATEGORY_LABELS", "CategoryScore", "NO_VALUE", "QUIZ_GAME", "Summary", "TABLE_HEADERS", "Trend",
           "accuracy_of", "category_label", "category_scores", "summarize", "table_row", "trend", "weakest"]
