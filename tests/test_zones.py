"""Test two-player mode: hands matched to players by screen zone, per-player streams, and unchanged one-player behaviour."""

from __future__ import annotations

import json
import socket
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import numpy as np

from cv_engine.continuity import LabelContinuity
from cv_engine.controller import run_tracking
from cv_engine.gesture_processor import GestureProcessor
from cv_engine.models import (
    ControlPosition, ControlResult, Gesture, HandControl, Landmark, TrackedHand, TrackingResult,
)
from cv_engine.position_processor import PositionProcessor
from cv_engine.preview import draw_overlay
from cv_engine.udp_sender import UdpSender, hand_state
from cv_engine.zones import DETECTION_LIMIT, ZoneAssigner
from shared.config import ConfigurationError, ControlSettings, SenderSettings, TrackingSettings, load_settings
from shared.protocol import CVState, ProtocolError, decode_cv_state
from tests.gesture_fixtures import gesture_hand


def hand(x=0.5, y=0.5, label="right", confidence=0.95) -> TrackedHand:
    return TrackedHand(label, confidence, (Landmark(x, y, -0.02),) * 21)


def frame(*hands: TrackedHand) -> TrackingResult:
    return TrackingResult(tuple(hands), processing_ms=1.0)


SETTINGS = ControlSettings(smoothing_alpha=1, dead_zone=0)


class AssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.zones = ZoneAssigner(2, SETTINGS)

    def test_hands_belong_to_the_zone_they_are_in_whatever_their_label(self) -> None:
        for labels in (("right", "right"), ("left", "left"), ("left", "right"), ("right", "left")):
            with self.subTest(labels=labels):
                result = ZoneAssigner(2, SETTINGS).assign(frame(hand(0.25, label=labels[0]), hand(0.75, label=labels[1])), 0.0)
                self.assertEqual(["left", "right"], [h.hand for h in result.hands])
                self.assertEqual([0.25, 0.75], [h.landmarks[0].x for h in result.hands])
                self.assertTrue(all(h.label_corrected for h in result.hands))

    def test_input_order_does_not_matter(self) -> None:
        result = self.zones.assign(frame(hand(0.75), hand(0.25)), 0.0)
        self.assertEqual([0.25, 0.75], [h.landmarks[0].x for h in result.hands])

    def test_a_single_player_gets_only_their_own_zone(self) -> None:
        self.assertEqual(["right"], [h.hand for h in self.zones.assign(frame(hand(0.8)), 0.0).hands])
        self.assertEqual(["left"], [h.hand for h in ZoneAssigner(2, SETTINGS).assign(frame(hand(0.1)), 0.0).hands])
        self.assertEqual((), self.zones.assign(frame(), 0.0).hands)

    def test_the_most_confident_hand_in_a_zone_wins_and_spares_are_dropped(self) -> None:
        result = self.zones.assign(frame(hand(0.2, confidence=0.8), hand(0.3, confidence=0.95), hand(0.4, confidence=0.9)), 0.0)
        self.assertEqual(1, len(result.hands))
        self.assertEqual(0.3, result.hands[0].landmarks[0].x)

    def test_a_spare_hand_in_one_zone_does_not_hide_the_other_players_hand(self) -> None:
        result = self.zones.assign(frame(hand(0.2), hand(0.3), hand(0.8)), 0.0)  # player 1 raised both hands
        self.assertEqual(["left", "right"], [h.hand for h in result.hands])

    def test_zone_of_clamps_and_the_line_belongs_to_the_right_zone(self) -> None:
        self.assertEqual([0, 0, 1, 1, 1], [self.zones.zone_of(x) for x in (-0.2, 0.4999, 0.5, 1.0, 1.3)])

    def test_invalid_detections_are_skipped(self) -> None:
        bad_position = hand(float("nan"))
        bad_confidence = hand(0.2, confidence=float("nan"))
        short = TrackedHand("right", 0.9, (Landmark(0.3, 0.3, 0),))
        self.assertEqual((), self.zones.assign(frame(bad_position, bad_confidence, short), 0.0).hands)

    def test_only_two_players_are_supported(self) -> None:
        for players in (0, 1, 3, True, 2.0):
            with self.subTest(players=players), self.assertRaises(ValueError):
                ZoneAssigner(players, SETTINGS)  # type: ignore[arg-type]


class HysteresisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.zones = ZoneAssigner(2, SETTINGS)  # hysteresis 0.04, radius 0.25, timeout 0.5

    def owner(self, x, y=0.5, now=0.0) -> str:
        return self.zones.assign(frame(hand(x, y)), now).hands[0].hand

    def test_a_hand_near_the_line_stays_with_the_player_it_just_belonged_to(self) -> None:
        self.assertEqual("left", self.owner(0.3, now=0.0))
        self.assertEqual("left", self.owner(0.52, now=0.05))   # across the line, but within the margin
        self.assertEqual("right", self.owner(0.58, now=0.10))  # clearly across
        self.assertEqual("right", self.owner(0.48, now=0.15))  # and it stays there on the way back

    def test_without_recent_history_the_side_decides(self) -> None:
        self.assertEqual("right", self.owner(0.52))
        self.assertEqual("left", ZoneAssigner(2, SETTINGS).assign(frame(hand(0.48)), 0.0).hands[0].hand)

    def test_old_history_expires(self) -> None:
        self.assertEqual("left", self.owner(0.3, now=0.0))
        self.assertEqual("right", self.owner(0.52, now=1.0))

    def test_a_hand_far_from_the_last_one_is_not_captured_by_it(self) -> None:
        self.assertEqual("left", self.owner(0.1, now=0.0))
        self.assertEqual("right", self.owner(0.52, now=0.05))  # 0.42 away, beyond the 0.25 radius

    def test_the_other_players_hand_is_not_affected(self) -> None:
        first = self.zones.assign(frame(hand(0.3), hand(0.8)), 0.0)
        second = self.zones.assign(frame(hand(0.52), hand(0.8)), 0.05)
        self.assertEqual(["left", "right"], [h.hand for h in first.hands])
        self.assertEqual(["left", "right"], [h.hand for h in second.hands])
        self.assertEqual([0.52, 0.8], [h.landmarks[0].x for h in second.hands])

    def test_hysteresis_can_be_turned_off(self) -> None:
        zones = ZoneAssigner(2, replace(SETTINGS, zone_hysteresis=0))
        zones.assign(frame(hand(0.3)), 0.0)
        self.assertEqual("right", zones.assign(frame(hand(0.51)), 0.05).hands[0].hand)


class OutputMappingTests(unittest.TestCase):
    def test_the_middle_of_each_zone_is_stretched_to_the_full_range(self) -> None:
        zones = ZoneAssigner(2, SETTINGS)  # trim 0.15 of each zone at each edge
        self.assertAlmostEqual(0.5, zones.zone_x(0.25, 0))
        self.assertAlmostEqual(0.5, zones.zone_x(0.75, 1))
        self.assertAlmostEqual(0.0, zones.zone_x(0.05, 0))     # inside the trimmed edge
        self.assertAlmostEqual(0.0, zones.zone_x(0.5, 1))      # the line itself
        self.assertAlmostEqual(1.0, zones.zone_x(0.95, 1))
        self.assertAlmostEqual((0.175 - 0.15) / 0.7, zones.zone_x(0.0875, 0))
        self.assertAlmostEqual((0.8 - 0.15) / 0.7, zones.zone_x(0.9, 1))

    def test_a_trim_of_zero_only_rescales_the_zone(self) -> None:
        zones = ZoneAssigner(2, replace(SETTINGS, zone_edge_trim=0))
        self.assertAlmostEqual(0.75, zones.zone_x(0.375, 0))
        self.assertAlmostEqual(0.25, zones.zone_x(0.625, 1))

    def test_only_the_sent_position_changes(self) -> None:
        zones = ZoneAssigner(2, SETTINGS)
        raw, smooth = ControlPosition(0.1, 0.3, 0.0), ControlPosition(0.1, 0.4, -0.1)
        controls = ControlResult((HandControl("left", "tracking", 0.9, raw, smooth),
                                  HandControl("right", "lost")))
        out = zones.output_controls(controls)
        self.assertEqual(["left", "right"], [h.hand for h in out.hands])
        self.assertAlmostEqual((0.2 - 0.15) / 0.7, out.hands[0].position.x)
        self.assertEqual((0.4, -0.1), (out.hands[0].position.y, out.hands[0].position.z))
        self.assertEqual(raw, out.hands[0].raw_position)       # the camera-space values stay for the preview
        self.assertIsNone(out.hands[1].position)               # a lost hand is still lost
        self.assertEqual(smooth, controls.hands[0].position)   # and the input is not modified


