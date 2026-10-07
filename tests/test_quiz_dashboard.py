"""Test the scam quiz dashboard: the numbers (no Qt) and the Scam Quiz tab of the dashboard window (Qt offscreen)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from app import quiz_model as model
from app.dashboard import DashboardWindow
from backend.quiz_results import enrich
from shared.protocol import QuizSessionEnd
from shared.questions import CATEGORIES
from tests.test_dashboard import WindowTestCase
from tests.test_results import make_result
from tests.test_quiz_results import BANK, quiz_result

FIVE = ("sms-01", "phone-01", "otp-01", "social-01", "skill-01")  # the last one is a challenge question


def stored(outcomes, ids=FIVE, **changes) -> QuizSessionEnd:
    """A round as the receiver stores it: with each question's category and right answer filled in."""
    return enrich(quiz_result(outcomes=outcomes, ids=ids, **changes), BANK)


def document(outcomes, ids=FIVE, **changes) -> dict:
    return {**stored(outcomes, ids, **changes).to_document(), "user_id": None}


ALL_RIGHT = (("right", 4000),) * 5


class ModelTests(unittest.TestCase):
    def test_empty_summary_has_no_values(self) -> None:
        summary = model.summarize([])
        self.assertEqual(0, summary.rounds)
        self.assertIsNone(summary.accuracy)
        self.assertIsNone(summary.average_answer_time)
        self.assertIsNone(summary.latest_level)
        self.assertIsNone(summary.last_played)
        self.assertIsNone(summary.challenge_accuracy)
        self.assertEqual([], model.category_scores([]))

    def test_summary_totals_are_over_questions_not_over_round_percentages(self) -> None:
        good = document(ALL_RIGHT, endedAt=1770000100000)
        poor = document((("wrong", 8000), ("skip", None), ("right", 2000), ("wrong", 6000), ("wrong", 4000)),
                        endedAt=1770000200000, difficulty="level_3")
        summary = model.summarize([good, poor])
        self.assertEqual((2, 10, 6), (summary.rounds, summary.questions, summary.correct))
        self.assertAlmostEqual(0.6, summary.accuracy)
        self.assertEqual(1, summary.skipped)
        self.assertEqual(5, summary.best_streak)
        # Nine answered questions: five at 4 s, then 8, 2, 6 and 4 s - a skipped question has no time.
        self.assertAlmostEqual((5 * 4 + 8 + 2 + 6 + 4) / 9, summary.average_answer_time)
        self.assertEqual(3, summary.latest_level)  # the newest round, whatever order they are passed in
        self.assertEqual(1770000200000, summary.last_played)

    def test_challenge_question_results_are_counted_on_their_own(self) -> None:
        a = document(ALL_RIGHT)
        b = document((("right", 1), ("right", 1), ("right", 1), ("right", 1), ("wrong", 9000)))
        summary = model.summarize([a, b])
        self.assertEqual((2, 1), (summary.challenge_asked, summary.challenge_correct))
        self.assertAlmostEqual(0.5, summary.challenge_accuracy)

    def test_topics_are_listed_weakest_first_and_a_skip_counts_as_not_correct(self) -> None:
        rounds = [document((("wrong", 2000), ("right", 2000), ("skip", None), ("right", 2000), ("right", 2000))),
                  document((("wrong", 2000), ("right", 2000), ("right", 2000), ("right", 2000), ("right", 2000)))]
        scores = model.category_scores(rounds)
        by_name = {s.category: (s.correct, s.asked) for s in scores}
        self.assertEqual((0, 2), by_name["sms_phishing"])
        self.assertEqual((1, 2), by_name["protect_personal_info"])  # the skipped otp-01 question
        self.assertEqual((2, 2), by_name["phone_scam"])
        self.assertEqual("sms_phishing", scores[0].category)
        self.assertEqual("protect_personal_info", scores[1].category)
        self.assertEqual(sorted(s.accuracy for s in scores), [s.accuracy for s in scores])

    def test_a_question_the_bank_did_not_know_belongs_to_no_topic(self) -> None:
        unknown = document(ALL_RIGHT, ids=("nope-01", "phone-01", "otp-01", "social-01", "skill-01"))
        self.assertNotIn(None, [s.category for s in model.category_scores([unknown])])
        self.assertEqual(4, sum(s.asked for s in model.category_scores([unknown])))

    def test_the_topic_to_practise_needs_two_answers_and_a_mistake(self) -> None:
        once = model.category_scores([document((("wrong", 1),) + (("right", 1),) * 4)])
        self.assertIsNone(model.weakest(once))  # one wrong answer in a topic is not yet a pattern
        twice = model.category_scores([document((("wrong", 1),) + (("right", 1),) * 4)] * 2)
        weak = model.weakest(twice)
        self.assertEqual(("sms_phishing", 0, 2), (weak.category, weak.correct, weak.asked))
        perfect = model.category_scores([document(ALL_RIGHT)] * 2)
        self.assertIsNone(model.weakest(perfect))

    def test_trend_is_oldest_first(self) -> None:
        late = document(ALL_RIGHT, endedAt=1770000900000)
        early = document((("wrong", 1),) * 5, endedAt=1770000100000)
        data = model.trend([late, early])
        self.assertEqual([0.0, 1.0], data.accuracy)

    def test_table_row_cells(self) -> None:
        row = model.table_row(document((("right", 4000), ("right", 6000), ("skip", None), ("wrong", 2000), ("right", 4000))))
        self.assertEqual("2", row[1])
        self.assertEqual("3/5", row[2])
        self.assertEqual("60%", row[3])
        self.assertEqual("4.00 s", row[4])
        self.assertEqual("2", row[5])
        self.assertEqual("1 min 00 s", row[6])
        self.assertEqual(len(model.TABLE_HEADERS), len(row))

    def test_a_round_with_no_answers_shows_dashes_for_time(self) -> None:
        row = model.table_row(document((("skip", None),) * 5))
        self.assertEqual(["0/5", "0%", "-"], row[2:5])

    def test_every_topic_in_the_bank_has_a_readable_label(self) -> None:
        for category in CATEGORIES:
            self.assertIn(category, model.CATEGORY_LABELS)
        self.assertEqual("Some new topic", model.category_label("some_new_topic"))


