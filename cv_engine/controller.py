"""Run hand tracking, palm/gesture processing, and Phase 5 UDP transmission."""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import ExitStack
from dataclasses import replace
from time import perf_counter

from shared.config import ConfigurationError, Settings, load_settings
from shared.logger import start_logging


LOGGER = logging.getLogger("motionplay.cv_engine.controller")


def run_tracking(
    settings: Settings, show_preview: bool = True, max_frames: int | None = None,
    send_udp: bool = True,
) -> int:
    """Run until user exit or the frame limit; always release opened resources.

    Native imports are deferred so --help does not initialize models or devices.
    Return the processed frame count for diagnostics and tests.
    """
    import cv2

    from cv_engine.camera import Camera
    from cv_engine.continuity import LabelContinuity
    from cv_engine.hand_tracker import HandTracker
    from cv_engine.keys import stop_requested
    from cv_engine.gesture_processor import GestureProcessor
    from cv_engine.position_processor import PositionProcessor
    from cv_engine.preview import Preview
    from cv_engine.udp_sender import UdpSender
    from cv_engine.zones import DETECTION_LIMIT, ZONE_LABELS, ZoneAssigner

    if max_frames is not None and max_frames < 1:
        raise ValueError("max_frames must be positive.")
    players = settings.tracking.players
    # Two players: match hands to people by screen zone and send one stream per player. One player: unchanged.
    zones = ZoneAssigner(players, settings.control) if players > 1 else None
    tracking_settings = settings.tracking if zones is None else replace(
        settings.tracking, max_hands=max(settings.tracking.max_hands, DETECTION_LIMIT))
    with ExitStack() as stack:
        # Check the optional desktop preview before accessing the camera.
        preview = stack.enter_context(Preview()) if show_preview else None
        camera = stack.enter_context(Camera(settings.camera))
        tracker = stack.enter_context(HandTracker(tracking_settings, settings.camera.mirror))
        senders = []
        if send_udp:
            for slot in range(players if zones is not None else 1):
                sender_settings = settings.sender if zones is None else replace(settings.sender, hand=ZONE_LABELS[slot])
                senders.append(stack.enter_context(UdpSender(
                    settings.udp_host, settings.cv_to_unity_port, sender_settings, settings.camera.mirror,
                    slot=slot if zones is not None else None,
                )))
        continuity = LabelContinuity(settings.control)
        processor = PositionProcessor(settings.control)
        gesture_processor = GestureProcessor(settings.gestures, settings.control)
        LOGGER.info("Tracking started. Frames stay local; UDP numerical states %s.",
                    "enabled" if senders else "disabled")
        LOGGER.info("Settings: camera %d requested %dx%d mirror=%s; model complexity %d; max hands %d; "
                    "control hand %s; label continuity %.2f s within %.2f.", settings.camera.index,
                    settings.camera.width, settings.camera.height, settings.camera.mirror,
                    settings.tracking.model_complexity, tracking_settings.max_hands, settings.sender.hand,
                    settings.control.continuity_seconds, settings.control.continuity_radius)
        if zones is not None:
            LOGGER.info("Two players: player 1 is the left half of the picture, player 2 the right half. Hands are "
                        "matched by zone, not by left/right label; the label-continuity fix is not used.")
        if preview is None:
            LOGGER.info("No preview window: press Q or Esc in this console (or Ctrl+C) to stop.")
        started_at = last_report_at = perf_counter()
        previous_tracking = False
        frame_count = 0
        lost_events = 0
        try:
            while max_frames is None or frame_count < max_frames:
                frame = camera.read()
                if settings.camera.mirror:
                    frame = cv2.flip(frame, 1)
                result = tracker.process(frame)
                frame_count += 1
                now = perf_counter()
                result = continuity.apply(result, now) if zones is None else zones.assign(result, now)
                controls = processor.update(result, now)
                gestures = gesture_processor.update(result, controls, now, frame.shape[1] / frame.shape[0])
                # Each stream picks its own player's hand out of the same controls (see UdpSender).
                outgoing = controls if zones is None else zones.output_controls(controls)
                for sender in senders:
                    sender.send(outgoing, gestures, now)
                loop_fps = frame_count / max(now - started_at, 1e-9)
                if controls.tracking != previous_tracking:
                    LOGGER.info("Hand tracking restored." if controls.tracking else "Hand tracking lost.")
                    lost_events += not controls.tracking
                    previous_tracking = controls.tracking
                if now - last_report_at >= 5:
                    LOGGER.info("Loop FPS %.1f; latest MediaPipe processing %.1f ms; label corrections so far: %d.",
                                loop_fps, result.processing_ms, continuity.corrections)
                    for sender in senders:
                        LOGGER.info("UDP totals%s: %d accepted, %d failed, %d frames skipped; delivery unconfirmed.",
                                    "" if sender.slot is None else f" (player {sender.slot + 1})",
                                    sender.sent, sender.failed, sender.skipped)
                    last_report_at = now
                if preview is not None:
                    extra = {} if zones is None else {"zones": players}
                    if not preview.show(frame, result, loop_fps, controls, gestures, **extra):
                        break
                if preview is None and stop_requested():
                    LOGGER.info("Stop key pressed.")
                    break
        finally:  # Also runs on Ctrl+C or an error, so the run summary is never lost.
            elapsed = perf_counter() - started_at
            LOGGER.info("Tracking finished after %d frame(s) in %.1f s (average %.1f FPS); hand lost %d time(s); "
                        "%d label correction(s).", frame_count, elapsed, frame_count / max(elapsed, 1e-9),
                        lost_events, continuity.corrections)
        return frame_count


def _positive_integer(value: str) -> int:
    """Validate the CLI frame limit before any resource is opened."""
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def main(argv: list[str] | None = None) -> int:
    """CLI entry point with actionable failures and controlled exit codes."""
    parser = argparse.ArgumentParser(description="MotionPlay Phase 5: hand tracking, gestures, and UDP states")
    parser.add_argument("--no-preview", action="store_true", help="Process frames without opening a window (still requires a webcam)")
    parser.add_argument("--max-frames", type=_positive_integer, help="Stop after this many processed frames")
    parser.add_argument("--no-udp", action="store_true", help="Preview/process locally without sending UDP states")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        start_logging(settings, "CV engine")
    except (ConfigurationError, OSError) as error:
        print(f"MotionPlay configuration/logging error: {error}", file=sys.stderr)
        return 1

    from cv_engine.errors import CVEngineError

    try:
        run_tracking(settings, show_preview=not args.no_preview, max_frames=args.max_frames,
                     send_udp=not args.no_udp)
    except KeyboardInterrupt:
        LOGGER.info("Tracking stopped by user.")
    except CVEngineError as error:
        LOGGER.error("%s", error)
        LOGGER.debug("CV failure details", exc_info=True)
        return 1
    except (ImportError, OSError):
        LOGGER.exception("A dependency or native library is unavailable. Run python -m app.health_check in the Python 3.11 environment.")
        return 1
    except Exception:
        LOGGER.exception("Unexpected tracking error. Resources were released; see logs/motionplay.log.")
        return 1
    finally:
        LOGGER.info("MotionPlay CV engine stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
