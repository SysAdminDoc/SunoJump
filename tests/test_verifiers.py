#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

import sunojump
from sunojump import AudioProcessor
import verifiers


class VerifierProtocolTests(unittest.TestCase):
    def test_result_to_dict_includes_required_fields(self):
        result = verifiers.VerifierResult(
            adapter="test", available=True,
            before_score=100.0, after_score=45.0, delta=55.0,
        )
        d = result.to_dict()
        self.assertEqual(d["adapter"], "test")
        self.assertTrue(d["available"])
        self.assertEqual(d["before_score"], 100.0)
        self.assertEqual(d["after_score"], 45.0)
        self.assertEqual(d["delta"], 55.0)

    def test_unavailable_result_has_error(self):
        result = verifiers.VerifierResult(
            adapter="missing", available=False, error="not installed",
        )
        d = result.to_dict()
        self.assertFalse(d["available"])
        self.assertIn("error", d)


class ConstellationVerifierTests(unittest.TestCase):
    def test_constellation_available_with_processor(self):
        proc = AudioProcessor({}, seed=1)
        v = verifiers.ConstellationVerifier(proc)
        self.assertTrue(v.is_available())

    def test_constellation_unavailable_without_processor(self):
        v = verifiers.ConstellationVerifier()
        self.assertFalse(v.is_available())

    def test_constellation_scores_identical_audio_high(self):
        sr = 8000
        t = np.arange(sr * 5, dtype=np.float64) / sr
        audio = 0.25 * np.sin(2.0 * np.pi * 440.0 * t)
        proc = AudioProcessor({}, seed=1)
        v = verifiers.ConstellationVerifier(proc)
        result = v.score(audio, audio, sr)
        self.assertTrue(result.available)
        self.assertIsNone(result.error)
        self.assertGreater(result.after_score, 80.0)

    def test_constellation_scores_different_audio_lower(self):
        sr = 8000
        t = np.arange(sr * 5, dtype=np.float64) / sr
        original = 0.25 * np.sin(2.0 * np.pi * 440.0 * t)
        processed = 0.25 * np.sin(2.0 * np.pi * 523.0 * t)
        proc = AudioProcessor({}, seed=1)
        v = verifiers.ConstellationVerifier(proc)
        result = v.score(original, processed, sr)
        self.assertTrue(result.available)
        self.assertLess(result.after_score, 80.0)


class DiscoveryTests(unittest.TestCase):
    def test_discover_includes_constellation(self):
        proc = AudioProcessor({}, seed=1)
        adapters = verifiers.discover_adapters(proc)
        names = [a.name for a in adapters]
        self.assertIn("constellation", names)

    def test_run_all_returns_results_for_all_adapters(self):
        proc = AudioProcessor({}, seed=1)
        adapters = verifiers.discover_adapters(proc)
        sr = 8000
        t = np.arange(sr * 3, dtype=np.float64) / sr
        audio = 0.25 * np.sin(2.0 * np.pi * 440.0 * t)
        results = verifiers.run_all(adapters, audio, audio, sr)
        self.assertEqual(len(results), len(adapters))
        for r in results:
            self.assertIsInstance(r, verifiers.VerifierResult)


if __name__ == "__main__":
    unittest.main()