class PipelineTests(unittest.TestCase):
    """The whole processing chain, with two people who both show a hand MediaPipe calls "right"."""

    def setUp(self) -> None:
        self.settings = load_settings(Path(tempfile.gettempdir()) / "no-such.env", environ={})
        self.control = ControlSettings(smoothing_alpha=1, dead_zone=0)

    def test_both_players_keep_a_cursor_even_when_both_hands_are_labelled_right(self) -> None:
        zones, processor = ZoneAssigner(2, self.control), PositionProcessor(self.control)
        controls = processor.update(zones.assign(frame(hand(0.25, label="right"), hand(0.75, label="right")), 0.0), 0.0)
        self.assertEqual(["left", "right"], [c.hand for c in controls.hands if c.tracking])

    def test_the_label_based_pipeline_loses_one_of_them(self) -> None:
        """The reason for zones: with labels alone, two right hands compete for one slot."""
        continuity, processor = LabelContinuity(self.control), PositionProcessor(self.control)
        controls = processor.update(continuity.apply(frame(hand(0.25, label="right"), hand(0.75, label="right")), 0.0), 0.0)
        self.assertEqual(1, sum(1 for c in controls.hands if c.tracking))

    def test_flickering_labels_do_not_disturb_either_player(self) -> None:
        zones, processor = ZoneAssigner(2, self.control), PositionProcessor(self.control)
        for step, (a, b) in enumerate([("right", "left"), ("left", "left"), ("left", "right"), ("right", "right")]):
            controls = processor.update(zones.assign(frame(hand(0.25, label=a), hand(0.75, label=b)), step * 0.04), step * 0.04)
            self.assertEqual(2, sum(1 for c in controls.hands if c.tracking), step)

    def test_each_player_has_their_own_gesture(self) -> None:
        zones, processor = ZoneAssigner(2, self.control), PositionProcessor(self.control)
        gestures = GestureProcessor(self.settings.gestures, self.control)
        aspect = 4 / 3
        result = None
        for step in range(8):
            now = step * 0.04
            observed = zones.assign(frame(
                gesture_hand(Gesture.OPEN_HAND, label="right", offset=(0.25, 0.2), image_aspect_ratio=aspect),
                gesture_hand(Gesture.FIST, label="right", offset=(0.75, 0.2), image_aspect_ratio=aspect),
            ), now)
            controls = processor.update(observed, now)
            result = gestures.update(observed, controls, now, aspect)
        by_player = {g.hand: g.gesture for g in result.hands if g.tracking}
        self.assertEqual({"left": Gesture.OPEN_HAND, "right": Gesture.FIST}, by_player)


class ProtocolSlotTests(unittest.TestCase):
    def state(self, **changes) -> CVState:
        values = dict(stream_id=str(uuid4()), sequence=0, timestamp=0, hand="left", tracking=False, position=None,
                      gesture="UNKNOWN", confidence=0.0, mirrored=True)
        values.update(changes)
        return CVState(**values)

    def test_a_one_player_packet_has_no_slot_field(self) -> None:
        data = json.loads(self.state().to_bytes())
        self.assertNotIn("slot", data)
        self.assertIsNone(decode_cv_state(self.state().to_bytes()).slot)

    def test_a_slot_is_sent_and_read_back(self) -> None:
        for slot in (0, 1):
            with self.subTest(slot=slot):
                payload = self.state(slot=slot).to_bytes()
                self.assertEqual(slot, json.loads(payload)["slot"])
                self.assertEqual(slot, decode_cv_state(payload).slot)

    def test_bad_slots_are_rejected(self) -> None:
        for slot in (2, -1, True, 1.0, "0"):
            with self.subTest(slot=slot), self.assertRaises(ProtocolError):
                self.state(slot=slot)
        data = json.loads(self.state().to_bytes())
        for slot in (2, -1, True, 1.5, "1"):
            with self.subTest(wire=slot), self.assertRaises(ProtocolError):
                decode_cv_state(json.dumps({**data, "slot": slot}).encode())

    def test_hand_state_carries_the_slot(self) -> None:
        self.assertEqual(1, hand_state(ControlResult(), MagicMock(hands=()), "right", str(uuid4()), 0, 0, True, 1).slot)
        self.assertIsNone(hand_state(ControlResult(), MagicMock(hands=()), "right", str(uuid4()), 0, 0, True).slot)


