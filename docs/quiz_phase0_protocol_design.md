# Scam Quiz — Phase 0: protocol, storage and consumer design

Status: **proposal, nothing built yet.** This is the design spike that comes before Phase 1 of the
Scam Quiz conversion plan (`plan.md`, kept outside the repository). It settles the three
things the plan under-estimated, so that Phases 2 and 3 can be built against a fixed contract.

**Amended after the plan was settled with the author:** the quiz is for **two players** (each with a coloured cursor,
both answering every question), a round is **5 random questions** from a bank of 50 or more, and the difficulty level is
**shared by the pair**, starting at level 1. One `QUIZ_SESSION_END` is sent **per player**, linked by a shared
`roundId`. The sections below reflect that.

The quiz is added **alongside** Reach Garden. Every decision below is judged first by one rule: *a Reach Garden
round must be stored, listed and reported exactly as before.*

## Why a Phase 0

The plan lists the result protocol, receiver and storage as "Small" or "unchanged". Reading the code shows
otherwise:

| Plan says | Code says |
| --- | --- |
| Result protocol: "add fields" (Small) | `SessionEnd` in `shared/protocol.py` is a frozen dataclass; `decode_session_end` requires **every** field and enforces garden rules (`score <= targetsAttempted`, `bestStreak <= targetsCompleted`). A quiz result cannot pass it. |
| `responses[]` log inside `SESSION_END` | `MAX_DATAGRAM_BYTES = 1200` (Python) and `SessionResultCodec.MaxDatagramBytes = 1200` (C#). The plan's payload measures about **2,100 bytes** (envelope 310 + stats 143 + 10 verbose responses 1,471 + category breakdown 190). It would be rejected on both sides. |
| Storage: "schema-flexible, unchanged" | `SqliteResultStore` builds its columns from `SessionEnd.__dataclass_fields__`; `JsonlResultStore` and `MongoResultStore` store any dict but `save()` is typed to `SessionEnd`. |
| Dashboard/report: "detect game type" (Medium) | `app/dashboard.py` and `app/report.py` call `list_sessions(user_id=...)` **without** a `game` filter, then `dashboard_model` indexes `d["targetsAttempted"]` directly. The first stored quiz round would raise `KeyError` and break the dashboard for that player. |
| `ResultSender`/`ResultDelivery` unchanged | `ResultDelivery` is byte-based and needs no change. `ResultSender.Submit(SessionResult)` encodes a garden `SessionResult`, so it needs one small overload. |

## Decision 1 — A separate message type, not a modified `SESSION_END`

**Proposal:** add a new message `QUIZ_SESSION_END` with its own dataclass `QuizSessionEnd`. `SESSION_END` and
`SessionEnd` are not touched. `RESULT_ACK` is reused as is (it is keyed only by `session_id`).

This deviates from the plan, which kept the `SESSION_END` envelope and switched on `game`. Reasons:

- The two messages have different required fields and different invariants. One class validating both needs
  conditional logic in the most security-sensitive file in the project (it decodes untrusted datagrams).
- A separate type means **zero change to garden validation**, so the 355 existing Python tests stay a regression net.
- Dispatch is one line in the receiver: switch on `type`.

The envelope fields are identical (`stream_id`, `sequence`, `timestamp`, `session_id`, `game`, `hand`, `difficulty`,
`startedAt`, `endedAt`, `duration`) and use the same validators. `PROTOCOL_VERSION` stays `1`: a new `type` is additive.

### Wire format

```json
{"type":"QUIZ_SESSION_END","version":1,
 "stream_id":"…","sequence":12,"timestamp":1790000150000,
 "session_id":"…","game":"scam_quiz","hand":"right","difficulty":"level_1",
 "startedAt":1790000000000,"endedAt":1790000150000,"duration":150.123,
 "roundId":"…","slot":0,"bankVersion":1,
 "totalQuestions":10,"correct":7,"wrong":2,"skipped":1,"bestStreak":4,
 "averageResponseTime":6.123,"fastestResponse":2.1,"slowestResponse":11.4,
 "responses":[["sms-01",1,4200],["phone-03",2,9100],["love-02",-1,null]]}
```

- `slot` is the player, `0` or `1`. `roundId` is one UUID shared by both players' results for the same round; each
  player's result still has its own `session_id`, so storage stays exactly-once per player.
- `hand` is whichever hand the player used (`left` or `right`), as today.
- `difficulty` is the shared level the round was played at (`level_1` to `level_5`).

`responses` entries are `[question_id, selected, responseMs]`:

- `selected` is the index into the question's **original** `choices` array (not the 2 or 3 choices shown at low
  levels), or `-1` if time ran out.
