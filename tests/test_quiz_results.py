"""Test saving scam quiz results: the message and its checks, the lookup of each question's details, all three stores,
the receiver (including whose account each player's result goes to), the session tools, and that the Reach Garden
dashboard and report are not disturbed by quiz rounds."""

from __future__ import annotations

import contextlib
import io
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app import report
from app.dashboard import DashboardWindow
from backend import result_receiver, sessions
from backend.auth import UserStore
from backend.quiz_results import enrich
from backend.result_receiver import ResultReceiver
from backend.storage import JsonlResultStore, MongoResultStore, SqliteResultStore, StorageError
from shared.protocol import (MAX_DATAGRAM_BYTES, ProtocolError, QuizSessionEnd, decode_quiz_session_end,
                             decode_session_end, encode_result_ack, peek_message_type, peek_session_id)
from shared.questions import load_bank
from tests.test_dashboard import WindowTestCase
from tests.test_results import make_result

BANK = load_bank()
IDS = ("sms-01", "phone-01", "otp-01", "social-01", "love-01")
DEFAULT_OUTCOMES = (("right", 4200), ("right", 9100), ("skip", None), ("wrong", 3000), ("right", 6500))


def quiz_result(outcomes=DEFAULT_OUTCOMES, ids=IDS, **changes) -> QuizSessionEnd:
    """A valid round whose counts, streak and times are worked out from ``outcomes`` the way Unity would."""
    responses, streak, best, times = [], 0, 0, []
    for question_id, (outcome, ms) in zip(ids, outcomes):
        question = BANK.by_id(question_id)
        if outcome == "skip":
            responses.append((question_id, -1, None))
            streak = 0
            continue
        right = outcome == "right"
        correct_choice = question.correct if question is not None else 0  # made-up ids have no bank entry
        responses.append((question_id, correct_choice if right else (correct_choice + 1) % 4, ms))
        times.append(ms)
        streak = streak + 1 if right else 0
        best = max(best, streak)
    correct = sum(1 for outcome, _ in outcomes if outcome == "right")
    wrong = sum(1 for outcome, _ in outcomes if outcome == "wrong")
    base = QuizSessionEnd(
        stream_id=str(uuid4()), sequence=0, timestamp=1770000001000, session_id=str(uuid4()), game="scam_quiz",
        hand="left", difficulty="level_2", startedAt=1770000000000, endedAt=1770000060000, duration=60.0,
        roundId=str(uuid4()), slot=0, bankVersion=BANK.version, totalQuestions=len(outcomes), correct=correct,
        wrong=wrong, skipped=len(outcomes) - correct - wrong, bestStreak=best,
        averageResponseTime=sum(times) / len(times) / 1000 if times else None,
        fastestResponse=min(times) / 1000 if times else None, slowestResponse=max(times) / 1000 if times else None,
        responses=tuple(responses))
    return replace(base, **changes)


def wire(result: QuizSessionEnd | None = None, **changes) -> dict:
    return {**json.loads((result or quiz_result()).to_bytes()), **changes}


def encode(data: dict) -> bytes:
    return json.dumps(data, separators=(",", ":")).encode()


