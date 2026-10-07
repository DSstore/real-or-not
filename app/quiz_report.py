"""Scam quiz progress report: a PDF with a summary, an earlier-versus-later comparison, the topics to practise, trend
charts, accuracy by topic, and a round table.

Command line:  .\.venv\Scripts\python.exe -m app.report --game quiz --user NAME [--days 30 | --since 2026-10-01]
The dashboard's Export report button uses the same builder on the Scam Quiz tab. The pages, PDF writing and period
filter are shared with the Reach Garden report (app/report.py).

The numbers describe answers in a practice game. They are not a test of ability and the report says so.
"""

from __future__ import annotations

import textwrap
from datetime import datetime

from app import dashboard_model as shared_model
from app import quiz_model as model
from app.report import MIN_ROUNDS_TO_COMPARE, ROWS_PER_PAGE, Change, ReportError, _page, _table

DISCLAIMER = ("Results from a practice quiz about staying safe online. They show which topics a player knows well and "
              "which are worth another look. They are not a test of ability.")
# label, formatter, (higher is better), smallest change worth calling a change, text for the change
_MEASURES = (
    ("Accuracy", shared_model.percent, True, 0.05, lambda d: f"{d * 100:+.0f} pts"),
    ("Answer time", shared_model.seconds, False, 0.5, lambda d: f"{d:+.1f} s"),
)


def default_filename(username: str, now: datetime) -> str:
    return f"MotionPlay-quiz-progress-{username}-{now.strftime('%Y%m%d-%H%M')}.pdf"


def _ordered(documents: list[dict]) -> list[dict]:
    return sorted(documents, key=lambda d: (d["endedAt"], d["session_id"]))


def compare(documents: list[dict]) -> list[Change] | None:
    """Earlier half versus later half of the rounds by time, or None if there are too few rounds.

    A round has only five or so questions, so accuracy moves in big steps and the margin is wider than for Reach Garden."""
    if len(documents) < MIN_ROUNDS_TO_COMPARE:
        return None
    ordered = _ordered(documents)
    half = len(ordered) // 2
    groups = []
    for group in (ordered[:half], ordered[half:]):
        summary = model.summarize(group)
        groups.append({"Accuracy": summary.accuracy, "Answer time": summary.average_answer_time})
    earlier, later = groups
    changes = []
    for label, fmt, higher_is_better, threshold, describe in _MEASURES:
        before, after = earlier[label], later[label]
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


def _summary_page(documents: list[dict], username: str, generated_at: datetime, period: str) -> "Figure":
    summary = model.summarize(documents)
    figure = _page()
    figure.text(0.08, 0.95, "MotionPlay scam quiz report", fontsize=20, weight="bold", va="top")
    figure.text(0.08, 0.915, f"Player: {username}", fontsize=12, va="top")
    figure.text(0.08, 0.895, f"Period: {period}   |   Rounds from {shared_model.when(min(d['endedAt'] for d in documents))} "
                f"to {shared_model.when(summary.last_played)}", fontsize=10, va="top")
    figure.text(0.08, 0.875, f"Generated {generated_at.strftime('%Y-%m-%d %H:%M')}", fontsize=10, va="top")
    figure.text(0.08, 0.845, "\n".join(textwrap.wrap(DISCLAIMER, 105)), fontsize=8, style="italic", va="top")

    figure.text(0.08, 0.78, "Summary", fontsize=14, weight="bold", va="top")
    challenge = (model.NO_VALUE if summary.challenge_asked == 0
                 else f"{summary.challenge_correct} of {summary.challenge_asked} "
                      f"({shared_model.percent(summary.challenge_accuracy)})")
    rows = [
        ["Rounds played", str(summary.rounds)],
        ["Questions answered correctly", f"{summary.correct} of {summary.questions}"],
        ["Overall accuracy", shared_model.percent(summary.accuracy)],
        ["Questions that ran out of time", str(summary.skipped)],
        ["Best streak in a round", str(summary.best_streak)],
        ["Average answer time", shared_model.seconds(summary.average_answer_time)],
        ["Level at the latest round", model.NO_VALUE if summary.latest_level is None else str(summary.latest_level)],
        ["Challenge questions", challenge],
        ["Total play time", shared_model.duration(summary.total_seconds)],
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
                    "A measure counts as changed only past a margin.", fontsize=9, va="top")
        _table(figure.add_axes([0.08, 0.31, 0.84, 0.11]),
               [[c.label, c.earlier, c.later, c.change, c.verdict] for c in changes],
               ["Measure", "Earlier", "Later", "Change", "Result"], [0.24, 0.17, 0.17, 0.2, 0.22])
        figure.text(0.08, 0.30, "With few questions, a difference can come from luck as easily as from learning.",
                    fontsize=9, va="top", style="italic")
    if shared_model.mixed_levels(documents):
        figure.text(0.08, 0.275, "\n".join(textwrap.wrap(
            "These rounds were played at different levels (see the Level column). A higher level means harder "
            "questions and fewer hints, so accuracy can fall even when knowledge improves.", 105)),
            fontsize=9, va="top", style="italic")

    figure.text(0.08, 0.21, "Topics to practise", fontsize=14, weight="bold", va="top")
    topics = model.practice_topics(model.category_scores(documents), limit=3)
    if topics:
        lines = [f"- {t.label}: {t.correct} of {t.asked} answered correctly ({shared_model.percent(t.accuracy)})"
                 for t in topics]
        text = "\n".join(lines)
    else:
        text = "None yet: every topic asked at least twice was answered correctly."
    figure.text(0.08, 0.18, text, fontsize=10, va="top", linespacing=1.6)
    return figure


