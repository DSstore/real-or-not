"""Optional real Python → C# socket check using the same core compiled for Unity.

First build tests/csharp/MotionPlay.ReceiverHarness.csproj with the .NET 8 SDK.
Run from the repository root: python -m tests.check_python_unity_udp --dotnet dotnet
This does not launch Unity, a camera, or a graphical window.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import subprocess
from pathlib import Path

from cv_engine.gesture_processor import GestureProcessor
from cv_engine.models import ControlResult, Gesture, GestureResult, TrackingResult
from cv_engine.position_processor import PositionProcessor
from cv_engine.udp_sender import UdpSender, hand_state
from shared.config import ControlSettings, GestureSettings, SenderSettings
from tests.gesture_fixtures import gesture_hand


ROOT = Path(__file__).resolve().parents[1]


def start_probe(dotnet: str, assembly: Path) -> tuple[subprocess.Popen[str], int]:
    """Wait for the C# listener's READY marker before sending any packets."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen([dotnet, str(assembly), "--probe", str(port)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None
    if process.stdout.readline().strip() != "READY":
        process.kill()
        _output, errors = process.communicate()
        raise RuntimeError(f"C# listener did not start: {errors}")
    return process, port


def stop_probe(process: subprocess.Popen[str]) -> None:
    """Release the subprocess even if an assertion fails."""
    if process.poll() is None:
        process.kill()
    process.communicate()


def check_pipeline(dotnet: str, assembly: Path) -> None:
    """Synthetic landmarks through real Python processors and sender to real C#."""
    control_settings = ControlSettings()
    positions = PositionProcessor(control_settings)
    gestures = GestureProcessor(GestureSettings(), control_settings)
    observation = TrackingResult((gesture_hand(Gesture.OPEN_HAND),), 1)
    for index in range(5):
        controls = positions.update(observation, index / 30)
        confirmed = gestures.update(observation, controls, index / 30)
    assert confirmed.hands[0].gesture == Gesture.OPEN_HAND
    process, port = start_probe(dotnet, assembly)
    assert process.stdout is not None
    try:
        # Malformed input must not terminate the real receiver.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as malformed_sender:
            malformed_sender.sendto(b"invalid-json", ("127.0.0.1", port))
        with UdpSender("127.0.0.1", port, SenderSettings(), True) as sender:
            sent = sender.send(controls, confirmed, 0)
            assert sent
            tracked = json.loads(process.stdout.readline())
            expected = hand_state(controls, confirmed, "right", sender.stream_id, 0, 0, True)
            assert tracked["tracking"] is True and tracked["gesture"] == "OPEN_HAND"
            assert tracked["stream_id"] == sender.stream_id and tracked["sequence"] == 0
            assert expected.position is not None
            assert tracked["position"] == {"x": expected.position.x, "y": expected.position.y, "z": expected.position.z}
            assert math.isclose(tracked["cursor"]["x"], (2 * expected.position.x - 1) * 3.8, abs_tol=1e-12)
            assert math.isclose(tracked["cursor"]["y"], (1 - 2 * expected.position.y) * 2.8, abs_tol=1e-12)
            assert tracked["invalid"] == 1
            sent = sender.send(ControlResult(), GestureResult(), 0.04)
            assert sent
            lost = json.loads(process.stdout.readline())
            assert lost["tracking"] is False and lost["position"] is None and lost["gesture"] == "UNKNOWN"
            assert lost["cursor"] is None
        tail, errors = process.communicate(timeout=12)
        assert process.returncode == 0, errors
        assert "TIMEOUT" in tail
        print("PASS Python landmarks -> palm/gestures -> UDP -> C# cursor mapping/loss; invalid packet rejected")
    finally:
        stop_probe(process)


