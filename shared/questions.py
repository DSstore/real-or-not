"""Scam quiz question bank: load and validate ``data/questions.json``, and pick the questions for one round.

The bank is plain data, so a mistake in it should be reported in full and early: ``parse_bank`` collects every problem
it finds instead of stopping at the first. Nothing here touches the network or the game; the picker is a pure function
of the bank, the level, the recently asked ids and a random generator, so tests can seed it.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BANK_PATH = PROJECT_ROOT / "data" / "questions.json"
DEFAULT_RECENT_PATH = PROJECT_ROOT / "data" / "recent_questions.json"

# Questions about everyday digital skills (apps, payments, settings) rather than scams. A round's last question is one of
# these, whatever the level, and no other question in a round is. See pick_round.
FINAL_CATEGORY = "digital_skills"
CATEGORIES = (
    # Scams
    "sms_phishing", "phone_scam", "email_phishing", "fake_website", "social_media_scam",
    "ecommerce_scam", "investment_scam", "love_scam", "job_loan_scam",
    # Staying safe and sensible online beyond scams
    "protect_personal_info", "misinformation", "genai", "messaging_safety", "banking_payments",
    "device_security", "digital_wellbeing", "singpass_services",
    FINAL_CATEGORY,
)
MIN_DIFFICULTY, MAX_DIFFICULTY = 1, 5
CHOICE_COUNT = 4  # Always four are written; the game shows fewer at low levels.
MIN_PER_DIFFICULTY = 10  # Below this, the easiest levels run out of fresh questions after a couple of rounds.
ROUND_LENGTH = 5
MAX_ROUND_LENGTH = 20
RECENT_ROUNDS = 3  # Rounds remembered when avoiding repeats.
SOURCE_PREFIX = "https://www.digitalforlife.gov.sg/"

_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_QUESTION_FIELDS = ("id", "category", "difficulty", "question", "choices", "correct", "explanation", "source")
_TEXT_LIMITS = {"question": 400, "explanation": 500, "source": 300}
_CHOICE_LIMIT = 120


class QuestionBankError(ValueError):
    """The bank is unreadable or invalid; ``problems`` lists everything found wrong."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    difficulty: int
    question: str
    choices: tuple[str, ...]
    correct: int
    explanation: str
    source: str


@dataclass(frozen=True)
class QuestionBank:
    version: int
    questions: tuple[Question, ...]

    def by_id(self, question_id: str) -> Question | None:
        return next((q for q in self.questions if q.id == question_id), None)

    def difficulty_counts(self) -> dict[int, int]:
        """Questions per difficulty, 1 to 5, including zeros. The final-question pool is left out: those questions are
        only ever asked last, so they cannot fill the rest of a round at any level."""
        counts = {level: 0 for level in range(MIN_DIFFICULTY, MAX_DIFFICULTY + 1)}
        for question in self.questions:
            if question.category != FINAL_CATEGORY:
                counts[question.difficulty] += 1
        return counts

    def final_count(self) -> int:
        """How many questions can be asked last."""
        return sum(1 for question in self.questions if question.category == FINAL_CATEGORY)


def _is_int(value: object) -> bool:
    return type(value) is int