def _charts_page(documents: list[dict]) -> "Figure":
    ordered = _ordered(documents)
    figure = _page()
    figure.text(0.08, 0.95, "Trends by round (oldest to newest)", fontsize=16, weight="bold", va="top")
    series = (
        ("Accuracy (%)", [model.accuracy_of(d) for d in ordered], 100, (0, 105)),
        ("Answer time (s)", [d["averageResponseTime"] for d in ordered], 1, None),
        ("Level", [shared_model.level_of(d) for d in ordered], 1, (0.5, 5.5)),
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
    axes[2].yaxis.get_major_locator().set_params(integer=True)
    axes[-1].set_xlabel("Round")
    axes[-1].xaxis.get_major_locator().set_params(integer=True)
    figure.text(0.08, 0.025, "\n".join(textwrap.wrap(
        "A gap in answer time means no question was answered in that round. The level is shared by the pair and rises "
        "as they do well.", 120)), fontsize=8, style="italic")
    return figure


def _topics_page(documents: list[dict]) -> "Figure":
    scores = model.category_scores(documents)
    figure = _page()
    figure.text(0.08, 0.95, "Accuracy by topic", fontsize=16, weight="bold", va="top")
    figure.text(0.08, 0.92, "Weakest at the top. The numbers in brackets are correct answers out of questions asked.",
                fontsize=9, va="top")
    axis = figure.add_axes([0.40, 0.14, 0.54, 0.74])
    axis.barh(range(len(scores)), [s.accuracy * 100 for s in scores])
    axis.set_yticks(range(len(scores)))
    axis.set_yticklabels([f"{s.label} ({s.correct}/{s.asked})" for s in scores], fontsize=9)
    axis.invert_yaxis()
    axis.set_xlim(0, 105)
    axis.set_xlabel("Accuracy (%)")
    axis.grid(True, axis="x", alpha=0.3)
    figure.text(0.08, 0.03, "\n".join(textwrap.wrap(
        "A topic asked only once or twice says little; look for topics that stay low across rounds. Questions the "
        "question bank did not recognise are left out.", 120)), fontsize=8, style="italic")
    return figure


def _table_pages(documents: list[dict]) -> list["Figure"]:
    newest_first = list(reversed(_ordered(documents)))
    pages = []
    for start in range(0, len(newest_first), ROWS_PER_PAGE):
        chunk = newest_first[start:start + ROWS_PER_PAGE]
        figure = _page()
        figure.text(0.08, 0.95, "Rounds, newest first", fontsize=16, weight="bold", va="top")
        figure.text(0.08, 0.92, f"Rounds {start + 1} to {start + len(chunk)} of {len(newest_first)}", fontsize=9, va="top")
        _table(figure.add_axes([0.04, 0.05, 0.92, 0.85]), [model.table_row(d) for d in chunk], model.TABLE_HEADERS,
               [0.2, 0.08, 0.12, 0.14, 0.16, 0.16, 0.14])
        pages.append(figure)
    return pages


def build_report(documents: list[dict], username: str, generated_at: datetime, period: str = "All rounds") -> list["Figure"]:
    """The report as page figures. Raises ReportError if there are no rounds."""
    if not documents:
        raise ReportError("There are no quiz rounds in that period, so there is nothing to report.")
    pages = [_summary_page(documents, username, generated_at, period), _charts_page(documents),
             _topics_page(documents), *_table_pages(documents)]
    for number, page in enumerate(pages, start=1):
        page.text(0.5, 0.012, f"MotionPlay scam quiz report - {username} - page {number} of {len(pages)}",
                  fontsize=7, ha="center", color="#555555")
    return pages
