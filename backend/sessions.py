"""Inspect and migrate stored sessions: list rounds, import another store, claim unassigned rounds."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.auth import AuthError
from backend.result_receiver import add_store_arguments, store_from_args
from backend.storage import ResultStore, StorageError
from backend.users import Prompt, add_user_arguments, log_in, open_users
from shared.config import PROJECT_ROOT, ConfigurationError, load_settings
from shared.protocol import QUIZ_GAME, ProtocolError, QuizSessionEnd, decode_session_end


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}s"


def format_row(document: dict, names: dict[str, str] | None = None) -> str:
    """One readable line; missing metrics show as '-', never as zero. ``names`` maps user ids to usernames."""
    ended = datetime.fromtimestamp(document["endedAt"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    player = (names or {}).get(document.get("user_id"), "-")
    if document.get("game") == QUIZ_GAME:
        return (f"{ended}  {player:<12} {document['game']:<13} {document['hand']:<5} "
                f"{document['correct']}/{document['totalQuestions']} right  "
                f"avg {_seconds(document['averageResponseTime']):>7}  best run {document['bestStreak']}  "
                f"{document['difficulty']}  player {document['slot'] + 1}  {document['session_id']}")
    return (f"{ended}  {player:<12} {document['game']:<13} {document['hand']:<5} "
            f"{document['targetsCompleted']}/{document['targetsAttempted']} watered  "
            f"acc {_percent(document['accuracy']):>4}  react {_seconds(document['averageReactionTime']):>7}  "
            f"stab {_percent(document['averageHoldStability']):>4}  eff {_percent(document['pathEfficiency']):>4}  "
            f"{document['session_id']}")


def import_jsonl(path: Path, store: ResultStore, user_id: str | None = None) -> tuple[int, int, int]:
    """Copy a JSONL results file into a store. Returns (added, already_present, invalid).

    A line keeps the player it was saved with; ``user_id`` is used only for lines that have none."""
    added = present = invalid = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if isinstance(data, dict) and data.get("game") == QUIZ_GAME:
                    result = QuizSessionEnd.from_document(data)
                else:
                    result = decode_session_end(json.dumps({"type": "SESSION_END", "version": 1, **data}).encode("utf-8"))
                owner = data.get("user_id")
                if owner is not None and not isinstance(owner, str):
                    raise ProtocolError("user_id must be a string or null.")
            except (ValueError, ProtocolError):
                invalid += 1
                continue
            if store.save(result, owner if owner is not None else user_id):
                added += 1
            else:
                present += 1
    return added, present, invalid


def main(argv: list[str] | None = None, *, prompt: Prompt = getpass.getpass, **auth_options) -> int:
    parser = argparse.ArgumentParser(description="MotionPlay stored sessions")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="Show a player's most recent rounds (--user), or everyone's (--all)")
    add_store_arguments(listing)
    add_user_arguments(listing)
    listing.add_argument("--all", action="store_true",
                         help="Local overview of every player's rounds without logging in")
    listing.add_argument("--game", help="Only this game, e.g. reach_garden")
    listing.add_argument("--limit", type=int, default=10, help="How many rounds to show (default: 10)")
    copy = commands.add_parser("import", help="Copy a JSONL results file into the chosen store")
    add_store_arguments(copy)
    add_user_arguments(copy)
    copy.add_argument("source", type=Path, help="JSONL file to read, e.g. data/results.jsonl")
    claim = commands.add_parser("claim", help="Give rounds saved before accounts existed to a player")
    add_store_arguments(claim)
    add_user_arguments(claim, required_user=True)
    args = parser.parse_args(argv)

    store: ResultStore | None = None
    try:
        settings = load_settings()
        if args.command == "list" and not args.user and not args.all:
            raise ConfigurationError("Choose --user NAME to see your rounds, or --all for everyone's.")
        if args.command == "list" and args.limit < 1:
            raise ConfigurationError("--limit must be at least 1.")
        user = log_in(args.user, args.users_db, prompt, **auth_options) if args.user else None
        store = store_from_args(args.store, args.file, settings)
        if args.command == "list":
            owner = user.user_id if user else None
            users = open_users(args.users_db, **auth_options)
            try:
                names = {u.user_id: u.username for u in users.list_users()}
            finally:
                users.close()
            total = store.count(game=args.game, user_id=owner)
            print(f"{total} session(s) stored; showing the newest {min(total, args.limit)}.")
            for document in store.list_sessions(game=args.game, user_id=owner, limit=args.limit):
                print(format_row(document, names))
        elif args.command == "claim":
            print(f"Assigned {store.claim_unassigned(user.user_id)} unassigned round(s) to {user.username}.")
        else:
            source = args.source if args.source.is_absolute() else PROJECT_ROOT / args.source
            added, present, invalid = import_jsonl(source, store, user.user_id if user else None)
            print(f"Imported {added} new, {present} already present, {invalid} invalid line(s).")
    except AuthError as error:
        print(f"Login failed: {error}", file=sys.stderr)
        return 1
    except (ConfigurationError, StorageError, OSError, EOFError) as error:
        print(f"Sessions error: {error}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
