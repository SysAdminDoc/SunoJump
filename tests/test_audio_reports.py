#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import soundfile as sf

from audio_reports import render_spectrogram_comparison_png
from batch_manifest import BatchManifestStore
from render_results import RenderErrorCode, RenderState
import sunojump


ROOT = Path(__file__).resolve().parents[1]


class SpectrogramComparisonTests(unittest.TestCase):
    @staticmethod
    def _tone(frequency, sample_rate=8000):
        time_axis = np.arange(sample_rate, dtype=np.float64) / sample_rate
        return 0.25 * np.sin(2.0 * np.pi * frequency * time_axis)

    def test_png_is_deterministic_side_by_side_and_schema_versioned(self):
        before = self._tone(440.0)
        after = self._tone(880.0)

        first, metadata = render_spectrogram_comparison_png(
            before,
            after,
            8000,
            panel_width=128,
            panel_height=96,
        )
        second, _ = render_spectrogram_comparison_png(
            before,
            after,
            8000,
            panel_width=128,
            panel_height=96,
        )

        self.assertEqual(first, second)
        self.assertTrue(first.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = struct.unpack(">II", first[16:24])
        self.assertGreater(width, 128 * 2)
        self.assertGreater(height, 96)
        self.assertEqual(
            metadata["schema_id"],
            "com.sunojump.spectrogram-comparison",
        )
        self.assertEqual(metadata["schema_version"], 1)
        self.assertTrue(metadata["shared_color_scale"])

    def test_short_or_invalid_inputs_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "64 audio frames"):
            render_spectrogram_comparison_png(
                np.zeros(32),
                np.zeros(32),
                8000,
            )
        with self.assertRaisesRegex(ValueError, "at least 64 by 64"):
            render_spectrogram_comparison_png(
                self._tone(440.0),
                self._tone(880.0),
                8000,
                panel_width=32,
            )

    def test_processor_report_failure_is_typed_partial_with_usable_audio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "input.wav"
            output_path = temp_path / "output.wav"
            sf.write(input_path, self._tone(440.0), 8000)
            params = {
                **sunojump.PRESETS["Gentle"],
                "output_format": "wav",
            }
            with mock.patch.object(
                sunojump,
                "_write_binary_atomic_no_replace",
                side_effect=OSError("audit disk full"),
            ):
                result = sunojump.AudioProcessor(
                    params,
                    seed=7,
                    audit_options={"spectrogram": True},
                ).process(input_path, output_path)

            self.assertEqual(result.state, RenderState.PARTIAL)
            self.assertEqual(
                result.error_code,
                RenderErrorCode.AUDIT_ARTIFACT_FAILED,
            )
            self.assertTrue(output_path.is_file())
            self.assertTrue(Path(result.sidecar_path).is_file())
            self.assertEqual(result.artifacts, ())


class SpectrogramCliIntegrationTests(unittest.TestCase):
    def test_cli_exports_job_linked_png_and_manifest_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "input.wav"
            output_dir = temp_path / "output"
            sf.write(input_path, SpectrogramComparisonTests._tone(440.0), 8000)

            completed = subprocess.run(
                [
                    sys.executable,
                    "sunojump.py",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_dir),
                    "--preset",
                    "gentle",
                    "--seed",
                    "7",
                    "--spectrogram",
                    "--result-format",
                    "json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

            payload = json.loads(completed.stdout)
            artifact = payload["results"][0]["artifacts"][0]
            artifact_path = Path(artifact["path"])
            manifest = BatchManifestStore.load(payload["manifest_path"])

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(artifact["kind"], "spectrogram_comparison")
            self.assertEqual(artifact["media_type"], "image/png")
            self.assertTrue(artifact_path.is_file())
            self.assertTrue(
                artifact_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
            )
            self.assertTrue(manifest.audit_options["spectrogram"])
            self.assertEqual(
                manifest.jobs[0]["artifacts"][0]["sha256"],
                artifact["sha256"],
            )
            self.assertEqual(manifest.reconcile(), [])


if __name__ == "__main__":
    unittest.main()
