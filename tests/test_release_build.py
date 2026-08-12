#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "build_release",
        ROOT / "tools" / "build_release.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Completed:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class ReleaseBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_runtime_and_build_locks_are_fully_pinned_and_hashed(self):
        for lock_name in (
            "requirements-lock.txt",
            "requirements-build-lock.txt",
        ):
            entries = self.mod.parse_hashed_lock(ROOT / lock_name)
            self.assertGreater(len(entries), 0)
            self.assertEqual(
                len(entries),
                len({entry.normalized_name for entry in entries}),
            )
            for entry in entries:
                self.assertEqual(len(entry.sha256), 64)

    def test_release_source_requires_windows_accessibility_smoke(self):
        self.assertIn(
            pathlib.Path("tools/smoke_accessibility.ps1"),
            self.mod.REQUIRED_SOURCE_FILES,
        )

    def test_release_source_includes_c2pa_preflight_guard(self):
        self.assertIn(
            pathlib.Path("c2pa_provenance.py"),
            self.mod.REQUIRED_SOURCE_FILES,
        )

    def test_release_source_and_gate_include_compatibility_evidence(self):
        self.assertIn(
            pathlib.Path("tools/compatibility_baseline.json"),
            self.mod.REQUIRED_SOURCE_FILES,
        )
        self.assertIn(
            pathlib.Path("tools/dsp_golden.py"),
            self.mod.REQUIRED_SOURCE_FILES,
        )
        baseline = self.mod.load_compatibility_baseline()
        self.assertIn("rollback", baseline)
        self.assertIn("native_runtime", baseline)
        self.assertIn("dsp_golden", baseline)

    def test_native_runtime_must_match_compatibility_baseline(self):
        baseline = {
            "native_runtime": {
                "libsndfile": "1.2.2",
                "qt6": "6.11.1",
            }
        }
        self.mod.validate_native_compatibility(
            {"libsndfile": "1.2.2", "qt6": "6.11.1"},
            baseline,
        )
        with self.assertRaises(self.mod.ReleaseError):
            self.mod.validate_native_compatibility(
                {"libsndfile": "1.2.2", "qt6": "6.11.0"},
                baseline,
            )

    def test_unhashed_lock_entry_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock = pathlib.Path(temp_dir) / "bad-lock.txt"
            lock.write_text("example==1.0\n", encoding="utf-8")
            with self.assertRaises(self.mod.ReleaseError):
                self.mod.parse_hashed_lock(lock)

    def test_isolated_distribution_set_rejects_missing_or_ambient_packages(self):
        runtime = [self.mod.LockEntry("runtime", "1", "a" * 64)]
        build = [self.mod.LockEntry("builder", "2", "b" * 64)]
        installed = {
            "runtime": {"name": "runtime", "version": "1"},
            "builder": {"name": "builder", "version": "2"},
            "pip": {"name": "pip", "version": "1"},
        }
        self.mod.validate_installed_distributions(installed, runtime, build)
        installed["pytest"] = {"name": "pytest", "version": "9"}
        with self.assertRaises(self.mod.ReleaseError):
            self.mod.validate_installed_distributions(
                installed,
                runtime,
                build,
            )

    def test_sha256sums_produces_correct_digests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            artifact = temp_path / "test.bin"
            artifact.write_bytes(b"hello world")
            output = temp_path / "SHA256SUMS"
            self.mod.generate_sha256sums([artifact], output)
            content = output.read_text(encoding="utf-8")
            self.assertIn("test.bin", content)
            self.assertIn(
                self.mod.sha256_file(artifact),
                content,
            )

    def test_sha256sums_fails_when_any_artifact_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            with self.assertRaises(self.mod.ReleaseError):
                self.mod.generate_sha256sums(
                    [temp_path / "missing.exe"],
                    temp_path / "SHA256SUMS",
                )

    def test_release_copy_refuses_every_destination_except_dist(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            stage = temp_path / "stage"
            stage.mkdir()
            (stage / "artifact").write_bytes(b"release")
            with self.assertRaises(self.mod.ReleaseError):
                self.mod._copy_release_tree(stage, temp_path / "other")

    def test_missing_executable_fails_release_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(self.mod.ReleaseError):
                self.mod.verify_executable(
                    pathlib.Path(temp_dir) / "SunoJump.exe",
                    "1.2.3",
                    0,
                )

    def test_stale_executable_fails_before_smoke_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = pathlib.Path(temp_dir) / "SunoJump.exe"
            executable.write_bytes(b"x" * (1024 * 1024 + 1))
            old_time = time.time() - 60
            os.utime(executable, (old_time, old_time))
            with self.assertRaises(self.mod.ReleaseError) as context:
                self.mod.verify_executable(
                    executable,
                    "1.2.3",
                    time.time_ns(),
                )
        self.assertIn("stale", str(context.exception))

    def test_wrong_executable_version_fails_release_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = pathlib.Path(temp_dir) / "SunoJump.exe"
            executable.write_bytes(b"x" * (1024 * 1024 + 1))

            def wrong_version(_command, **_kwargs):
                return _Completed(stdout="SunoJump v0.0.0\n")

            with self.assertRaises(self.mod.ReleaseError) as context:
                self.mod.verify_executable(
                    executable,
                    "1.2.3",
                    0,
                    runner=wrong_version,
                )
        self.assertIn("wrong executable version", str(context.exception))

    def test_version_and_help_smoke_require_exact_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = pathlib.Path(temp_dir) / "SunoJump.exe"
            executable.write_bytes(b"x" * (1024 * 1024 + 1))

            def successful(command, **_kwargs):
                if command[-1] == "--version":
                    return _Completed(stdout="SunoJump v1.2.3\n")
                return _Completed(
                    stdout="usage: SunoJump [-h] [--native-runtime]\n"
                )

            result = self.mod.verify_executable(
                executable,
                "1.2.3",
                0,
                runner=successful,
            )
        self.assertEqual(result["version"], "SunoJump v1.2.3")
        self.assertTrue(result["help_has_usage"])

    def test_cyclonedx_output_uses_17_and_runtime_components(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = pathlib.Path(temp_dir) / "sbom.cdx.json"
            runtime = [self.mod.LockEntry("Example", "1.0", "a" * 64)]
            licenses = [{
                "package": "Example",
                "reviewed_license": "MIT",
                "distribution": "bundled",
                "source_url": "https://example.com/source",
            }]
            self.mod.generate_cyclonedx_sbom(
                output,
                "1.2.3",
                "b" * 64,
                runtime,
                licenses,
                {"example": []},
                {"libsndfile": "1.2.2"},
                "2026-07-29T00:00:00Z",
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["bomFormat"], "CycloneDX")
        self.assertEqual(payload["specVersion"], "1.7")
        self.assertEqual(payload["$schema"], self.mod.CYCLONEDX_SCHEMA)
        self.assertEqual(payload["components"][0]["name"], "Example")
        self.assertEqual(
            payload["components"][0]["hashes"][0]["content"],
            "a" * 64,
        )

    def test_forbidden_analysis_entry_is_rejected(self):
        payload = [None] * 20
        for index in (13, 14, 15, 18, 19):
            payload[index] = []
        payload[14] = [
            (
                "torch.nn",
                str(ROOT / "synthetic" / "torch" / "nn.py"),
                "PYMODULE",
            )
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            analysis = temp_path / "Analysis-00.toc"
            analysis.write_text(repr(tuple(payload)), encoding="utf-8")
            site_packages = temp_path / "site-packages"
            site_packages.mkdir()
            with self.assertRaises(self.mod.ReleaseError) as context:
                self.mod.build_artifact_inventory(
                    analysis,
                    site_packages,
                    self.mod.parse_hashed_lock(
                        ROOT / "requirements-lock.txt"
                    ),
                    self.mod.parse_hashed_lock(
                        ROOT / "requirements-build-lock.txt"
                    ),
                )
        self.assertIn("forbidden", str(context.exception))

    def test_spec_is_unsigned_console_capable_and_excludes_ambient_stacks(self):
        spec = (ROOT / "SunoJump.spec").read_text(encoding="utf-8")
        self.assertIn("console=True", spec)
        self.assertIn("hide_console='hide-early'", spec)
        self.assertIn("codesign_identity=None", spec)
        self.assertIn("upx=False", spec)
        for package in (
            "_distutils_hack",
            "pytest",
            "torch",
            "matplotlib",
            "pandas",
            "numba",
            "scipy._lib.array_api_compat.cupy",
            "scipy._lib.array_api_compat.torch",
            "setuptools",
        ):
            self.assertIn(f"'{package}'", spec)

    def test_release_script_uses_temporary_venv_and_hash_gate(self):
        source = (
            ROOT / "tools" / "build_release.py"
        ).read_text(encoding="utf-8")
        self.assertIn("TemporaryDirectory(prefix=\"sunojump-release-\")", source)
        self.assertIn('"--require-hashes"', source)
        self.assertIn('"--only-binary=:all:"', source)
        self.assertIn("smoke_gui_executable", source)
        self.assertNotIn("signtool", source.lower())


if __name__ == "__main__":
    unittest.main()
