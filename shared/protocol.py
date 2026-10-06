"""Versioned JSON wire models; no CV, UI, database, or socket dependencies."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
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


def peek_session_id(payload: bytes) -> str | None:
    """Best-effort session_id of a malformed SESSION_END so the sender can be told to stop retrying."""
    try:
        data = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        return None
    if isinstance(data, dict) and data.get("type") == "SESSION_END" and _canonical_uuid(data.get("session_id")):
        return data["session_id"]
    return None


def encode_result_ack(session_id: str, status: str) -> bytes:
    """Reply telling Unity whether it may stop retrying; error is the only non-final status."""
    if not _canonical_uuid(session_id) or status not in ACK_STATUSES:
        raise ProtocolError("Invalid acknowledgement.")
    return json.dumps({"type": "RESULT_ACK", "version": PROTOCOL_VERSION,
                       "session_id": session_id, "status": status},
                      separators=(",", ":")).encode("utf-8")
