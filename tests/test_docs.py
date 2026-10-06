"""Keep the documentation honest: links resolve, the configuration reference matches the code, and diagrams are well formed.

These checks run without hardware. They cannot judge whether prose is accurate, but they catch the drift that happens most:
a renamed file, a changed default, a setting that was added but never documented.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from shared.config import load_settings

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MARKDOWN = [ROOT / "README.md", *sorted(DOCS.glob("*.md"))]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
KNOWN_DIAGRAMS = ("flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram", "erDiagram", "gantt")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text))


def anchors(path: Path) -> set[str]:
    return {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.*)$", read(path), re.MULTILINE)}


class LinkTests(unittest.TestCase):
    def test_every_relative_link_points_at_a_real_file_and_heading(self) -> None:
        broken = []
        for source in MARKDOWN:
            text = re.sub(r"```.*?```", "", read(source), flags=re.DOTALL)  # links inside code samples are not links
            for target in LINK.findall(text):
                if re.match(r"^(https?:|mailto:)", target):
                    continue
                file_part, _, fragment = target.partition("#")
                path = source if not file_part else (source.parent / file_part).resolve()
                if not path.exists():
                    broken.append(f"{source.relative_to(ROOT)} -> {target} (no such file)")
                elif fragment and path.suffix == ".md" and fragment not in anchors(path):
                    broken.append(f"{source.relative_to(ROOT)} -> {target} (no such heading)")
        self.assertEqual([], broken)

    def test_every_image_in_the_documents_exists(self) -> None:
        missing = []
        for source in MARKDOWN:
            text = re.sub(r"<!--.*?-->", "", re.sub(r"```.*?```", "", read(source), flags=re.DOTALL), flags=re.DOTALL)
            for target in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", text):
                if not re.match(r"^https?:", target) and not (source.parent / target).resolve().exists():
                    missing.append(f"{source.relative_to(ROOT)} -> {target}")
        self.assertEqual([], missing)

    def test_the_readme_links_to_every_phase_guide_and_the_main_documents(self) -> None:
        readme = read(ROOT / "README.md")
        for guide in sorted(DOCS.glob("phase*.md")):
            with self.subTest(guide.name):
                self.assertIn(f"docs/{guide.name}", readme)
        for name in ("architecture.md", "configuration.md", "udp_protocol.md", "setup.md", "performance.md",
                     "lessons-learned.md", "demo-guide.md"):
            with self.subTest(name):
                self.assertIn(f"docs/{name}", readme)


class DiagramTests(unittest.TestCase):
    def test_every_mermaid_block_is_a_recognisable_diagram(self) -> None:
        # Full parsing needs Mermaid itself (checked by hand when diagrams change); this catches empty or mistyped blocks.
        found = 0
        for source in MARKDOWN:
            for block in re.findall(r"```mermaid\r?\n(.*?)```", read(source), flags=re.DOTALL):
                found += 1
                with self.subTest(source.name):
                    self.assertTrue(block.strip().startswith(KNOWN_DIAGRAMS), block[:40])
                    self.assertEqual(block.count("["), block.count("]"), "unbalanced brackets in a diagram")
        self.assertGreaterEqual(found, 3)


def documented_settings() -> dict[str, str]:
    """Setting name -> documented default, from the first two columns of configuration.md's tables."""
    rows = {}
    for match in re.finditer(r"^\|\s*`([A-Z][A-Z0-9_]+)`\s*\|\s*`([^`]*)`\s*\|", read(DOCS / "configuration.md"), re.MULTILINE):
        rows[match.group(1)] = match.group(2)
    return rows


def example_settings() -> dict[str, str]:
    values = {}
    for line in read(ROOT / ".env.example").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def same(a: object, b: object) -> bool:
    try:
        return float(str(a)) == float(str(b))
    except ValueError:
        return str(a).lower() == str(b).lower()


