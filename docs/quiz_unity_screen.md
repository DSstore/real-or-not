# Scam quiz: the Unity screen

Status: **built and type-checked against Unity 2022.3, not yet run inside Unity.** The game rules are fully tested in the
C# harness; the drawing, layout and scene setup have been compiled but never looked at on a screen, so expect to adjust
sizes and spacing after the first play.

Two people (or one) stand in front of the camera and each move their own coloured cursor with a hand. They see the same
question and hold their cursor on an answer to choose it. A round is five questions picked at random from the question
bank (`data/questions.json`).

## Running it

1. Put `TRACKING_PLAYERS=2` in `.env` (leave it at `1` for one player).
2. Open `unity/MotionPlay` in Unity 2022.3 and choose **MotionPlay > Create Scam Quiz Scene**, then press **Play**.
   The scene is made by the menu item so that its references match your copy of the project.
3. Start the engine: `.\.venv\Scripts\python.exe -m cv_engine.controller` (a preview window shows the two zones).
4. Both people show one hand, one on each side of the picture. The round starts when both hands have appeared.

For one player, set **Players** to 1 on the *Scam Quiz* object in Unity and keep `TRACKING_PLAYERS=1`.

| Key | Effect |
|---|---|
| **R** or **S** | While waiting: start with whoever has shown a hand. On the summary: play again |
| **[** and **]** | Lower or raise the level, between rounds only |

## How a round plays

- **Joining.** Each player's hand must appear once. Player 1 is the left half of the camera picture and player 2 the
  right half, shown as a **blue disc** and an **orange diamond** with a "1" or "2" beside the cursor, so they can be told
  apart without colour.
- **Choosing.** Hold your cursor on an answer; a bar in your colour fills along the bottom of the answer. Moving to
  another answer starts again; leaving drains the bar. Once the bar is full your answer is locked and a marker shows it.
- **Both answered, or time up.** The question ends when every player has answered or the time runs out. A player who
  did not answer is recorded as *skipped*, not wrong.
- **The clock** runs only while at least one hand is tracked, so putting your hand down to think costs nothing.
- **Explanation.** The right answer turns green with a tick, a wrong choice turns orange-red with a cross (shapes as well
  as colours), and each player sees "Well done!" or "Not quite. Here is why:" with the explanation. It stays up for at
  least 6 seconds, longer for a longer explanation (0.03 s a character, up to 15 s).
- **Summary.** Each player's score, average answer time and best run, an encouraging message, and a *Play again* button
  to hold your cursor on (or press R). Wrong answers cost nothing; they are counted so that reports can show what to practise.

## Levels

The level is shared by the players, starts at 1 and moves one step after **two** rounds in a row that were too easy or too
hard for the pair together (combined score at least 90% with quick answers, or at most 40%). It is saved on this machine.

| Level | Time per question | Answers shown | Hardest question | Hold to choose |
|---|---|---|---|---|
| 1 | 25 s | 2 | 1 | 1.2 s |
| 2 | 20 s | 2 | 2 | 1.0 s |
| 3 | 15 s | 3 | 3 | 0.8 s |
| 4 | 12 s | 4 | 4 | 0.7 s |
| 5 | 10 s | 4 | 5 | 0.6 s |

With fewer than four answers shown, the right answer is always among them, plus randomly chosen wrong ones, in a random
order. Questions asked in the last three rounds are avoided while there are others to pick from, and each round's
questions are spread over different topics where possible.

## Where things are

| File | What it is |
|---|---|
| `Core/ScamQuizGame.cs` | The rules: joining, questions, holding, timing, feedback, summary. No Unity types |
| `Core/ScamQuizStats.cs`, `ScamQuizDifficulty.cs`, `QuestionData.cs` | Per-player statistics, the levels, the question bank and round picker |
| `Runtime/ScamQuizController.cs` | Turns cursors into answer numbers and draws everything |
| `Runtime/QuestionBankLoader.cs` | Reads `data/questions.json` (Editor) or `StreamingAssets/questions.json` (a built player) |
| `Editor/ScamQuizSceneSetup.cs` | The menu item that builds the scene, and the build step that copies the question bank |
| `Core/UdpStateListener.cs`, `Runtime/UdpReceiver.cs` | One buffer per player; `HandCursorController` has a `slot` |

The copy in `Assets/StreamingAssets` is made before every build and is not committed; `data/questions.json` is the source.

## Saving results

When a round ends, each player's result is sent to the Python result receiver, which stores it once and acknowledges it
(the summary shows "Saving results...", then "Results saved", or says if the receiver is not running). Each player has
their own result, all sharing one round id. Start the receiver before playing:

```powershell
.\.venv\Scripts\python.exe -m backend.result_receiver --store sqlite --user alice --user2 bob
```

Player 1 (the left half) is saved to `--user` and player 2 to `--user2`; leave one out and that player is saved without an
owner. Each stored round keeps, for every question, the choice made, the time taken and (looked up from the question
bank) its category, difficulty and right answer. The message and storage are described in
[the protocol](udp_protocol.md#scam-quiz-results). `backend.sessions list --game scam_quiz` shows them.

## Not done yet

- **No dashboard or report for the quiz.** The dashboard and PDF reports still show Reach Garden rounds only (they now
  ask for that game by name, so quiz rounds in the same database do not disturb them).
- **`MotionPlay-Start.cmd` and `start.ps1` start the receiver for one player.** Start the receiver by hand with `--user2`
  for two players.
- Nobody has looked at the layout in Unity yet (question and answer text sizes, the spacing of the panels).
