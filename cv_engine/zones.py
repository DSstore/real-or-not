"""Two players, one camera: match each hand to a player by where it is in the picture, not by its label.

MediaPipe's left/right label says which hand it is, not whose. Two people can both show a right hand, and the one-hand
-per-label processing would then drop one of them. Here the (mirrored) picture is split into equal vertical zones:
the left zone is player 1 and the right zone is player 2. The most confident hand in each zone belongs to that player.

Each zone's hand is relabelled with a fixed name ("left" for player 1, "right" for player 2) so the existing position
and gesture processors, which keep one filter per label, work unchanged: they simply see two steady "hands". The label
is therefore only a name for the zone in this mode, never a statement about which hand the person used.

Two measures come from the two-person camera test:
- a hand within ``zone_hysteresis`` of the line between zones stays with the player whose hand was just nearby, so it
  does not flip players while it crosses the line;
- people move their hands over a small range (about 17% of the picture width when waving), so each zone's middle part
  is stretched to the full 0..1 cursor range, ignoring ``zone_edge_trim`` at each edge.
"""

from __future__ import annotations

import math
from dataclasses import replace

from cv_engine.coordinates import palm_center
from cv_engine.models import ControlPosition, ControlResult, TrackingResult
from shared.config import ControlSettings

# Player 1 is the left zone, player 2 the right zone.
ZONE_LABELS = ("left", "right")
MAX_PLAYERS = len(ZONE_LABELS)
# One hand per player, so look for two hands in all. There is only room for one hand each, and MediaPipe runs its
# slower hand search on every frame while it tracks fewer hands than it may return, so a higher limit costs frame rate
# (a live run with a limit of 4 and two hands in view went from 30 to about 22 fps). The catch: if one person raises
# a second hand, MediaPipe may report it instead of the other player's hand. Players are asked to use one hand each.
DETECTION_LIMIT = 2


class PlayerTally:
    """How well one player's hand was tracked during a run: for the run summary, not for control.

    A dropout is any frame without the player's hand after it had been tracked; lowering a hand counts as one, so the
    numbers show how often and how long a hand went missing, not why. Short gaps are the likelier tracking losses and
    long ones the likelier lowered hands, so the summary counts the short ones separately."""

    SHORT_GAP_SECONDS = 0.5

    def __init__(self) -> None:
        self.frames = self.tracked = self.dropouts = self.short_dropouts = 0
        self.longest_dropout = 0.0
        self._seen = False
        self._dropout_start: float | None = None

    def update(self, tracking: bool, now: float) -> None:
        self.frames += 1
        if tracking:
            self.tracked += 1
            self._seen = True
            if self._dropout_start is not None:
                gap = now - self._dropout_start
                self.longest_dropout = max(self.longest_dropout, gap)
                self.short_dropouts += gap <= self.SHORT_GAP_SECONDS
                self._dropout_start = None
        elif self._seen and self._dropout_start is None:
            self.dropouts += 1
            self._dropout_start = now

    def summary(self) -> str:
        if not self.tracked:
            return "never tracked"
        # A dropout still open at the end is the player lowering their hand, so it is not counted as the longest.
        return (f"tracked in {100 * self.tracked / self.frames:.0f}% of frames; hand went missing {self.dropouts} "
                f"time(s), {self.short_dropouts} of them for {self.SHORT_GAP_SECONDS:g} s or less; "
                f"longest gap {self.longest_dropout:.2f} s")


class ZoneAssigner:
    """Turn a frame's detections into at most one hand per player, relabelled by zone."""

    def __init__(self, players: int, settings: ControlSettings) -> None:
        if type(players) is not int or not 2 <= players <= MAX_PLAYERS:
            raise ValueError(f"Zones need 2 to {MAX_PLAYERS} players.")
        self.players = players
        self.settings = settings
        # Hand-frames in which the line margin kept a hand with its player although it was across the line.
        self.holds = 0
        self._last: dict[int, tuple[float, float, float]] = {}  # zone -> (x, y, time) of its last accepted hand

    def zone_of(self, x: float) -> int:
        """The zone a position is in, ignoring hysteresis. Positions outside the picture clamp to the end zones."""
        return min(self.players - 1, max(0, math.floor(x * self.players)))

    def _distance_to_line(self, x: float) -> float:
        return min(abs(x - line / self.players) for line in range(1, self.players))

    def _zone_for(self, x: float, y: float, now: float) -> int:
        plain = self.zone_of(x)
        if self._distance_to_line(x) >= self.settings.zone_hysteresis:
            return plain
        # Near a line: prefer the player whose hand was last seen close to this one, if that was recent.
        best, best_distance = plain, self.settings.continuity_radius
        for zone, (last_x, last_y, seen) in self._last.items():
            if now - seen > self.settings.tracking_timeout:
                continue
            distance = math.hypot(x - last_x, y - last_y)
            if distance <= best_distance:
                best, best_distance = zone, distance
        self.holds += best != plain
        return best

    def assign(self, result: TrackingResult, now: float) -> TrackingResult:
        """The same frame with each player's hand relabelled for their zone and the others dropped."""
        candidates: dict[int, list[tuple]] = {}
        for hand in result.hands:
            if not math.isfinite(hand.handedness_confidence):
                continue
            try:
                centre = palm_center(hand)
            except ValueError:
                continue
            zone = self._zone_for(centre.x, centre.y, now)
            candidates.setdefault(zone, []).append((hand, centre))
        chosen = {zone: max(items, key=lambda item: item[0].handedness_confidence)
                  for zone, items in candidates.items()}
        # A hand that has clearly crossed into another zone is the same hand that was tracked in its old one, so the
        # old zone's remembered position must not keep competing for hands near the line. Zones that still have
        # their own hand this frame are refreshed below and are not affected.
        for zone in [zone for zone in self._last if zone not in chosen]:
            last_x, last_y, _ = self._last[zone]
            if any(math.hypot(centre.x - last_x, centre.y - last_y) <= self.settings.continuity_radius
                   for _, centre in chosen.values()):
                del self._last[zone]
        hands = []
        for zone in sorted(chosen):
            hand, centre = chosen[zone]
            self._last[zone] = (centre.x, centre.y, now)
            # label_corrected tells the position processor to skip its left/right label confidence check, which
            # means nothing here because the label is not used to tell people apart.
            hands.append(replace(hand, hand=ZONE_LABELS[zone], label_corrected=True))
        return TrackingResult(tuple(hands), result.processing_ms)

    def zone_x(self, x: float, zone: int) -> float:
        """A palm's x within its zone, stretched so the middle of the zone covers the whole 0..1 range."""
        local = (x - zone / self.players) * self.players
        trim = self.settings.zone_edge_trim
        return min(1.0, max(0.0, (local - trim) / (1 - 2 * trim)))

    def output_controls(self, controls: ControlResult) -> ControlResult:
        """Controls with each tracked position's x stretched for sending; the camera-space ones stay for the preview."""
        mapped = []
        for control in controls.hands:
            if control.position is not None and control.hand in ZONE_LABELS:
                zone = ZONE_LABELS.index(control.hand)
                position = ControlPosition(self.zone_x(control.position.x, zone), control.position.y, control.position.z)
                control = replace(control, position=position)
            mapped.append(control)
        return ControlResult(tuple(mapped))
