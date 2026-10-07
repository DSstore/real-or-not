# MotionPlay UDP protocol

Two messages are implemented. **Python → Unity `CV_STATE`** (Phase 5; the Unity receiver is
Phase 6) streams hand state. **Unity → Python `SESSION_END`** with its `RESULT_ACK` reply
(Phase 10) delivers each finished round. The packet monitor is a diagnostic tool.

## Transport

| Direction | Destination | Default port | Status |
|---|---|---|---|
| Python CV → Unity | `UDP_HOST` | `CV_TO_UNITY_PORT=5005` | Sender and receiver implemented |
| Unity → Python backend | `127.0.0.1` | `UNITY_TO_PYTHON_PORT=5006` | `SESSION_END` and `RESULT_ACK` implemented (Phase 10) |

One UDP datagram contains one UTF-8 JSON object, without a newline, length prefix,
or fragmentation at the application layer. CV packets are limited to **1,200
bytes**. The two configured ports must differ. Python sends from an OS-assigned
ephemeral source port; it does not bind the result port. Use a literal unicast
IPv4 destination. Hostnames and IPv6 are not supported in this phase, avoiding
DNS resolution in the processing loop. Default `127.0.0.1` keeps messages on the
same computer. Configuring another address sends numerical states to that host;
frames and landmarks are never included.

UDP has no delivery, ordering, duplicate suppression, or liveness guarantee.
`sendto` success means the local OS accepted a packet, not that Unity received
it. Logs say **delivery unconfirmed**. There is no connection handshake or
application acknowledgment in Phase 5. A receiver must validate each packet,
keep only its latest accepted state, and clear control after a receive timeout.
Use **0.5 seconds** as the initial receiver timeout, measured on the receiver's
monotonic clock; clearing `tracking=false` takes effect immediately on receipt.
Python's filtering timeout cannot protect a receiver when Python stops sending.

## Implemented CV_STATE, version 1

```json
{
  "type": "CV_STATE",
  "version": 1,
  "stream_id": "a1e111a1-1111-4111-8111-111111111111",
  "sequence": 7,
  "timestamp": 1770000000123,
  "hand": "right",
  "tracking": true,
  "position": { "x": 0.55, "y": 0.31, "z": -0.03 },
  "gesture": "OPEN_HAND",
  "confidence": 0.95,
  "mirrored": true
}
```

| Field | Type | Contract |
|---|---|---|
| `type` | string | Exactly `CV_STATE` |
| `version` | integer | Exactly `1`; booleans are invalid |
| `stream_id` | string | Canonical UUID generated for a sender run |
| `sequence` | integer | 0..2^63−1, starting at zero, increasing per send attempt |
| `timestamp` | integer | 0..2^63−1, Unix UTC milliseconds at serialization |
| `hand` | string | Physical `left` or `right`, selected by `CONTROL_HAND` |
| `tracking` | boolean | Fresh accepted palm available for this selected hand |
| `position` | object or null | Filtered palm: finite x/y in [0,1], finite z |
| `gesture` | string | Debounced `OPEN_HAND`, `FIST`, `PINCH`, `POINT`, or `UNKNOWN` |
| `confidence` | number | Finite [0,1] handedness classification confidence |
| `mirrored` | boolean | Whether Python mirrored the input before inference |
| `slot` | integer, optional | Two-player mode only (`TRACKING_PLAYERS=2`): the player, `0` (left half of the picture) or `1` (right half). Left out of the packet entirely in one-player mode |

All fields except `slot` are required. X increases to the image right, Y increases downward.
Z is wrist-relative MediaPipe model depth, not meters. Confidence is confidence
in the left/right label, not landmark accuracy. Native landmarks and raw gesture
candidates are excluded. A usable palm with unusable gesture geometry still
sends `tracking=true`, its position, and `gesture=UNKNOWN`.

With `mirrored=true`, Python has already mirrored the coordinates; the default Unity Mirror Control
uses X directly. Unity converts Y downward to its own coordinate system.
Phase 7 inverts X when the packet mirror flag differs from the desired Mirror
Control setting, providing mirror-like control even for an unmirrored packet. Labels always refer to the physical hand.

