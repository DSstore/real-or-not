# Scam quiz dashboard

The dashboard (`python -m app.dashboard`) has one tab per game. **Reach Garden** is unchanged. **Scam Quiz** shows the
logged-in player's own quiz rounds, read from the same store. Each player in a two-player round has their own account
(`--user`, `--user2` on the result receiver), so each sees only their own answers.

```
backend storage (quiz_sessions) ─ list_sessions(game="scam_quiz", user_id=…)
        └─ app/dashboard.py        the window and its tabs
              └─ app/quiz_panel.py   the Scam Quiz tab (Qt)
                    └─ app/quiz_model.py   the numbers and text (no Qt, tested directly)
```

## What the tab shows

- **Cards:** rounds played; accuracy over all questions (correct of asked, a skipped question counts as not correct);
  best streak; average answer time over the questions that were answered; the level of the most recent round; and the
  challenge question record (for example `3/5`).
- **Topic to practise:** the topic with the lowest accuracy among topics asked at least twice with at least one mistake.
  One wrong answer is not named a weakness; a history with no mistakes says there are no weak topics yet.
- **Accuracy by round** (left chart, oldest first) and **accuracy by topic** (right chart, the eight weakest, weakest at
  the top, with `correct/asked` in each label).
- **Round table**, newest first: played, level, correct, accuracy, average answer time, best streak, duration.

Topic results use the category the receiver stored with each answer, so they stay correct if the question bank changes
later. A question the bank did not know when the round was saved has no topic and is left out of the topic chart. The
everyday-skills challenge questions are their own topic, "Digital skills (challenge)".

## PDF report

**Export report...** makes the PDF for the game on the tab you are looking at. From the command line:

```powershell
.\.venv\Scripts\python.exe -m app.report --game quiz --user alice [--days 30 | --since 2026-10-01] [--out FILE]
```

Without `--game` the command makes the Reach Garden report, as before. The builder is `app/quiz_report.py`; it shares
the page helpers, PDF writing and period filter with `app/report.py`.

| Page | Contents |
|---|---|
| 1 | Player, period and date, a plain note that this is a practice quiz and not a test of ability; a summary table; an earlier-versus-later comparison; up to three topics to practise |
| 2 | Three trend charts by round, oldest to newest: accuracy, answer time, level |
| 3 | Accuracy for every topic, weakest at the top, with correct out of asked |
| 4 onward | Every round, newest first, 28 per page |

The comparison needs at least 4 rounds and splits them into an earlier and a later half. Accuracy counts as changed at 5
points or more (a round has only about five questions, so it moves in big steps) and answer time at 0.5 s or more; a
lower answer time is better. When the rounds were played at different levels the report says so, because a higher level
is harder and can lower accuracy while knowledge improves.

## Acceptance checklist

- [ ] After a two-player round with the receiver running as `--user alice --user2 bob`, alice sees her round on the
      **Scam Quiz** tab within about 5 seconds (or click **Refresh**), and bob sees his in his own login.
- [ ] Neither player sees the other's answers.
- [ ] A new player sees the "No quiz rounds yet" message on the tab.
- [ ] The topic chart lists the weakest topics first and the "Topic to practise" line names the first one.
- [ ] The Reach Garden tab behaves exactly as before.
- [ ] The text and charts are readable in light and dark Windows themes.
- [ ] **Export report...** on the Scam Quiz tab saves a four-page PDF that opens, with your name and your rounds only.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_quiz_dashboard -v
```

The numbers (empty data, totals over questions rather than round percentages, skips, answer-time averaging, challenge
counts, weakest-first topics, the practice rule, trend order, table cells, a label for every topic in the bank) and the
tab (empty state, own rounds only, tabs kept apart, practice text, charts and refresh, the report button, a storage
failure). `tests/test_quiz_report.py` covers the comparison, the pages, the PDF, `--game quiz` and the dashboard export.
They run with Qt's offscreen mode. How the window looks on a real display is not covered by tests.
