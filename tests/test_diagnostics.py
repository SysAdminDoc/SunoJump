#!/usr/bin/env python3
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path
import zipfile

import sunojump
from sunojump import (
    APP_NAME,
    VERSION,
    RunDiagnostics,
    export_support_bundle,
)


class RunDiagnosticsTests(unittest.TestCase):
    def test_header_records_reproducible_run_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'run.log'
            diag = RunDiagnostics(path=path)

            diag.write_header(
                'cli',
                ['input.wav'],
                'out',
                {'output_format': 'wav', 'spectral_enabled': True},
                preset_name='Gentle',
                seed=42,
            )
            diag.write('Output path: out/input_sj.wav')
            diag.write('Result: success')

            text = path.read_text(encoding='utf-8')

        self.assertIn(f"{APP_NAME} v{VERSION} run started", text)
        self.assertIn("Mode: cli", text)
        self.assertIn("Preset: Gentle", text)
        self.assertIn("Seed: 42", text)
        self.assertIn("Input 1: input.wav", text)
        self.assertIn("Output dir: out", text)
        self.assertIn("ffmpeg:", text)
        self.assertIn('"spectral_enabled": true', text)
        self.assertIn("Output path: out/input_sj.wav", text)
        self.assertIn("Result: success", text)

    def test_registered_absolute_paths_are_redacted_and_close_is_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            path = temp_path / "run.log"
            input_path = temp_path / "private" / "song.wav"
            output_dir = temp_path / "private-output"
            diag = RunDiagnostics(path=path)

            diag.write_header(
                "gui",
                [str(input_path)],
                str(output_dir),
                {"output_format": "wav"},
            )
            diag.write(f"Output path: {output_dir / 'song_sj.wav'}")
            diag.close()
            before = path.read_bytes()

            with self.assertRaises(RuntimeError):
                diag.write("must not recreate")

            self.assertEqual(path.read_bytes(), before)
            text = path.read_text(encoding="utf-8")

        self.assertTrue(diag.closed)
        self.assertNotIn(str(temp_path), text)
        self.assertIn("<input-1>", text)
        self.assertIn("<output-dir>", text)
        self.assertIn("Path redaction: enabled", text)

    def test_explicit_redaction_opt_out_is_labeled(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            path = temp_path / "raw.log"
            input_path = temp_path / "private.wav"
            diag = RunDiagnostics(path=path, redact=False)
            diag.write_header(
                "cli",
                [str(input_path)],
                str(temp_path),
                {},
            )
            diag.close()
            text = path.read_text(encoding="utf-8")

        self.assertIn(str(input_path), text)
        self.assertIn("disabled by explicit choice", text)


class PathRedactionTests(unittest.TestCase):
    def test_redact_replaces_home_path(self):
        from sunojump import _redact_home_paths
        home = str(Path.home())
        text = f"Input: {home}/music/song.wav"
        result = _redact_home_paths(text)
        self.assertNotIn(home, result)
        self.assertIn("~/music/song.wav", result)

    def test_redact_handles_no_home_in_text(self):
        from sunojump import _redact_home_paths
        text = "Input: /tmp/song.wav"
        result = _redact_home_paths(text)
        self.assertEqual(text, result)

    def test_retention_cap_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            old_fn = sunojump._diagnostics_dir
            sunojump._diagnostics_dir = lambda: log_dir
            try:
                for i in range(35):
                    (log_dir / f"run-{i:03d}.log").write_text(f"log {i}")
                self.assertEqual(len(list(log_dir.glob("*.log"))), 35)
                sunojump._enforce_log_retention(max_logs=30)
                remaining = list(log_dir.glob("*.log"))
                self.assertLessEqual(len(remaining), 30)
            finally:
                sunojump._diagnostics_dir = old_fn

    def test_retention_and_deletion_report_failures_and_remaining_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            keep = log_dir / "keep.log"
            remove = log_dir / "remove.log"
            keep.write_text("keep", encoding="utf-8")
            remove.write_text("remove", encoding="utf-8")
            original_unlink = Path.unlink

            def selective_unlink(path, *args, **kwargs):
                if path.name == "keep.log":
                    raise PermissionError("locked")
                return original_unlink(path, *args, **kwargs)

            with mock.patch.object(Path, "unlink", selective_unlink):
                report = sunojump._delete_diagnostic_logs(log_dir)

            self.assertEqual(report["found"], 2)
            self.assertEqual(report["deleted"], ["remove.log"])
            self.assertEqual(report["failures"][0]["name"], "keep.log")
            self.assertEqual(report["remaining"], ["keep.log"])

    def test_support_bundle_is_bounded_redacted_and_excludes_user_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            log_dir = temp_path / "logs"
            log_dir.mkdir()
            home = str(Path.home())
            raw_external = r"D:\Private Audio\song.wav"
            (log_dir / "run.log").write_text(
                f"Home input: {home}\\Music\\song.wav\n"
                f"External input: {raw_external}\n",
                encoding="utf-8",
            )
            output_dir = temp_path / "output"
            output_dir.mkdir()
            destination = temp_path / "support.zip"

            result = export_support_bundle(
                destination,
                redact=True,
                output_dir=output_dir,
                settings_location=temp_path / "settings.ini",
                log_dir=log_dir,
            )

            with zipfile.ZipFile(destination) as bundle:
                names = set(bundle.namelist())
                manifest = json.loads(
                    bundle.read("support-bundle.json")
                )
                archived = bundle.read("logs/001-run.log").decode("utf-8")
                locations = bundle.read("data-locations.txt").decode("utf-8")

        self.assertEqual(result["included_logs"], 1)
        self.assertTrue(result["redacted"])
        self.assertIn("environment.txt", names)
        self.assertEqual(manifest["schema_version"], 1)
        self.assertIn("audio files", manifest["excluded_by_design"])
        self.assertNotIn(home, archived)
        self.assertNotIn(raw_external, archived)
        self.assertNotIn(str(temp_path), locations)

    def test_support_bundle_never_replaces_existing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            destination = temp_path / "support.zip"
            destination.write_bytes(b"operator-owned")

            with self.assertRaises(FileExistsError):
                export_support_bundle(
                    destination,
                    output_dir=temp_path,
                    settings_location=temp_path / "settings.ini",
                    log_dir=temp_path / "logs",
                )

            self.assertEqual(destination.read_bytes(), b"operator-owned")

    def test_data_locations_identify_logs_history_sidecars_and_settings(self):
        lines = sunojump._diagnostic_data_locations(
            "C:/Output",
            "C:/Settings/session.ini",
            "C:/Temp/preview",
        )
        text = "\n".join(lines)
        self.assertIn("Run logs:", text)
        self.assertIn("GUI settings:", text)
        self.assertIn("Batch history/manifests:", text)
        self.assertIn(sunojump.BATCH_MANIFEST_SUFFIX, text)
        self.assertIn("Replay sidecars:", text)
        self.assertIn("Preview audio:", text)


if __name__ == '__main__':
    unittest.main()
