#!/usr/bin/env python3
import unittest
from unittest import mock

from tools.run_ab_regression import SCOPE_NOTICE, run_suite


class AbRegressionSuiteTests(unittest.TestCase):
    def test_generated_music_contract_reports_pairs_and_negative_control(self):
        with mock.patch.dict("os.environ", {"VISQOL_BINARY": ""}):
            report = run_suite()

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["scope"], SCOPE_NOTICE)
        self.assertIn("no external-platform inference", report["scope"])
        self.assertTrue(report["summary"]["passed"], report["summary"])
        self.assertEqual(report["summary"]["pair_count"], 2)
        self.assertEqual(
            {pair["sample_rate_hz"] for pair in report["pairs"]},
            {44100, 48000},
        )
        for pair in report["pairs"]:
            self.assertEqual(pair["channels"], 2)
            self.assertEqual(pair["license"], "CC0-1.0")
            self.assertIsInstance(pair["fixed_seed"], int)
            self.assertEqual(
                pair["quality"]["before"]["standard"],
                "ITU-R BS.1770-5",
            )
            self.assertEqual(len(pair["detectors"]), 2)
            for detector in pair["detectors"]:
                self.assertIn("coverage", detector)
                self.assertIn("offset_seconds", detector)
        self.assertEqual(len(report["negative_controls"]), 1)
        negative = report["negative_controls"][0]
        self.assertLess(negative["detectors"][0]["value"], 50.0)


if __name__ == "__main__":
    unittest.main()
