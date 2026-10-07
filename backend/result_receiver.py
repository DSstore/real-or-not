"""Receives SESSION_END and QUIZ_SESSION_END results from Unity, stores them once, and acknowledges each one."""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
from pathlib import Path

from backend.auth import AuthError
from backend.quiz_results import enrich
from backend.storage import STORE_KINDS, ResultStore, StorageError, open_store
from backend.users import add_user_arguments, log_in
from shared.logger import start_logging
from shared.config import PROJECT_ROOT, ConfigurationError, load_settings
from shared.protocol import (MAX_DATAGRAM_BYTES, QUIZ_MESSAGE, ProtocolError, QuizSessionEnd,
                             decode_quiz_session_end, decode_session_end, encode_result_ack, peek_message_type,
                             peek_session_id)
from shared.questions import DEFAULT_BANK_PATH, QuestionBank, QuestionBankError, load_bank

LOGGER = logging.getLogger("motionplay.backend.result_receiver")
MAX_INVALID_LOGS = 5  # Invalid datagrams logged individually per window; the rest are counted.
INVALID_LOG_WINDOW_SECONDS = 60.0
POLL_SECONDS = 0.5  # How often serve() wakes up so Ctrl+C and stop requests are noticed.
DEFAULT_FILES ={"jsonl": Path("data") / "results.jsonl", "sqlite": Path("data") / "motionplay.db"}


class ResultReceiver:
    """Socket-free core: bytes in, optional acknowledgement bytes out."""

    def __init__(self, store: ResultStore, user_id: str | None = None, clock=time.monotonic, *,
                 second_user_id: str | None = None, bank: QuestionBank | None = None) -> None:
        self._store = store
        # Reach Garden rounds, and quiz player 1 (slot 0), belong to this player; None means unassigned.
        self._user_id = user_id
        self._second_user_id = second_user_id  # Quiz player 2 (slot 1).
        self._bank = bank  # Used to fill in each quiz question's category and right answer; optional.
        self._clock = clock
        self._window_start = clock()
        self._logged_invalid = 0
        self._suppressed_invalid = 0

    def _log_invalid(self, size: int, error: Exception, kind: str = "SESSION_END") -> None:
        """Log a rejected datagram, but at most MAX_INVALID_LOGS per minute so a noisy sender cannot flood the log."""
        now = self._clock()
        if now - self._window_start >= INVALID_LOG_WINDOW_SECONDS:
            if self._suppressed_invalid:
                LOGGER.warning("%d more invalid datagram(s) in the last minute were not logged one by one.",
                               self._suppressed_invalid)
            self._window_start, self._logged_invalid, self._suppressed_invalid = now, 0, 0
        if self._logged_invalid < MAX_INVALID_LOGS:
            self._logged_invalid += 1
            LOGGER.warning("Rejected an invalid %s datagram (%d bytes): %s", kind, size, error)
        else:
            self._suppressed_invalid += 1

    def handle(self, payload: bytes) -> bytes | None:
        kind = peek_message_type(payload)
        try:
            if kind == QUIZ_MESSAGE:
                result = decode_quiz_session_end(payload)
            else:
                result = decode_session_end(payload)
        except ProtocolError as error:
            self._log_invalid(len(payload), error, kind or "SESSION_END")
            session_id = peek_session_id(payload)
            return encode_result_ack(session_id, "rejected") if session_id else None
        owner = self._user_id
        stored = result
        if isinstance(result, QuizSessionEnd):
            owner = self._user_id if result.slot == 0 else self._second_user_id
            stored = enrich(result, self._bank)
        try:
            created = self._store.save(stored, owner)
        except StorageError as error:
            LOGGER.error("Result %s not stored: %s", result.session_id, error)
            return encode_result_ack(result.session_id, "error")
        except Exception:  # Keep serving: Unity retries, and one bad store call must not end the receiver.
            LOGGER.exception("Result %s not stored: unexpected error.", result.session_id)
            return encode_result_ack(result.session_id, "error")
        status = "stored" if created else "was already stored"
        if isinstance(result, QuizSessionEnd):
            LOGGER.info("Result %s %s (%s, player %d, %s, %d of %d right).", result.session_id, status, result.game,
                        result.slot + 1, result.difficulty, result.correct, result.totalQuestions)
        else:
            LOGGER.info("Result %s %s (%s, %s, %d of %d watered).", result.session_id, status, result.game,
                        result.difficulty, result.targetsCompleted, result.targetsAttempted)
        return encode_result_ack(result.session_id, "stored" if created else "duplicate")


