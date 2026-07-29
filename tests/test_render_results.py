#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf
from PyQt6.QtCore import QCoreApplication

import sunojump
from render_results import (
    BatchResult,
    OutputValidation,
    RenderErrorCode,
    RenderResult,
    RenderState,
    format_batch_result,
    format_render_result,
)


ROOT = Path(__file__).resolve().parents[1]


def _validation(seed="a"):
    return OutputValidation(
        input_sha256=seed * 64,
        output_sha256="b" * 64,
        output_bytes=100,
        sample_rate_hz=8000,
        channels=1,
        frames=8000,
        duration_seconds=1.0,
        peak=0.25,
        hashes_distinct=True,
        decoder="soundfile",
    )


class RenderResultContractTests(unittest.TestCase):
    def test_success_requires_a_validated_output(self):
        with self.assertRaises(ValueError):
            RenderResult(
                state=RenderState.SUCCEEDED,
                input_path="input.wav",
                output_path="output.wav",
            )

    def test_failed_and_cancelled_results_cannot_expose_output(self):
        for state, code in (
            (RenderState.FAILED, RenderErrorCode.DECODE_FAILED),
            (RenderState.CANCELLED, RenderErrorCode.CANCELLED),
        ):
            with self.subTest(state=state):
                with self.assertRaises(ValueError):
                    RenderResult(
                        state=state,
                        input_path="input.wav",
                        output_path="output.wav",
                        error_code=code,
                        validation=_validation(),
                    )

    def test_partial_result_keeps_validated_output_and_error_code(self):
        result = RenderResult(
            state=RenderState.PARTIAL,
            input_path="input.wav",
            output_path="output.wav",
            error_code=RenderErrorCode.SIDECAR_WRITE_FAILED,
            validation=_validation(),
        )
        self.assertTrue(result)
        self.assertTrue(result.usable_output)
        self.assertEqual(
            result.to_dict()["error_code"],
            "sidecar_write_failed",
        )

    def test_batch_derives_all_terminal_states_and_error_counts(self):
        succeeded = RenderResult(
            state=RenderState.SUCCEEDED,
            input_path="good.wav",
            output_path="good_sj.wav",
            validation=_validation(),
        )
        failed = RenderResult(
            state=RenderState.FAILED,
            input_path="bad.wav",
            error_code=RenderErrorCode.DECODE_FAILED,
        )
        partial = BatchResult.from_results([succeeded, failed], 1.25)
        self.assertEqual(partial.state, RenderState.PARTIAL)
        self.assertEqual(partial.counts["succeeded"], 1)
        self.assertEqual(partial.counts["failed"], 1)
        self.assertEqual(partial.error_counts, {"decode_failed": 1})
        self.assertIn("Batch partial", format_batch_result(partial))

        cancelled = RenderResult(
            state=RenderState.CANCELLED,
            input_path="later.wav",
            error_code=RenderErrorCode.CANCELLED,
        )
        self.assertEqual(
            BatchResult.from_results([succeeded, cancelled], 2.0).state,
            RenderState.CANCELLED,
        )
        self.assertEqual(
            BatchResult.from_results([failed], 2.0).state,
            RenderState.FAILED,
        )
        self.assertEqual(
            BatchResult.from_results([succeeded], 2.0).state,
            RenderState.SUCCEEDED,
        )

    def test_format_includes_state_code_and_output_hash(self):
        result = RenderResult(
            state=RenderState.PARTIAL,
            input_path="input.wav",
            output_path="output.wav",
            error_code=RenderErrorCode.SIDECAR_WRITE_FAILED,
            message="sidecar unavailable",
            validation=_validation(),
        )
        rendered = format_render_result(result)
        self.assertIn("Result: partial [sidecar_write_failed]", rendered)
        self.assertIn("sha256:bbbbbbbbbbbb", rendered)


