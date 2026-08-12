#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from sunojump import AudioProcessor
import verifiers


ROOT = Path(__file__).resolve().parents[1]


class VerifierResultTests(unittest.TestCase):
    def test_measured_result_serializes_adapter_version_and_coverage(self):
        result = verifiers.VerifierResult(
            adapter="test.adapter",
            adapter_version="2.1",
            metric="overlap",
            state=verifiers.VerifierState.MEASURED,
            value=45.0,
            unit="percent",
            coverage={"compared_samples": 1000},
            offset_seconds=0.125,
        )
        payload = result.to_dict()
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["adapter"], "test.adapter")
        self.assertEqual(payload["adapter_version"], "2.1")
        self.assertEqual(payload["state"], "measured")
        self.assertEqual(payload["value"], 45.0)
        self.assertEqual(payload["coverage"]["compared_samples"], 1000)
        self.assertEqual(payload["offset_seconds"], 0.125)

    def test_unavailable_result_has_no_numeric_value(self):
        result = verifiers.VerifierResult(
            adapter="missing",
            adapter_version="1",
            metric="overlap",
            state=verifiers.VerifierState.UNAVAILABLE,
            reason="not_installed",
            coverage={"compared_samples": 0},
        )
        payload = result.to_dict()
        self.assertEqual(payload["state"], "unavailable")
        self.assertEqual(payload["reason"], "not_installed")
        self.assertNotIn("value", payload)
        self.assertNotIn("unit", payload)

    def test_error_result_has_no_numeric_value(self):
        result = verifiers.VerifierResult(
            adapter="broken",
            adapter_version="1",
            metric="overlap",
            state=verifiers.VerifierState.ERROR,
            reason="adapter_exception:RuntimeError",
        )
        payload = result.to_dict()
        self.assertEqual(payload["state"], "error")
        self.assertNotIn("value", payload)

    def test_invalid_state_value_combinations_fail_closed(self):
        with self.assertRaises(ValueError):
            verifiers.VerifierResult(
                adapter="test",
                adapter_version="1",
                metric="overlap",
                state=verifiers.VerifierState.UNAVAILABLE,
                value=0.0,
                reason="insufficient",
            )
        with self.assertRaises(ValueError):
            verifiers.VerifierResult(
                adapter="test",
                adapter_version="1",
                metric="overlap",
                state=verifiers.VerifierState.MEASURED,
                value=1.0,
                unit="percent",
            )


