#!/usr/bin/env python3
import subprocess
import sys
import unittest
from pathlib import Path

import sunojump


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_CLAIMS = (
    "audio fingerprint masking",
    "bypass",
    "detection signature",
    "field-tested",
    "highly likely effective",
    "likely effective",
    "re-upload",
    "success rate",
)


class ProductContractTests(unittest.TestCase):
    def test_source_and_readme_do_not_publish_outcome_claims(self):
        surfaces = {
            "README.md": (ROOT / "README.md").read_text(encoding="utf-8"),
            "sunojump.py": (ROOT / "sunojump.py").read_text(encoding="utf-8"),
        }
        for surface, text in surfaces.items():
            lowered = text.lower()
            for claim in FORBIDDEN_CLAIMS:
                self.assertNotIn(claim, lowered, f"{surface}: {claim}")

    def test_readme_states_rights_scope_metrics_and_product_boundary(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("audio you own or are authorized to modify", readme)
        self.assertIn("does not predict or guarantee any platform", readme)
        self.assertIn("does not upload or resubmit audio", readme)
        self.assertIn("sunojump.signal_change v1", readme)

    def test_contract_is_machine_readable(self):
        self.assertEqual(
            sunojump.EVIDENCE_CONTRACT["rights_scope"],
            "owned-or-authorized-audio-only",
        )
        self.assertFalse(
            sunojump.EVIDENCE_CONTRACT["platform_outcome_guaranteed"]
        )
        self.assertFalse(
            sunojump.EVIDENCE_CONTRACT["upload_or_resubmission_automation"]
        )
        self.assertEqual(
            sunojump.SIGNAL_CHANGE_METRIC["adapter"],
            "sunojump.signal_change",
        )
        self.assertEqual(sunojump.SIGNAL_CHANGE_METRIC["version"], "1")
        self.assertFalse(hasattr(sunojump.AudioProcessor, "_compute_detection_risk"))

    def test_cli_help_states_scope_without_outcome_claims(self):
        result = subprocess.run(
            [sys.executable, "sunojump.py", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lowered = result.stdout.lower()
        self.assertIn("rights-owned audio variation", lowered)
        self.assertIn("do not predict or", lowered)
        self.assertIn("guarantee any platform", lowered)
        for claim in FORBIDDEN_CLAIMS:
            self.assertNotIn(claim, lowered)


if __name__ == "__main__":
    unittest.main()