def serve(receiver: ResultReceiver, port: int, *, max_results: int | None = None,
          idle_timeout: float | None = None, stop: threading.Event | None = None) -> int:
    """Listen on loopback only until ``max_results`` are answered, ``stop`` is set, or the idle timeout
    passes (raises ``socket.timeout``). Returns how many datagrams were answered.

    The socket wakes every ``POLL_SECONDS`` instead of blocking forever: on Windows, Python cannot
    react to Ctrl+C while it is blocked inside a socket call."""
    answered = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", port))
        sock.settimeout(POLL_SECONDS)
        print(f"Listening for results on 127.0.0.1:{port}. Press Ctrl+C to stop "
              "(Ctrl+Break if it does not respond).", flush=True)
        idle_since = time.monotonic()
        while (max_results is None or answered < max_results) and not (stop is not None and stop.is_set()):
            try:
                payload, source = sock.recvfrom(MAX_DATAGRAM_BYTES + 1)
            except socket.timeout:
                if idle_timeout is not None and time.monotonic() - idle_since >= idle_timeout:
                    raise
                continue
            except ConnectionResetError:
                continue  # Windows reports an earlier reply sent to a closed port here; nothing was lost.
            idle_since = time.monotonic()
            reply = receiver.handle(payload)
            if reply is not None:
                try:
                    sock.sendto(reply, source)
                except ConnectionResetError:
                    pass  # The sender went away; it will retry or report the result as not confirmed.
                answered += 1
    return answered


def store_from_args(kind: str, file: Path | None, settings) -> ResultStore:
    """Open the chosen store; file defaults are relative to the project root."""
    path = None
    if kind in DEFAULT_FILES:
        path = file if file is not None else DEFAULT_FILES[kind]
        path = path if path.is_absolute() else PROJECT_ROOT / path
    return open_store(kind, path=path, mongodb_uri=settings.mongodb_uri,
                      mongodb_database=settings.mongodb_database)


def add_store_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--store", choices=STORE_KINDS, default="jsonl",
                        help="Where results are kept (default: jsonl; sqlite needs no server; mongo uses .env)")
    parser.add_argument("--file", type=Path,
                        help="File for jsonl or sqlite, relative to the project root "
                             "(default: data/results.jsonl or data/motionplay.db)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MotionPlay Unity result receiver")
    add_store_arguments(parser)
    add_user_arguments(parser)
    parser.add_argument("--user2", help="Scam quiz with two players: log in the second player. Quiz results of "
                                        "player 2 are saved to this account; player 1 uses --user")
    parser.add_argument("--questions", type=Path, default=DEFAULT_BANK_PATH,
                        help="Question file used to fill in each quiz question's category and right answer "
                             "(default: data/questions.json)")
    parser.add_argument("--port", type=int, help="Override UNITY_TO_PYTHON_PORT")
    parser.add_argument("--max-results", type=int, help="Exit after answering this many datagrams")
    parser.add_argument("--timeout", type=float, help="Exit after this many idle seconds")
    args = parser.parse_args(argv)
    store: ResultStore | None = None
    try:
        settings = load_settings()
        start_logging(settings, "result receiver")
        port = args.port if args.port is not None else settings.unity_to_python_port
        if not 1024 <= port <= 65535:
            raise ConfigurationError("Result port must be from 1024 to 65535.")
        user = log_in(args.user, args.users_db) if args.user else None
        second = log_in(args.user2, args.users_db) if args.user2 else None
        store = store_from_args(args.store, args.file, settings)
        if user is not None:
            print(f"Saving rounds for {user.username}.", flush=True)
        else:
            print("No --user given: rounds are saved without a player.", flush=True)
        if second is not None:
            print(f"Quiz player 2 saves to {second.username}.", flush=True)
        try:
            bank = load_bank(args.questions)
        except QuestionBankError as error:
            LOGGER.warning("Question bank not loaded, so quiz results will not show categories: %s", error)
            print("Question bank not loaded: quiz results are saved without categories.", flush=True)
            bank = None
        serve(ResultReceiver(store, user.user_id if user else None, second_user_id=second.user_id if second else None,
                             bank=bank), port, max_results=args.max_results, idle_timeout=args.timeout)
    except socket.timeout:
        print("No result arrived before the idle timeout.", file=sys.stderr)
        return 1
    except AuthError as error:
        print(f"Login failed: {error}", file=sys.stderr)
        return 1
    except (ConfigurationError, StorageError, OSError, EOFError) as error:
        print(f"Result receiver error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Result receiver stopped.")
    finally:
        if store is not None:
            store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
