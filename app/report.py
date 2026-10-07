"""Progress report: a PDF with a summary, an earlier-versus-later comparison, trend charts, and a round table.

Command line:  .\.venv\Scripts\python.exe -m app.report --user NAME [--days 30 | --since 2026-10-01] [--out FILE]
The dashboard's Export report button uses the same builder.

The numbers are gameplay measurements from a prototype. They are not medical measurements and the
report says so.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure

from app import dashboard_model as model
from backend.auth import AuthError
from backend.result_receiver import add_store_arguments, store_from_args
from backend.storage import StorageError
from backend.users import Prompt, add_user_arguments, log_in
from shared.config import PROJECT_ROOT, ConfigurationError, load_settings
from shared.logger import start_logging

PAGE_SIZE = (8.27, 11.69)  # A4 portrait, inches
ROWS_PER_PAGE = 28
MIN_ROUNDS_TO_COMPARE = 4
MAX_ROUNDS = 5000
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports"
LOGGER = logging.getLogger("motionplay.app.report")

DISCLAIMER = ("Gameplay measurements from a prototype game. This is not a medical device or therapy tool, "
              "and these numbers are not a basis for health conclusions.")


class ReportError(Exception):
    """The report cannot be made; the message is safe to show."""


@dataclass(frozen=True)
class Change:
    """One measure compared between the earlier and later half of the rounds."""

    label: str
    earlier: str
    later: str
    change: str
    verdict: str  # "improved", "declined", "about the same", or "not enough data"


def _group_values(group: list[dict]) -> dict[str, float | None]:
    summary = model.summarize(group)
    return {"accuracy": summary.accuracy, "reaction": summary.average_reaction,
            "stability": summary.average_stability, "efficiency": summary.average_efficiency}


# label, key, formatter, (higher is better), smallest change worth calling a change, unit for the change text
_MEASURES = (
    ("Accuracy", "accuracy", model.percent, True, 0.02, lambda d: f"{d * 100:+.0f} pts"),
    ("Reaction time", "reaction", model.seconds, False, 0.03, lambda d: f"{d:+.2f} s"),
    ("Hold stability", "stability", model.percent, True, 0.02, lambda d: f"{d * 100:+.0f} pts"),
    ("Path efficiency", "efficiency", model.percent, True, 0.02, lambda d: f"{d * 100:+.0f} pts"),
)


def compare(documents: list[dict]) -> list[Change] | None:
    """Earlier half versus later half of the rounds by time, or None if there are too few rounds."""
    if len(documents) < MIN_ROUNDS_TO_COMPARE:
        return None
    ordered = sorted(documents, key=lambda d: (d["endedAt"], d["session_id"]))
    half = len(ordered) // 2
    earlier, later = _group_values(ordered[:half]), _group_values(ordered[half:])
    changes = []
    for label, key, fmt, higher_is_better, threshold, describe in _MEASURES:
        before, after = earlier[key], later[key]
        if before is None or after is None:
            changes.append(Change(label, fmt(before), fmt(after), model.NO_VALUE, "not enough data"))
            continue
        delta = after - before
        if abs(delta) < threshold:
            verdict = "about the same"
        else:
            verdict = "improved" if (delta > 0) == higher_is_better else "declined"
        changes.append(Change(label, fmt(before), fmt(after), describe(delta), verdict))
    return changes


def _page() -> Figure:
    return Figure(figsize=PAGE_SIZE)


def _table(axis, rows: list[list[str]], header: list[str], widths: list[float] | None = None):
    axis.axis("off")
    table = axis.table(cellText=rows, colLabels=header, colWidths=widths, loc="upper center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.5)
    for (row, _column), cell in table.get_celld().items():
        if row == 0:
            cell.set_text_props(weight="bold")
            cell.set_facecolor("#e6e6e6")
    return table


def _summary_page(documents: list[dict], username: str, generated_at: datetime, period: str) -> Figure:
    summary = model.summarize(documents)
    figure = _page()
    figure.text(0.08, 0.95, "MotionPlay progress report", fontsize=20, weight="bold", va="top")
    figure.text(0.08, 0.915, f"Player: {username}", fontsize=12, va="top")
    figure.text(0.08, 0.895, f"Period: {period}   |   Rounds from {model.when(min(d['endedAt'] for d in documents))} "
                f"to {model.when(summary.last_played)}", fontsize=10, va="top")
    figure.text(0.08, 0.875, f"Generated {generated_at.strftime('%Y-%m-%d %H:%M')}", fontsize=10, va="top")
    figure.text(0.08, 0.845, "\n".join(textwrap.wrap(DISCLAIMER, 105)), fontsize=8, style="italic", va="top")

    figure.text(0.08, 0.78, "Summary", fontsize=14, weight="bold", va="top")
    rows = [
        ["Rounds played", str(summary.rounds)],
        ["Flowers watered", f"{summary.targets_completed} of {summary.targets_attempted}"],
        ["Overall accuracy", model.percent(summary.accuracy)],
        ["Best streak in a round", str(summary.best_streak)],
        ["Total play time", model.duration(summary.total_seconds)],
        ["Average reaction time", model.seconds(summary.average_reaction)],
        ["Average movement time", model.seconds(summary.average_movement)],
        ["Average hold stability", model.percent(summary.average_stability)],
        ["Average path efficiency", model.percent(summary.average_efficiency)],
    ]
    _table(figure.add_axes([0.08, 0.50, 0.84, 0.26]), rows, ["Measure", "Value"], [0.6, 0.4])

    figure.text(0.08, 0.48, "Progress: earlier rounds compared with later rounds", fontsize=14, weight="bold", va="top")
    changes = compare(documents)
    if changes is None:
        figure.text(0.08, 0.445, f"Not enough rounds to compare yet (at least {MIN_ROUNDS_TO_COMPARE} are needed).",
                    fontsize=10, va="top")
    else:
        half = len(documents) // 2
        figure.text(0.08, 0.458, f"Earlier = the first {half} round(s); later = the other {len(documents) - half}. "
                    "A measure counts as changed only past a small margin.", fontsize=9, va="top")
        _table(figure.add_axes([0.08, 0.265, 0.84, 0.16]),
               [[c.label, c.earlier, c.later, c.change, c.verdict] for c in changes],
               ["Measure", "Earlier", "Later", "Change", "Result"], [0.24, 0.17, 0.17, 0.2, 0.22])
        figure.text(0.08, 0.275, "With few rounds, a difference can come from luck, tiredness, or lighting "
                    "as easily as from practice.", fontsize=9, va="top", style="italic")
    if model.mixed_levels(documents):
        figure.text(0.08, 0.235, "\n".join(textwrap.wrap(
            "These rounds were played at different difficulty levels (see the Level column), so earlier and later "
            "rounds are not like for like: a higher level is harder, which can lower accuracy even when play improves.",
            105)), fontsize=9, va="top", style="italic")
    return figure


def _charts_page(documents: list[dict]) -> Figure:
    ordered = sorted(documents, key=lambda d: (d["endedAt"], d["session_id"]))
    figure = _page()
    figure.text(0.08, 0.95, "Trends by round (oldest to newest)", fontsize=16, weight="bold", va="top")
    series = (
        ("Accuracy (%)", [d["accuracy"] for d in ordered], 100, (0, 105)),
        ("Reaction time (s)", [d["averageReactionTime"] for d in ordered], 1, None),
        ("Hold stability (%)", [d["averageHoldStability"] for d in ordered], 100, (0, 105)),
        ("Path efficiency (%)", [d["pathEfficiency"] for d in ordered], 100, (0, 105)),
    )
    axes = figure.subplots(len(series), 1, sharex=True, gridspec_kw={"left": 0.12, "right": 0.94, "top": 0.89,
                                                                       "bottom": 0.10, "hspace": 0.35})
    for axis, (label, values, scale, limits) in zip(axes, series):
        points = [float("nan") if v is None else v * scale for v in values]
        axis.plot(range(1, len(points) + 1), points, marker="o", linewidth=1.5)
        axis.set_ylabel(label, fontsize=9)
        axis.grid(True, alpha=0.3)
        known = [p for p in points if p == p]
        if limits:
            axis.set_ylim(*limits)
        elif known and max(known) > 0:
            axis.set_ylim(0, max(known) * 1.15)
        else:
            axis.set_ylim(0, 1)
    axes[-1].set_xlabel("Round")
    axes[-1].xaxis.get_major_locator().set_params(integer=True)
    figure.text(0.08, 0.04, "A gap means the round had no value for that measure (for example, no flower was watered).",
                fontsize=8, style="italic")
    return figure


def _table_pages(documents: list[dict]) -> list[Figure]:
    newest_first = sorted(documents, key=lambda d: (d["endedAt"], d["session_id"]), reverse=True)
    pages = []
    for start in range(0, len(newest_first), ROWS_PER_PAGE):
        chunk = newest_first[start:start + ROWS_PER_PAGE]
        figure = _page()
        figure.text(0.08, 0.95, "Rounds, newest first", fontsize=16, weight="bold", va="top")
        figure.text(0.08, 0.92, f"Rounds {start + 1} to {start + len(chunk)} of {len(newest_first)}", fontsize=9, va="top")
        _table(figure.add_axes([0.04, 0.05, 0.92, 0.85]), [model.table_row(d) for d in chunk], model.TABLE_HEADERS,
               [0.18, 0.07, 0.06, 0.09, 0.09, 0.1, 0.1, 0.1, 0.11, 0.1])
        pages.append(figure)
    return pages


def build_report(documents: list[dict], username: str, generated_at: datetime, period: str = "All rounds") -> list[Figure]:
    """The report as page figures. Raises ReportError if there are no rounds."""
    if not documents:
        raise ReportError("There are no rounds in that period, so there is nothing to report.")
    pages = [_summary_page(documents, username, generated_at, period), _charts_page(documents), *_table_pages(documents)]
    for number, page in enumerate(pages, start=1):
        page.text(0.5, 0.012, f"MotionPlay progress report - {username} - page {number} of {len(pages)}",
                  fontsize=7, ha="center", color="#555555")
    return pages


def write_pdf(pages: list[Figure], path: Path) -> Path:
    """Write the PDF through a temporary file so a failure cannot leave a half-written report."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with PdfPages(temporary, metadata={"Title": "MotionPlay progress report", "Creator": "MotionPlay"}) as pdf:
            for page in pages:
                pdf.savefig(page)
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ReportError(f"Cannot write the report: {error.strerror or error}") from error
    return path


