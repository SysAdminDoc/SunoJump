#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from audio_quality import BS1770_VERSION, measure_bs1770
from verifiers import estimate_offset_seconds
from verifiers_visqol import Adapter as VisqolAudioVerifier


class Bs1770MeasurementTests(unittest.TestCase):
    def test_stereo_one_kilohertz_reference_has_expected_loudness_and_peak(self):
        sample_rate = 48000
        time_axis = np.arange(sample_rate * 3, dtype=np.float64) / sample_rate
        mono = 0.1 * np.sin(2.0 * np.pi * 1000.0 * time_axis)
        audio = np.column_stack((mono, mono))

        result = measure_bs1770(audio, sample_rate)

        self.assertEqual(result.standard, BS1770_VERSION)
        self.assertEqual(result.channels, 2)
        self.assertAlmostEqual(result.integrated_lufs, -20.0, delta=0.25)
        self.assertAlmostEqual(result.true_peak_dbtp, -20.0, delta=0.1)
        self.assertEqual(result.true_peak_oversample, 4)

    def test_silence_is_explicitly_unmeasurable(self):
        result = measure_bs1770(np.zeros((48000, 2)), 48000)

        self.assertIsNone(result.integrated_lufs)
        self.assertIsNone(result.true_peak_dbtp)

    def test_invalid_audio_fails_closed(self):
        with self.assertRaises(ValueError):
            measure_bs1770(np.array([0.0, np.nan]), 48000)
        with self.assertRaises(ValueError):
            measure_bs1770(np.zeros(10), 0)


class AlignmentOffsetTests(unittest.TestCase):
    def test_positive_offset_means_processed_audio_is_delayed(self):
        sample_rate = 8000
        delay_samples = int(0.25 * sample_rate)
        time_axis = np.arange(sample_rate * 4, dtype=np.float64) / sample_rate
        envelope = np.zeros_like(time_axis)
        envelope[sample_rate:sample_rate * 2] = np.linspace(
            0.1,
            1.0,
            sample_rate,
        )
        original = envelope * np.sin(2.0 * np.pi * 440.0 * time_axis)
        processed = np.concatenate((
            np.zeros(delay_samples),
            original[:-delay_samples],
        ))

        offset = estimate_offset_seconds(original, processed, sample_rate)

        self.assertAlmostEqual(offset, 0.25, delta=0.01)


class VisqolAdapterTests(unittest.TestCase):
    def test_missing_binary_is_typed_unavailable(self):
        adapter = VisqolAudioVerifier("definitely-not-a-visqol-command")
        audio = np.ones((44100 * 3, 2), dtype=np.float64) * 0.01

        result = adapter.score(audio, audio, 44100)

        self.assertEqual(result.state.value, "unavailable")
        self.assertEqual(result.reason, "visqol_binary_not_available")
        self.assertTrue(result.coverage["resampled"])

    def test_cli_adapter_resamples_to_48k_and_parses_mos_lqo(self):
        fake_source = """
import csv
import sys
import wave

def value(flag):
    return sys.argv[sys.argv.index(flag) + 1]

with wave.open(value('--reference_file'), 'rb') as handle:
    assert handle.getframerate() == 48000
with open(value('--results_csv'), 'w', newline='', encoding='utf-8') as handle:
    writer = csv.writer(handle)
    writer.writerow(['reference', 'degraded', 'moslqo'])
    writer.writerow(['reference.wav', 'degraded.wav', '4.25'])
""".lstrip()
        sample_rate = 44100
        time_axis = np.arange(sample_rate * 3, dtype=np.float64) / sample_rate
        tone = 0.1 * np.sin(2.0 * np.pi * 440.0 * time_axis)
        audio = np.column_stack((tone, tone))
        with tempfile.TemporaryDirectory() as temp_dir:
            binary = Path(temp_dir) / "fake_visqol.py"
            binary.write_text(fake_source, encoding="utf-8")
            result = VisqolAudioVerifier(binary).score(
                audio,
                audio,
                sample_rate,
            )

        self.assertEqual(result.state.value, "measured")
        self.assertEqual(result.value, 4.25)
        self.assertEqual(result.unit, "MOS-LQO")
        self.assertEqual(result.coverage["visqol_sample_rate_hz"], 48000)
        self.assertTrue(result.coverage["resampled"])


if __name__ == "__main__":
    unittest.main()