- `responseMs` is an integer count of milliseconds of *tracked* time (the timer is paused while the hand is lost),
  or `null` for a timeout. Timeouts therefore never enter the averages, matching the plan.
- `category`, `difficulty` and `is_correct` are **not sent**. The receiver derives them from `question_id` plus
  `bankVersion` (below).

### Datagram budget

Measured with compact JSON separators:

| Payload | Bytes |
| --- | --- |
| Plan as written (10 verbose responses + category breakdown) | ~2,114 — **over the 1,200 limit** |
| Proposed, 5 questions (the default round) | ~580, plus ~57 for `roundId` and `slot` = ~640 |
| Proposed, 10 questions | ~704 + 57 = ~760 |
| Proposed, 20 questions | ~954 + 57 = ~1,010 |
| Practical ceiling at 1,200 | ~28 questions |

`QuizSessionEnd` therefore **validates `1 <= totalQuestions <= 20`** and `len(responses) == totalQuestions`. A round is
5 questions by default; the length stays configurable in Unity but is clamped to 20. If a longer round is ever wanted,
the answer is a second `QUIZ_DETAIL` message, not a bigger datagram: UDP datagrams above the path MTU fragment, and
the 1,200 limit exists for that reason. Sending one result per player (rather than one for the pair) keeps each
datagram within the same budget.

### Invariants the receiver enforces

Mirroring the existing garden checks, a quiz result is rejected unless:

- `correct + wrong + skipped == totalQuestions == len(responses)`.
- Counts derived from `responses` equal the stated `correct`, `wrong`, `skipped` (the sender cannot claim a score
  its own log contradicts).
- `selected` is `-1` exactly when `responseMs` is `null`, and otherwise `0 <= selected < 4`.
- `bestStreak <= correct`.
- `responseMs` is a nonnegative integer; the three time fields are `null` or finite and nonnegative, and are `null`
  exactly when no question was answered. *Null means no samples, never zero.*
- `question_id` strings are 1–64 characters; ids within one round are unique.
- `slot` is 0 or 1; `roundId` is a canonical UUID string; `difficulty` is `level_1` to `level_5`.

### Receiver

`ResultReceiver.handle` decodes by `type`:

1. `SESSION_END` → `decode_session_end` → `store.save` (unchanged path).
2. `QUIZ_SESSION_END` → `decode_quiz_session_end` → enrich → `store.save`.

`peek_session_id` must accept both types so a malformed quiz packet still gets a `rejected` ack and Unity stops
retrying. The "stored" log line prints a type-appropriate summary instead of "n of m watered".

**Two players, one receiver.** Today the receiver is started with one `--user`, and every round is saved to that
player. For the quiz it maps `slot -> user`: `--user` is given twice (or `--users a,b`), each player is authenticated
through `backend/auth.py`, and a quiz result is saved to the user for its `slot`. A slot with no user saves as a guest
(`user_id = null`), which `claim_unassigned` can give to a player later. Garden results keep using the single `--user`.

### Enrichment and `bankVersion`

The receiver loads `data/questions.json` (via `shared/questions.py`, Phase 1) and writes a **self-contained** stored
document: each response gains `category`, `difficulty`, `correct` (the right index) and `is_correct`.