class QuizTabTests(WindowTestCase):
    def window(self, user) -> DashboardWindow:
        window = DashboardWindow(self.store, user, refresh_ms=0)
        self.addCleanup(window.close)
        return window

    def save(self, user, outcomes=ALL_RIGHT, **changes) -> None:
        self.store.save(replace(stored(outcomes, **changes), session_id=str(uuid4())), user.user_id)

    def test_the_tab_shows_the_empty_state_for_a_player_with_no_quiz_rounds(self) -> None:
        panel = self.window(self.alice).quiz_panel
        self.assertEqual(1, panel.pages.currentIndex())
        self.assertIn("No quiz rounds yet for alice", panel.empty_label.text())
        self.assertIn("--user2", panel.empty_label.text())
        self.assertEqual("0", panel.cards["Rounds"].text())
        self.assertEqual("-", panel.cards["Accuracy"].text())
        self.assertEqual("", panel.practice_label.text())

    def test_it_shows_only_the_players_own_quiz_rounds(self) -> None:
        self.save(self.alice, endedAt=1770000300000)
        self.save(self.bob, (("wrong", 1),) * 5, endedAt=1770000200000)
        panel = self.window(self.alice).quiz_panel
        self.assertEqual(0, panel.pages.currentIndex())
        self.assertEqual(1, panel.table.rowCount())
        self.assertEqual("5/5", panel.table.item(0, 2).text())
        self.assertEqual("100%", panel.cards["Accuracy"].text())
        self.assertEqual("5", panel.cards["Best streak"].text())
        self.assertEqual("4.00 s", panel.cards["Answer time"].text())
        self.assertEqual("2", panel.cards["Level now"].text())
        self.assertEqual("1/1", panel.cards["Challenge"].text())
        self.assertEqual("1", self.window(self.bob).quiz_panel.cards["Rounds"].text())

    def test_garden_and_quiz_rounds_each_stay_on_their_own_tab(self) -> None:
        self.store.save(make_result(endedAt=1770000300000), self.alice.user_id)
        self.save(self.alice)
        window = self.window(self.alice)
        self.assertEqual(1, window.table.rowCount())
        self.assertEqual("1", window.cards["Rounds"].text())
        self.assertEqual(1, window.quiz_panel.table.rowCount())
        self.assertEqual(["Reach Garden", "Scam Quiz"], [window.tabs.tabText(i) for i in range(window.tabs.count())])
        self.assertIn("Reach Garden: 1 round(s)", window.status_label.text())
        self.assertIn("Scam Quiz: 1 round(s)", window.status_label.text())

    def test_the_topic_to_practise_is_named(self) -> None:
        self.save(self.alice, (("wrong", 1),) + (("right", 1),) * 4)
        self.save(self.alice, (("wrong", 1),) + (("right", 1),) * 4)
        text = self.window(self.alice).quiz_panel.practice_label.text()
        self.assertIn("SMS scams", text)
        self.assertIn("0 of 2", text)

    def test_the_charts_are_drawn_and_a_new_round_appears_on_refresh(self) -> None:
        window = self.window(self.alice)
        self.save(self.alice)
        window.refresh_button.click()
        panel = window.quiz_panel
        self.assertEqual(1, panel.table.rowCount())
        self.assertEqual(2, len(panel.figure.axes))
        self.assertEqual(0, panel.pages.currentIndex())

    def test_the_report_button_is_off_on_the_quiz_tab(self) -> None:
        window = self.window(self.alice)
        self.assertTrue(window.export_button.isEnabled())
        window.tabs.setCurrentIndex(1)
        self.assertFalse(window.export_button.isEnabled())
        self.assertIn("not available yet", window.export_button.toolTip())
        window.tabs.setCurrentIndex(0)
        self.assertTrue(window.export_button.isEnabled())
        self.assertEqual("", window.export_button.toolTip())

    def test_the_garden_report_still_works_with_quiz_rounds_stored(self) -> None:
        self.store.save(make_result(endedAt=1770000300000), self.alice.user_id)
        self.save(self.alice)
        self.assertTrue(self.window(self.alice).export_report(Path(self.folder.name) / "out.pdf"))

    def test_a_storage_failure_is_reported_not_raised(self) -> None:
        window = self.window(self.alice)
        self.store.close()
        window.refresh()
        self.assertIn("Could not read rounds", window.status_label.text())


if __name__ == "__main__":
    unittest.main()
