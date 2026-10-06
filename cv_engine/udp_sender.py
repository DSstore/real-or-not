"""Paced, best-effort transmission of fresh control states using one UDP socket."""

from __future__ import annotations

import logging
import math
import socket
from time import time_ns
from uuid import uuid4

from cv_engine.errors import CVEngineError
from cv_engine.models import ControlResult, GestureResult
from shared.config import SenderSettings, validate_udp_host
from shared.protocol import CVState, Position


LOGGER = logging.getLogger("motionplay.cv_engine.udp_sender")


class UdpError(CVEngineError):
    """The sender socket could not be initialized."""


def hand_state(
    controls: ControlResult, gestures: GestureResult, hand: str,
    stream_id: str, sequence: int, timestamp: int, mirrored: bool, slot: int | None = None,
) -> CVState:
    """Adapt the selected hand only; do not silently switch to the other hand.

    In two-player mode ``slot`` is the player and ``hand`` names that player's zone ("left" is player 1)."""
    control = next((item for item in controls.hands if item.hand == hand and item.tracking), None)
    gesture = next((item for item in gestures.hands if item.hand == hand and item.tracking), None)
    position = None
    if control is not None and control.position is not None:
        position = Position(control.position.x, control.position.y, control.position.z)
    return CVState(
        stream_id=stream_id, sequence=sequence, timestamp=timestamp, hand=hand,
        tracking=position is not None, position=position,
        gesture=gesture.gesture.value if position is not None and gesture is not None else "UNKNOWN",
        confidence=control.handedness_confidence if position is not None and control is not None else 0.0,
        mirrored=mirrored, slot=slot,
    )


class UdpSender:
    """One socket per run; skip excess frames instead of queuing or blocking CV.

    Successful sendto means only that the OS accepted the datagram. Receiver
    availability/delivery cannot be established by this one-way protocol.
    """

    def __init__(self, host: str, port: int, settings: SenderSettings, mirrored: bool,
                 slot: int | None = None) -> None:
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("UDP destination port must be from 1024 to 65535.")
        self.destination = (validate_udp_host(host), port)
        self.settings = settings
        self.mirrored = mirrored
        self.slot = slot  # The player this stream carries in two-player mode; None otherwise.
        self.stream_id = str(uuid4())
        self.sent = self.failed = self.skipped = 0
        self._sequence = 0
        self._socket: socket.socket | None = None
        self._next_due: float | None = None
        self._last_clock: float | None = None
        self._last_warning: float | None = None
        self._unhealthy = False

    def open(self) -> UdpSender:
        """Create a nonblocking IPv4 sender without a UDP connect handshake."""
        if self._socket is not None:
            return self
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            # Windows may turn ICMP port-unreachable into socket errors. This
            # sender never reads replies; receiver liveness remains unknown.
            if hasattr(socket, "SIO_UDP_CONNRESET"):
                sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        except OSError as error:
            if sock is not None:
                sock.close()
            raise UdpError("Cannot initialize UDP sender; check local socket permissions or use --no-udp.") from error
        self._socket = sock
        who = f"player {self.slot + 1} ({self.settings.hand} half)" if self.slot is not None else f"{self.settings.hand} hand"
        LOGGER.info("UDP sender ready: %s:%d, target %d Hz, %s. Receiver delivery is unconfirmed.",
                    *self.destination, self.settings.fps, who)
        return self

    def send(self, controls: ControlResult, gestures: GestureResult, now: float) -> bool:
        """Attempt the latest state when due; false means skipped or send failure."""
        if self._socket is None:
            raise UdpError("UDP sender is not open.")
        if not math.isfinite(now) or (self._last_clock is not None and now < self._last_clock):
            raise ValueError("UDP pacing requires a finite, nondecreasing monotonic clock.")
        self._last_clock = now
        if self._next_due is not None and now + 1e-12 < self._next_due:
            self.skipped += 1
            return False
        interval = 1 / self.settings.fps
        if self._next_due is None:
            self._next_due = now + interval
        else:
            # Anchor the cadence: resetting to now+interval each frame halves
            # throughput for a camera running just above 30 FPS. Skip expired
            # slots instead of sending a catch-up burst.
            slots = math.floor(max(0.0, now - self._next_due) / interval) + 1
            self._next_due += slots * interval
        return self._transmit(controls, gestures, now)

    def _transmit(self, controls: ControlResult, gestures: GestureResult, now: float) -> bool:
        """Allocate a sequence per attempt; dropped sends therefore leave gaps."""
        state = hand_state(controls, gestures, self.settings.hand, self.stream_id,
                           self._sequence, time_ns() // 1_000_000, self.mirrored, self.slot)
        payload = state.to_bytes()
        self._sequence += 1
        try:
            assert self._socket is not None
            if self._socket.sendto(payload, self.destination) != len(payload):
                raise OSError("The socket accepted an incomplete datagram.")
        except OSError:
            self.failed += 1
            self._unhealthy = True
            if self._last_warning is None or now - self._last_warning >= 5:
                LOGGER.warning("UDP send failed; tracking continues. Check UDP_HOST, port, and firewall. Delivery is unconfirmed.")
                self._last_warning = now
            return False
        self.sent += 1
        if self._unhealthy:
            LOGGER.info("UDP sends resumed; receiver delivery remains unconfirmed.")
            self._unhealthy = False
        return True

    def close(self) -> None:
        """Send one best-effort lost state at shutdown, then release the socket."""
        if self._socket is None:
            return
        try:
            # Bypass normal pacing once at shutdown so a clean exit can clear
            # the cursor. A future receiver must still enforce its own timeout.
            self._transmit(ControlResult(), GestureResult(), self._last_clock or 0.0)
        finally:
            self._socket.close()
            self._socket = None
            LOGGER.info("UDP sender closed: %d accepted, %d failed, %d frames skipped.",
                        self.sent, self.failed, self.skipped)

    def __enter__(self) -> UdpSender:
        """Own the socket through the controller's ExitStack."""
        return self.open()

    def __exit__(self, *args: object) -> None:
        """Release the socket on user exit and processing failures."""
        self.close()