### Two players on one camera

With `TRACKING_PLAYERS=2` the engine sends **two independent streams to the same port**, one per player. Each has its
own `stream_id` and `sequence` counter and carries `slot` 0 or 1, so a receiver keeps one buffer per `slot` and applies
the rules above to each separately. Hands are matched to players by where they are in the (mirrored) picture, not by
MediaPipe's left/right label, so two people can both show a right hand. In this mode `hand` is only the name of the
player's zone (`left` for slot 0, `right` for slot 1) and says nothing about which hand the person used, and `x` is
stretched so the middle of the player's half covers the whole 0 to 1 range (see `CONTROL_ZONE_EDGE_TRIM`). The Unity
receiver (`UdpStateListener`) keeps one buffer per `slot`, each with its own stream and ordering rules, so the two
players' streams never compete. A packet without a `slot` goes to player 0 and `Read()` reads player 0, so one-player
code is unchanged; two-player code reads `Read(slot)`.

Only the selected hand controls this stream. There is no automatic fallback to
the other hand, even when both are tracked. Separate filter/gesture states remain
available locally. Missing/low-confidence selected hands send:

```json
{
  "type": "CV_STATE",
  "version": 1,
  "stream_id": "a1e111a1-1111-4111-8111-111111111111",
  "sequence": 8,
  "timestamp": 1770000000157,
  "hand": "right",
  "tracking": false,
  "position": null,
  "gesture": "UNKNOWN",
  "confidence": 0.0,
  "mirrored": true
}
```

Untracked packets must use null position, UNKNOWN gesture, and zero confidence.
They are also sent before the first detection. No stale coordinates are reused.

### Pacing, shutdown, and receiver responsibilities

The sender uses monotonic deadlines and a configurable cadence
(`UDP_SEND_FPS=30`). At most one fresh packet is attempted per due processing
frame. Excess frames are skipped, missed slots are discarded, and states are
never queued or replayed. The cadence stays anchored to avoid halving throughput
when a webcam runs just above 30 FPS. Actual throughput depends on webcam and
model processing speed; this is a target cadence, not a per-packet minimum
spacing guarantee. There is no background heartbeat while camera/inference
blocks. A clean exit attempts one final lost state, bypassing pacing once;
delivery is not guaranteed, so the receiver timeout is essential.

Use `sequence` to ignore duplicate/older packets in an active `stream_id`.
Missing numbers can mean OS send errors or network loss. Skipped processing
frames do not consume sequence numbers. A new run has a new stream ID and starts
at zero. The Phase 6 receiver adopts a new stream ID after the prior stream has
timed out and retires the old ID, so late packets from a retired run cannot
reactivate it. Retirement is bounded at 128 IDs per listener lifetime;
disable/re-enable to reset after that limit. This is state streaming,
not gesture event delivery; a held gesture appears repeatedly. Games must decide
whether an action uses a gesture edge or a continuously held state.

Wall-clock timestamps can jump and clocks on different computers can differ.
Use sequence numbers for ordering, and local monotonic time for receive timeout.
Timestamps alone do not measure UDP or Unity update latency; measuring them would need
clock-aware instrumentation that is not implemented.

`shared.protocol.decode_cv_state` rejects oversized packets, invalid UTF-8/JSON,
duplicate keys, missing fields, unsupported versions/types, invalid enums,
booleans in numeric fields, nonfinite coordinates, and inconsistent lost states.
Unknown extra fields are ignored to allow additive extensions. A breaking change
requires a new protocol version. UDP JSON is unauthenticated; the local monitor
binds only to loopback.

## Phase 6 receiver implementation

`unity/MotionPlay/Assets/MotionPlay/Core` validates CV packets on a socket worker, keeps only the latest state, ignores duplicate/older sequence numbers without refreshing the deadline, and retires timed-out streams when a new one takes over. It binds exclusively to **127.0.0.1**. Unity's main-thread `UdpReceiver.Update` observes fresh immutable snapshots; timeout produces a null current state. The receiver test scene renders numerical states; Phase 7 adds a separate hand cursor scene consuming these same fresh snapshots. The parser caps nesting at 16 levels and rejects comments, unquoted keys, single quotes, and nonfinite numbers. See the [Unity setup guide](phase6_unity_receiver.md).

