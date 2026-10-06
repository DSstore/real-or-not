"""Load validated configuration without starting application services."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from ipaddress import AddressValueError, IPv4Address
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigurationError(ValueError):
    """A configuration setting is missing or invalid."""


CAMERA_BACKENDS = ("auto", "dshow", "msmf", "default")


@dataclass(frozen=True)
class CameraSettings:
    """Requested capture settings; a device may negotiate different dimensions/FPS."""

    index: int = 0
    width: int = 640
    height: int = 480
    fps: int = 30
    mirror: bool = True
    # How OpenCV talks to the camera. auto = DirectShow on Windows (its default Media Foundation backend can take
    # several seconds to open some webcams), with a fallback to OpenCV's default if DirectShow cannot open it.
    backend: str = "auto"

    def __post_init__(self) -> None:
        if self.backend not in CAMERA_BACKENDS:
            raise ConfigurationError("CAMERA_BACKEND must be auto, dshow, msmf, or default.")


@dataclass(frozen=True)
class TrackingSettings:
    """MediaPipe detection/tracking thresholds, not handedness confidence gates."""

    max_hands: int = 1
    model_complexity: int = 1
    detection_confidence: float = 0.6
    tracking_confidence: float = 0.6
    # 1 = one player, the original behaviour. 2 = two players sharing the camera, matched to hands by screen
    # zone (left half, right half) instead of by MediaPipe's left/right label. See cv_engine.zones.
    players: int = 1

    def __post_init__(self) -> None:
        if type(self.players) is not int or not 1 <= self.players <= 2:
            raise ConfigurationError("TRACKING_PLAYERS must be an integer from 1 to 2.")


@dataclass(frozen=True)
class ControlSettings:
    """Palm filtering settings; distances are in normalized image coordinates."""

    smoothing_alpha: float = 0.35
    dead_zone: float = 0.008
    min_handedness_confidence: float = 0.75
    tracking_timeout: float = 0.5
    # A hand that clearly continues one tracked within this many seconds keeps that hand's label even if
    # MediaPipe briefly flips it or reports low confidence. 0 disables the correction.
    continuity_seconds: float = 0.3
    # How far (normalized image distance) the palm may move between frames and still count as the same hand.
    continuity_radius: float = 0.25
    # Two-player zones only. A hand within this distance (normalized image width) of the line between two
    # zones stays with the player whose hand was just there, so it does not flip players at the line.
    zone_hysteresis: float = 0.04
    # Two-player zones only. Share of each zone's width, at each edge, that a hand does not need to reach: the
    # remaining middle is stretched to the full 0..1 range, because people move their hands over a small range.
    zone_edge_trim: float = 0.15

    def __post_init__(self) -> None:
        checks = (
            ("CONTROL_ZONE_HYSTERESIS", self.zone_hysteresis, 0 <= self.zone_hysteresis <= 0.25),
            ("CONTROL_ZONE_EDGE_TRIM", self.zone_edge_trim, 0 <= self.zone_edge_trim < 0.5),
            ("CONTROL_CONTINUITY_SECONDS", self.continuity_seconds, 0 <= self.continuity_seconds <= 1),
            ("CONTROL_CONTINUITY_RADIUS", self.continuity_radius, 0 < self.continuity_radius <= 1),
            ("SMOOTHING_ALPHA", self.smoothing_alpha, 0 < self.smoothing_alpha <= 1),
            ("SMOOTHING_DEAD_ZONE", self.dead_zone, 0 <= self.dead_zone <= 1),
            ("CONTROL_MIN_HANDEDNESS_CONFIDENCE", self.min_handedness_confidence,
             0 <= self.min_handedness_confidence <= 1),
            ("CONTROL_TRACKING_TIMEOUT", self.tracking_timeout, self.tracking_timeout > 0),
        )
        for name, value, valid in checks:
            if not math.isfinite(value) or not valid:
                raise ConfigurationError(f"{name} is out of range; see .env.example.")


@dataclass(frozen=True)
class GestureSettings:
    """Geometry thresholds and the consecutive-frame confirmation requirement."""

    debounce_frames: int = 5
    pinch_ratio: float = 0.3
    extended_angle: float = 160.0
    curled_angle: float = 105.0
    reach_ratio: float = 1.2

    def __post_init__(self) -> None:
        if type(self.debounce_frames) is not int or not 1 <= self.debounce_frames <= 60:
            raise ConfigurationError("GESTURE_DEBOUNCE_FRAMES must be an integer from 1 to 60.")
        if not math.isfinite(self.pinch_ratio) or not 0 < self.pinch_ratio <= 1:
            raise ConfigurationError("GESTURE_PINCH_RATIO must be finite and in (0, 1].")
        finite_angles = all(math.isfinite(value) for value in (self.curled_angle, self.extended_angle))
        if not finite_angles or not 0 < self.curled_angle < self.extended_angle <= 180:
            raise ConfigurationError(
                "GESTURE_CURLED_ANGLE and GESTURE_EXTENDED_ANGLE must satisfy "
                "0 < curled < extended <= 180 degrees."
            )
        if not math.isfinite(self.reach_ratio) or self.reach_ratio <= 1:
            raise ConfigurationError("GESTURE_REACH_RATIO must be finite and greater than 1.")


@dataclass(frozen=True)
class SenderSettings:
    """Fresh-state send limit and explicit physical hand selection."""

    fps: int = 30
    hand: str = "right"

    def __post_init__(self) -> None:
        if type(self.fps) is not int or not 1 <= self.fps <= 120:
            raise ConfigurationError("UDP_SEND_FPS must be an integer from 1 to 120.")
        if self.hand not in {"left", "right"}:
            raise ConfigurationError("CONTROL_HAND must be left or right.")


def validate_udp_host(host: str) -> str:
    """Use a literal unicast IPv4 address to avoid DNS work in the CV loop."""
    try:
        address = IPv4Address(host)
    except AddressValueError:
        raise ConfigurationError("UDP_HOST must be an IPv4 address, e.g. 127.0.0.1.") from None
    if address.is_unspecified or address.is_multicast or str(address) == "255.255.255.255":
        raise ConfigurationError("UDP_HOST must be a unicast IPv4 address.")
    return str(address)


@dataclass(frozen=True)
class Settings:
    """Validated application settings; connection URIs stay out of repr()."""

    log_level: str
    log_dir: Path
    udp_host: str
    cv_to_unity_port: int
    unity_to_python_port: int
    mongodb_uri: str = field(repr=False)
    mongodb_database: str
    camera: CameraSettings = field(default_factory=CameraSettings)
    tracking: TrackingSettings = field(default_factory=TrackingSettings)
    control: ControlSettings = field(default_factory=ControlSettings)
    gestures: GestureSettings = field(default_factory=GestureSettings)
    sender: SenderSettings = field(default_factory=SenderSettings)


def _value(values: Mapping[str, str | None], name: str, default: str) -> str:
    """Read a non-empty setting without exposing its value in errors."""
    value = values.get(name, default)
    if value is None or not value.strip():
        raise ConfigurationError(f"{name} must not be empty.")
    return value.strip()


def _port(values: Mapping[str, str | None], name: str, default: str) -> int:
    """Validate an unprivileged UDP port."""
    try:
        port = int(_value(values, name, default))
    except ValueError:
        raise ConfigurationError(f"{name} must be an integer from 1024 to 65535.") from None
    if not 1024 <= port <= 65535:
        raise ConfigurationError(f"{name} must be an integer from 1024 to 65535.")
    return port


def _integer(
    values: Mapping[str, str | None], name: str, default: int, low: int, high: int
) -> int:
    """Read a bounded integer setting without exposing its supplied value."""
    try:
        result = int(_value(values, name, str(default)))
    except ValueError:
        raise ConfigurationError(f"{name} must be an integer from {low} to {high}.") from None
    if not low <= result <= high:
        raise ConfigurationError(f"{name} must be an integer from {low} to {high}.")
    return result


def _confidence(values: Mapping[str, str | None], name: str, default: float) -> float:
    """Reject nonfinite and out-of-range MediaPipe probability thresholds."""
    try:
        result = float(_value(values, name, str(default)))
    except ValueError:
        raise ConfigurationError(f"{name} must be a finite number from 0.0 to 1.0.") from None
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ConfigurationError(f"{name} must be a finite number from 0.0 to 1.0.")
    return result


def _boolean(values: Mapping[str, str | None], name: str, default: bool) -> bool:
    """Read explicit true/false values, accepting 1/0 for environment overrides."""
    value = _value(values, name, str(default)).lower()
    if value not in {"true", "false", "1", "0"}:
        raise ConfigurationError(f"{name} must be true, false, 1, or 0.")
    return value in {"true", "1"}


def _positive_float(values: Mapping[str, str | None], name: str, default: float) -> float:
    """Read a finite, strictly positive duration."""
    try:
        result = float(_value(values, name, str(default)))
    except ValueError:
        raise ConfigurationError(f"{name} must be a finite positive number.") from None
    if not math.isfinite(result) or result <= 0:
        raise ConfigurationError(f"{name} must be a finite positive number.")
    return result


def load_settings(
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Load defaults, then .env, then environment overrides without mutating os.environ.

    Paths are relative to the project root, regardless of the working directory.
    Passing an explicit environment mapping allows deterministic tests. Variable
    interpolation is disabled so credentials containing '$' remain literal.
    """
    path = env_file if env_file is not None else PROJECT_ROOT / ".env"
    try:
        values: dict[str, str | None] = dict(dotenv_values(path, interpolate=False))
    except (OSError, UnicodeError):
        raise ConfigurationError("Cannot read the .env file; check its access and UTF-8 encoding.") from None
    values.update(os.environ if environ is None else environ)

    log_level = _value(values, "LOG_LEVEL", "INFO").upper()
    if log_level not in LOG_LEVELS:
        raise ConfigurationError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL.")
    log_dir = Path(_value(values, "LOG_DIR", "logs")).expanduser()
    if not log_dir.is_absolute():
        log_dir = PROJECT_ROOT / log_dir

    cv_port = _port(values, "CV_TO_UNITY_PORT", "5005")
    result_port = _port(values, "UNITY_TO_PYTHON_PORT", "5006")
    if cv_port == result_port:
        raise ConfigurationError("CV_TO_UNITY_PORT and UNITY_TO_PYTHON_PORT must differ.")

    mongodb_uri = _value(values, "MONGODB_URI", "mongodb://127.0.0.1:27017")
    if not mongodb_uri.startswith(("mongodb://", "mongodb+srv://")):
        raise ConfigurationError("MONGODB_URI must use mongodb:// or mongodb+srv://.")

    return Settings(
        log_level=log_level,
        log_dir=log_dir.resolve(),
        udp_host=validate_udp_host(_value(values, "UDP_HOST", "127.0.0.1")),
        cv_to_unity_port=cv_port,
        unity_to_python_port=result_port,
        mongodb_uri=mongodb_uri,
        mongodb_database=_value(values, "MONGODB_DATABASE", "motionplay"),
        camera=CameraSettings(
            index=_integer(values, "CAMERA_INDEX", 0, 0, 32),
            width=_integer(values, "CAMERA_WIDTH", 640, 160, 7680),
            height=_integer(values, "CAMERA_HEIGHT", 480, 120, 4320),
            fps=_integer(values, "CAMERA_FPS", 30, 1, 120),
            mirror=_boolean(values, "CAMERA_MIRROR", True),
            backend=_value(values, "CAMERA_BACKEND", "auto").lower(),
        ),
        tracking=TrackingSettings(
            max_hands=_integer(values, "TRACKING_MAX_HANDS", 1, 1, 2),
            model_complexity=_integer(values, "TRACKING_MODEL_COMPLEXITY", 1, 0, 1),
            detection_confidence=_confidence(values, "TRACKING_DETECTION_CONFIDENCE", 0.6),
            tracking_confidence=_confidence(values, "TRACKING_MIN_CONFIDENCE", 0.6),
            players=_integer(values, "TRACKING_PLAYERS", 1, 1, 2),
        ),
        control=ControlSettings(
            smoothing_alpha=_confidence(values, "SMOOTHING_ALPHA", 0.35),
            dead_zone=_confidence(values, "SMOOTHING_DEAD_ZONE", 0.008),
            min_handedness_confidence=_confidence(values, "CONTROL_MIN_HANDEDNESS_CONFIDENCE", 0.75),
            tracking_timeout=_positive_float(values, "CONTROL_TRACKING_TIMEOUT", 0.5),
            continuity_seconds=_confidence(values, "CONTROL_CONTINUITY_SECONDS", 0.3),
            continuity_radius=_confidence(values, "CONTROL_CONTINUITY_RADIUS", 0.25),
            zone_hysteresis=_confidence(values, "CONTROL_ZONE_HYSTERESIS", 0.04),
            zone_edge_trim=_confidence(values, "CONTROL_ZONE_EDGE_TRIM", 0.15),
        ),
        gestures=GestureSettings(
            debounce_frames=_integer(values, "GESTURE_DEBOUNCE_FRAMES", 5, 1, 60),
            pinch_ratio=_positive_float(values, "GESTURE_PINCH_RATIO", 0.3),
            extended_angle=_positive_float(values, "GESTURE_EXTENDED_ANGLE", 160.0),
            curled_angle=_positive_float(values, "GESTURE_CURLED_ANGLE", 105.0),
            reach_ratio=_positive_float(values, "GESTURE_REACH_RATIO", 1.2),
        ),
        sender=SenderSettings(
            fps=_integer(values, "UDP_SEND_FPS", 30, 1, 120),
            hand=_value(values, "CONTROL_HAND", "right").lower(),
        ),
    )