`bankVersion` is the `version` field of `questions.json`. If it differs from the receiver's copy, or a
`question_id` is unknown, the round is **still stored**, with `category: null` for the unresolved responses, and a
warning is logged. Dropping a senior's round because the question file was edited between play and
receipt would be the wrong trade-off. Rule for content authors: **never reuse or repurpose a `question_id`; retire
it and bump `version`.**

## Decision 2 — Storage

JSONL and MongoDB already hold arbitrary documents and key on `session_id`; they need only the `save()` type
widened to `SessionEnd | QuizSessionEnd`. `list_sessions(game=…)` and `count(game=…)` already filter on the `game`
field.

SQLite is the real work, because its columns are fixed:

- **Add a second table, `quiz_sessions`**, with scalar columns for the envelope and counts (including `slot` and
  `round_id`), plus `responses TEXT NOT NULL` holding the enriched list as JSON. Create it with `CREATE TABLE IF NOT EXISTS`. The
  existing `sessions` table is untouched, so **no migration** is needed for existing databases.
- Why not one wide table with garden columns nullable? It would change a table that currently has real data,
  require `ALTER TABLE`, and weaken the "null means no samples" convention (null would also mean "not that game").
- `save`, `get`, `count`, `list_sessions`, `claim_unassigned` route by type or `game`. With `game=None`,
  `list_sessions` queries both tables and merges by `(endedAt, session_id)` descending, like today.
- `get(session_id)` checks both tables. `INSERT OR IGNORE` on each table's primary key still gives exactly-once
  storage per table; a cross-table UUID collision is not a practical concern.
- `responses` is stored as JSON text, not a child table: rounds are read whole, never queried per response, and the
  per-category numbers are computed in Python for a single player's rounds.

## Decision 3 — Consumers must filter by game

This is the regression that would hurt most, and it is easy to miss.

- `app/dashboard.py:202,223` and `app/report.py:272` must pass `game="reach_garden"` (or `"scam_quiz"`) to
  `list_sessions`. Never call it unfiltered and assume garden fields.
- `dashboard_model` gets a quiz twin (`quiz_summarize`, `quiz_trend`, `quiz_table_row`) rather than branches inside
  the garden functions, so garden code and tests are unchanged.
- The game selector in the plan (Phase 5) is the only place the two meet.
- `backend/sessions.py` prints `targetsCompleted/targetsAttempted watered` for every document; it needs a per-`game`
  formatter. The `--game` option already exists.
- Add a test that stores one round of each game for the same user and asserts that neither view crashes or shows the
  other game's rounds.

## Unity side

- `SessionResult.cs` gains `QuizSessionResult` and `QuizSessionResultCodec.Encode` beside the garden ones;
  `TryDecodeAck` is shared and unchanged.
- `ResultSender` gets `Submit(string sessionId, byte[] payload)`; the existing `Submit(SessionResult)` calls it. The
  retry/ack logic in `ResultDelivery` is untouched.
- `Encode` throws if the payload exceeds 1,200 bytes, as the garden codec does, so an over-long round fails loudly in
  a unit test rather than silently in play.
- The C# harness in `tests/csharp` compiles the Core sources, so the codec gets the same no-engine tests as today.

## Documents to update when this is built

`docs/udp_protocol.md` (new message, field table, budget), `docs/architecture.md` (data flow), and the README's
"two UDP messages" wording (now three types on the wire). `test_docs.py` checks links and the configuration reference,
so new settings (round length, audio) need rows in `docs/configuration.md`.

## Test plan for Phase 0 deliverables