## Phase 7 cursor consumption

`MotionPlay.Control.CursorMapper` maps fresh tracked snapshots to an orthographic camera plane, with upward Y and a horizontal flip only when desired and packet mirror flags differ. Model Z does not control cursor depth. A lost or expired state yields no mapped point. `HandCursorController` applies that point on Unity's main thread and hides its renderer when no current position exists. The disc radius plus an edge margin keeps it within the camera view. No wire fields or version changed. See the [cursor proof-of-concept guide](phase7_hand_cursor.md).

## Unity → Python result messages (Phase 10)

Unity sends one `SESSION_END` datagram when a round completes; the Python result
receiver replies to the sender's address with a `RESULT_ACK`. The same limits as
`CV_STATE` apply: UTF-8 JSON, one object, at most 1,200 bytes, no duplicate keys,
no `NaN`/`Infinity`. Unity's `stream_id` and `sequence` belong to its own sender,
independently of the CV stream. Every field below is required; metrics with no
samples are sent as explicit `null`, never zero.

| Field | Type | Rule |
|---|---|---|
| `type`, `version` | string, int | `"SESSION_END"`, `1` |
| `stream_id`, `session_id` | string | Canonical UUIDs; `session_id` identifies the round and makes repeats idempotent |
| `sequence`, `timestamp` | int | Nonnegative; timestamp is UTC milliseconds |
| `game`, `difficulty` | string | 1 to 64 / 1 to 32 characters (`reach_garden`; `level_1` to `level_5`, or `default` for rounds saved before Phase 15) |
| `hand` | string | `left` or `right`, the hand that played |
| `startedAt`, `endedAt` | int | UTC milliseconds, `endedAt` not before `startedAt` |
| `duration` | number | Seconds, at least 0 |
| `score`, `targetsAttempted`, `targetsCompleted`, `currentStreak`, `bestStreak` | int | At least 0; completed and score at most attempted; `currentStreak` ≤ `bestStreak` ≤ completed |
| `accuracy`, `averageHoldStability`, `pathEfficiency` | number or null | In [0, 1] |
| `averageReactionTime`, `averageMovementTime` | number or null | Seconds, at least 0 |

Metric definitions are in [Phase 9](phase9_session_stats.md). `GAME_STATE` and
`SESSION_UPDATE` from the earlier plan are not implemented; only final results are sent.

```json
{
  "type": "SESSION_END",
  "version": 1,
  "stream_id": "b2e222b2-2222-4222-8222-222222222222",
  "sequence": 0,
  "timestamp": 1770000060000,
  "session_id": "c3e333c3-3333-4333-8333-333333333333",
  "game": "reach_garden",
  "hand": "right",
  "difficulty": "default",
  "startedAt": 1770000000000,
  "endedAt": 1770000060000,
  "duration": 60.0,
  "score": 6,
  "targetsAttempted": 8,
  "targetsCompleted": 6,
  "currentStreak": 2,
  "bestStreak": 4,
  "accuracy": 0.75,
  "averageReactionTime": 0.42,
  "averageMovementTime": 1.1,
  "averageHoldStability": 0.9,
  "pathEfficiency": null
}
```

### Scam quiz results

The scam quiz sends **one `QUIZ_SESSION_END` per player** when a round ends, so a two-player round is two datagrams
that share a `roundId`. It uses the same transport rules, the same `RESULT_ACK` reply and the same retry behaviour as
`SESSION_END`; the receiver tells the two apart by `type`. Every field is required, and unknown extra fields are ignored.