def default_filename(username: str, now: datetime) -> str:
    return f"MotionPlay-progress-{username}-{now.strftime('%Y%m%d-%H%M')}.pdf"


def filter_period(documents: list[dict], since: datetime | None) -> list[dict]:
    if since is None:
        return list(documents)
    cutoff = int(since.timestamp() * 1000)
    return [d for d in documents if d["endedAt"] >= cutoff]


def main(argv: list[str] | None = None, *, prompt: Prompt = getpass.getpass, now: datetime | None = None,
         **auth_options) -> int:
    parser = argparse.ArgumentParser(description="MotionPlay progress report (PDF)")
    add_store_arguments(parser)
    parser.set_defaults(store="sqlite")
    add_user_arguments(parser, required_user=True)
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--days", type=int, help="Only rounds from the last N days")
    window.add_argument("--since", help="Only rounds from this date on, as YYYY-MM-DD")
    parser.add_argument("--out", type=Path, help="Where to save the PDF (default: reports/ in the project)")
    args = parser.parse_args(argv)
    now = now or datetime.now()

    store = None
    try:
        since, period = None, "All rounds"
        if args.days is not None:
            if args.days < 1:
                raise ConfigurationError("--days must be at least 1.")
            since, period = now - timedelta(days=args.days), f"Last {args.days} day(s)"
        elif args.since is not None:
            try:
                since = datetime.strptime(args.since, "%Y-%m-%d")
            except ValueError:
                raise ConfigurationError("--since must be a date like 2026-10-01.") from None
            period = f"Since {args.since}"
        settings = load_settings()
        start_logging(settings, "report", console=False)
        user = log_in(args.user, args.users_db, prompt, **auth_options)
        store = store_from_args(args.store, args.file, settings)
        documents = filter_period(store.list_sessions(game=model.GARDEN_GAME, user_id=user.user_id, limit=MAX_ROUNDS), since)
        pages = build_report(documents, user.username, now, period)
        out = args.out or DEFAULT_REPORT_DIR / default_filename(user.username, now)
        out = out if out.is_absolute() else Path.cwd() / out
        write_pdf(pages, out)
        LOGGER.info("Report saved for %s: %d round(s), %d page(s), %s", user.username, len(documents), len(pages), out)
        print(f"Saved {len(pages)}-page report for {user.username} ({len(documents)} round(s)): {out}")
    except AuthError as error:
        print(f"Login failed: {error}", file=sys.stderr)
        return 1
    except (ConfigurationError, StorageError, ReportError, OSError, EOFError) as error:
        print(f"Report error: {error}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