| Test | Asserts |
| --- | --- |
| Python: `decode_quiz_session_end` valid/invalid | each invariant above rejects with `ProtocolError`; nulls round-trip |
| Python: garden decode regression | the existing `SESSION_END` tests pass unmodified |
| Python: `peek_session_id` | works for both types; malformed quiz packet gets `rejected` |
| Python: storage, all three backends | quiz save is exactly-once; mixed-game list/count/get; claim covers both |
| Python: SQLite on a pre-existing DB | opens a database created before this change and adds `quiz_sessions` without touching `sessions` |
| Python: enrichment | unknown id and stale `bankVersion` store the round with `category: null` and log a warning |
| Python: consumers | dashboard, report and `sessions` list handle a user who has both games |
| Python + C# cross-language | `tests/check_python_unity_udp.py` sends a quiz packet; C# encodes one that Python decodes |
| Size | 20-question worst case (longest ids, all answered) is `<= 1200` bytes in both languages |

## Decisions made with the author

1. **Round length.** 5 random questions per round; 20 stays as the validator's hard cap (datagram budget).
2. **Separate message type.** Accepted: `QUIZ_SESSION_END`, with `SESSION_END` untouched.
3. **Timeouts.** `selected = -1`, `responseMs = null`; no time-spent-before-timeout field.
4. **Players.** Two, matched to hands by screen zone (left half, right half of the mirrored camera frame), not by
   MediaPipe's left/right label. Each gets a coloured cursor and a result of their own. See the conversion plan for the
   per-slot `CV_STATE` (`slot` field, one packet and one `stream_id` per slot).
5. **Difficulty.** Adaptive, shared by the pair, starting at level 1.

## Spike 0a: two people on one webcam (done)

Measured on the development PC and webcam (640x480, light model), two people side by side, one hand each:

| Test | Result |
| --- | --- |
| Both hands held still in their own halves | Both tracked in 100% of frames, one in each half |
| Both waving in their own halves | 100% tracked |
| Both moving toward the centre line and back | Both tracked in 90% of frames; longest dropout 1.5 s |
| Both raising both hands (4 hands) with a limit of 2 | Both detections on the same side in 47% of frames: one player lost |

- **Frame rate was 15 fps, set by the camera.** `camera.read` took about 46 ms a frame while MediaPipe took about 20 ms,
  and the camera reports 30 fps. Earlier runs read frames in 3 to 4 ms, so this is probably the camera lengthening its
  exposure in dim light. Fifteen samples a second is still enough for a one-second dwell.
- **Two-player mode looks for 2 hands, one per person.** There is only room for one hand each, so the limit is 2 and
  players are asked to use one hand: if one person raises a second hand, MediaPipe may report it instead of the other
  person's hand (the 47% case above). I first set the limit to 4 to prevent that, on a measurement of about 1 ms extra
  per frame (19.5 ms against 20.7 ms). That measurement was taken with no hands in view and was wrong for real use: in a
  live run with two hands in view, a limit of 4 took MediaPipe from about 18 ms to 33 to 52 ms a frame and the loop from
  30 to about 22 fps, probably because MediaPipe keeps running its slower hand search on every frame while it tracks
  fewer hands than it may return.
- **Hand movement is small.** Waving covered about 17% of the picture width per person; reaching toward the centre
  covered about 36% (left person) and 24% (right person). Each half's middle part is therefore stretched to the full
  cursor range (`CONTROL_ZONE_EDGE_TRIM`) rather than the whole half.
- **Not tested:** both people using the same hand (so MediaPipe gives both the same label; here the labels happened to
  differ), hands crossing the centre line (the closest approach was about 20% of the picture width), and larger
  distances. Zones do not depend on the label, and the line margin (`CONTROL_ZONE_HYSTERESIS`) is unproven in use.

The Python side is built (`cv_engine/zones.py`, the `slot` field, one stream per player), unit-tested, and has been run
with two people on the real camera (about 26 to 27 fps with the 2-hand limit). The Unity receiver now keeps one buffer per
`slot`, and `tests/check_python_unity_udp.py` sends two players' synthetic hands over real sockets to the C# receiver.

## Still open

- **Where the shared level is persisted.** Reach Garden stores its level per machine, not per player; the quiz can do
  the same for the pair. Check `ReachGardenDifficulty.cs` when building it.
