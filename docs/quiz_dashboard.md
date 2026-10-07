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

The **Export report...** button makes the Reach Garden PDF only, so it is switched off on the Scam Quiz tab. A quiz
progress report is not built yet.

## Acceptance checklist

- [ ] After a two-player round with the receiver running as `--user alice --user2 bob`, alice sees her round on the
      **Scam Quiz** tab within about 5 seconds (or click **Refresh**), and bob sees his in his own login.
- [ ] Neither player sees the other's answers.
- [ ] A new player sees the "No quiz rounds yet" message on the tab.
- [ ] The topic chart lists the weakest topics first and the "Topic to practise" line names the first one.
- [ ] The Reach Garden tab behaves exactly as before.
- [ ] The text and charts are readable in light and dark Windows themes.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_quiz_dashboard -v
```

The numbers (empty data, totals over questions rather than round percentages, skips, answer-time averaging, challenge
counts, weakest-first topics, the practice rule, trend order, table cells, a label for every topic in the bank) and the
tab (empty state, own rounds only, tabs kept apart, practice text, charts and refresh, the report button, a storage
failure). They run with Qt's offscreen mode. How the window looks on a real display is not covered by tests.
