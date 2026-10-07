# Configuration reference

Every setting is a key in `.env` (copy `.env.example` to create it; `MotionPlay-Setup.cmd` does this for you). A key you leave out uses its
default. Settings load in this order, later ones winning: built-in defaults, the project's `.env`, then process environment variables. The loader
never changes your environment, and `$` in a value is kept literally (so passwords in a MongoDB URI are safe). An invalid value stops the
program with a message naming the setting, before any camera, socket or database is opened.

Relative paths resolve from the project folder, not from where you run the command.

## Who reads what

The Python programs read every key except `UNITY_RECEIVE_TIMEOUT`. The Unity project reads only `CV_TO_UNITY_PORT`, `UNITY_TO_PYTHON_PORT` and
`UNITY_RECEIVE_TIMEOUT`, from the same `.env` file, so the two sides always agree on ports. Database credentials are never read by Unity.

## Logging

| Setting | Default | Valid values | Meaning |
|---|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | Detail written to the log. `DEBUG` also records each label correction. |
| `LOG_DIR` | `logs` | a folder path | Where `motionplay.log` goes (2 MiB per file, three backups). |

## Camera

| Setting | Default | Valid values | Meaning |
|---|---|---|---|
| `CAMERA_INDEX` | `0` | 0 to 32 | Which camera. Try `1` for a second one. |
| `CAMERA_WIDTH` | `640` | 160 to 7680 | Requested width in pixels. The camera may negotiate another size. |
| `CAMERA_HEIGHT` | `480` | 120 to 4320 | Requested height in pixels. |
| `CAMERA_FPS` | `30` | 1 to 120 | Requested capture rate. |
| `CAMERA_MIRROR` | `true` | `true`, `false`, `1`, `0` | Mirror the image before tracking and preview, so movement feels like a mirror. Left/right labels stay correct either way. |
| `CAMERA_BACKEND` | `auto` | `auto`, `dshow`, `msmf`, `default` | How OpenCV opens the camera. `auto` uses DirectShow on Windows (about 3 s to open, against 6 to 29 s for Media Foundation on the test machine) and falls back to OpenCV's default if DirectShow fails. See [Phase 2](phase2_hand_tracking.md). |

## Hand tracking (MediaPipe)

| Setting | Default | Valid values | Meaning |
|---|---|---|---|
| `TRACKING_MAX_HANDS` | `1` | 1 or 2 | Hands MediaPipe looks for. |
| `TRACKING_MODEL_COMPLEXITY` | `1` | 0 or 1 | `0` is the lighter model: about twice as fast (29 against 17 FPS headless on the test machine) with similar stability. `1` is the full model. |
| `TRACKING_DETECTION_CONFIDENCE` | `0.6` | 0 to 1 | MediaPipe's threshold for detecting a new hand. |
| `TRACKING_MIN_CONFIDENCE` | `0.6` | 0 to 1 | MediaPipe's threshold for continuing to track a hand. |
| `TRACKING_PLAYERS` | `1` | 1 or 2 | `1` is the original one-player behaviour. `2` lets two people share the camera: the left half of the picture is player 1 and the right half is player 2, each with their own hand, smoothing, gestures and `CV_STATE` packets (a `slot` field, `0` or `1`). In this mode the engine looks for at most 2 hands (one per person), keeps the most confident hand in each half, and does not use the label-flicker fix. Each person should use one hand: if one person raises a second hand, MediaPipe may report it instead of the other person's hand. `CONTROL_HAND` is ignored. |

## Palm control filtering

Coordinates here are normalized image coordinates (0 to 1).