class QuizMessageTests(unittest.TestCase):
    def test_a_result_survives_the_wire(self) -> None:
        result = quiz_result()
        decoded = decode_quiz_session_end(result.to_bytes())
        self.assertEqual(result, decoded)
        self.assertEqual((3, 1, 1, 1), (decoded.correct, decoded.wrong, decoded.skipped, decoded.bestStreak - 1))
        self.assertEqual(tuple(IDS), tuple(item[0] for item in decoded.responses))

    def test_the_wire_message_has_exactly_the_agreed_fields(self) -> None:
        data = wire()
        expected = {"type", "version", "stream_id", "sequence", "timestamp", "session_id", "game", "hand", "difficulty",
                    "startedAt", "endedAt", "duration", "roundId", "slot", "bankVersion", "totalQuestions", "correct",
                    "wrong", "skipped", "bestStreak", "averageResponseTime", "fastestResponse", "slowestResponse",
                    "responses"}
        self.assertEqual(expected, set(data))
        self.assertEqual("QUIZ_SESSION_END", data["type"])
        self.assertEqual(["otp-01", -1, None], data["responses"][2])  # a timeout: no answer, no time
        self.assertNotIn("detail", data)

    def test_a_round_where_nothing_was_answered_has_null_times(self) -> None:
        result = quiz_result(outcomes=(("skip", None),) * 5)
        self.assertEqual((None, None, None), (result.averageResponseTime, result.fastestResponse, result.slowestResponse))
        self.assertEqual(result, decode_quiz_session_end(result.to_bytes()))

    def test_a_full_length_round_fits_in_one_datagram(self) -> None:
        ids = [q.id for q in BANK.questions][:20]
        result = quiz_result(outcomes=(("right", 12345),) * 20, ids=tuple(ids))
        self.assertLessEqual(len(result.to_bytes()), MAX_DATAGRAM_BYTES)

    def test_an_oversized_round_is_refused_on_encoding(self) -> None:
        ids = tuple("x" * 63 + str(n % 10) + chr(97 + n // 10) for n in range(20))
        ids = tuple(i[:64] for i in ids)
        with self.assertRaises(ProtocolError):
            quiz_result(outcomes=(("right", 5),) * 20, ids=ids).to_bytes()

    def invalid(self, **changes) -> None:
        with self.assertRaises(ProtocolError, msg=str(changes)):
            replace(quiz_result(), **changes)

    def test_envelope_fields_are_checked(self) -> None:
        self.invalid(slot=2)
        self.invalid(slot=-1)
        self.invalid(slot=True)
        self.invalid(slot=0.0)
        self.invalid(game="reach_garden")
        self.invalid(hand="up")
        self.invalid(difficulty="default")
        self.invalid(difficulty="level_6")
        self.invalid(difficulty="level_0")
        self.invalid(roundId="not-a-uuid")
        self.invalid(session_id="not-a-uuid")
        self.invalid(stream_id=str(uuid4()).upper())
        self.invalid(bankVersion=0)
        self.invalid(sequence=-1)
        self.invalid(timestamp=True)
        self.invalid(endedAt=1769999999999)
        self.invalid(duration=-1.0)
        self.invalid(duration=float("nan"))

    def test_the_counts_must_agree_with_the_responses(self) -> None:
        self.invalid(totalQuestions=4)
        self.invalid(correct=4)                       # no longer adds up to the total
        self.invalid(skipped=2, wrong=0)              # adds up, but the log has exactly one timeout
        self.invalid(correct=3, wrong=0, skipped=2)   # and here the log has four answered questions, not three
        # Which answers were right needs the question bank, so the receiver checks that (see EnrichTests).
        self.invalid(bestStreak=4)
        with self.assertRaises(ProtocolError):
            quiz_result(outcomes=(("right", 1000),) * 21, ids=tuple(q.id for q in BANK.questions)[:21])
        with self.assertRaises(ProtocolError):
            quiz_result(outcomes=(), ids=())

    def test_each_response_is_checked(self) -> None:
        good = list(quiz_result().responses)

        def with_response(index: int, entry) -> None:
            changed = list(good)
            changed[index] = entry
            self.invalid(responses=tuple(changed))

        with_response(0, ("sms-01", 4, 4200))          # no such choice
        with_response(0, ("sms-01", -2, 4200))
        with_response(0, ("sms-01", True, 4200))
        with_response(0, ("sms-01", 1, None))          # answered but no time
        with_response(0, ("sms-01", 1, 4.5))           # not whole milliseconds
        with_response(0, ("sms-01", 1, -1))
        with_response(0, ("sms-01", 1, 3_600_001))
        with_response(2, ("otp-01", -1, 5))            # a timeout has no time
        with_response(0, ("Bad Id", 1, 4200))
        with_response(0, ("", 1, 4200))
        with_response(0, (7, 1, 4200))
        with_response(1, ("sms-01", 2, 9100))          # a question asked twice
        self.invalid(responses=tuple(good[:4]) + (("x",),))
        self.invalid(responses="not a list")

    def test_response_times_must_match_the_response_log(self) -> None:
        self.invalid(averageResponseTime=5.0)
        self.invalid(fastestResponse=2.0)
        self.invalid(slowestResponse=9.5)
        self.invalid(averageResponseTime=None)
        self.invalid(averageResponseTime=-1.0)
        self.invalid(fastestResponse=float("inf"))
        # Whole-millisecond rounding on the sender's side is tolerated.
        quiz_result(averageResponseTime=quiz_result().averageResponseTime + 0.0015)
        with self.assertRaises(ProtocolError):
            quiz_result(outcomes=(("skip", None),) * 5, averageResponseTime=1.0)

    def test_malformed_packets_are_rejected(self) -> None:
        good = quiz_result().to_bytes()
        for payload in (b"", b"[]", b"not json", b"\xff", good + b" extra", bytes(MAX_DATAGRAM_BYTES + 1),
                        encode(wire(type="SESSION_END")), encode(wire(version=2)), encode(wire(version=True))):
            with self.subTest(payload=payload[:30]), self.assertRaises(ProtocolError):
                decode_quiz_session_end(payload)
        missing = wire()
        del missing["roundId"]
        with self.assertRaises(ProtocolError):
            decode_quiz_session_end(encode(missing))
        duplicate = good.replace(b'"slot":0', b'"slot":0,"slot":1')
        with self.assertRaises(ProtocolError):
            decode_quiz_session_end(duplicate)
        with self.assertRaises(ProtocolError):
            decode_quiz_session_end(good.replace(b'"duration":60.0', b'"duration":NaN'))

    def test_extra_fields_are_ignored_so_the_message_can_grow(self) -> None:
        self.assertEqual(quiz_result().correct, decode_quiz_session_end(encode(wire(future_field=1))).correct)

    def test_the_message_type_and_session_can_be_read_from_a_bad_packet(self) -> None:
        broken = wire(totalQuestions=99)
        self.assertEqual("QUIZ_SESSION_END", peek_message_type(encode(broken)))
        self.assertEqual(broken["session_id"], peek_session_id(encode(broken)))
        garden = make_result().to_bytes()
        self.assertEqual("SESSION_END", peek_message_type(garden))
        self.assertIsNone(peek_message_type(b"junk"))
        self.assertIsNone(peek_message_type(encode({"type": "OTHER"})))
        self.assertIsNone(peek_session_id(encode({"type": "QUIZ_SESSION_END", "session_id": "nope"})))

    def test_a_garden_result_is_not_a_quiz_result_and_the_other_way_round(self) -> None:
        with self.assertRaises(ProtocolError):
            decode_quiz_session_end(make_result().to_bytes())
        with self.assertRaises(ProtocolError):
            decode_session_end(quiz_result().to_bytes())

    def test_storage_documents_round_trip(self) -> None:
        plain = quiz_result()
        document = plain.to_document()
        self.assertEqual("scam_quiz", document["game"])
        self.assertEqual({"question_id", "selected", "response_ms", "category", "difficulty", "correct", "is_correct"},
                         set(document["responses"][0]))
        self.assertIsNone(document["responses"][0]["category"])
        self.assertEqual(plain, QuizSessionEnd.from_document({**document, "user_id": "someone"}))
        looked_up = enrich(plain, BANK)
        again = QuizSessionEnd.from_document(looked_up.to_document())
        self.assertEqual(looked_up.to_document(), again.to_document())
        self.assertEqual("sms_phishing", again.to_document()["responses"][0]["category"])
        for bad in ({}, {**document, "responses": None}, {**document, "responses": [{"x": 1}]}):
            with self.assertRaises(ProtocolError):
                QuizSessionEnd.from_document(bad)


class EnrichTests(unittest.TestCase):
    def test_each_response_gets_its_category_difficulty_and_right_answer(self) -> None:
        result = enrich(quiz_result(), BANK)
        rows = result.to_document()["responses"]
        sms = BANK.by_id("sms-01")
        self.assertEqual(("sms_phishing", sms.difficulty, sms.correct, True),
                         (rows[0]["category"], rows[0]["difficulty"], rows[0]["correct"], rows[0]["is_correct"]))
        self.assertFalse(rows[3]["is_correct"])            # the wrong answer
        self.assertEqual(-1, rows[2]["selected"])
        self.assertFalse(rows[2]["is_correct"])            # a timeout is not correct
        self.assertEqual(BANK.by_id("otp-01").category, rows[2]["category"])
        self.assertEqual(quiz_result().responses, result.responses)  # what was sent is unchanged
        self.assertNotIn("detail", json.loads(result.to_bytes()))

    def test_without_a_bank_the_result_is_stored_as_sent(self) -> None:
        result = quiz_result()
        self.assertIs(result, enrich(result, None))

    def test_unknown_questions_are_kept_with_empty_details_and_a_warning(self) -> None:
        result = quiz_result(ids=("sms-01", "phone-01", "otp-01", "social-01", "retired-99"))
        with self.assertLogs("motionplay.backend.quiz_results", level="WARNING") as logs:
            rows = enrich(result, BANK).to_document()["responses"]
        self.assertIn("retired-99", "\n".join(logs.output))
        self.assertIsNone(rows[4]["category"])
        self.assertIsNone(rows[4]["is_correct"])
        self.assertIsNotNone(rows[0]["category"])

    def test_a_different_bank_version_is_noted_but_known_questions_are_still_used(self) -> None:
        with self.assertLogs("motionplay.backend.quiz_results", level="WARNING") as logs:
            rows = enrich(quiz_result(bankVersion=BANK.version + 5), BANK).to_document()["responses"]
        self.assertIn("version", "\n".join(logs.output))
        self.assertEqual("sms_phishing", rows[0]["category"])

    def test_a_count_that_disagrees_with_the_answers_is_noted_but_kept(self) -> None:
        # The message cannot tell which answers were right without the bank, so this passes the message checks.
        tweaked = quiz_result()
        flipped = list(tweaked.responses)
        right_question = BANK.by_id(flipped[0][0])
        flipped[0] = (flipped[0][0], (right_question.correct + 1) % 4, flipped[0][2])  # now wrong, but still claimed right
        claimed = replace(tweaked, responses=tuple(flipped))
        with self.assertLogs("motionplay.backend.quiz_results", level="WARNING") as logs:
            stored = enrich(claimed, BANK)
        self.assertIn("says 3 correct but its answers give 2", "\n".join(logs.output))
        self.assertEqual(3, stored.correct)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)

    def stores(self):
        yield "jsonl", JsonlResultStore(self.path / "results.jsonl")
        yield "sqlite", SqliteResultStore(self.path / "m.db")

    def test_a_round_is_stored_once_and_read_back(self) -> None:
        for name, store in self.stores():
            with self.subTest(store=name):
                result = enrich(quiz_result(), BANK)
                self.assertTrue(store.save(result, "u1"))
                self.assertFalse(store.save(result, "u2"), "a repeat is not stored again")
                document = store.get(result.session_id)
                self.assertEqual("u1", document["user_id"], "the first owner is kept")
                self.assertEqual("scam_quiz", document["game"])
                self.assertEqual(3, document["correct"])
                self.assertEqual(result.roundId, document["roundId"])
                self.assertEqual(IDS[0], document["responses"][0]["question_id"])
                self.assertEqual("sms_phishing", document["responses"][0]["category"])
                self.assertIsNone(store.get(str(uuid4())))
                store.close()

    def test_both_games_live_side_by_side_and_are_filtered_by_name(self) -> None:
        for name, store in self.stores():
            with self.subTest(store=name):
                garden = make_result(endedAt=1770000100000)
                first = quiz_result(endedAt=1770000200000, startedAt=1770000140000)
                second = quiz_result(endedAt=1770000300000, startedAt=1770000240000, slot=1)
                store.save(garden, "u1")
                store.save(first, "u1")
                store.save(second, "u2")
                self.assertEqual(3, store.count())
                self.assertEqual(2, store.count(game="scam_quiz"))
                self.assertEqual(1, store.count(game="reach_garden"))
                self.assertEqual(2, store.count(user_id="u1"))
                self.assertEqual([second.session_id, first.session_id, garden.session_id],
                                 [d["session_id"] for d in store.list_sessions()])
                self.assertEqual([first.session_id], [d["session_id"] for d in store.list_sessions(game="scam_quiz", user_id="u1")])
                self.assertEqual([garden.session_id], [d["session_id"] for d in store.list_sessions(game="reach_garden")])
                self.assertEqual(["scam_quiz"] * 2, [d["game"] for d in store.list_sessions(game="scam_quiz")])
                self.assertEqual([second.session_id, first.session_id], [d["session_id"] for d in store.list_sessions(limit=2)])
                self.assertEqual([], store.list_sessions(game="other"))
                store.close()

    def test_unassigned_rounds_of_both_games_can_be_claimed(self) -> None:
        for name, store in self.stores():
            with self.subTest(store=name):
                garden, quiz = make_result(), quiz_result()
                store.save(garden)
                store.save(quiz)
                self.assertEqual(2, store.claim_unassigned("u9"))
                self.assertEqual(0, store.claim_unassigned("u9"))
                self.assertEqual("u9", store.get(quiz.session_id)["user_id"])
                self.assertEqual("u9", store.get(garden.session_id)["user_id"])
                store.close()

    def test_a_round_without_details_is_stored_with_empty_ones(self) -> None:
        for name, store in self.stores():
            with self.subTest(store=name):
                result = quiz_result()
                store.save(result)
                row = store.get(result.session_id)["responses"][0]
                self.assertIsNone(row["category"])
                self.assertEqual(IDS[0], row["question_id"])
                store.close()

    def test_rounds_survive_reopening_the_store(self) -> None:
        for name, make in (("jsonl", lambda: JsonlResultStore(self.path / "again.jsonl")),
                           ("sqlite", lambda: SqliteResultStore(self.path / "again.db"))):
            with self.subTest(store=name):
                store = make()
                result = enrich(quiz_result(), BANK)
                store.save(result, "u1")
                store.close()
                reopened = make()
                self.assertEqual("u1", reopened.get(result.session_id)["user_id"])
                self.assertFalse(reopened.save(result, "u1"))
                reopened.close()

    def test_an_older_sqlite_database_gets_the_quiz_table_without_losing_rounds(self) -> None:
        database = self.path / "old.db"
        store = SqliteResultStore(database)
        garden = make_result()
        store.save(garden, "u1")
        store.close()
        raw = sqlite3.connect(database)
        raw.execute("DROP TABLE quiz_sessions")  # what a database made before the quiz looks like
        raw.commit()
        raw.close()
        reopened = SqliteResultStore(database)
        quiz = quiz_result()
        self.assertTrue(reopened.save(quiz, "u1"))
        self.assertEqual(2, reopened.count(user_id="u1"))
        self.assertEqual("reach_garden", reopened.get(garden.session_id)["game"])
        reopened.close()

    def test_the_garden_table_is_untouched_by_quiz_rounds(self) -> None:
        store = SqliteResultStore(self.path / "m.db")
        store.save(quiz_result())
        raw = sqlite3.connect(self.path / "m.db")
        self.assertEqual(0, raw.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
        self.assertEqual(1, raw.execute("SELECT COUNT(*) FROM quiz_sessions").fetchone()[0])
        raw.close()
        store.close()

    def test_mongo_stores_the_same_document(self) -> None:
        collection = MagicMock()
        client = MagicMock()
        client.__getitem__.return_value.__getitem__.return_value = collection
        store = MongoResultStore("mongodb://x", "db", client=client)
        self.assertTrue(store.save(enrich(quiz_result(), BANK), "u1"))
        document = collection.insert_one.call_args[0][0]
        self.assertEqual("scam_quiz", document["game"])
        self.assertEqual("u1", document["user_id"])
        self.assertEqual("sms_phishing", document["responses"][0]["category"])
        json.dumps(document["responses"])  # plain data only


class ReceiverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = SqliteResultStore(Path(self.folder.name) / "m.db")
        self.addCleanup(self.store.close)

    def receiver(self, **options) -> ResultReceiver:
        return ResultReceiver(self.store, "player-one", second_user_id="player-two", bank=BANK, **options)

    def test_each_player_s_result_goes_to_their_own_account(self) -> None:
        receiver = self.receiver()
        first, second = quiz_result(slot=0), quiz_result(slot=1, hand="right")
        self.assertEqual(encode_result_ack(first.session_id, "stored"), receiver.handle(first.to_bytes()))
        self.assertEqual(encode_result_ack(second.session_id, "stored"), receiver.handle(second.to_bytes()))
        self.assertEqual("player-one", self.store.get(first.session_id)["user_id"])
        self.assertEqual("player-two", self.store.get(second.session_id)["user_id"])

    def test_a_repeat_is_acknowledged_as_a_duplicate(self) -> None:
        receiver, result = self.receiver(), quiz_result()
        receiver.handle(result.to_bytes())
        self.assertEqual(encode_result_ack(result.session_id, "duplicate"), receiver.handle(result.to_bytes()))
        self.assertEqual(1, self.store.count())

    def test_a_player_without_an_account_is_saved_as_a_guest(self) -> None:
        receiver = ResultReceiver(self.store, "player-one", bank=BANK)  # no second account
        second = quiz_result(slot=1)
        receiver.handle(second.to_bytes())
        self.assertIsNone(self.store.get(second.session_id)["user_id"])
        only_second = ResultReceiver(self.store, None, second_user_id="player-two", bank=BANK)
        first = quiz_result(slot=0)
        only_second.handle(first.to_bytes())
        self.assertIsNone(self.store.get(first.session_id)["user_id"])

    def test_the_stored_round_has_its_questions_looked_up(self) -> None:
        result = quiz_result()
        self.receiver().handle(result.to_bytes())
        row = self.store.get(result.session_id)["responses"][0]
        self.assertEqual(("sms_phishing", True), (row["category"], row["is_correct"]))
        without = quiz_result()
        ResultReceiver(self.store, "player-one").handle(without.to_bytes())
        self.assertIsNone(self.store.get(without.session_id)["responses"][0]["category"])

    def test_garden_results_still_go_to_the_first_account(self) -> None:
        receiver, garden = self.receiver(), make_result()
        self.assertEqual(encode_result_ack(garden.session_id, "stored"), receiver.handle(garden.to_bytes()))
        self.assertEqual("player-one", self.store.get(garden.session_id)["user_id"])

    def test_a_bad_quiz_result_is_rejected_and_the_sender_told_to_stop(self) -> None:
        receiver = self.receiver()
        broken = wire(totalQuestions=99)
        with self.assertLogs("motionplay.backend.result_receiver", level="WARNING") as logs:
            reply = receiver.handle(encode(broken))
        self.assertEqual(encode_result_ack(broken["session_id"], "rejected"), reply)
        self.assertIn("QUIZ_SESSION_END", "\n".join(logs.output))
        self.assertEqual(0, self.store.count())
        with self.assertLogs("motionplay.backend.result_receiver", level="WARNING"):
            self.assertIsNone(receiver.handle(b"junk"))

    def test_a_storage_failure_is_reported_so_unity_retries(self) -> None:
        store = MagicMock()
        store.save.side_effect = StorageError("disk full")
        result = quiz_result()
        with self.assertLogs("motionplay.backend.result_receiver", level="ERROR"):
            reply = ResultReceiver(store, "u").handle(result.to_bytes())
        self.assertEqual(encode_result_ack(result.session_id, "error"), reply)

    def test_main_logs_in_both_players_and_loads_the_question_bank(self) -> None:
        users = {"alice": MagicMock(user_id="id-alice", username="alice"), "bob": MagicMock(user_id="id-bob", username="bob")}
        output = io.StringIO()
        first, second = quiz_result(slot=0), quiz_result(slot=1)

        def serve_two_rounds(receiver, port, **options):  # the store is only open while main is running
            receiver.handle(first.to_bytes())
            receiver.handle(second.to_bytes())

        with patch("backend.result_receiver.log_in", side_effect=lambda name, db: users[name]), \
                patch("backend.result_receiver.serve", side_effect=serve_two_rounds), contextlib.redirect_stdout(output):
            code = result_receiver.main(["--user", "alice", "--user2", "bob", "--store", "sqlite", "--file",
                                         str(Path(self.folder.name) / "main.db")])
        self.assertEqual(0, code)
        self.assertIn("Quiz player 2 saves to bob", output.getvalue())
        opened = SqliteResultStore(Path(self.folder.name) / "main.db")
        self.addCleanup(opened.close)
        self.assertEqual("id-alice", opened.get(first.session_id)["user_id"])
        self.assertEqual("id-bob", opened.get(second.session_id)["user_id"])
        self.assertEqual("sms_phishing", opened.get(first.session_id)["responses"][0]["category"])

    def test_main_carries_on_without_a_question_bank(self) -> None:
        output = io.StringIO()
        with patch("backend.result_receiver.serve") as serve, contextlib.redirect_stdout(output), \
                self.assertLogs("motionplay.backend.result_receiver", level="WARNING"):
            code = result_receiver.main(["--store", "jsonl", "--file", str(Path(self.folder.name) / "r.jsonl"),
                                         "--questions", str(Path(self.folder.name) / "missing.json")])
        self.assertEqual(0, code)
        self.assertIn("saved without categories", output.getvalue())
        serve.assert_called_once()


class LoopbackTests(unittest.TestCase):
    def test_both_players_results_are_acknowledged_over_real_sockets_and_stored_once(self) -> None:
        import socket
        import threading

        with tempfile.TemporaryDirectory() as folder:
            store = JsonlResultStore(Path(folder) / "r.jsonl")  # a SQLite connection cannot be used from the server thread
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            receiver = ResultReceiver(store, "player-one", second_user_id="player-two", bank=BANK)
            server = threading.Thread(target=result_receiver.serve, args=(receiver, port),
                                      kwargs={"max_results": 3, "idle_timeout": 5}, daemon=True)
            server.start()
            first, second = quiz_result(slot=0), quiz_result(slot=1)
            statuses = []
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(3)
                for result in (first, second, first):  # the last is a repeat, as when an acknowledgement is lost
                    for _attempt in range(20):  # the server thread may not have bound yet
                        client.sendto(result.to_bytes(), ("127.0.0.1", port))
                        try:
                            reply = json.loads(client.recvfrom(2048)[0])
                            self.assertEqual(result.session_id, reply["session_id"])
                            statuses.append(reply["status"])
                            break
                        except (socket.timeout, ConnectionResetError):
                            continue
            server.join(5)
            try:
                self.assertEqual(["stored", "stored", "duplicate"], statuses)
                self.assertEqual(2, store.count(game="scam_quiz"))
                self.assertEqual("player-two", store.get(second.session_id)["user_id"])
            finally:
                store.close()


class SessionToolTests(unittest.TestCase):
    def test_a_quiz_round_is_listed_in_words(self) -> None:
        line = sessions.format_row({**enrich(quiz_result(), BANK).to_document(), "user_id": "u1"}, {"u1": "alice"})
        for text in ("scam_quiz", "alice", "3/5 right", "avg", "best run 2", "level_2", "player 1"):
            self.assertIn(text, line)

    def test_a_round_with_no_answers_shows_dashes_not_zero(self) -> None:
        line = sessions.format_row({**quiz_result(outcomes=(("skip", None),) * 5).to_document(), "user_id": None})
        self.assertIn("0/5 right", line)
        self.assertIn("avg       -", line)

    def test_a_results_file_with_both_games_can_be_imported(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        source = Path(folder.name) / "results.jsonl"
        garden, quiz = make_result(), enrich(quiz_result(), BANK)
        lines = [json.dumps({**garden.to_document(), "user_id": "u1"}),
                 json.dumps({**quiz.to_document(), "user_id": "u2"}),
                 json.dumps({**quiz.to_document(), "user_id": "u2"}),            # the same round again
                 json.dumps({**quiz.to_document(), "session_id": "broken"}),      # not a valid round
                 "not json"]
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")
        target = SqliteResultStore(Path(folder.name) / "m.db")
        self.addCleanup(target.close)
        self.assertEqual((2, 1, 2), sessions.import_jsonl(source, target))
        self.assertEqual("u2", target.get(quiz.session_id)["user_id"])
        self.assertEqual("sms_phishing", target.get(quiz.session_id)["responses"][0]["category"])
        self.assertEqual("u1", target.get(garden.session_id)["user_id"])


class GardenViewsAreUndisturbedTests(WindowTestCase):
    """One stored quiz round must not break what a player's Reach Garden dashboard and report show."""

    def test_the_dashboard_shows_only_garden_rounds(self) -> None:
        self.store.save(make_result(endedAt=1770000300000), self.alice.user_id)
        self.store.save(quiz_result(endedAt=1770000400000, startedAt=1770000340000), self.alice.user_id)
        window = DashboardWindow(self.store, self.alice, refresh_ms=0)
        self.addCleanup(window.close)
        self.assertEqual(1, window.table.rowCount())
        self.assertEqual("1", window.cards["Rounds"].text())
        self.assertTrue(window.export_report(Path(self.folder.name) / "out.pdf"))

    def test_a_player_with_only_quiz_rounds_sees_the_empty_garden_dashboard(self) -> None:
        self.store.save(quiz_result(), self.alice.user_id)
        window = DashboardWindow(self.store, self.alice, refresh_ms=0)
        self.addCleanup(window.close)
        self.assertEqual(0, window.table.rowCount())
        self.assertIn("No rounds yet for alice", window.empty_label.text())

    def test_the_report_command_ignores_quiz_rounds(self) -> None:
        database = Path(self.folder.name) / "report.db"
        users = UserStore(database, rounds=4)
        carol = users.create_user("carol", "carol-password")
        users.close()
        store = SqliteResultStore(database)
        now = datetime.now()
        stamp = int(now.timestamp() * 1000)
        store.save(make_result(endedAt=stamp, startedAt=stamp - 40000), carol.user_id)
        store.save(quiz_result(endedAt=stamp, startedAt=stamp - 40000), carol.user_id)
        store.close()
        out = Path(self.folder.name) / "carol.pdf"
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            code = report.main(["--user", "carol", "--store", "sqlite", "--file", str(database), "--users-db",
                                str(database), "--out", str(out)], prompt=lambda _t: "carol-password", now=now, rounds=4)
        self.assertEqual(0, code)
        self.assertIn("(1 round(s))", text.getvalue())
        self.assertTrue(out.read_bytes().startswith(b"%PDF-"))


if __name__ == "__main__":
    unittest.main()
