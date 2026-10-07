# Changelog

What changed, by phase, with the date of each commit. Entries describe behaviour, not code. Dates are when the work was committed.

## Unreleased

- **Scam quiz: question bank.** 60 original questions from senior-tagged Digital for Life pages (12 per difficulty, 17 topics, each citing its source), a validator (`tools/validate_questions.py`) and a random round picker (`shared/questions.py`).
- **Scam quiz: two players on one camera (Python side).** `TRACKING_PLAYERS=2` matches hands to players by screen zone (left half, right half) instead of by MediaPipe's left/right label, so two people can both show a right hand. Each player gets their own smoothing, gestures and `CV_STATE` stream (new optional `slot` field); each half's middle is stretched to the full cursor range. One-player behaviour and packets are unchanged. The Unity side is not built yet.
- **Scam quiz: the Unity screen (not yet run in Unity).** A two-player quiz: each player's coloured cursor (blue disc, orange diamond) is held on an answer to choose it; five questions per round from the bank, shared adaptive level starting at 1, explanations that stay up longer when they are longer. The receiver keeps one buffer per player. See `docs/quiz_unity_screen.md`. Results are not saved yet.
- **Phase 20: portfolio polish.** Generated screenshots and sample report from invented demo data (`tools/make_demo_assets.py`); performance charts and a performance write-up; a retrospective of challenges and lessons; a guide to recording the demo; a CI workflow; README gallery and highlights.

## Documentation, setup and quality (2026-10-06)

- **Phase 19: README and architecture documentation.** New architecture and configuration documents; README rewritten with a verification table; stale statements fixed; tests that keep documentation in step with the code.
- **Setup fix.** The start-script tests no longer depend on a local `.venv`.
- **Phase 18: Windows scripts.** `MotionPlay-Setup.cmd`, `MotionPlay-Start.cmd` and `MotionPlay-Test.cmd`, with PowerShell scripts behind them. Idempotent setup, `-CheckOnly`, `-DryRun`, safe player-name validation.
- **Camera.** Opens with DirectShow on Windows (about 2.6 s instead of 6 to 29 s), with a setting to change it and a fallback; skips camera settings already in place; the run summary is logged even on Ctrl+C.
- **Phase 17: logging and error handling.** Every working command logs to the rotating file; uncaught errors recorded with tracebacks; start-up timings, settings and run summaries; account events logged without passwords; rate-limited warnings for malformed packets; the receiver survives unexpected storage errors; the dashboard shows an error dialog instead of aborting.
- **Phase 16: broader testing.** Python coverage from 89% to 96% by testing every command-line entry point, the preview window and the dashboard's dialogs; a fault-injection check of the new tests.
- **Q or Esc stops the headless engine**, as a second way out besides Ctrl+C.
- **Label continuity.** Keeps a hand's left/right label steady across brief flips and low-confidence frames, removing most cursor dropouts.
- **Phase 15: adaptive difficulty.** Five Reach Garden levels that move after two qualifying rounds, saved between sessions, shown in the dashboard and reports; manual `[` and `]` keys.
- **Unity project files** (settings, package lock, test scene) are now tracked.

## Results, accounts and reports (2026-10-06)

- **Phase 14: PDF progress reports** with a summary, an earlier-versus-later comparison, trend charts and a round table.
- **Phase 13: PyQt6 dashboard** with login, summary cards, charts and a live-refreshing table.
- **Receiver fix.** Ctrl+C now stops the result receiver on Windows.
- **Phase 12: local player accounts** with bcrypt passwords and login lockout; rounds are tagged with their player.
- **Phase 11: storage abstraction** with JSONL, SQLite and MongoDB stores, and a command to list and migrate rounds.
- **Phase 10: result delivery.** Each finished round is sent to a Python receiver, acknowledged, retried and stored once.
- **Phase 9: round statistics** (accuracy, streaks, reaction and movement time, hold stability, path efficiency).
- **Phase 8: Reach Garden**, the first game.

## Tracking and transport (2026-10-04)

- **Phase 7: hand-controlled Unity cursor** with mirror-aware mapping and bounds.
- **Phase 6: Unity UDP receiver** with validation, ordering, timeout and diagnostics.
- **Phase 5: paced UDP sender** and the `CV_STATE` protocol.
- **Phase 4: gesture engine** (open hand, fist, pinch, point) with per-hand debouncing.
- **Phase 3: palm coordinates** with smoothing, a dead zone and tracking-loss timeouts.
- **Phase 2: webcam capture and MediaPipe Hands** with a landmark preview.
- **Phase 1: project foundation**: Python environment, validated `.env` configuration, rotating logs, health check.
