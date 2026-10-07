"""Fill in what a quiz result leaves out: each question's category, difficulty and right answer.

Unity sends only ``(question_id, selected, response_ms)`` per question, to keep the datagram small and because the
question bank already knows the rest. The receiver looks the rest up so that a stored round is self-contained and the
dashboard can say which scam types a player finds hard, even after the bank changes later.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from shared.protocol import NO_ANSWER, QuizSessionEnd
from shared.questions import QuestionBank

LOGGER = logging.getLogger("motionplay.backend.quiz_results")


def enrich(result: QuizSessionEnd, bank: QuestionBank | None) -> QuizSessionEnd:
    """The same result with ``detail`` filled from the bank.

    Nothing here can reject a result: a round played against a different or newer bank, or one this receiver cannot
    read, is still stored. Questions the bank does not know get null category, difficulty and answer, and a warning is
    logged. A question id is never reused (see docs/quiz_phase0_protocol_design.md), so a known id is trusted even
    when the bank version differs."""
    if bank is None:
        return result
    if result.bankVersion != bank.version:
        LOGGER.warning("Result %s was played with question bank version %d; this receiver has version %d.",
                       result.session_id, result.bankVersion, bank.version)
    detail = []
    unknown = []
    for question_id, selected, response_ms in result.responses:
        question = bank.by_id(question_id)
        if question is None:
            unknown.append(question_id)
            detail.append({"question_id": question_id, "selected": selected, "response_ms": response_ms,
                           "category": None, "difficulty": None, "correct": None, "is_correct": None})
            continue
        detail.append({"question_id": question_id, "selected": selected, "response_ms": response_ms,
                       "category": question.category, "difficulty": question.difficulty, "correct": question.correct,
                       "is_correct": selected != NO_ANSWER and selected == question.correct})
    if unknown:
        LOGGER.warning("Result %s has %d question(s) that are not in the bank: %s", result.session_id, len(unknown),
                       ", ".join(unknown))
    elif sum(1 for item in detail if item["is_correct"]) != result.correct:
        # The sender's own count disagrees with its answers. Keep what was sent, but say so.
        LOGGER.warning("Result %s says %d correct but its answers give %d.", result.session_id, result.correct,
                       sum(1 for item in detail if item["is_correct"]))
    return replace(result, detail=tuple(detail))
