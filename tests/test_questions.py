"""Test the scam quiz question bank: validation, the shipped bank, the random picker, recent-question memory, and the validator tool."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import random
import tempfile
import unittest
from pathlib import Path

from shared import questions
from shared.questions import (CATEGORIES, QuestionBank, QuestionBankError, coverage_shortfalls, load_bank, load_recent,
                              parse_bank, pick_round, remember_round)
from tools import validate_questions

SOURCE = questions.SOURCE_PREFIX + "learn/resources/all-resources/tips-for-adults-on-online-scams"


def raw_question(number: int = 1, *, category: str = "sms_phishing", difficulty: int = 1) -> dict:
    return {"id": f"q-{number}", "category": category, "difficulty": difficulty, "question": f"Question {number}?",
            "choices": ["a", "b", "c", "d"], "correct": number % 4, "explanation": "Because.", "source": SOURCE}


def raw_bank(*items: dict) -> dict:
    return {"version": 1, "questions": list(items) or [raw_question()]}


def big_bank() -> QuestionBank:
    """Five questions at every difficulty, spread over all nine categories."""
    items = [raw_question(n, category=CATEGORIES[n % len(CATEGORIES)], difficulty=n % 5 + 1) for n in range(50)]
    return parse_bank(raw_bank(*items))


def problems_of(data: object) -> list[str]:
    with self_assert_raises() as caught:
        parse_bank(data)
    return caught.problems


@contextlib.contextmanager
def self_assert_raises():
    holder = type("Holder", (), {"problems": []})()
    try:
        yield holder
    except QuestionBankError as error:
        holder.problems = error.problems
    else:
        raise AssertionError("QuestionBankError was not raised.")


class ParseBankTests(unittest.TestCase):
    def test_a_valid_bank_is_parsed(self) -> None:
        bank = parse_bank(raw_bank(raw_question(1), raw_question(2, category="phone_scam", difficulty=3)))
        self.assertEqual(1, bank.version)
        self.assertEqual(["q-1", "q-2"], [q.id for q in bank.questions])
        self.assertEqual(("a", "b", "c", "d"), bank.questions[0].choices)
        self.assertEqual("q-2", bank.by_id("q-2").id)
        self.assertIsNone(bank.by_id("nope"))

    def test_the_file_must_be_an_object_with_a_version_and_questions(self) -> None:
        self.assertEqual(["The question file must hold a JSON object."], problems_of([]))
        self.assertTrue(any("version" in p for p in problems_of({"questions": [raw_question()]})))
        self.assertTrue(any("version" in p for p in problems_of({"version": True, "questions": [raw_question()]})))
        self.assertTrue(any("non-empty list" in p for p in problems_of({"version": 1, "questions": []})))
        self.assertTrue(any("Unknown top-level" in p for p in problems_of({**raw_bank(), "extra": 1})))

    def test_duplicate_ids_are_reported(self) -> None:
        problems = problems_of(raw_bank(raw_question(1), raw_question(1)))
        self.assertTrue(any("Duplicate question id 'q-1'" in p for p in problems))

    def test_each_field_is_checked(self) -> None:
        cases = {
            "id": ("Bad Id!", "id must be"),
            "category": ("door_to_door", "unknown category"),
            "difficulty": (6, "difficulty"),
            "question": ("   ", "question must be"),
            "explanation": ("", "explanation must be"),
            "source": ("https://example.com/page", "source must be a page"),
            "correct": (4, "correct must be"),
            "choices": (["a", "b", "c"], "choices must be a list of exactly 4"),
        }
        for field, (value, expected) in cases.items():
            with self.subTest(field=field):
                item = raw_question()
                item[field] = value
                self.assertTrue(any(expected in p for p in problems_of(raw_bank(item))), field)

    def test_booleans_and_floats_are_not_whole_numbers(self) -> None:
        for field, value in (("difficulty", True), ("difficulty", 2.0), ("correct", False), ("correct", 1.0)):
            with self.subTest(field=field, value=value):
                item = raw_question()
                item[field] = value
                self.assertTrue(problems_of(raw_bank(item)))

    def test_choices_must_be_different_and_not_blank(self) -> None:
        item = raw_question()
        item["choices"] = ["Same", "same ", "c", "d"]
        self.assertTrue(any("all be different" in p for p in problems_of(raw_bank(item))))
        item["choices"] = ["a", "", "c", "d"]
        self.assertTrue(any("choices must be a list" in p for p in problems_of(raw_bank(item))))

    def test_missing_and_unknown_fields_are_named(self) -> None:
        item = raw_question()
        del item["source"]
        item["colour"] = "red"
        problems = problems_of(raw_bank(item))
        self.assertTrue(any("missing source" in p for p in problems))
        self.assertTrue(any("unknown field(s) colour" in p for p in problems))

    def test_every_problem_is_reported_not_just_the_first(self) -> None:
        bad_a, bad_b = raw_question(1), raw_question(2)
        bad_a["category"], bad_b["correct"] = "nope", 9
        self.assertEqual(2, len(problems_of(raw_bank(bad_a, bad_b))))


class ShippedBankTests(unittest.TestCase):
    def test_the_shipped_bank_is_valid_and_cites_digital_for_life(self) -> None:
        bank = load_bank()
        self.assertGreaterEqual(len(bank.questions), 10)
        self.assertTrue(all(q.source.startswith(questions.SOURCE_PREFIX) for q in bank.questions))

    def test_the_starter_bank_can_fill_a_round_at_level_two_and_covers_several_categories(self) -> None:
        bank = load_bank()
        self.assertEqual(5, len(pick_round(bank, 2, rng=random.Random(1))))
        self.assertGreaterEqual(len({q.category for q in bank.questions}), 6)

    def test_load_bank_reports_missing_and_damaged_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "none.json"
            self.assertTrue(any("Cannot read" in p for p in self._load_problems(missing)))
            damaged = Path(folder) / "bad.json"
            damaged.write_text("{not json", encoding="utf-8")
            self.assertTrue(any("not valid JSON" in p for p in self._load_problems(damaged)))

    @staticmethod
    def _load_problems(path: Path) -> list[str]:
        try:
            load_bank(path)
        except QuestionBankError as error:
            return error.problems
        raise AssertionError("expected an error")


class CoverageTests(unittest.TestCase):
    def test_shortfalls_list_each_difficulty_that_needs_more(self) -> None:
        bank = big_bank()  # ten at each difficulty
        self.assertEqual({}, coverage_shortfalls(bank, 10))
        self.assertEqual({d: 2 for d in range(1, 6)}, coverage_shortfalls(bank, 12))
        self.assertEqual({1: 9, 2: 10, 3: 10, 4: 10, 5: 10}, coverage_shortfalls(parse_bank(raw_bank()), 10))


class PickRoundTests(unittest.TestCase):
    def test_picks_five_different_questions_within_the_level(self) -> None:
        bank = big_bank()
        for level in range(1, 6):
            with self.subTest(level=level):
                chosen = pick_round(bank, level, rng=random.Random(level))
                self.assertEqual(5, len(chosen))
                self.assertEqual(5, len({q.id for q in chosen}))
                self.assertTrue(all(q.difficulty <= level for q in chosen))

    def test_level_one_only_asks_the_easiest_questions(self) -> None:
        self.assertEqual({1}, {q.difficulty for q in pick_round(big_bank(), 1, rng=random.Random(3))})

    def test_questions_come_easiest_first(self) -> None:
        for seed in range(20):
            difficulties = [q.difficulty for q in pick_round(big_bank(), 5, rng=random.Random(seed))]
            self.assertEqual(sorted(difficulties), difficulties)

    def test_categories_are_spread_before_any_repeats(self) -> None:
        for seed in range(20):
            chosen = pick_round(big_bank(), 5, rng=random.Random(seed))
            self.assertEqual(5, len({q.category for q in chosen}), seed)

    def test_recent_questions_are_avoided_while_others_exist(self) -> None:
        bank = big_bank()
        first = pick_round(bank, 5, rng=random.Random(1))
        second = pick_round(bank, 5, recent=[q.id for q in first], rng=random.Random(1))
        self.assertTrue({q.id for q in first}.isdisjoint(q.id for q in second))

    def test_recent_questions_are_used_when_the_pool_is_too_small(self) -> None:
        bank = parse_bank(raw_bank(*[raw_question(n, category=CATEGORIES[n]) for n in range(6)]))
        chosen = pick_round(bank, 1, recent=[f"q-{n}" for n in range(6)], rng=random.Random(2))
        self.assertEqual(5, len(chosen))  # all six are recent, so some must repeat rather than short-change the round

    def test_a_small_pool_gives_a_short_round_not_an_error(self) -> None:
        bank = parse_bank(raw_bank(raw_question(1), raw_question(2, category="phone_scam")))
        self.assertEqual(2, len(pick_round(bank, 1, rng=random.Random(0))))

    def test_the_same_seed_gives_the_same_round(self) -> None:
        bank = big_bank()
        ids = lambda seed: [q.id for q in pick_round(bank, 3, rng=random.Random(seed))]  # noqa: E731
        self.assertEqual(ids(7), ids(7))
        self.assertNotEqual({tuple(ids(s)) for s in range(10)}, {tuple(ids(7))})  # the seed does change the round

    def test_every_question_can_come_up(self) -> None:
        bank, seen, rng = big_bank(), set(), random.Random(11)
        for _ in range(400):
            seen.update(q.id for q in pick_round(bank, 5, rng=rng))
        self.assertEqual({q.id for q in bank.questions}, seen)

    def test_bad_arguments_are_rejected(self) -> None:
        bank = big_bank()
        for level in (0, 6, True, 2.0):
            with self.subTest(level=level), self.assertRaises(ValueError):
                pick_round(bank, level)  # type: ignore[arg-type]
        for count in (0, 21, False):
            with self.subTest(count=count), self.assertRaises(ValueError):
                pick_round(bank, 3, count)  # type: ignore[arg-type]


class RecentQuestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.path = Path(self._folder.name) / "data" / "recent.json"
        self.bank = big_bank()

    def test_a_missing_or_damaged_file_means_nothing_is_recent(self) -> None:
        self.assertEqual([], load_recent(self.path))
        self.path.parent.mkdir()
        for text in ("{not json", "[]", '{"ids": "x"}', '{"ids": [1, "a", null]}'):
            self.path.write_text(text, encoding="utf-8")
            self.assertEqual(["a"] if "a" in text else [], load_recent(self.path), text)

    def test_a_round_is_remembered_and_read_back_oldest_first(self) -> None:
        first = pick_round(self.bank, 5, rng=random.Random(1))
        self.assertEqual([q.id for q in first], remember_round(first, self.path))
        self.assertEqual([q.id for q in first], load_recent(self.path))

    def test_only_the_newest_ids_are_kept_and_repeats_move_to_the_end(self) -> None:
        for start in (0, 5, 10, 15):
            remember_round(self.bank.questions[start:start + 5], self.path)
        kept = load_recent(self.path)
        self.assertEqual(15, len(kept))
        self.assertEqual([q.id for q in self.bank.questions[5:20]], kept)
        remember_round(self.bank.questions[5:6], self.path)
        self.assertEqual("q-5", load_recent(self.path)[-1])
        self.assertEqual(15, len(load_recent(self.path)))

    def test_a_file_that_cannot_be_written_is_not_fatal(self) -> None:
        blocker = Path(self._folder.name) / "blocker"
        blocker.write_text("a file, not a folder", encoding="utf-8")
        with self.assertLogs("shared.questions", level="WARNING"):
            ids = remember_round(self.bank.questions[:2], blocker / "recent.json")
        self.assertEqual(["q-0", "q-1"], ids)


class ValidatorToolTests(unittest.TestCase):
    def run_tool(self, data: object, *args: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "q.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = validate_questions.main(["--file", str(path), *args])
        return code, out.getvalue()

    def test_the_shipped_bank_passes_the_tool(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, validate_questions.main([]))

    def test_a_valid_but_small_bank_warns_and_passes_unless_strict(self) -> None:
        code, output = self.run_tool(raw_bank())
        self.assertEqual(0, code)
        self.assertIn("warning", output)
        code, output = self.run_tool(raw_bank(), "--strict")
        self.assertEqual(1, code)
        self.assertIn("error", output)

    def test_a_complete_bank_passes_strict(self) -> None:
        code, output = self.run_tool(copy.deepcopy({"version": 1, "questions": [
            raw_question(n, category=CATEGORIES[n % 9], difficulty=n % 5 + 1) for n in range(50)]}), "--strict")
        self.assertEqual(0, code, output)

    def test_an_invalid_bank_lists_every_problem_and_fails(self) -> None:
        bad = raw_question()
        bad["category"], bad["correct"] = "nope", 9
        code, output = self.run_tool(raw_bank(bad))
        self.assertEqual(1, code)
        self.assertIn("2 problem(s)", output)

    def test_a_missing_file_fails_cleanly(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(1, validate_questions.main(["--file", "no-such-file.json"]))
        self.assertIn("Cannot read", out.getvalue())


if __name__ == "__main__":
    unittest.main()
