#!/usr/bin/env python3
from __future__ import annotations

import struct
import unittest
from pathlib import Path

import sunojump

ROOT = Path(__file__).resolve().parents[1]


def png_info(path: Path) -> tuple[int, int, int]:
    header = path.read_bytes()[:26]
    if len(header) != 26 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"not a PNG: {path}")
    if header[12:16] != b"IHDR":
        raise AssertionError(f"missing IHDR: {path}")
    width, height = struct.unpack(">II", header[16:24])
    return width, height, header[25]


class MarketingAssetTests(unittest.TestCase):
    def test_repository_artwork_has_release_ready_dimensions(self):
        self.assertEqual(png_info(ROOT / "banner.png")[:2], (1536, 448))
        social = ROOT / ".github" / "social-preview.png"
        self.assertEqual(png_info(social)[:2], (1280, 640))
        self.assertLess(social.stat().st_size, 1_000_000)

    def test_mark_source_and_runtime_mark_preserve_alpha(self):
        source = png_info(ROOT / "assets" / "sunojump-mark-source.png")
        runtime = png_info(ROOT / "assets" / "sunojump-mark.png")
        self.assertEqual(source[2], 6)
        self.assertEqual(runtime, (1024, 1024, 6))

    def test_windows_icon_contains_small_and_large_sizes(self):
        payload = (ROOT / "assets" / "sunojump.ico").read_bytes()
        reserved, icon_type, count = struct.unpack("<HHH", payload[:6])
        self.assertEqual((reserved, icon_type), (0, 1))
        self.assertGreaterEqual(count, 7)
        sizes = set()
        for index in range(count):
            width, height = payload[6 + index * 16 : 8 + index * 16]
            sizes.add((width or 256, height or 256))
        self.assertIn((16, 16), sizes)
        self.assertIn((256, 256), sizes)

    def test_readme_uses_current_populated_screenshots(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in (
            "workspace.png",
            "pipeline.png",
            "evidence.png",
            "spectrogram-report.png",
        ):
            path = ROOT / "docs" / "screenshots" / name
            self.assertTrue(path.is_file(), name)
            self.assertIn(f"docs/screenshots/{name}", readme)
        self.assertIn(f"version-{sunojump.VERSION}", readme)

    def test_capture_tool_exposes_reproducible_scenes(self):
        source = (ROOT / "tools" / "capture_screenshot.py").read_text(encoding="utf-8")
        for scene in ("workspace", "pipeline", "evidence"):
            self.assertIn(f'"{scene}"', source)
        self.assertIn("TemporaryDirectory", source)

    def test_public_docs_avoid_dash_punctuation(self):
        for name in ("README.md", "CHANGELOG.md"):
            path = ROOT / name
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\N{EM DASH}", text, name)
            self.assertNotIn("\N{EN DASH}", text, name)
            self.assertNotIn(" - ", text, name)

    def test_repository_has_no_hosted_workflows(self):
        workflow_dir = ROOT / ".github" / "workflows"
        self.assertFalse(workflow_dir.exists() and any(workflow_dir.iterdir()))


if __name__ == "__main__":
    unittest.main()