class TwoStreamTests(unittest.TestCase):
    """Real loopback UDP: one stream per player, each reading only their own zone's hand."""

    def test_each_sender_reports_only_its_own_player(self) -> None:
        controls = ControlResult((
            HandControl("left", "tracking", 0.9, ControlPosition(0.1, 0.2, 0), ControlPosition(0.3, 0.2, 0)),
            HandControl("right", "tracking", 0.8, ControlPosition(0.1, 0.2, 0), ControlPosition(0.7, 0.6, 0)),
        ))
        gestures = MagicMock(hands=())
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(2)
            port = receiver.getsockname()[1]
            with UdpSender("127.0.0.1", port, SenderSettings(hand="left"), True, slot=0) as first, \
                    UdpSender("127.0.0.1", port, SenderSettings(hand="right"), True, slot=1) as second:
                first.send(controls, gestures, 0.0)
                second.send(controls, gestures, 0.0)
                packets = [decode_cv_state(receiver.recv(1201)) for _ in range(2)]
        self.assertEqual([0, 1], [p.slot for p in packets])
        self.assertEqual(["left", "right"], [p.hand for p in packets])
        self.assertEqual([0.3, 0.7], [p.position.x for p in packets])
        self.assertNotEqual(packets[0].stream_id, packets[1].stream_id)

    def test_a_one_player_sender_still_sends_exactly_the_old_packet(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(2)
            with UdpSender("127.0.0.1", receiver.getsockname()[1], SenderSettings(), True) as sender:
                sender.send(ControlResult(), MagicMock(hands=()), 0.0)
                payload = receiver.recv(1201)
        self.assertNotIn(b"slot", payload)


class ControllerTests(unittest.TestCase):
    def settings(self, receiver_port: int, **extra: str):
        with tempfile.TemporaryDirectory() as directory:
            return load_settings(Path(directory) / ".env", environ={
                "CV_TO_UNITY_PORT": str(receiver_port), "UNITY_TO_PYTHON_PORT": "1024", **extra})

    @patch("cv_engine.controller.perf_counter", side_effect=[0, 0, 0.04, 0.08, 0.12])
    @patch("cv_engine.hand_tracker.HandTracker")
    @patch("cv_engine.camera.Camera")
    def test_two_players_get_two_streams_with_their_own_stretched_positions(
        self, camera_factory: MagicMock, tracker_factory: MagicMock, clock: MagicMock,
    ) -> None:
        camera_factory.return_value.__enter__.return_value.read.return_value = np.zeros((480, 640, 3), np.uint8)
        # Both people show a hand MediaPipe calls "right"; player 1 raises a second hand too.
        both = TrackingResult((hand(0.1, 0.5, "right", 0.9), hand(0.9, 0.5, "right", 0.9), hand(0.3, 0.2, "left", 0.7)), 1)
        tracker_factory.return_value.__enter__.return_value.process.side_effect = [both] * 3
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(2)
            settings = self.settings(receiver.getsockname()[1], TRACKING_PLAYERS="2")
            self.assertEqual(3, run_tracking(settings, show_preview=False, max_frames=3))
            packets = [decode_cv_state(receiver.recv(1201)) for _ in range(8)]  # 3 states + 1 final lost, per player
        self.assertEqual(DETECTION_LIMIT, tracker_factory.call_args.args[0].max_hands)
        by_slot = {slot: [p for p in packets if p.slot == slot] for slot in (0, 1)}
        self.assertEqual([4, 4], [len(by_slot[0]), len(by_slot[1])])
        self.assertEqual(1, len({p.stream_id for p in by_slot[0]}))
        self.assertNotEqual(by_slot[0][0].stream_id, by_slot[1][0].stream_id)
        for slot, expected_x in ((0, (0.2 - 0.15) / 0.7), (1, (0.8 - 0.15) / 0.7)):
            tracked = [p for p in by_slot[slot] if p.tracking]
            self.assertEqual(3, len(tracked), slot)
            self.assertAlmostEqual(expected_x, tracked[-1].position.x, places=6)
            self.assertEqual(sorted(p.sequence for p in by_slot[slot]), [0, 1, 2, 3])
            self.assertFalse(max(by_slot[slot], key=lambda p: p.sequence).tracking)  # the final lost state on shutdown

    @patch("cv_engine.controller.perf_counter", side_effect=[0, 0, 0.04, 0.08])
    @patch("cv_engine.hand_tracker.HandTracker")
    @patch("cv_engine.camera.Camera")
    def test_one_player_mode_is_unchanged(
        self, camera_factory: MagicMock, tracker_factory: MagicMock, clock: MagicMock,
    ) -> None:
        camera_factory.return_value.__enter__.return_value.read.return_value = np.zeros((480, 640, 3), np.uint8)
        tracker_factory.return_value.__enter__.return_value.process.side_effect = [TrackingResult((hand(0.1),), 1)] * 2
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(2)
            settings = self.settings(receiver.getsockname()[1])
            run_tracking(settings, show_preview=False, max_frames=2)
            payloads = [receiver.recv(1201) for _ in range(3)]
        self.assertEqual(1, tracker_factory.call_args.args[0].max_hands)  # the .env value, not the zone limit
        self.assertTrue(all(b"slot" not in payload for payload in payloads))
        self.assertAlmostEqual(0.1, decode_cv_state(payloads[0]).position.x)  # not stretched: the old behaviour

    @patch("cv_engine.preview.Preview")
    @patch("cv_engine.hand_tracker.HandTracker")
    @patch("cv_engine.camera.Camera")
    def test_the_preview_is_told_about_the_zones_only_in_two_player_mode(
        self, camera_factory: MagicMock, tracker_factory: MagicMock, preview_factory: MagicMock,
    ) -> None:
        camera_factory.return_value.__enter__.return_value.read.return_value = np.zeros((4, 6, 3), np.uint8)
        tracker_factory.return_value.__enter__.return_value.process.return_value = frame(hand(0.2))
        preview = preview_factory.return_value.__enter__.return_value
        preview.show.side_effect = [True, False]
        run_tracking(self.settings(5005, TRACKING_PLAYERS="2"), send_udp=False)
        self.assertEqual({"zones": 2}, preview.show.call_args.kwargs)
        preview.show.side_effect = [False]
        run_tracking(self.settings(5005), send_udp=False)
        self.assertEqual({}, preview.show.call_args.kwargs)


class PreviewTests(unittest.TestCase):
    def test_zone_lines_are_drawn_only_when_asked(self) -> None:
        image = np.zeros((100, 200, 3), dtype=np.uint8)
        plain = draw_overlay(image, frame(), 30)
        zoned = draw_overlay(image, frame(), 30, zones=2)
        self.assertTrue((plain[70, 95:106] == 0).all())  # row 70: below the status text, above the player names
        self.assertTrue((zoned[70, 95:106] > 0).any())


class SettingsTests(unittest.TestCase):
    def load(self, **environ: str):
        with tempfile.TemporaryDirectory() as directory:
            return load_settings(Path(directory) / ".env", environ=environ)

    def test_defaults_keep_the_original_one_player_behaviour(self) -> None:
        settings = self.load()
        self.assertEqual(1, settings.tracking.players)
        self.assertEqual((0.04, 0.15), (settings.control.zone_hysteresis, settings.control.zone_edge_trim))

    def test_values_are_read_from_the_environment(self) -> None:
        settings = self.load(TRACKING_PLAYERS="2", CONTROL_ZONE_HYSTERESIS="0.1", CONTROL_ZONE_EDGE_TRIM="0.2")
        self.assertEqual((2, 0.1, 0.2), (settings.tracking.players, settings.control.zone_hysteresis,
                                         settings.control.zone_edge_trim))

    def test_invalid_values_are_rejected(self) -> None:
        for name, value in (("TRACKING_PLAYERS", "3"), ("TRACKING_PLAYERS", "0"), ("TRACKING_PLAYERS", "two"),
                            ("CONTROL_ZONE_HYSTERESIS", "0.3"), ("CONTROL_ZONE_HYSTERESIS", "-0.1"),
                            ("CONTROL_ZONE_EDGE_TRIM", "0.5"), ("CONTROL_ZONE_EDGE_TRIM", "nan")):
            with self.subTest(name=name, value=value), self.assertRaises(ConfigurationError):
                self.load(**{name: value})

    def test_the_settings_classes_check_their_own_values(self) -> None:
        for players in (0, 3, True, 1.0):
            with self.subTest(players=players), self.assertRaises(ConfigurationError):
                TrackingSettings(players=players)  # type: ignore[arg-type]
        for changes in ({"zone_hysteresis": float("nan")}, {"zone_hysteresis": 0.26}, {"zone_edge_trim": 0.5},
                        {"zone_edge_trim": -0.01}):
            with self.subTest(changes=changes), self.assertRaises(ConfigurationError):
                ControlSettings(**changes)
        ControlSettings(zone_hysteresis=0, zone_edge_trim=0)  # both off is allowed


if __name__ == "__main__":
    unittest.main()