| Setting | Default | Valid values | Meaning |
|---|---|---|---|
| `SMOOTHING_ALPHA` | `0.35` | above 0, up to 1 | Exponential smoothing: lower is smoother and slower to respond. |
| `SMOOTHING_DEAD_ZONE` | `0.008` | 0 to 1 | Movements smaller than this are ignored, to stop jitter. `0` turns it off. |
| `CONTROL_MIN_HANDEDNESS_CONFIDENCE` | `0.75` | 0 to 1 | Minimum confidence in the left/right label for a hand to control the cursor. This is about the label, not landmark accuracy. |
| `CONTROL_TRACKING_TIMEOUT` | `0.5` | above 0 (seconds) | Gap after which filter history is discarded. |
| `CONTROL_CONTINUITY_SECONDS` | `0.3` | 0 to 1 (seconds) | A hand detected where a tracked hand was this recently keeps that hand's label even if MediaPipe flips it or reports low confidence. `0` turns the correction off. See [Phase 3](phase3_coordinates.md). |
| `CONTROL_CONTINUITY_RADIUS` | `0.25` | above 0, up to 1 | Largest palm movement between frames still treated as the same hand. |
| `CONTROL_ZONE_HYSTERESIS` | `0.04` | 0 to 0.25 | Two players only. A hand this close to the line between the two halves (as a share of the picture width) stays with the player whose hand was just there, so it does not flip players at the line. `0` turns it off. |
| `CONTROL_ZONE_EDGE_TRIM` | `0.15` | 0 up to, not including, 0.5 | Two players only. The share of each half's width, at each edge, that a hand does not have to reach. The middle part is stretched to the full cursor range, because people move their hands over a small range (measured in the two-person test: about 17% of the picture width when waving). |
| `CONTROL_HAND` | `right` | `left`, `right` | The physical hand that controls the cursor. It never switches on its own. |

## Gesture rules

Geometric rules, not a learned model. A gesture is confirmed only after several consecutive matching frames. See [Phase 4](phase4_gestures.md).

| Setting | Default | Valid values | Meaning |
|---|---|---|---|
| `GESTURE_DEBOUNCE_FRAMES` | `5` | 1 to 60 | Consecutive frames needed to confirm a change. |
| `GESTURE_PINCH_RATIO` | `0.3` | above 0, up to 1 | Thumb to index tip distance divided by palm size, below which it counts as a pinch. |
| `GESTURE_EXTENDED_ANGLE` | `160` | degrees, up to 180 | A joint angle above this counts as extended (180 is perfectly straight). |
| `GESTURE_CURLED_ANGLE` | `105` | degrees, above 0 | A joint angle below this counts as curled. Must be below the extended angle. |
| `GESTURE_REACH_RATIO` | `1.2` | above 1 | Fingertip distance from the wrist relative to the finger base. |

## Networking

| Setting | Default | Valid values | Meaning |
|---|---|---|---|
| `UDP_HOST` | `127.0.0.1` | a literal unicast IPv4 address | Where hand states are sent. The default keeps them on this computer. A hostname or IPv6 address is refused so no DNS lookup can stall the camera loop. |
| `CV_TO_UNITY_PORT` | `5005` | 1024 to 65535 | Port Unity listens on for hand states. |
| `UNITY_TO_PYTHON_PORT` | `5006` | 1024 to 65535 | Port the Python result receiver listens on for finished rounds. Must differ from the one above. |
| `UDP_SEND_FPS` | `30` | 1 to 120 | Most hand states sent per second; also limited by camera speed. |
| `UNITY_RECEIVE_TIMEOUT` | `0.5` | above 0 (seconds) | Unity only. With no packet for this long, Unity treats the hand as lost. |

## Database

| Setting | Default | Valid values | Meaning |
|---|---|---|---|
| `MONGODB_URI` | `mongodb://127.0.0.1:27017` | must start with `mongodb://` or `mongodb+srv://` | Used only with `--store mongo`. If it holds credentials, keep it in `.env` and never commit it; it is hidden from logs and from the settings' printed form. |
| `MONGODB_DATABASE` | `motionplay` | a name | The MongoDB database for rounds. |

## Not in `.env`

- **Reach Garden difficulty** is not configured here. The level (1 to 5) is chosen by the adaptive rules, changed with `[` and `]` in Unity, and
  remembered by Unity itself. See [Phase 15](phase15_adaptive_difficulty.md).
- **Where results are stored** is chosen per command: `--store jsonl` (default), `sqlite` or `mongo`, with `--file` for the file's location.
  See [Phase 11](phase11_storage.md).
- **Who is playing** is chosen per command with `--user NAME`.
