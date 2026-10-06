"""Check the scam quiz question bank and report every problem in it.

    .\\.venv\\Scripts\\python.exe -m tools.validate_questions                 # checks data/questions.json
    .\\.venv\\Scripts\\python.exe -m tools.validate_questions --strict        # also fail if a difficulty has too few
    .\\.venv\\Scripts\\python.exe -m tools.validate_questions --file OTHER.json

Structure problems always fail. Too few questions at a difficulty is a warning by default, because the bank grows in
stages; use --strict once it should be complete. Exit status is 0 when the bank passes and 1 otherwise.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from shared.questions import (DEFAULT_BANK_PATH, MIN_PER_DIFFICULTY, QuestionBankError, coverage_shortfalls,
                              load_bank)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the scam quiz question bank.")
    parser.add_argument("--file", type=Path, default=DEFAULT_BANK_PATH, help="question file (default: data/questions.json)")
    parser.add_argument("--min-per-difficulty", type=int, default=MIN_PER_DIFFICULTY,
                        help=f"questions wanted at each difficulty 1 to 5 (default {MIN_PER_DIFFICULTY})")
    parser.add_argument("--strict", action="store_true", help="treat too few questions at a difficulty as an error")
    args = parser.parse_args(argv)

    try:
        bank = load_bank(args.file)
    except QuestionBankError as error:
        print(f"{args.file}: {len(error.problems)} problem(s) found.")
        for problem in error.problems:
            print(f"  - {problem}")
        return 1

    counts = bank.difficulty_counts()
    print(f"{args.file}: {len(bank.questions)} questions, version {bank.version}.")
    print("  per difficulty: " + ", ".join(f"{level}: {count}" for level, count in counts.items()))
    shortfalls = coverage_shortfalls(bank, args.min_per_difficulty)
    if shortfalls:
        needs = ", ".join(f"difficulty {level} needs {more} more" for level, more in shortfalls.items())
        print(f"  {'error' if args.strict else 'warning'}: fewer than {args.min_per_difficulty} per difficulty ({needs}).")
        if args.strict:
            return 1
    print("  ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
