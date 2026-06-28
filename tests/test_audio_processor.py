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


if __name__ == '__main__':
    unittest.main()
