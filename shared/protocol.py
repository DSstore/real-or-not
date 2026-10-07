"""Versioned JSON wire models; no CV, UI, database, or socket dependencies."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field, fields
from uuid import UUID


PROTOCOL_VERSION = 1
MAX_DATAGRAM_BYTES = 1200
MAX_WIRE_INTEGER = 2**63 - 1
MAX_SLOTS = 2  # Players that can share one camera.
GESTURES = frozenset({"OPEN_HAND", "FIST", "PINCH", "POINT", "UNKNOWN"})


class ProtocolError(ValueError):
    """A packet violates the supported wire contract."""


def _number(value: object, low: float | None = None, high: float | None = None) -> bool:
    """Reject booleans, nonfinite values, and coordinates outside their range."""
    if type(value) not in (int, float):
        return False
    try:
        finite = math.isfinite(value)
    except OverflowError:
        return False
    return (
        finite
        and (low is None or value >= low) and (high is None or value <= high)
    )


@dataclass(frozen=True)
class Position:
    """Filtered palm coordinates; z is relative model depth, not meters."""

    x: float
    y: float
    z: float


@dataclass(frozen=True)
class CVState:
    """One selected hand's current state, never a queued frame or gesture event."""

    stream_id: str
    sequence: int
    timestamp: int
    hand: str
    tracking: bool
    position: Position | None
    gesture: str
    confidence: float
    mirrored: bool
    # Which player this stream belongs to when two people share one camera (0 or 1). None for the original
    # single-player stream, which then leaves the field out of the packet entirely.
    slot: int | None = None

    def __post_init__(self) -> None:
        if self.slot is not None and (type(self.slot) is not int or not 0 <= self.slot < MAX_SLOTS):
            raise ProtocolError(f"slot must be omitted or a whole number from 0 to {MAX_SLOTS - 1}.")
        try:
            valid_id = isinstance(self.stream_id, str) and str(UUID(self.stream_id)) == self.stream_id
        except ValueError:
            valid_id = False
        if not valid_id:
            raise ProtocolError("stream_id must be a canonical UUID string.")
        if any(type(value) is not int or not 0 <= value <= MAX_WIRE_INTEGER
               for value in (self.sequence, self.timestamp)):
            raise ProtocolError("sequence and timestamp must be nonnegative signed-64-bit integers.")
        if (not isinstance(self.hand, str) or self.hand not in {"left", "right"}
                or not isinstance(self.gesture, str) or self.gesture not in GESTURES):
            raise ProtocolError("Unsupported hand or gesture.")
        if type(self.tracking) is not bool or type(self.mirrored) is not bool:
            raise ProtocolError("tracking and mirrored must be booleans.")
        if not _number(self.confidence, 0, 1):
            raise ProtocolError("confidence must be finite and in [0, 1].")
        if self.tracking:
            if not isinstance(self.position, Position):
                raise ProtocolError("A tracked hand requires a position.")
            if not (_number(self.position.x, 0, 1) and _number(self.position.y, 0, 1)
                    and _number(self.position.z)):
                raise ProtocolError("Position requires finite z and x/y in [0, 1].")
        elif self.position is not None or self.gesture != "UNKNOWN" or self.confidence != 0:
            raise ProtocolError("Lost tracking requires null position, UNKNOWN gesture, and zero confidence.")

    def to_bytes(self) -> bytes:
        """Serialize one complete UTF-8 JSON object with finite numeric values."""
        data = {"type": "CV_STATE", "version": PROTOCOL_VERSION, **asdict(self)}
        if self.slot is None:
            del data["slot"]
        payload = json.dumps(data, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(payload) > MAX_DATAGRAM_BYTES:
            raise ProtocolError("CV_STATE exceeds the datagram size limit.")
        return payload


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON keys rather than silently accepting the last one."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("Duplicate JSON key.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    """JSON forbids NaN/Infinity, including in unrecognized extension fields."""
    raise ProtocolError("Nonfinite JSON constant.")


def decode_cv_state(payload: bytes) -> CVState:
    """Validate a diagnostic packet; optional extra keys allow additive evolution."""
    if not payload or len(payload) > MAX_DATAGRAM_BYTES:
        raise ProtocolError("Empty or oversized datagram.")
    try:
        data = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
        if not isinstance(data, dict) or data.get("type") != "CV_STATE":
            raise ProtocolError("Expected a CV_STATE object.")
        if type(data.get("version")) is not int or data["version"] != PROTOCOL_VERSION:
            raise ProtocolError("Unsupported protocol version.")
        position = data["position"]
        if position is not None:
            if not isinstance(position, dict):
                raise ProtocolError("position must be an object or null.")
            position = Position(position["x"], position["y"], position["z"])
        return CVState(
            stream_id=data["stream_id"], sequence=data["sequence"], timestamp=data["timestamp"],
            hand=data["hand"], tracking=data["tracking"], position=position,
            gesture=data["gesture"], confidence=data["confidence"], mirrored=data["mirrored"],
            slot=data.get("slot"),
        )
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError) as error:
        raise ProtocolError("Invalid CV_STATE packet; see docs/udp_protocol.md.") from error