def _is_text(value: object, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _parse_question(raw: object, position: int, problems: list[str]) -> Question | None:
    name = f"question {position}"
    if not isinstance(raw, dict):
        problems.append(f"{name} must be an object.")
        return None
    if isinstance(raw.get("id"), str) and raw["id"]:
        name = f"question {position} ({raw['id']})"
    start = len(problems)
    missing = [field for field in _QUESTION_FIELDS if field not in raw]
    unknown = sorted(set(raw) - set(_QUESTION_FIELDS))
    if missing:
        problems.append(f"{name} is missing {', '.join(missing)}.")
    if unknown:
        problems.append(f"{name} has unknown field(s) {', '.join(unknown)}.")
    if missing:
        return None
    if not (isinstance(raw["id"], str) and _ID.match(raw["id"])):
        problems.append(f"{name}: id must be 1-64 characters of a-z, 0-9, '-' or '_'.")
    if raw["category"] not in CATEGORIES:
        problems.append(f"{name}: unknown category {raw['category']!r}.")
    if not (_is_int(raw["difficulty"]) and MIN_DIFFICULTY <= raw["difficulty"] <= MAX_DIFFICULTY):
        problems.append(f"{name}: difficulty must be a whole number from {MIN_DIFFICULTY} to {MAX_DIFFICULTY}.")
    for field, limit in _TEXT_LIMITS.items():
        if not _is_text(raw[field], limit):
            problems.append(f"{name}: {field} must be non-empty text of at most {limit} characters.")
    if _is_text(raw["source"], _TEXT_LIMITS["source"]) and not raw["source"].startswith(SOURCE_PREFIX):
        problems.append(f"{name}: source must be a page on {SOURCE_PREFIX}")
    choices = raw["choices"]
    if not (isinstance(choices, list) and len(choices) == CHOICE_COUNT
            and all(_is_text(choice, _CHOICE_LIMIT) for choice in choices)):
        problems.append(f"{name}: choices must be a list of exactly {CHOICE_COUNT} non-empty texts "
                        f"of at most {_CHOICE_LIMIT} characters.")
    elif len({choice.strip().lower() for choice in choices}) != CHOICE_COUNT:
        problems.append(f"{name}: choices must all be different.")
    if not (_is_int(raw["correct"]) and 0 <= raw["correct"] < CHOICE_COUNT):
        problems.append(f"{name}: correct must be a whole number from 0 to {CHOICE_COUNT - 1}.")
    if len(problems) > start:
        return None
    return Question(raw["id"], raw["category"], raw["difficulty"], raw["question"].strip(), tuple(choices),
                    raw["correct"], raw["explanation"].strip(), raw["source"])


def parse_bank(data: object) -> QuestionBank:
    """Validate decoded JSON and return the bank, or raise ``QuestionBankError`` listing every problem."""
    if not isinstance(data, dict):
        raise QuestionBankError(["The question file must hold a JSON object."])
    problems: list[str] = []
    unknown = sorted(set(data) - {"version", "questions"})
    if unknown:
        problems.append(f"Unknown top-level field(s): {', '.join(unknown)}.")
    version = data.get("version")
    if not (_is_int(version) and version >= 1):
        problems.append("version must be a whole number of at least 1.")
    raw_questions = data.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        problems.append("questions must be a non-empty list.")
        raise QuestionBankError(problems)
    parsed = [_parse_question(raw, position, problems) for position, raw in enumerate(raw_questions, start=1)]
    seen: set[str] = set()
    for question in parsed:
        if question is None:
            continue
        if question.id in seen:
            problems.append(f"Duplicate question id {question.id!r}.")
        seen.add(question.id)
    if problems:
        raise QuestionBankError(problems)
    return QuestionBank(version, tuple(q for q in parsed if q is not None))


def load_bank(path: Path | str = DEFAULT_BANK_PATH) -> QuestionBank:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise QuestionBankError([f"Cannot read the question file: {error.strerror or error}"]) from error
    try:
        data = json.loads(text)
    except ValueError as error:
        raise QuestionBankError([f"The question file is not valid JSON: {error}"]) from error
    return parse_bank(data)


def coverage_shortfalls(bank: QuestionBank, minimum: int = MIN_PER_DIFFICULTY) -> dict[int, int]:
    """Difficulty -> how many more questions it needs to reach ``minimum``; empty when covered."""
    return {level: minimum - count for level, count in bank.difficulty_counts().items() if count < minimum}


def pick_round(bank: QuestionBank, level: int, count: int = ROUND_LENGTH, recent: Iterable[str] = (),
               rng: random.Random | None = None) -> list[Question]:
    """Choose up to ``count`` different questions for a round at ``level`` (difficulty 1 to ``level``).

    If the bank has final-question pool questions (category ``FINAL_CATEGORY``), the last question of the round is one
    of them, whatever the level, and the other ``count - 1`` come from the rest of the bank. The final question
    prefers one not asked recently.

    Each of the others prefers, in order: a question not asked recently from a category not yet used this round; any
    question not asked recently; a recent question from an unused category; any remaining question. So repeats and
    one-topic rounds only happen when the pool is too small to avoid them. Fewer than ``count`` are returned only if
    fewer exist. Those others are ordered easiest first (ties keep their random order), so a round eases the players in.
    """
    if not (_is_int(level) and MIN_DIFFICULTY <= level <= MAX_DIFFICULTY):
        raise ValueError(f"level must be a whole number from {MIN_DIFFICULTY} to {MAX_DIFFICULTY}.")
    if not (_is_int(count) and 1 <= count <= MAX_ROUND_LENGTH):
        raise ValueError(f"count must be a whole number from 1 to {MAX_ROUND_LENGTH}.")
    rng = rng if rng is not None else random.Random()
    recent_ids = set(recent)
    finals = [q for q in bank.questions if q.category == FINAL_CATEGORY]
    regular_count = count - 1 if finals else count
    remaining = [q for q in bank.questions if q.difficulty <= level and q.category != FINAL_CATEGORY]
    chosen: list[Question] = []
    used_categories: set[str] = set()
    while remaining and len(chosen) < regular_count:
        fresh = [q for q in remaining if q.id not in recent_ids]
        tiers = ([q for q in fresh if q.category not in used_categories], fresh,
                 [q for q in remaining if q.category not in used_categories], remaining)
        pick = rng.choice(next(tier for tier in tiers if tier))
        chosen.append(pick)
        used_categories.add(pick.category)
        remaining.remove(pick)
    chosen.sort(key=lambda q: q.difficulty)
    if finals:
        chosen.append(rng.choice([q for q in finals if q.id not in recent_ids] or finals))
    return chosen


def load_recent(path: Path | str = DEFAULT_RECENT_PATH) -> list[str]:
    """Ids asked in the last few rounds, oldest first. A missing or damaged file means none."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ids = data.get("ids") if isinstance(data, dict) else None
    return [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else []


def remember_round(questions: Iterable[Question], path: Path | str = DEFAULT_RECENT_PATH,
                   keep: int = RECENT_ROUNDS * ROUND_LENGTH) -> list[str]:
    """Record a finished round's questions, keeping the newest ``keep`` ids. Never raises: a file that cannot be
    written only means the same questions may come up again."""
    combined = [*load_recent(path), *(question.id for question in questions)]
    newest_first: list[str] = []
    for question_id in reversed(combined):
        if question_id not in newest_first:
            newest_first.append(question_id)
    ids = list(reversed(newest_first[:keep]))
    target = Path(path)
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps({"ids": ids}, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, target)
    except OSError as error:
        LOGGER.warning("Could not save the recently asked questions: %s", error.strerror or error)
    return ids