class OutputValidationTests(unittest.TestCase):
    @staticmethod
    def _tone(sr=8000, seconds=1.0, amplitude=0.25):
        t = np.arange(int(sr * seconds), dtype=np.float64) / sr
        return amplitude * np.sin(2.0 * np.pi * 440.0 * t)

    def test_valid_output_records_decode_shape_peak_and_hashes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "input.wav"
            encoded_path = temp_path / ".output.tmp.wav"
            output_path = temp_path / "output.wav"
            sf.write(input_path, self._tone(amplitude=0.20), 8000)
            sf.write(
                encoded_path,
                self._tone(amplitude=0.25),
                8000,
                subtype="PCM_24",
            )

            result = sunojump._validate_render_output(
                input_path,
                encoded_path,
                output_path,
                "wav",
                8000,
                1,
                8000,
            )

        self.assertEqual(result.sample_rate_hz, 8000)
        self.assertEqual(result.channels, 1)
        self.assertEqual(result.frames, 8000)
        self.assertGreater(result.peak, 0.24)
        self.assertTrue(result.hashes_distinct)
        self.assertEqual(len(result.output_sha256), 64)

    def test_same_input_and_output_path_is_rejected_before_render(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.wav"
            sf.write(input_path, self._tone(), 8000)
            before = input_path.read_bytes()

            result = sunojump.AudioProcessor(
                {"strip_metadata": True},
                seed=1,
            ).process(str(input_path), str(input_path))

            self.assertEqual(result.state, RenderState.FAILED)
            self.assertEqual(
                result.error_code,
                RenderErrorCode.OUTPUT_MAPPING_INVALID,
            )
            self.assertEqual(input_path.read_bytes(), before)

    def test_hardlink_alias_cannot_be_used_as_the_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "input.wav"
            alias_path = temp_path / "alias.wav"
            sf.write(input_path, self._tone(), 8000)
            os.link(input_path, alias_path)

            result = sunojump.AudioProcessor(
                {"strip_metadata": True},
                seed=1,
            ).process(str(input_path), str(alias_path))

            self.assertEqual(result.state, RenderState.FAILED)
            self.assertEqual(
                result.error_code,
                RenderErrorCode.OUTPUT_MAPPING_INVALID,
            )

    def test_corrupt_encoded_output_is_not_promoted(self):
        old_write = sunojump.sf.write

        def corrupt_write(path, *_args, **_kwargs):
            Path(path).write_bytes(b"not audio")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "input.wav"
            output_path = temp_path / "output.wav"
            old_write(input_path, self._tone(), 8000)
            sunojump.sf.write = corrupt_write
            try:
                result = sunojump.AudioProcessor(
                    {"strip_metadata": True},
                    seed=1,
                ).process(str(input_path), str(output_path))
            finally:
                sunojump.sf.write = old_write

            self.assertEqual(result.state, RenderState.FAILED)
            self.assertEqual(
                result.error_code,
                RenderErrorCode.OUTPUT_DECODE_FAILED,
            )
            self.assertFalse(output_path.exists())
            self.assertEqual(
                [path.name for path in temp_path.iterdir()],
                ["input.wav"],
            )

    def test_silent_output_is_not_promoted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "input.wav"
            output_path = temp_path / "output.wav"
            sf.write(input_path, np.zeros(8000, dtype=np.float64), 8000)

            result = sunojump.AudioProcessor(
                {"strip_metadata": True},
                seed=1,
            ).process(str(input_path), str(output_path))

            self.assertEqual(result.state, RenderState.FAILED)
            self.assertEqual(result.error_code, RenderErrorCode.OUTPUT_SILENT)
            self.assertFalse(output_path.exists())

    def test_nonfinite_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "input.wav"
            encoded_path = temp_path / ".output.tmp.wav"
            output_path = temp_path / "output.wav"
            sf.write(input_path, self._tone(), 8000)
            invalid = self._tone()
            invalid[100] = np.nan
            sf.write(
                encoded_path,
                invalid,
                8000,
                format="WAV",
                subtype="FLOAT",
            )

            with self.assertRaises(sunojump.OutputValidationError) as context:
                sunojump._validate_render_output(
                    input_path,
                    encoded_path,
                    output_path,
                    "wav",
                    8000,
                    1,
                    8000,
                )

        self.assertEqual(
            context.exception.code,
            RenderErrorCode.OUTPUT_NONFINITE,
        )

    def test_rate_channel_and_duration_mismatches_have_distinct_codes(self):
        cases = (
            (16000, 1, 8000, RenderErrorCode.OUTPUT_SAMPLE_RATE_MISMATCH),
            (8000, 2, 8000, RenderErrorCode.OUTPUT_CHANNEL_MISMATCH),
            (8000, 1, 4000, RenderErrorCode.OUTPUT_DURATION_MISMATCH),
        )
        for actual_sr, actual_channels, frames, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    input_path = temp_path / "input.wav"
                    encoded_path = temp_path / ".output.tmp.wav"
                    output_path = temp_path / "output.wav"
                    sf.write(input_path, self._tone(), 8000)
                    audio = self._tone(
                        sr=actual_sr,
                        seconds=frames / actual_sr,
                    )
                    if actual_channels == 2:
                        audio = np.column_stack([audio, audio])
                    sf.write(encoded_path, audio, actual_sr)

                    with self.assertRaises(
                        sunojump.OutputValidationError
                    ) as context:
                        sunojump._validate_render_output(
                            input_path,
                            encoded_path,
                            output_path,
                            "wav",
                            8000,
                            1,
                            8000,
                        )
                self.assertEqual(context.exception.code, expected_code)

    def test_sidecar_failure_is_partial_and_never_reports_full_progress(self):
        progress = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "input.wav"
            output_path = temp_path / "output.wav"
            sf.write(input_path, self._tone(), 8000)
            processor = sunojump.AudioProcessor(
                {"strip_metadata": True},
                progress_fn=progress.append,
                seed=1,
            )
            processor._write_sidecar = lambda *_args, **_kwargs: False

            result = processor.process(str(input_path), str(output_path))

            self.assertEqual(result.state, RenderState.PARTIAL)
            self.assertEqual(
                result.error_code,
                RenderErrorCode.SIDECAR_WRITE_FAILED,
            )
            self.assertTrue(output_path.is_file())
            self.assertNotIn(100, progress)

    def test_ffmpeg_outputs_are_decoded_and_validated_before_promotion(self):
        if not sunojump._check_ffmpeg():
            self.skipTest("ffmpeg is not installed")
        available = [
            fmt
            for fmt in ("mp3", "m4a")
            if sunojump._ffmpeg_encoder_available(fmt)
        ]
        if not available:
            self.skipTest("ffmpeg has no supported SunoJump output encoder")
        for fmt in available:
            with self.subTest(fmt=fmt):
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    input_path = temp_path / "input.wav"
                    output_path = temp_path / f"output.{fmt}"
                    sf.write(input_path, self._tone(), 8000)

                    result = sunojump.AudioProcessor(
                        {
                            "strip_metadata": True,
                            "output_format": fmt,
                        },
                        seed=1,
                    ).process(str(input_path), str(output_path))

                    self.assertEqual(
                        result.state,
                        RenderState.SUCCEEDED,
                        result,
                    )
                    self.assertTrue(output_path.is_file())
                    self.assertEqual(
                        result.validation.decoder,
                        "ffmpeg+soundfile",
                    )
                    self.assertEqual(result.validation.sample_rate_hz, 8000)
                    self.assertEqual(result.validation.channels, 1)


class ProcessWorkerOutcomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_setup_failure_finishes_every_job_as_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "not-a-directory"
            output_path.write_text("occupied", encoding="utf-8")
            worker = sunojump.ProcessWorker(
                ["one.wav", "two.wav"],
                {"output_format": "wav"},
                str(output_path),
            )
            jobs = []
            batches = []
            worker.file_done.connect(lambda idx, result: jobs.append((idx, result)))
            worker.all_done.connect(batches.append)

            worker.run()

        self.assertEqual(len(jobs), 2)
        self.assertTrue(
            all(
                result.error_code is RenderErrorCode.OUTPUT_DIR_UNAVAILABLE
                for _, result in jobs
            )
        )
        self.assertEqual(batches[0].state, RenderState.FAILED)
        self.assertEqual(batches[0].counts["failed"], 2)

    def test_cancel_before_start_finishes_every_job_as_cancelled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = sunojump.ProcessWorker(
                ["one.wav", "two.wav"],
                {"output_format": "wav"},
                temp_dir,
            )
            jobs = []
            batches = []
            progress = []
            worker.file_done.connect(lambda idx, result: jobs.append((idx, result)))
            worker.all_done.connect(batches.append)
            worker.progress_signal.connect(progress.append)
            worker.cancel()

            worker.run()

        self.assertEqual(len(jobs), 2)
        self.assertTrue(
            all(result.state is RenderState.CANCELLED for _, result in jobs)
        )
        self.assertEqual(batches[0].state, RenderState.CANCELLED)
        self.assertNotIn(100, progress)

    def test_mixed_worker_results_are_partial_and_progress_is_below_100(self):
        original_processor = sunojump.AudioProcessor

        class FakeProcessor:
            def __init__(self, _params, progress_fn=None, **_kwargs):
                self.progress = progress_fn

            def process(self, input_path, output_path):
                self.progress(100)
                if Path(input_path).stem == "good":
                    return RenderResult(
                        state=RenderState.SUCCEEDED,
                        input_path=str(input_path),
                        output_path=str(output_path),
                        validation=_validation(),
                    )
                return RenderResult(
                    state=RenderState.FAILED,
                    input_path=str(input_path),
                    error_code=RenderErrorCode.DECODE_FAILED,
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            worker = sunojump.ProcessWorker(
                ["good.wav", "bad.wav"],
                {"output_format": "wav"},
                temp_dir,
            )
            jobs = []
            batches = []
            progress = []
            worker.file_done.connect(lambda idx, result: jobs.append((idx, result)))
            worker.all_done.connect(batches.append)
            worker.progress_signal.connect(progress.append)
            sunojump.AudioProcessor = FakeProcessor
            try:
                worker.run()
            finally:
                sunojump.AudioProcessor = original_processor

        self.assertEqual([result.state for _, result in jobs], [
            RenderState.SUCCEEDED,
            RenderState.FAILED,
        ])
        self.assertEqual(batches[0].state, RenderState.PARTIAL)
        self.assertEqual(batches[0].error_counts, {"decode_failed": 1})
        self.assertLess(max(progress), 100)


class CliOutcomeIntegrationTests(unittest.TestCase):
    @staticmethod
    def _tone(sr=8000):
        t = np.arange(sr, dtype=np.float64) / sr
        return 0.25 * np.sin(2.0 * np.pi * 440.0 * t)

    def test_mixed_cli_batch_returns_partial_exit_code_and_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "output"
            input_dir.mkdir()
            sf.write(input_dir / "good.wav", self._tone(), 8000)
            (input_dir / "bad.wav").write_bytes(b"not audio")

            result = subprocess.run(
                [
                    sys.executable,
                    "sunojump.py",
                    "-i",
                    str(input_dir),
                    "-o",
                    str(output_dir),
                    "-p",
                    "gentle",
                    "--seed",
                    "3",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Result: succeeded", result.stdout)
        self.assertIn("Result: failed [invalid_input]", result.stdout)
        self.assertIn("Batch partial:", result.stdout)
        self.assertIn("1 succeeded", result.stdout)
        self.assertIn("1 failed", result.stdout)

    def test_all_failed_cli_batch_returns_exit_code_two(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "output"
            input_dir.mkdir()
            (input_dir / "bad.wav").write_bytes(b"not audio")

            result = subprocess.run(
                [
                    sys.executable,
                    "sunojump.py",
                    "-i",
                    str(input_dir),
                    "-o",
                    str(output_dir),
                    "-p",
                    "gentle",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("Batch failed:", result.stdout)
        self.assertIn("invalid_input=1", result.stdout)


if __name__ == "__main__":
    unittest.main()
