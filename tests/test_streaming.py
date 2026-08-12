#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

import sunojump
from sunojump import AudioProcessor, RenderErrorCode, RenderState


class BoundedStreamingTests(unittest.TestCase):
    @staticmethod
    def _write_tone(path, *, samplerate=8000, seconds=11.0, stereo=True):
        t = np.arange(int(samplerate * seconds), dtype=np.float64) / samplerate
        left = 0.2 * np.sin(2.0 * np.pi * 220.0 * t)
        audio = left
        if stereo:
            right = 0.2 * np.sin(2.0 * np.pi * 330.0 * t)
            audio = np.column_stack([left, right])
        sf.write(path, audio, samplerate)
        return audio

    def test_streaming_processes_bounded_overlapping_chunks(self):
        calls = []
        logs = []
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.wav"
            output_path = Path(temp_dir) / "output.wav"
            source = self._write_tone(input_path)
            processor = AudioProcessor(
                {
                    "strip_metadata": False,
                    "spectral_enabled": True,
                    "spectral_scan_enabled": False,
                    "output_format": "wav",
                },
                log_fn=logs.append,
                seed=123,
                audit_options={
                    "spectrogram": True,
                    "loudness": True,
                    "signal_statistics": True,
                },
                streaming_threshold_bytes=1,
                streaming_chunk_seconds=4.0,
            )

            def bounded_transform(audio, _sample_rate):
                calls.append(audio.shape)
                return audio * 0.99

            processor._spectral_perturb = bounded_transform
            result = processor.process(input_path, output_path)

            self.assertEqual(result.state, RenderState.SUCCEEDED, logs)
            self.assertGreater(len(calls), 1)
            self.assertLessEqual(max(shape[0] for shape in calls), 4 * 8000)
            rendered, rate = sf.read(output_path, always_2d=True)
            self.assertEqual(rate, 8000)
            self.assertEqual(rendered.shape, source.shape)
            self.assertTrue(np.all(np.isfinite(rendered)))
            sidecar = json.loads(
                output_path.with_suffix(".sidecar.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(sidecar["schema_version"], 4)
            self.assertTrue(sidecar["streaming"]["enabled"])
            self.assertEqual(
                sidecar["decode"]["processing_strategy"],
                "bounded-overlap-memmap",
            )
            self.assertEqual(sidecar["streaming"]["chunk_samples"], 32000)
            self.assertEqual(
                result.artifacts[0]["kind"],
                "spectrogram_comparison",
            )
            self.assertTrue(Path(result.artifacts[0]["path"]).is_file())
            self.assertEqual(
                result.artifacts[1]["kind"],
                "loudness_comparison",
            )
            self.assertEqual(
                result.artifacts[2]["kind"],
                "signal_statistics_comparison",
            )

    def test_streaming_supports_mono_metadata_only_render(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.wav"
            output_path = Path(temp_dir) / "output.wav"
            source = self._write_tone(
                input_path,
                seconds=2.5,
                stereo=False,
            )
            result = AudioProcessor(
                {"strip_metadata": True, "output_format": "wav"},
                seed=123,
                streaming_threshold_bytes=1,
                streaming_chunk_seconds=2.0,
            ).process(input_path, output_path)

            self.assertEqual(result.state, RenderState.SUCCEEDED, result.message)
            rendered, rate = sf.read(output_path)
            self.assertEqual(rate, 8000)
            self.assertEqual(rendered.shape, source.shape)

    def test_streaming_pass_failure_leaves_no_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.wav"
            output_path = Path(temp_dir) / "output.wav"
            self._write_tone(input_path, seconds=3.0)
            processor = AudioProcessor(
                {
                    "strip_metadata": False,
                    "spectral_enabled": True,
                    "spectral_scan_enabled": False,
                },
                seed=123,
                streaming_threshold_bytes=1,
                streaming_chunk_seconds=2.0,
            )

            def fail_transform(_audio, _sample_rate):
                raise RuntimeError("synthetic streaming failure")

            processor._spectral_perturb = fail_transform
            result = processor.process(input_path, output_path)

            self.assertEqual(result.state, RenderState.FAILED)
            self.assertEqual(result.error_code, RenderErrorCode.PASS_FAILED)
            self.assertFalse(output_path.exists())
            self.assertFalse(output_path.with_suffix(".sidecar.json").exists())

    def test_streaming_configuration_is_validated(self):
        with self.assertRaisesRegex(ValueError, "threshold"):
            AudioProcessor({}, streaming_threshold_bytes=-1)
        with self.assertRaisesRegex(ValueError, "exceed overlap"):
            AudioProcessor({}, streaming_chunk_seconds=1.0)


if __name__ == "__main__":
    unittest.main()
