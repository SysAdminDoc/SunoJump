#!/usr/bin/env python3
import json
from pathlib import Path
import unittest

import numpy as np

from tools.dsp_golden import render_golden


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tools" / "compatibility_baseline.json"


class DspGoldenTests(unittest.TestCase):
    def test_fixed_seed_pipeline_matches_compatibility_baseline(self):
        expected = json.loads(BASELINE.read_text(encoding="utf-8"))[
            "dsp_golden"
        ]
        rendered, actual = render_golden()

        self.assertEqual(actual, expected)
        self.assertEqual(rendered.shape, tuple(expected["shape"]))
        self.assertTrue(np.isfinite(rendered).all())

    def test_fixed_seed_pipeline_repeats_exact_quantized_signature(self):
        _first_audio, first = render_golden()
        _second_audio, second = render_golden()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