class ConfigurationReferenceTests(unittest.TestCase):
    def test_every_setting_in_env_example_is_documented_and_nothing_extra_is(self) -> None:
        self.assertEqual(sorted(example_settings()), sorted(documented_settings()))

    def test_documented_defaults_match_env_example(self) -> None:
        example = example_settings()
        for key, documented in documented_settings().items():
            with self.subTest(key):
                self.assertTrue(same(documented, example[key]), f"{key}: docs say {documented}, .env.example says {example[key]}")

    def test_documented_defaults_match_what_the_code_actually_uses(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            s = load_settings(Path(folder) / ".env", environ={})
        actual = {
            "LOG_LEVEL": s.log_level, "CAMERA_INDEX": s.camera.index, "CAMERA_WIDTH": s.camera.width,
            "CAMERA_HEIGHT": s.camera.height, "CAMERA_FPS": s.camera.fps, "CAMERA_MIRROR": str(s.camera.mirror).lower(),
            "CAMERA_BACKEND": s.camera.backend, "TRACKING_MAX_HANDS": s.tracking.max_hands,
            "TRACKING_MODEL_COMPLEXITY": s.tracking.model_complexity,
            "TRACKING_DETECTION_CONFIDENCE": s.tracking.detection_confidence,
            "TRACKING_MIN_CONFIDENCE": s.tracking.tracking_confidence, "SMOOTHING_ALPHA": s.control.smoothing_alpha,
            "SMOOTHING_DEAD_ZONE": s.control.dead_zone,
            "CONTROL_MIN_HANDEDNESS_CONFIDENCE": s.control.min_handedness_confidence,
            "CONTROL_TRACKING_TIMEOUT": s.control.tracking_timeout,
            "CONTROL_CONTINUITY_SECONDS": s.control.continuity_seconds,
            "CONTROL_CONTINUITY_RADIUS": s.control.continuity_radius, "CONTROL_HAND": s.sender.hand,
            "TRACKING_PLAYERS": s.tracking.players, "CONTROL_ZONE_HYSTERESIS": s.control.zone_hysteresis,
            "CONTROL_ZONE_EDGE_TRIM": s.control.zone_edge_trim,
            "GESTURE_DEBOUNCE_FRAMES": s.gestures.debounce_frames, "GESTURE_PINCH_RATIO": s.gestures.pinch_ratio,
            "GESTURE_EXTENDED_ANGLE": s.gestures.extended_angle, "GESTURE_CURLED_ANGLE": s.gestures.curled_angle,
            "GESTURE_REACH_RATIO": s.gestures.reach_ratio, "UDP_HOST": s.udp_host,
            "CV_TO_UNITY_PORT": s.cv_to_unity_port, "UNITY_TO_PYTHON_PORT": s.unity_to_python_port,
            "UDP_SEND_FPS": s.sender.fps, "MONGODB_URI": s.mongodb_uri, "MONGODB_DATABASE": s.mongodb_database,
            "LOG_DIR": s.log_dir.name,
        }
        documented = documented_settings()
        for key, value in actual.items():
            with self.subTest(key):
                self.assertTrue(same(documented[key], value), f"{key}: docs say {documented[key]}, code uses {value}")
        # Unity-only: its default lives in C#, so compare the two written sources.
        self.assertTrue(same(documented["UNITY_RECEIVE_TIMEOUT"], example_settings()["UNITY_RECEIVE_TIMEOUT"]))
        self.assertIn('["UNITY_RECEIVE_TIMEOUT"] = "0.5"', read(ROOT / "unity/MotionPlay/Assets/MotionPlay/Core/ReceiverConfiguration.cs"))

    def test_every_key_the_loader_reads_is_documented(self) -> None:
        loader = read(ROOT / "shared" / "config.py")
        read_keys = set(re.findall(r'"([A-Z][A-Z0-9_]{3,})"', loader[loader.index("def load_settings"):]))
        read_keys -= {"INFO", "DEBUG", "WARNING", "ERROR", "CRITICAL"}
        missing = sorted(k for k in read_keys if k.isupper() and "_" in k and k not in documented_settings())
        self.assertEqual([], missing, "settings the code reads but configuration.md does not document")


class WorkflowTests(unittest.TestCase):
    def test_the_ci_workflow_runs_the_same_checks_as_the_test_script(self) -> None:
        workflow = read(ROOT / ".github" / "workflows" / "tests.yml")
        script = read(ROOT / "scripts" / "test.ps1")
        for expected in ('python-version: "3.11"', "unittest discover -s tests", "MotionPlay.ReceiverHarness.csproj",
                         "tests.check_python_unity_udp", "requirements-dev.txt"):
            with self.subTest(expected):
                self.assertIn(expected, workflow)
        # What the local script runs and what CI runs must stay the same set of checks.
        for check in ("unittest", "MotionPlay.ReceiverHarness.csproj", "tests.check_python_unity_udp"):
            self.assertIn(check, script)


if __name__ == "__main__":
    unittest.main()
