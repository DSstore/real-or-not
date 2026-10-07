"""Test the scam quiz progress report: the comparison, the pages, the command with --game quiz, and the dashboard export."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import contextlib
import io
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PyQt6.QtWidgets import QApplication

from app import quiz_report, report
from app.dashboard import DashboardWindow
from backend.auth import UserStore
from backend.quiz_results import enrich
from backend.storage import SqliteResultStore
from tests.test_quiz_results import BANK, quiz_result
from tests.test_report import HOUR_MS, page_text
from tests.test_results import make_result

APP = QApplication.instance() or QApplication([])
NOW = datetime(2026, 10, 6, 12, 0)
FIVE = ("sms-01", "phone-01", "otp-01", "social-01", "skill-01")
RIGHT = (("right", 4000),) * 5
WRONG = (("wrong", 6000),) * 5


def stored(outcomes=RIGHT, index: int = 0, **changes):
    stamp = 1770000000000 + index * HOUR_MS
    return replace(enrich(quiz_result(outcomes=outcomes, ids=FIVE, endedAt=stamp, startedAt=stamp - 60000,
                                      **changes), BANK), session_id=str(uuid4()))


def document(outcomes=RIGHT, index: int = 0, **changes) -> dict:
    return {**stored(outcomes, index, **changes).to_document(), "user_id": "u"}


def rounds(count: int, outcomes=RIGHT, **changes) -> list[dict]:
    return [document(outcomes, i, **changes) for i in range(count)]


class CompareTests(unittest.TestCase):
    def test_too_few_rounds_gives_no_comparison(self) -> None:
        self.assertIsNone(quiz_report.compare(rounds(3)))
        self.assertIsNotNone(quiz_report.compare(rounds(4)))

    def test_improvement_is_judged_by_direction_for_accuracy_and_time(self) -> None:
        early = [document(WRONG, i) for i in range(2)]
        late = [document(RIGHT, i + 2) for i in range(2)]
        by_label = {c.label: c for c in quiz_report.compare(early + late)}
        self.assertEqual("improved", by_label["Accuracy"].verdict)
        self.assertEqual("+100 pts", by_label["Accuracy"].change)
        self.assertEqual("improved", by_label["Answer time"].verdict, "a lower answer time is better")
        self.assertEqual("-2.0 s", by_label["Answer time"].change)

    def test_a_decline_and_small_changes(self) -> None:
        early = [document(RIGHT, i) for i in range(2)]
        late = [document(WRONG, i + 2) for i in range(2)]
        self.assertEqual("declined", {c.label: c for c in quiz_report.compare(early + late)}["Accuracy"].verdict)
        same = [document(RIGHT, i) for i in range(2)] + [document((("right", 4200),) * 5, i + 2) for i in range(2)]
        self.assertEqual("about the same", {c.label: c for c in quiz_report.compare(same)}["Answer time"].verdict)

    def test_rounds_with_no_answers_do_not_count_as_a_zero_time(self) -> None:
        skipped = [document((("skip", None),) * 5, i) for i in range(2)]
        late = [document(RIGHT, i + 2) for i in range(2)]
        by_label = {c.label: c for c in quiz_report.compare(skipped + late)}
        self.assertEqual("not enough data", by_label["Answer time"].verdict)
        self.assertEqual("-", by_label["Answer time"].earlier)

    def test_uses_time_order_not_input_order(self) -> None:
        early = [document(WRONG, i) for i in range(2)]
        late = [document(RIGHT, i + 2) for i in range(2)]
        forward = quiz_report.compare(early + late)
        self.assertEqual(forward, quiz_report.compare(list(reversed(early + late))))


class BuildTests(unittest.TestCase):
    def test_no_rounds_is_an_error(self) -> None:
        with self.assertRaises(report.ReportError):
            quiz_report.build_report([], "alice", NOW)

    def test_pages_for_a_normal_report(self) -> None:
        pages = quiz_report.build_report(rounds(6), "alice", NOW)
        self.assertEqual(4, len(pages))  # summary, charts, topics, one page of rounds
        summary = page_text(pages[0])
        self.assertIn("MotionPlay scam quiz report", summary)
        self.assertIn("Player: alice", summary)
        self.assertIn("30 of 30", summary)
        self.assertIn("100%", summary)
        self.assertIn("6 of 6 (100%)", summary)  # the challenge questions
        self.assertIn("not a test of ability", summary)
        self.assertIn("None yet", summary)  # no topic to practise
        self.assertIn("page 3 of 4", page_text(pages[2]))
        self.assertEqual(6, len(pages[3].axes[0].tables[0].get_celld()) // 7 - 1)

    def test_few_rounds_say_there_is_not_enough_to_compare(self) -> None:
        self.assertIn("Not enough rounds to compare", page_text(quiz_report.build_report(rounds(2), "alice", NOW)[0]))

    def test_the_topics_to_practise_are_named_weakest_first(self) -> None:
        mixed = [document((("wrong", 5000),) + (("right", 5000),) * 4, i) for i in range(2)]
        text = page_text(quiz_report.build_report(mixed, "alice", NOW)[0])
        self.assertIn("SMS scams: 0 of 2 answered correctly (0%)", text)
        self.assertNotIn("Phone call scams", text)

    def test_mixed_difficulty_levels_get_a_caveat(self) -> None:
        text = page_text(quiz_report.build_report(rounds(2, difficulty="level_1") + rounds(2, difficulty="level_3"),
                                                  "alice", NOW)[0])
        self.assertIn("played at different levels", text)
        self.assertNotIn("played at different levels", page_text(quiz_report.build_report(rounds(4), "alice", NOW)[0]))

    def test_the_topic_page_has_one_bar_per_topic(self) -> None:
        figure = quiz_report.build_report(rounds(2), "alice", NOW)[2]
        self.assertEqual(5, len(figure.axes[0].patches))  # sms, phone, protect, social, challenge
        labels = [t.get_text() for t in figure.axes[0].get_yticklabels()]
        self.assertIn("Digital skills (challenge) (2/2)", labels)

    def test_many_rounds_continue_on_more_table_pages(self) -> None:
        pages = quiz_report.build_report(rounds(report.ROWS_PER_PAGE + 5), "alice", NOW)
        self.assertEqual(5, len(pages))
        self.assertIn("Rounds 29 to 33 of 33", page_text(pages[4]))

    def test_a_round_with_no_answers_shows_a_dash_not_zero_and_a_chart_gap(self) -> None:
        pages = quiz_report.build_report([document((("skip", None),) * 5)], "alice", NOW)
        self.assertIn("-", page_text(pages[0]))
        line = pages[1].axes[1].lines[0]
        self.assertNotEqual(line.get_ydata()[0], line.get_ydata()[0], "answer time should be NaN, not 0")

    def test_the_pdf_can_be_written(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = report.write_pdf(quiz_report.build_report(rounds(5), "alice", NOW), Path(folder) / "q.pdf")
            self.assertTrue(path.read_bytes().startswith(b"%PDF-"))

    def test_the_default_filename_says_it_is_the_quiz_report(self) -> None:
        self.assertEqual("MotionPlay-quiz-progress-alice-20261006-1200.pdf", quiz_report.default_filename("alice", NOW))


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = Path(self.folder.name) / "m.db"
        users = UserStore(self.db, rounds=4)
        self.alice = users.create_user("alice", "alice-password")
        users.close()
        store = SqliteResultStore(self.db)
        self.now = datetime.now()
        stamp = int(self.now.timestamp() * 1000)
        for i in range(3):
            store.save(replace(stored(index=0), session_id=str(uuid4()), endedAt=stamp - i * HOUR_MS,
                               startedAt=stamp - i * HOUR_MS - 60000), self.alice.user_id)
        store.save(make_result(endedAt=stamp, startedAt=stamp - 40000), self.alice.user_id)
        store.close()
        self.out = Path(self.folder.name) / "quiz.pdf"

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        base = ["--user", "alice", "--store", "sqlite", "--file", str(self.db), "--users-db", str(self.db),
                "--out", str(self.out)]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = report.main([*base, *argv], prompt=lambda _t: "alice-password", now=self.now, rounds=4)
        return code, out.getvalue(), err.getvalue()

    def test_game_quiz_reports_only_the_quiz_rounds(self) -> None:
        code, out, _ = self.run_cli("--game", "quiz")
        self.assertEqual(0, code)
        self.assertIn("(3 round(s))", out)
        self.assertTrue(self.out.read_bytes().startswith(b"%PDF-"))

    def test_the_default_game_is_still_reach_garden(self) -> None:
        code, out, _ = self.run_cli()
        self.assertEqual(0, code)
        self.assertIn("(1 round(s))", out)

    def test_days_window_applies_to_the_quiz_report(self) -> None:
        self.assertIn("(3 round(s))", self.run_cli("--game", "quiz", "--days", "30")[1])

    def test_a_player_with_no_quiz_rounds_gets_a_clear_error(self) -> None:
        store = SqliteResultStore(self.db)
        store.close()
        users = UserStore(self.db, rounds=4)
        users.create_user("bob", "bob-password-1")
        users.close()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = report.main(["--user", "bob", "--game", "quiz", "--store", "sqlite", "--file", str(self.db),
                                "--users-db", str(self.db), "--out", str(self.out)],
                               prompt=lambda _t: "bob-password-1", now=self.now, rounds=4)
        self.assertEqual(1, code)
        self.assertIn("no quiz rounds", err.getvalue())
        self.assertFalse(self.out.exists())

    def test_an_unknown_game_is_refused(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli("--game", "chess")


class DashboardExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        db = Path(self.folder.name) / "m.db"
        self.users = UserStore(db, rounds=4)
        self.addCleanup(self.users.close)
        self.store = SqliteResultStore(db)
        self.addCleanup(self.store.close)
        self.alice = self.users.create_user("alice", "alice-password")
        self.window = DashboardWindow(self.store, self.alice, refresh_ms=0)
        self.addCleanup(self.window.close)
        self.path = Path(self.folder.name) / "out.pdf"

    def test_the_button_is_on_for_both_tabs(self) -> None:
        self.assertTrue(self.window.export_button.isEnabled())
        self.window.tabs.setCurrentIndex(1)
        self.assertTrue(self.window.export_button.isEnabled())

    def test_the_quiz_tab_exports_the_quiz_report(self) -> None:
        self.store.save(make_result(endedAt=1770000300000), self.alice.user_id)
        self.store.save(stored(), self.alice.user_id)
        self.window.tabs.setCurrentIndex(1)
        self.assertTrue(self.window.export_report(self.path))
        self.assertIn("Report saved (4 pages)", self.window.status_label.text())  # the garden one has 3 for one round
        self.assertTrue(self.path.read_bytes().startswith(b"%PDF-"))

    def test_the_garden_tab_still_exports_the_garden_report(self) -> None:
        self.store.save(make_result(endedAt=1770000300000), self.alice.user_id)
        self.assertTrue(self.window.export_report(self.path))
        self.assertIn("Report saved (3 pages)", self.window.status_label.text())

    def test_the_quiz_tab_without_quiz_rounds_says_why_and_writes_nothing(self) -> None:
        self.store.save(make_result(endedAt=1770000300000), self.alice.user_id)  # garden rounds do not count
        self.window.tabs.setCurrentIndex(1)
        self.assertFalse(self.window.export_report(self.path))
        self.assertIn("no quiz rounds", self.window.status_label.text())
        self.assertFalse(self.path.exists())

    def test_only_this_players_quiz_rounds_are_included(self) -> None:
        bob = self.users.create_user("bob", "bob-password-1")
        self.store.save(stored(), self.alice.user_id)
        self.store.save(stored(index=1), bob.user_id)
        self.window.tabs.setCurrentIndex(1)
        self.assertTrue(self.window.export_report(self.path))
        self.assertIn("1 to 1 of 1", "\n".join(
            page_text(p) for p in quiz_report.build_report(
                self.store.list_sessions(game="scam_quiz", user_id=self.alice.user_id), "alice", NOW)))


if __name__ == "__main__":
    unittest.main()
