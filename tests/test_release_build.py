#!/usr/bin/env python3
import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "build_release", ROOT / "tools" / "build_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_sha256sums_produces_correct_digests(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            artifact = td / "test.bin"
            artifact.write_bytes(b"hello world")
            output = td / "SHA256SUMS"
            self.mod.generate_sha256sums([artifact], output)
            content = output.read_text(encoding="utf-8")
            self.assertIn("test.bin", content)
            import hashlib
            expected = hashlib.sha256(b"hello world").hexdigest()
            self.assertIn(expected, content)

    def test_sbom_command_uses_cyclonedx_json(self):
        cmd = self.mod.sbom_command()
        self.assertIn("cyclonedx-json", cmd)
        self.assertIn("--no-deps", cmd)
        self.assertIn("--disable-pip", cmd)

    def test_sha256sums_skips_missing_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            output = td / "SHA256SUMS"
            self.mod.generate_sha256sums(
                [td / "nonexistent.exe"], output,
            )
            content = output.read_text(encoding="utf-8")
            self.assertEqual(content.strip(), "")


if __name__ == "__main__":
    unittest.main()