class ConstellationVerifierTests(unittest.TestCase):
    @staticmethod
    def _complex_audio(sr=8000, seconds=5):
        t = np.arange(sr * seconds, dtype=np.float64) / sr
        return (
            0.30 * np.sin(2.0 * np.pi * 440.0 * t)
            + 0.20 * np.sin(2.0 * np.pi * 880.0 * t)
            + 0.10 * np.sin(2.0 * np.pi * 1760.0 * t)
        )

    def test_constellation_available_with_processor(self):
        verifier = verifiers.ConstellationVerifier(AudioProcessor({}, seed=1))
        self.assertTrue(verifier.is_available())

    def test_constellation_unavailable_without_processor(self):
        audio = self._complex_audio()
        result = verifiers.ConstellationVerifier().score(audio, audio, 8000)
        self.assertEqual(result.state, verifiers.VerifierState.UNAVAILABLE)
        self.assertEqual(result.reason, "adapter_not_initialized")
        self.assertIsNone(result.value)

    def test_identical_audio_is_measured_with_high_overlap_and_coverage(self):
        audio = self._complex_audio()
        verifier = verifiers.ConstellationVerifier(AudioProcessor({}, seed=1))
        result = verifier.score(audio, audio, 8000)

        self.assertEqual(result.state, verifiers.VerifierState.MEASURED)
        self.assertGreater(result.value, 95.0)
        self.assertEqual(result.adapter, "sunojump.constellation")
        self.assertEqual(result.adapter_version, "1")
        self.assertEqual(result.coverage["compared_seconds"], 5.0)
        self.assertGreaterEqual(
            result.coverage["original_landmarks"],
            verifiers.MIN_CONSTELLATION_LANDMARKS,
        )
        self.assertIn("offset_seconds", result.to_dict())

    def test_different_audio_is_measured_with_lower_overlap(self):
        original = self._complex_audio()
        sr = 8000
        t = np.arange(sr * 5, dtype=np.float64) / sr
        processed = (
            0.30 * np.sin(2.0 * np.pi * 523.25 * t)
            + 0.20 * np.sin(2.0 * np.pi * 1046.5 * t)
            + 0.10 * np.sin(2.0 * np.pi * 2093.0 * t)
        )
        verifier = verifiers.ConstellationVerifier(AudioProcessor({}, seed=1))
        result = verifier.score(original, processed, sr)

        self.assertEqual(result.state, verifiers.VerifierState.MEASURED)
        self.assertLess(result.value, 50.0)

    def test_silence_is_unavailable_not_zero(self):
        audio = np.zeros(8000 * 5, dtype=np.float64)
        result = verifiers.ConstellationVerifier(
            AudioProcessor({}, seed=1)
        ).score(audio, audio, 8000)

        self.assertEqual(result.state, verifiers.VerifierState.UNAVAILABLE)
        self.assertEqual(result.reason, "near_silence")
        self.assertIsNone(result.value)
        self.assertNotIn("value", result.to_dict())

    def test_empty_audio_is_unavailable_not_zero(self):
        audio = np.array([], dtype=np.float64)
        result = verifiers.ConstellationVerifier(
            AudioProcessor({}, seed=1)
        ).score(audio, audio, 8000)

        self.assertEqual(result.state, verifiers.VerifierState.UNAVAILABLE)
        self.assertEqual(result.reason, "empty_input")
        self.assertIsNone(result.value)

    def test_near_silence_is_unavailable_not_zero(self):
        audio = np.full(8000 * 5, 1e-8, dtype=np.float64)
        result = verifiers.ConstellationVerifier(
            AudioProcessor({}, seed=1)
        ).score(audio, audio, 8000)

        self.assertEqual(result.state, verifiers.VerifierState.UNAVAILABLE)
        self.assertEqual(result.reason, "near_silence")
        self.assertIsNone(result.value)

    def test_short_simple_audio_is_unavailable_not_zero(self):
        sr = 8000
        t = np.arange(sr, dtype=np.float64) / sr
        audio = 0.25 * np.sin(2.0 * np.pi * 440.0 * t)
        result = verifiers.ConstellationVerifier(
            AudioProcessor({}, seed=1)
        ).score(audio, audio, sr)

        self.assertEqual(result.state, verifiers.VerifierState.UNAVAILABLE)
        self.assertEqual(result.reason, "input_too_short")
        self.assertIsNone(result.value)

    def test_insufficient_landmarks_are_unavailable_not_zero(self):
        processor = AudioProcessor({}, seed=1)
        processor._constellation_hashes = lambda *_args, **_kwargs: set()
        audio = self._complex_audio()
        result = verifiers.ConstellationVerifier(processor).score(
            audio,
            audio,
            8000,
        )

        self.assertEqual(result.state, verifiers.VerifierState.UNAVAILABLE)
        self.assertEqual(result.reason, "insufficient_landmarks")
        self.assertIsNone(result.value)

    def test_adapter_exception_is_error_not_zero(self):
        processor = AudioProcessor({}, seed=1)

        def fail(*_args, **_kwargs):
            raise RuntimeError("synthetic verifier failure")

        processor._constellation_hashes = fail
        audio = self._complex_audio()
        result = verifiers.ConstellationVerifier(processor).score(
            audio,
            audio,
            8000,
        )

        self.assertEqual(result.state, verifiers.VerifierState.ERROR)
        self.assertEqual(result.reason, "adapter_exception:RuntimeError")
        self.assertIsNone(result.value)

    def test_formatter_renders_each_state_without_inventing_zero(self):
        short = np.ones(8000, dtype=np.float64) * 0.1
        result = verifiers.ConstellationVerifier(
            AudioProcessor({}, seed=1)
        ).score(short, short, 8000)
        rendered = verifiers.format_verifier_result(result)
        self.assertIn("unavailable", rendered)
        self.assertIn("input_too_short", rendered)
        self.assertNotIn(": 0%", rendered)


class DiscoveryTests(unittest.TestCase):
    def test_discover_includes_constellation(self):
        adapters = verifiers.discover_adapters(AudioProcessor({}, seed=1))
        self.assertIn("sunojump.constellation", [adapter.name for adapter in adapters])

    def test_run_all_returns_typed_results_for_all_adapters(self):
        adapters = verifiers.discover_adapters(AudioProcessor({}, seed=1))
        audio = ConstellationVerifierTests._complex_audio(seconds=3)
        results = verifiers.run_all(adapters, audio, audio, 8000)
        self.assertEqual(len(results), len(adapters))
        for result in results:
            self.assertIsInstance(result, verifiers.VerifierResult)
            self.assertIsInstance(result.state, verifiers.VerifierState)


class CliVerifierIntegrationTests(unittest.TestCase):
    def test_cli_and_sidecar_report_the_same_unavailable_state(self):
        samplerate = 8000
        t = np.arange(samplerate, dtype=np.float64) / samplerate
        audio = 0.25 * np.sin(2.0 * np.pi * 440.0 * t)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "short.wav"
            output_dir = temp_path / "output"
            sf.write(input_path, audio, samplerate)
            result = subprocess.run(
                [
                    sys.executable,
                    "sunojump.py",
                    "-i",
                    str(input_path),
                    "-o",
                    str(output_dir),
                    "-p",
                    "gentle",
                    "--seed",
                    "7",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "sunojump.constellation v1]: unavailable "
                "(input_too_short; 1.00s",
                result.stdout,
            )
            sidecar = json.loads(
                (output_dir / "short_sj.sidecar.json").read_text(
                    encoding="utf-8"
                )
            )
        verifier = sidecar["verifiers"][0]
        self.assertEqual(verifier["state"], "unavailable")
        self.assertEqual(verifier["reason"], "input_too_short")
        self.assertNotIn("value", verifier)


if __name__ == "__main__":
    unittest.main()