# ---------------------------------------------------------------------------
# Unity -> Python result messages (Phase 10)
# ---------------------------------------------------------------------------

ACK_STATUSES = frozenset({"stored", "duplicate", "rejected", "error"})
_RATIO_FIELDS = ("accuracy", "averageHoldStability", "pathEfficiency")
_TIME_FIELDS = ("averageReactionTime", "averageMovementTime")
_COUNT_FIELDS = ("score", "targetsAttempted", "targetsCompleted", "currentStreak", "bestStreak")


def _canonical_uuid(value: object) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


@dataclass(frozen=True)
class SessionEnd:
    """One finished game round. A null metric means no samples, never zero."""

    stream_id: str
    sequence: int
    timestamp: int
    session_id: str
    game: str
    hand: str
    difficulty: str
    startedAt: int
    endedAt: int
    duration: float
    score: int
    targetsAttempted: int
    targetsCompleted: int
    currentStreak: int
    bestStreak: int
    accuracy: float | None
    averageReactionTime: float | None
    averageMovementTime: float | None
    averageHoldStability: float | None
    pathEfficiency: float | None

    def __post_init__(self) -> None:
        if not (_canonical_uuid(self.stream_id) and _canonical_uuid(self.session_id)):
            raise ProtocolError("stream_id and session_id must be canonical UUID strings.")
        integers = (self.sequence, self.timestamp, self.startedAt, self.endedAt,
                    *(getattr(self, name) for name in _COUNT_FIELDS))
        if any(type(value) is not int or not 0 <= value <= MAX_WIRE_INTEGER for value in integers):
            raise ProtocolError("Counts, sequence, and timestamps must be nonnegative signed-64-bit integers.")
        if (not isinstance(self.game, str) or not 1 <= len(self.game) <= 64
                or not isinstance(self.difficulty, str) or not 1 <= len(self.difficulty) <= 32):
            raise ProtocolError("game and difficulty must be short nonempty strings.")
        if not isinstance(self.hand, str) or self.hand not in {"left", "right"}:
            raise ProtocolError("hand must be left or right.")
        if self.endedAt < self.startedAt:
            raise ProtocolError("endedAt must not precede startedAt.")
        if not _number(self.duration, 0):
            raise ProtocolError("duration must be finite and nonnegative.")
        if self.targetsCompleted > self.targetsAttempted or self.score > self.targetsAttempted:
            raise ProtocolError("Completed targets and score cannot exceed attempted targets.")
        if not self.currentStreak <= self.bestStreak <= self.targetsCompleted:
            raise ProtocolError("Streaks are inconsistent with completed targets.")
        for name in _RATIO_FIELDS:
            value = getattr(self, name)
            if value is not None and not _number(value, 0, 1):
                raise ProtocolError(f"{name} must be null or in [0, 1].")
        for name in _TIME_FIELDS:
            value = getattr(self, name)
            if value is not None and not _number(value, 0):
                raise ProtocolError(f"{name} must be null or finite and nonnegative.")

    def to_document(self) -> dict[str, object]:
        """Storage form: the validated fields without the wire envelope."""
        return asdict(self)

    def to_bytes(self) -> bytes:
        data = {"type": "SESSION_END", "version": PROTOCOL_VERSION, **asdict(self)}
        payload = json.dumps(data, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(payload) > MAX_DATAGRAM_BYTES:
            raise ProtocolError("SESSION_END exceeds the datagram size limit.")
        return payload


def decode_session_end(payload: bytes) -> SessionEnd:
    """Validate a Unity result packet. Every field is required; nullable ones as explicit null."""
    if not payload or len(payload) > MAX_DATAGRAM_BYTES:
        raise ProtocolError("Empty or oversized datagram.")
    try:
        data = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
        if not isinstance(data, dict) or data.get("type") != "SESSION_END":
            raise ProtocolError("Expected a SESSION_END object.")
        if type(data.get("version")) is not int or data["version"] != PROTOCOL_VERSION:
            raise ProtocolError("Unsupported protocol version.")
        fields = {name: data[name] for name in SessionEnd.__dataclass_fields__}
        return SessionEnd(**fields)
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError) as error:
        raise ProtocolError("Invalid SESSION_END packet; see docs/udp_protocol.md.") from error


RESULT_MESSAGE_TYPES = ("SESSION_END", "QUIZ_SESSION_END")


