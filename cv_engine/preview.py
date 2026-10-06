"""Local-only landmark rendering and an optional OpenCV preview window."""

from __future__ import annotations

import math
import os
import sys
from types import TracebackType

import cv2

from cv_engine.errors import CVEngineError
from cv_engine.models import ControlResult, GestureResult, HandLandmark, TrackingResult, VideoFrame


WINDOW_NAME = "MotionPlay - Hand Tracking"
# Anatomical connections: each finger plus the palm perimeter.
CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
)
FEATURE_POINTS = {
    HandLandmark.WRIST, HandLandmark.THUMB_MCP, HandLandmark.INDEX_MCP,
    HandLandmark.MIDDLE_MCP, HandLandmark.RING_MCP, HandLandmark.PINKY_MCP,
    HandLandmark.THUMB_TIP, HandLandmark.INDEX_TIP, HandLandmark.MIDDLE_TIP,
    HandLandmark.RING_TIP, HandLandmark.PINKY_TIP,
}


def draw_overlay(
    frame: VideoFrame, result: TrackingResult, loop_fps: float,
    controls: ControlResult | None = None,
    gestures: GestureResult | None = None,
    zones: int = 1,
) -> VideoFrame:
    """Draw raw landmarks on a copy, leaving the captured frame untouched.

    With ``zones`` of 2 or more, the lines between the players' zones and each player's name are drawn too."""
    canvas = frame.copy()
    height, width = canvas.shape[:2]
    for zone in range(zones if zones > 1 else 0):
        if zone:
            line_x = round(zone * (width - 1) / zones)
            cv2.line(canvas, (line_x, 0), (line_x, height - 1), (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(canvas, f"Player {zone + 1}", (round((zone + 0.5) * width / zones) - 30, height - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
    for hand in result.hands:
        if len(hand.landmarks) != 21 or not all(
            math.isfinite(value) for p in hand.landmarks for value in (p.x, p.y, p.z)
        ):
            continue
        points = [(round(p.x * (width - 1)), round(p.y * (height - 1))) for p in hand.landmarks]
        color = (110, 220, 120) if hand.hand == "right" else (240, 180, 100)
        for start, end in CONNECTIONS:
            cv2.line(canvas, points[start], points[end], color, 2, cv2.LINE_AA)
        for index, point in enumerate(points):
            cv2.circle(canvas, point, 5 if index in FEATURE_POINTS else 3, color, -1, cv2.LINE_AA)
    labels = ", ".join(f"{hand.hand} (label {hand.handedness_confidence:.2f})" for hand in result.hands)
    status = f"Hand detected: {labels}" if result.tracking else "No hand detected - show your palm"
    text_rows = [status, f"Loop FPS: {loop_fps:.1f} | MediaPipe: {result.processing_ms:.1f} ms | Q / Esc: exit"]
    if controls is not None:
        if not controls.hands:
            text_rows.append("Control: no accepted hand")
        for hand in controls.hands:
            if hand.tracking and hand.raw_position is not None and hand.position is not None:
                raw = (round(hand.raw_position.x * (width - 1)), round(hand.raw_position.y * (height - 1)))
                smooth = (round(hand.position.x * (width - 1)), round(hand.position.y * (height - 1)))
                cv2.circle(canvas, raw, 8, (255, 255, 255), 2, cv2.LINE_AA)
                cv2.drawMarker(canvas, smooth, (220, 80, 220), cv2.MARKER_CROSS, 22, 2, cv2.LINE_AA)
                text_rows.append(f"{hand.hand} control: x={hand.position.x:.3f} y={hand.position.y:.3f}")
            else:
                text_rows.append(f"{hand.hand} control: {hand.status.replace('_', ' ')}")
        text_rows.append("Palm: white circle = unfiltered | magenta cross = smoothed")
    if gestures is not None:
        for hand in gestures.hands:
            if hand.tracking:
                text_rows.append(f"{hand.hand} gesture: {hand.gesture.value} | raw: {hand.candidate.value} ({hand.consecutive_frames} frames)")
            else:
                text_rows.append(f"{hand.hand} gesture: UNKNOWN (tracking unavailable)")
    for row, text in enumerate(text_rows):
        y = 25 + row * 25
        cv2.putText(canvas, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(canvas, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return canvas


class PreviewError(CVEngineError):
    """The local preview window is unavailable."""


class Preview:
    """Own an OpenCV window without starting a PyQt6 application."""

    def __init__(self) -> None:
        self._opened = False

    def __enter__(self) -> Preview:
        if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise PreviewError("No desktop display is configured. Use --no-preview or run on your Windows desktop.")
        try:
            cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        except cv2.error as error:
            raise PreviewError("Cannot open the preview. Run on a desktop or use --no-preview.") from error
        self._opened = True
        return self

    def show(
        self, frame: VideoFrame, result: TrackingResult, loop_fps: float,
        controls: ControlResult | None = None,
        gestures: GestureResult | None = None,
        zones: int = 1,
    ) -> bool:
        """Return false when Q, Escape, or window close requests a clean stop."""
        try:
            cv2.imshow(WINDOW_NAME, draw_overlay(frame, result, loop_fps, controls, gestures, zones))
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                return False
            return cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) >= 1
        except cv2.error as error:
            raise PreviewError("Preview rendering failed. Restart on a desktop or use --no-preview.") from error

    def close(self) -> None:
        """Destroy only the window owned by MotionPlay."""
        if self._opened:
            try:
                cv2.destroyWindow(WINDOW_NAME)
            except cv2.error:
                pass  # The user may have already closed the native window.
            self._opened = False

    def __exit__(
        self, exc_type: type[BaseException] | None,
        exc: BaseException | None, traceback: TracebackType | None,
    ) -> None:
        self.close()