| Field | Type | Rule |
|---|---|---|
| `type`, `version` | string, int | `"QUIZ_SESSION_END"`, `1` |
| `stream_id`, `session_id`, `roundId` | string | Canonical UUIDs. `session_id` is this player's result (repeats are harmless); `roundId` is shared by the players of one round |
| `sequence`, `timestamp` | int | Nonnegative; timestamp is UTC milliseconds |
| `game` | string | `scam_quiz` |
| `difficulty` | string | `level_1` to `level_5`, the shared level the round was played at |
| `hand` | string | `left` or `right`. In a two-player game this is the player's zone, not necessarily the hand they used |
| `slot` | int | The player, `0` or `1` |
| `startedAt`, `endedAt`, `duration` | int, int, number | UTC milliseconds, `endedAt` not before `startedAt`; seconds, at least 0 |
| `bankVersion` | int | At least 1: the `version` of the question bank the round was played with |
| `totalQuestions` | int | 1 to 20, and equal to the number of `responses` |
| `correct`, `wrong`, `skipped`, `bestStreak` | int | `correct + wrong + skipped = totalQuestions`; `skipped` is the number of timeouts in `responses`; `correct + wrong` the number of answers; `bestStreak` ≤ `correct` |
| `averageResponseTime`, `fastestResponse`, `slowestResponse` | number or null | Seconds of tracked time. `null` exactly when nothing was answered, never zero; otherwise they must match the response log to within 2 ms |
| `responses` | list | One `[question_id, selected, response_ms]` per question, in the order asked |

In `responses`, `question_id` is the bank's id (lowercase letters, digits, `-` and `_`, up to 64 characters, no repeats),
`selected` is the index into the question's **written** choices (0 to 3, not the position on screen) or `-1` if time
ran out, and `response_ms` is whole milliseconds of tracked time, `null` exactly when `selected` is `-1`.

The category, difficulty and right answer of each question are **not sent**. The receiver looks them up in the question
bank (`data/questions.json`, or `--questions`) and stores them with each response, so a stored round is self-contained.
Which answers were right is only known with the bank, so a mismatch between `correct` and the answers is logged as a
warning, not rejected. A question the bank does not know, or a round played with a different `bankVersion`, is still
stored (a known id is trusted, since ids are never reused), with empty details for the unknown ones and a warning.

```json
{"type":"QUIZ_SESSION_END","version":1,"stream_id":"b2e222b2-2222-4222-8222-222222222222","sequence":0,
 "timestamp":1770000091000,"session_id":"e5e555e5-5555-4555-8555-555555555555","game":"scam_quiz","hand":"left",
 "difficulty":"level_2","startedAt":1770000000000,"endedAt":1770000090000,"duration":90.0,
 "roundId":"d4e444d4-4444-4444-8444-444444444444","slot":0,"bankVersion":2,"totalQuestions":5,"correct":3,
 "wrong":1,"skipped":1,"bestStreak":2,"averageResponseTime":5.7,"fastestResponse":3.0,"slowestResponse":9.1,
 "responses":[["sms-01",1,4200],["phone-01",2,9100],["otp-01",-1,null],["social-01",3,3000],["love-01",0,6500]]}
```

A five-question round is about 700 bytes and a twenty-question round about 1,000, both under the 1,200 limit.
Run the receiver for two players with `python -m backend.result_receiver --store sqlite --user NAME --user2 NAME`:
player 1 (`slot` 0) is saved to `--user`, player 2 (`slot` 1) to `--user2`, and a player without an account is saved
without an owner (claimable later with `backend.sessions claim`).

The reply is `{"type":"RESULT_ACK","version":1,"session_id":"…","status":"…"}`:

| Status | Meaning | Unity |
|---|---|---|
| `stored` | Saved now | Stop retrying, show "Result saved" |
| `duplicate` | This `session_id` was already saved | Stop retrying, show "Result saved" |
| `rejected` | The packet failed validation | Stop retrying, show "Result rejected" |
| `error` | Valid, but storage failed | Keep retrying |

UDP itself guarantees nothing, so delivery is confirmed only by an acknowledgement.
Unity sends up to 5 times, 1 second apart. With no acknowledgement it reports
"Result NOT saved" and does not pretend otherwise; a round that ended while the
receiver was off is not kept for later.