def start_slot_probe(dotnet: str, assembly: Path) -> tuple[subprocess.Popen[str], int]:
    """Like start_probe, for the two-player probe that reports each packet's player."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen([dotnet, str(assembly), "--probe-slots", str(port)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None
    if process.stdout.readline().strip() != "READY":
        process.kill()
        _output, errors = process.communicate()
        raise RuntimeError(f"C# listener did not start: {errors}")
    return process, port


def check_two_players(dotnet: str, assembly: Path) -> None:
    """Two people who both show a right hand, through the zones and two senders, to the real C# receiver."""
    from dataclasses import replace

    from cv_engine.models import Landmark, TrackedHand
    from cv_engine.zones import ZONE_LABELS, ZoneAssigner

    def hand(x: float) -> TrackedHand:
        return TrackedHand("right", 0.95, (Landmark(x, 0.5, -0.02),) * 21)

    control_settings = ControlSettings(smoothing_alpha=1, dead_zone=0)
    zones, positions = ZoneAssigner(2, control_settings), PositionProcessor(control_settings)
    controls = positions.update(zones.assign(TrackingResult((hand(0.1), hand(0.9)), 1), 0.0), 0.0)
    outgoing = zones.output_controls(controls)
    process, port = start_slot_probe(dotnet, assembly)
    assert process.stdout is not None
    try:
        senders = [UdpSender("127.0.0.1", port, replace(SenderSettings(), hand=ZONE_LABELS[slot]), True, slot=slot)
                   for slot in range(2)]
        with senders[0], senders[1]:
            for sender in senders:
                assert sender.send(outgoing, GestureResult(), 0)
            received = {}
            for _ in range(2):
                line = json.loads(process.stdout.readline())
                received[line["slot"]] = line
            assert sorted(received) == [0, 1]
            for slot, expected_x in ((0, (0.2 - 0.15) / 0.7), (1, (0.8 - 0.15) / 0.7)):
                line = received[slot]
                assert line["packet_slot"] == slot and line["tracking"] is True
                assert line["stream_id"] == senders[slot].stream_id and line["sequence"] == 0
                assert line["hand"] == ZONE_LABELS[slot]
                assert math.isclose(line["position"]["x"], expected_x, abs_tol=1e-12), line
                assert math.isclose(line["cursor"]["x"], (2 * expected_x - 1) * 3.8, abs_tol=1e-12), line
            assert received[0]["stream_id"] != received[1]["stream_id"]
            # Player 2 loses their hand; player 1 is unaffected.
            lost_controls = positions.update(zones.assign(TrackingResult((hand(0.1),), 1), 0.04), 0.04)
            assert senders[1].send(zones.output_controls(lost_controls), GestureResult(), 0.04)
            lost = json.loads(process.stdout.readline())
            assert lost["slot"] == 1 and lost["tracking"] is False and lost["position"] is None
        tail, errors = process.communicate(timeout=12)
        assert process.returncode == 0, errors
        assert "TIMEOUT" in tail
        print("PASS two players (both 'right' hands) -> zones -> two UDP streams -> C# per-player state; one lost hand is independent")
    finally:
        stop_probe(process)


def check_quiz_results_reach_the_store(dotnet: str, assembly: Path) -> None:
    """C# plays a whole round from the shipped question file; Python receives, checks and stores both results."""
    import tempfile

    from backend.result_receiver import ResultReceiver
    from backend.storage import SqliteResultStore
    from shared.protocol import decode_quiz_session_end, encode_result_ack
    from shared.questions import load_bank

    completed = subprocess.run([dotnet, str(assembly), "--emit-quiz-result", str(ROOT / "data" / "questions.json")],
                               capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    assert len(lines) == 2, completed.stdout
    results = [decode_quiz_session_end(line.encode("utf-8")) for line in lines]
    assert [r.slot for r in results] == [0, 1]
    assert results[0].roundId == results[1].roundId and results[0].session_id != results[1].session_id
    assert all(r.game == "scam_quiz" and r.totalQuestions == 5 and r.difficulty == "level_2" for r in results)
    bank = load_bank()
    with tempfile.TemporaryDirectory() as folder:
        store = SqliteResultStore(Path(folder) / "m.db")
        try:
            receiver = ResultReceiver(store, "user-one", second_user_id="user-two", bank=bank)
            for line, result in zip(lines, results):
                assert receiver.handle(line.encode("utf-8")) == encode_result_ack(result.session_id, "stored")
            first, second = (store.get(r.session_id) for r in results)
            assert (first["user_id"], second["user_id"]) == ("user-one", "user-two")
            for document in (first, second):
                assert all(row["category"] is not None for row in document["responses"]), document["responses"]
                # What C# counted as right is what the question bank says was right.
                assert sum(1 for row in document["responses"] if row["is_correct"]) == document["correct"]
            assert first["correct"] == 5 and second["correct"] == 3 and second["wrong"] == 2
        finally:
            store.close()
    print("PASS C# plays a round from data/questions.json -> QUIZ_SESSION_END x2 -> Python receiver -> stored per player with categories")


def check_silence_timeout(dotnet: str, assembly: Path) -> None:
    """Closing a raw sender sends no terminal packet; C# must clear by timeout."""
    process, port = start_probe(dotnet, assembly)
    assert process.stdout is not None
    try:
        from tests.test_udp import example_state

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(example_state().to_bytes(), ("127.0.0.1", port))
        tracked = json.loads(process.stdout.readline())
        assert tracked["tracking"] is True
        assert process.stdout.readline().strip() == "TIMEOUT"
        _output, errors = process.communicate(timeout=12)
        assert process.returncode == 0, errors
        print("PASS C# clears Python control after silence without a shutdown packet")
    finally:
        stop_probe(process)


def main() -> int:
    """Run optional integration checks without changing the Python dependency set."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dotnet", default="dotnet", help="Path to .NET executable")
    parser.add_argument("--assembly", type=Path, default=ROOT / "tests/csharp/bin/Debug/net8.0/MotionPlay.ReceiverHarness.dll")
    args = parser.parse_args()
    if not args.assembly.is_file():
        parser.error("Build the C# harness first; see docs/phase6_unity_receiver.md.")
    check_pipeline(args.dotnet, args.assembly)
    check_two_players(args.dotnet, args.assembly)
    check_quiz_results_reach_the_store(args.dotnet, args.assembly)
    check_silence_timeout(args.dotnet, args.assembly)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
