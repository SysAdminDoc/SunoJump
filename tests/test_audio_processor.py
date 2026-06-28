#!/usr/bin/env python3
import unittest

import numpy as np

from sunojump import AudioProcessor


class CoupledPitchTempoTests(unittest.TestCase):
    def _params(self):
        return {
            'pitch_enabled': True,
            'pitch_range': 0.8,
            'tempo_enabled': True,
            'tempo_range': 0.05,
        }

    def _sample_audio(self, sr=8000, seconds=3.0):
        t = np.arange(int(sr * seconds), dtype=np.float64) / sr
        left = 0.25 * np.sin(2.0 * np.pi * 220.0 * t)
        right = 0.25 * np.sin(2.0 * np.pi * 330.0 * t)
        return np.column_stack([left, right])

    def test_coupled_pitch_tempo_is_deterministic_and_length_stable(self):
        sr = 8000
        audio = self._sample_audio(sr=sr)

        proc_a = AudioProcessor(self._params(), seed=123)
        proc_b = AudioProcessor(self._params(), seed=123)

        out_a = proc_a._pitch_tempo_coupled_microvar(audio, sr)
        out_b = proc_b._pitch_tempo_coupled_microvar(audio, sr)

        self.assertEqual(out_a.shape, audio.shape)
        self.assertTrue(np.all(np.isfinite(out_a)))
        self.assertGreater(np.max(np.abs(out_a - audio)), 1e-6)
        np.testing.assert_allclose(out_a, out_b, rtol=0, atol=1e-12)

    def test_tempo_warp_keeps_chunk_boundaries_aligned(self):
        sr = 8000
        audio = self._sample_audio(sr=sr, seconds=1.0)
        proc = AudioProcessor(self._params(), seed=123)

        out = proc._tempo_warp_aligned_chunk(audio, 0.08)

        self.assertEqual(out.shape, audio.shape)
        np.testing.assert_allclose(out[0], audio[0], atol=1e-12)
        np.testing.assert_allclose(out[-1], audio[-1], atol=1e-9)


class SpectralBandTests(unittest.TestCase):
    def test_band_strength_falls_back_clamps_and_honors_enabled_flag(self):
        proc = AudioProcessor({
            'spectral_air_strength': 2.0,
            'spectral_presence_enabled': False,
        }, seed=123)

        self.assertEqual(proc._spectral_band_strength('spectral_air', 0.3), 1.0)
        self.assertEqual(proc._spectral_band_strength('spectral_presence', 0.7), 0.0)
        self.assertEqual(proc._spectral_band_strength('spectral_sub_bass', 0.4), 0.4)

    def test_air_band_perturbation_changes_output_with_other_bands_disabled(self):
        sr = 48000
        t = np.arange(sr, dtype=np.float64) / sr
        audio = (
            0.20 * np.sin(2.0 * np.pi * 60.0 * t)
            + 0.20 * np.sin(2.0 * np.pi * 300.0 * t)
            + 0.20 * np.sin(2.0 * np.pi * 4000.0 * t)
            + 0.20 * np.sin(2.0 * np.pi * 12000.0 * t)
        )
        params = {
            'spectral_sub_bass_enabled': False,
            'spectral_low_mids_enabled': False,
            'spectral_presence_enabled': False,
            'spectral_air_enabled': True,
            'spectral_air_strength': 1.0,
        }
        proc = AudioProcessor(params, seed=123)

        out = proc._spectral_perturb_ch(audio, sr, 0.0)

        self.assertEqual(out.shape, audio.shape)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertGreater(np.max(np.abs(out - audio)), 1e-6)


class WatermarkScanTests(unittest.TestCase):
    def test_scan_detects_stable_high_frequency_candidate(self):
        sr = 48000
        rng = np.random.default_rng(123)
        t = np.arange(sr * 2, dtype=np.float64) / sr
        tone = 0.45 * np.sin(2.0 * np.pi * 12000.0 * t)
        noise = rng.normal(0.0, 0.01, len(t))
        audio = np.column_stack([tone + noise, tone + noise])
        proc = AudioProcessor({'watermark_scan_enabled': True}, seed=123)

        candidates = proc._scan_watermark_bands(audio, sr)

        self.assertTrue(
            any(abs(c['center_hz'] - 12000.0) < 80.0 for c in candidates),
            candidates,
        )

    def test_detected_candidate_band_changes_spectral_output(self):
        sr = 48000
        t = np.arange(sr, dtype=np.float64) / sr
        audio = 0.35 * np.sin(2.0 * np.pi * 12000.0 * t)
        proc = AudioProcessor({
            'spectral_sub_bass_enabled': False,
            'spectral_low_mids_enabled': False,
            'spectral_presence_enabled': False,
            'spectral_air_enabled': False,
        }, seed=123)
        proc._watermark_candidates = [{
            'center_hz': 12000.0,
            'low_hz': 11800.0,
            'high_hz': 12200.0,
            'score': 12.0,
        }]

        out = proc._spectral_perturb_ch(audio, sr, 0.5)

        self.assertEqual(out.shape, audio.shape)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertGreater(np.max(np.abs(out - audio)), 1e-6)


class DynamicEqTests(unittest.TestCase):
    def test_dynamic_eq_preserves_loudness(self):
        sr = 16000
        t = np.arange(sr * 2, dtype=np.float64) / sr
        envelope = 0.6 + 0.3 * np.sin(2.0 * np.pi * 1.5 * t)
        left = envelope * (
            0.20 * np.sin(2.0 * np.pi * 140.0 * t)
            + 0.16 * np.sin(2.0 * np.pi * 1200.0 * t)
            + 0.12 * np.sin(2.0 * np.pi * 5200.0 * t)
        )
        right = envelope * (
            0.18 * np.sin(2.0 * np.pi * 220.0 * t)
            + 0.14 * np.sin(2.0 * np.pi * 2400.0 * t)
            + 0.10 * np.sin(2.0 * np.pi * 7000.0 * t)
        )
        audio = np.column_stack([left, right])
        proc = AudioProcessor({'dynamic_eq_amount': 0.8}, seed=123)

        before = proc._integrated_lufs(audio, sr)
        out = proc._dynamic_eq(audio, sr)
        after = proc._integrated_lufs(out, sr)

        self.assertEqual(out.shape, audio.shape)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertGreater(np.max(np.abs(out - audio)), 1e-6)
        self.assertLess(abs(before - after), 0.25)


if __name__ == '__main__':
    unittest.main()
