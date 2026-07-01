#!/usr/bin/env python3
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_audit_module():
    spec = importlib.util.spec_from_file_location(
        "audit_licenses", ROOT / "tools" / "audit_licenses.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LicenseAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_audit_module()

    def test_every_locked_package_is_reviewed(self):
        code, errors = self.mod.audit()
        self.assertEqual(code, 0, f"Unreviewed packages:\n" + "\n".join(errors))

    def test_reviewed_packages_cover_lock_file(self):
        locked = self.mod._parse_lock(self.mod.LOCK_FILE)
        locked_names = {
            self.mod._normalize(line.split("==")[0]) for line in locked
        }
        reviewed_names = set(self.mod.REVIEWED_PACKAGES.keys())
        missing = locked_names - reviewed_names
        self.assertFalse(missing, f"Locked packages missing review: {missing}")

    def test_copyleft_detection(self):
        self.assertTrue(self.mod._is_copyleft("GPL-3.0-only"))
        self.assertTrue(self.mod._is_copyleft("GPL-2.0-or-later"))
        self.assertTrue(self.mod._is_copyleft("LGPL-3.0"))
        self.assertTrue(self.mod._is_copyleft("AGPL-3.0-only"))
        self.assertFalse(self.mod._is_copyleft("MIT"))
        self.assertFalse(self.mod._is_copyleft("BSD-3-Clause"))
        self.assertFalse(self.mod._is_copyleft("Apache-2.0"))

    def test_inventory_write_produces_valid_json(self):
        with tempfile.TemporaryDirectory() as td:
            original = self.mod.INVENTORY_FILE
            try:
                self.mod.INVENTORY_FILE = pathlib.Path(td) / "inventory.json"
                self.mod.audit(write_inventory=True)
                data = json.loads(self.mod.INVENTORY_FILE.read_text(encoding="utf-8"))
                self.assertIsInstance(data, list)
                self.assertTrue(len(data) > 0)
                for entry in data:
                    self.assertIn("package", entry)
                    self.assertIn("version", entry)
                    self.assertIn("detected_license", entry)
                    self.assertIn("status", entry)
            finally:
                self.mod.INVENTORY_FILE = original

    def test_copyleft_packages_have_distribution_notes(self):
        for name, info in self.mod.REVIEWED_PACKAGES.items():
            if self.mod._is_copyleft(info["license"]):
                self.assertIn(
                    "note", info,
                    f"Copyleft package {name} ({info['license']}) "
                    "must have a distribution note",
                )


if __name__ == "__main__":
    unittest.main()