def peek_message_type(payload: bytes) -> str | None:
    """Which result message a datagram claims to be (without validating it), or None."""
    try:
        data = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        return None
    kind = data.get("type") if isinstance(data, dict) else None
    return kind if kind in RESULT_MESSAGE_TYPES else None


def peek_session_id(payload: bytes) -> str | None:
    """Best-effort session_id of a malformed result message so the sender can be told to stop retrying."""
    try:
        data = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        return None
    if (isinstance(data, dict) and data.get("type") in RESULT_MESSAGE_TYPES
            and _canonical_uuid(data.get("session_id"))):
        return data["session_id"]
    return None


# ---------------------------------------------------------------------------
# Unity -> Python scam quiz result (one per player per round)
# ---------------------------------------------------------------------------

QUIZ_MESSAGE = "QUIZ_SESSION_END"
QUIZ_GAME = "scam_quiz"
MAX_QUIZ_QUESTIONS = 20
NO_ANSWER = -1  # `selected` of a question where time ran out
QUIZ_CHOICES = 4
MAX_RESPONSE_MS = 3_600_000
_QUESTION_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_QUIZ_LEVELS = frozenset(f"level_{n}" for n in range(1, 6))
_TIME_TOLERANCE = 0.002  # Seconds: the sender rounds each response to whole milliseconds.
_QUIZ_COUNTS = ("sequence", "timestamp", "startedAt", "endedAt", "bankVersion", "totalQuestions", "correct", "wrong",
                "skipped", "bestStreak")
_NOT_ON_WIRE = ("responses", "detail")


@dataclass(frozen=True)
class QuizSessionEnd:
    """One player's finished scam quiz round. A null time means no answers, never zero.

    ``responses`` holds ``(question_id, selected, response_ms)`` per question: ``selected`` is the index into the
    question's written choices (0 to 3) or -1 if time ran out, and ``response_ms`` is whole tracked milliseconds, null
    exactly when ``selected`` is -1. Category, difficulty and which answer was right are not sent; the receiver
    looks them up and stores them in ``detail``, which is never part of the wire message."""

    stream_id: str
    sequence: int
    timestamp: int
    session_id: str
    game: str
    hand: str
    difficulty: str
    startedAt: int
    endedAt: int
    duration: float
    roundId: str
    slot: int
    bankVersion: int
    totalQuestions: int
    correct: int
    wrong: int
    skipped: int
    bestStreak: int
    averageResponseTime: float | None
    fastestResponse: float | None
    slowestResponse: float | None
    responses: tuple
    detail: tuple | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.responses, (tuple, list)) or not all(
                isinstance(item, (tuple, list)) and len(item) == 3 for item in self.responses):
            raise ProtocolError("responses must be a list of [question_id, selected, response_ms] entries.")
        object.__setattr__(self, "responses", tuple(tuple(item) for item in self.responses))
        if not all(_canonical_uuid(value) for value in (self.stream_id, self.session_id, self.roundId)):
            raise ProtocolError("stream_id, session_id and roundId must be canonical UUID strings.")
        if any(type(getattr(self, name)) is not int or not 0 <= getattr(self, name) <= MAX_WIRE_INTEGER
               for name in _QUIZ_COUNTS):
            raise ProtocolError("Counts, sequence, and timestamps must be nonnegative signed-64-bit integers.")
        if type(self.slot) is not int or not 0 <= self.slot < MAX_SLOTS:
            raise ProtocolError(f"slot must be a whole number from 0 to {MAX_SLOTS - 1}.")
        if self.bankVersion < 1:
            raise ProtocolError("bankVersion must be at least 1.")
        if not isinstance(self.game, str) or self.game != QUIZ_GAME:
            raise ProtocolError(f"game must be {QUIZ_GAME!r}.")
        if not isinstance(self.hand, str) or self.hand not in {"left", "right"}:
            raise ProtocolError("hand must be left or right.")
        if not isinstance(self.difficulty, str) or self.difficulty not in _QUIZ_LEVELS:
            raise ProtocolError("difficulty must be level_1 to level_5.")
        if self.endedAt < self.startedAt:
            raise ProtocolError("endedAt must not precede startedAt.")
        if not _number(self.duration, 0):
            raise ProtocolError("duration must be finite and nonnegative.")
        self._check_answers()

    def _check_answers(self) -> None:
        if not 1 <= self.totalQuestions <= MAX_QUIZ_QUESTIONS or len(self.responses) != self.totalQuestions:
            raise ProtocolError(f"A round has 1 to {MAX_QUIZ_QUESTIONS} questions and one response for each.")
        if self.correct + self.wrong + self.skipped != self.totalQuestions:
            raise ProtocolError("correct, wrong and skipped must add up to totalQuestions.")
        seen: set[str] = set()
        milliseconds: list[int] = []
        for question_id, selected, response_ms in self.responses:
            if not isinstance(question_id, str) or not _QUESTION_ID.match(question_id) or question_id in seen:
                raise ProtocolError("Each response needs a different, valid question id.")
            seen.add(question_id)
            if type(selected) is not int or not NO_ANSWER <= selected < QUIZ_CHOICES:
                raise ProtocolError("selected must be -1 or a choice number from 0 to 3.")
            if selected == NO_ANSWER:
                if response_ms is not None:
                    raise ProtocolError("A question where time ran out has no response time.")
            else:
                if type(response_ms) is not int or not 0 <= response_ms <= MAX_RESPONSE_MS:
                    raise ProtocolError("An answered question needs a whole-millisecond response time.")
                milliseconds.append(response_ms)
        if self.skipped != len(self.responses) - len(milliseconds) or self.correct + self.wrong != len(milliseconds):
            raise ProtocolError("skipped, correct and wrong do not match the responses.")
        if self.bestStreak > self.correct:
            raise ProtocolError("bestStreak cannot exceed correct.")
        times = (self.averageResponseTime, self.fastestResponse, self.slowestResponse)
        if not milliseconds:
            if any(value is not None for value in times):
                raise ProtocolError("Response times must be null when nothing was answered.")
            return
        if any(value is None or not _number(value, 0) for value in times):
            raise ProtocolError("Response times must be finite and nonnegative when something was answered.")
        actual = (sum(milliseconds) / len(milliseconds) / 1000, min(milliseconds) / 1000, max(milliseconds) / 1000)
        if any(abs(claimed - real) > _TIME_TOLERANCE for claimed, real in zip(times, actual)):
            raise ProtocolError("Response times do not match the response log.")

    def to_document(self) -> dict[str, object]:
        """Storage form: scalar fields, and one dict per question (with category and so on once the receiver has
        looked them up; null until then)."""
        document = {f.name: getattr(self, f.name) for f in fields(self) if f.name not in _NOT_ON_WIRE}
        if self.detail is not None:
            document["responses"] = [dict(item) for item in self.detail]
        else:
            document["responses"] = [
                {"question_id": q, "selected": s, "response_ms": ms, "category": None, "difficulty": None,
                 "correct": None, "is_correct": None} for q, s, ms in self.responses]
        return document

    @classmethod
    def from_document(cls, document: dict) -> "QuizSessionEnd":
        """Rebuild from a stored document (for example when importing a results file)."""
        try:
            values = {f.name: document[f.name] for f in fields(cls) if f.name not in _NOT_ON_WIRE}
            stored = document["responses"]
            detail = tuple(dict(item) for item in stored)
            responses = tuple((item["question_id"], item["selected"], item["response_ms"]) for item in stored)
        except (KeyError, TypeError, ValueError) as error:
            raise ProtocolError("Not a stored quiz result.") from error
        looked_up = any(item.get("category") is not None for item in detail)
        return cls(**values, responses=responses, detail=detail if looked_up else None)

    def to_bytes(self) -> bytes:
        data = {"type": QUIZ_MESSAGE, "version": PROTOCOL_VERSION}
        data.update({f.name: getattr(self, f.name) for f in fields(self) if f.name not in _NOT_ON_WIRE})
        data["responses"] = [list(item) for item in self.responses]
        payload = json.dumps(data, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(payload) > MAX_DATAGRAM_BYTES:
            raise ProtocolError("QUIZ_SESSION_END exceeds the datagram size limit.")
        return payload


def decode_quiz_session_end(payload: bytes) -> QuizSessionEnd:
    """Validate a Unity quiz result packet. Every field is required; nullable ones as explicit null."""
    if not payload or len(payload) > MAX_DATAGRAM_BYTES:
        raise ProtocolError("Empty or oversized datagram.")
    try:
        data = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
        if not isinstance(data, dict) or data.get("type") != QUIZ_MESSAGE:
            raise ProtocolError("Expected a QUIZ_SESSION_END object.")
        if type(data.get("version")) is not int or data["version"] != PROTOCOL_VERSION:
            raise ProtocolError("Unsupported protocol version.")
        values = {f.name: data[f.name] for f in fields(QuizSessionEnd) if f.name != "detail"}
        return QuizSessionEnd(**values)
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError) as error:
        raise ProtocolError("Invalid QUIZ_SESSION_END packet; see docs/udp_protocol.md.") from error


def encode_result_ack(session_id: str, status: str) -> bytes:
    """Reply telling Unity whether it may stop retrying; error is the only non-final status."""
    if not _canonical_uuid(session_id) or status not in ACK_STATUSES:
        raise ProtocolError("Invalid acknowledgement.")
    return json.dumps({"type": "RESULT_ACK", "version": PROTOCOL_VERSION,
                       "session_id": session_id, "status": status},
                      separators=(",", ":")).encode("utf-8")
