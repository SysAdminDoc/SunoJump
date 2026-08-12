#!/usr/bin/env python3
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from config_schema import (
    ConfigurationError,
    default_render_config,
    validate_render_config,
)
import sunojump


class ConfigurationSchemaTests(unittest.TestCase):
    def test_invalid_types_ranges_and_unknown_keys_fail_closed(self):
        invalid_values = (
            {"pitch_enabled": "false"},
            {"pitch_range": True},
            {"pitch_range": float("nan")},
            {"pitch_range": 5.1},
            {"typo_pitch_range": 1.0},
        )
        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ConfigurationError):
                    validate_render_config(
                        invalid,
                        base=default_render_config(),
                        require_complete=True,
                    )

    def test_c2pa_policy_is_optional_but_rejects_unknown_values(self):
        complete = validate_render_config(
            default_render_config(),
            require_complete=True,
        )
        self.assertNotIn("c2pa_policy", complete)
        with self.assertRaises(ConfigurationError):
            validate_render_config(
                {"c2pa_policy": "silently-drop"},
                base=default_render_config(),
                require_complete=True,
            )

    def test_partial_legacy_preset_migrates_then_fills_schema_defaults(self):
        document = sunojump._validate_preset_document({
            "name": "Legacy",
            "watermark_scan_enabled": False,
            "pitch_range": 1.25,
        })
        self.assertEqual(
            document["schema_version"],
            sunojump.PRESET_SCHEMA_VERSION,
        )
        self.assertFalse(document["params"]["spectral_scan_enabled"])
        self.assertEqual(document["params"]["pitch_range"], 1.25)
        self.assertEqual(
            set(document["params"]),
            set(default_render_config()),
        )

    def test_preset_envelope_rejects_bad_metadata_and_params(self):
        invalid_documents = (
            {"schema_version": "1", "params": {}},
            {"schema_version": 1, "name": 5, "params": {}},
            {
                "schema_version": 1,
                "params": {"strip_metadata": "false"},
            },
            {
                "schema_version": 1,
                "params": {"unknown_pass": True},
            },
            {
                "schema_version": sunojump.PRESET_SCHEMA_VERSION + 1,
                "params": {},
            },
        )
        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaises(ConfigurationError):
                    sunojump._validate_preset_document(document)

    def test_profile_composes_builtin_preset_with_sparse_overrides(self):
        document = sunojump._validate_profile_document({
            "name": "Gentle vocal edit",
            "schema_version": sunojump.PROFILE_SCHEMA_VERSION,
            "preset": "gentle",
            "overrides": {
                "pitch_range": 1.25,
                "stereo_enabled": True,
            },
        })

        self.assertEqual(document["name"], "Gentle vocal edit")
        self.assertEqual(document["preset"], "Gentle")
        self.assertEqual(document["params"]["pitch_range"], 1.25)
        self.assertTrue(document["params"]["stereo_enabled"])
        self.assertEqual(
            document["params"]["noise_level"],
            sunojump.PRESETS["Gentle"]["noise_level"],
        )

    def test_profile_rejects_ambiguous_or_unsafe_documents(self):
        valid = {
            "schema_version": sunojump.PROFILE_SCHEMA_VERSION,
            "preset": "moderate",
            "overrides": {},
        }
        invalid_documents = (
            [],
            {**valid, "schema_version": sunojump.PROFILE_SCHEMA_VERSION + 1},
            {**valid, "preset": "unknown"},
            {**valid, "overrides": []},
            {**valid, "unexpected": True},
            {**valid, "overrides": {"output_format": "flac"}},
            {**valid, "overrides": {"c2pa_policy": "allow-removal"}},
            {**valid, "overrides": {"pitch_range": 9.0}},
        )
        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaises(ConfigurationError):
                    sunojump._validate_profile_document(document)


class CliConfigurationTests(unittest.TestCase):
    VALUE_CASES = (
        ("--spectral", "0.4", "spectral_enabled"),
        ("--spectral-sub-bass", "0.4", "spectral_sub_bass_enabled"),
        ("--spectral-low-mids", "0.4", "spectral_low_mids_enabled"),
        ("--spectral-presence", "0.4", "spectral_presence_enabled"),
        ("--spectral-air", "0.4", "spectral_air_enabled"),
        ("--dynamic-eq", "0.4", "dynamic_eq_enabled"),
        ("--pitch", "1.2", "pitch_enabled"),
        ("--tempo", "0.08", "tempo_enabled"),
        ("--phase", "0.4", "phase_enabled"),
        ("--stereo", "0.2", "stereo_enabled"),
        ("--noise", "-42", "noise_enabled"),
        ("--dynamics", "0.4", "dynamics_enabled"),
        ("--humanize", "0.4", "humanize_enabled"),
        ("--reencode", "160", "reencode_enabled"),
    )

    def test_every_numeric_override_enables_its_pass(self):
        parser = sunojump._build_cli_parser()
        for flag, value, enabled_key in self.VALUE_CASES:
            with self.subTest(flag=flag):
                args = parser.parse_args(
                    ["--input", "unused.wav", flag, value]
                )
                config = sunojump._apply_cli_overrides(
                    sunojump.PRESETS["Gentle"],
                    args,
                )
                self.assertTrue(config[enabled_key])

    def test_profile_is_exclusive_and_cli_overrides_apply_last(self):
        parser = sunojump._build_cli_parser()
        args = parser.parse_args([
            "--input",
            "unused.wav",
            "--profile",
            "profile.json",
            "--pitch",
            "2.0",
        ])
        profile = sunojump._validate_profile_document({
            "schema_version": sunojump.PROFILE_SCHEMA_VERSION,
            "preset": "gentle",
            "overrides": {"pitch_range": 1.0},
        })

        config = sunojump._apply_cli_overrides(profile["params"], args)

        self.assertEqual(args.profile, "profile.json")
        self.assertEqual(config["pitch_range"], 2.0)
        self.assertTrue(config["pitch_enabled"])
        for conflicting in ("--preset", "--preset-file"):
            with self.subTest(conflicting=conflicting):
                values = [
                    "--input", "unused.wav",
                    "--profile", "profile.json",
                    conflicting,
                    "gentle" if conflicting == "--preset" else "preset.json",
                ]
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        parser.parse_args(values)

    def test_c2pa_policy_defaults_to_block_and_accepts_explicit_removal(self):
        parser = sunojump._build_cli_parser()
        blocked = parser.parse_args(["--input", "unused.wav"])
        allowed = parser.parse_args([
            "--input",
            "unused.wav",
            "--c2pa-policy",
            "allow-removal",
        ])
        self.assertEqual(blocked.c2pa_policy, "block")
        self.assertEqual(allowed.c2pa_policy, "allow-removal")

    def test_cli_diagnostic_privacy_and_retention_are_explicit(self):
        parser = sunojump._build_cli_parser()
        defaults = parser.parse_args(["--input", "unused.wav"])
        explicit = parser.parse_args([
            "--input",
            "unused.wav",
            "--diagnostic-retention",
            "12",
            "--no-redact-diagnostics",
        ])
        self.assertEqual(
            defaults.diagnostic_retention,
            sunojump.MAX_RETAINED_LOGS,
        )
        self.assertFalse(defaults.no_redact_diagnostics)
        self.assertEqual(explicit.diagnostic_retention, 12)
        self.assertTrue(explicit.no_redact_diagnostics)

        for invalid in ("0", "366", "not-a-number"):
            with self.subTest(invalid=invalid):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        parser.parse_args([
                            "--input",
                            "unused.wav",
                            "--diagnostic-retention",
                            invalid,
                        ])

    def test_cli_parallel_worker_count_is_bounded(self):
        parser = sunojump._build_cli_parser()
        defaults = parser.parse_args(["--input", "unused.wav"])
        explicit = parser.parse_args([
            "--input",
            "unused.wav",
            "--workers",
            "4",
        ])
        self.assertEqual(
            defaults.workers,
            sunojump.DEFAULT_PARALLEL_FILE_WORKERS,
        )
        self.assertEqual(explicit.workers, 4)
        for invalid in ("0", "9", "not-a-number"):
            with self.subTest(invalid=invalid):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        parser.parse_args([
                            "--input",
                            "unused.wav",
                            "--workers",
                            invalid,
                        ])

    def test_spectrogram_export_is_explicit(self):
        parser = sunojump._build_cli_parser()
        default = parser.parse_args(["--input", "unused.wav"])
        enabled = parser.parse_args([
            "--input", "unused.wav", "--spectrogram", "--loudness-report",
            "--signal-report",
        ])

        self.assertFalse(default.spectrogram)
        self.assertTrue(enabled.spectrogram)
        self.assertTrue(enabled.loudness_report)
        self.assertTrue(enabled.signal_report)
        self.assertEqual(
            sunojump._validated_audit_options({
                "spectrogram": True,
                "loudness": True,
                "signal_statistics": True,
            }),
            {
                "spectrogram": True,
                "loudness": True,
                "signal_statistics": True,
            },
        )
        for invalid in (
            {"spectrogram": "true"},
            {"loudness": "true"},
            {"signal_statistics": "true"},
            {"unknown": True},
            [],
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ConfigurationError):
                    sunojump._validated_audit_options(invalid)

    def test_cli_watch_mode_is_explicit_and_mutually_exclusive(self):
        parser = sunojump._build_cli_parser()
        args = parser.parse_args(["--watch", "incoming"])
        self.assertEqual(args.watch, "incoming")

        stderr = io.StringIO()
        argv = [
            "sunojump.py",
            "--watch",
            "incoming",
            "--input",
            "song.wav",
        ]
        with mock.patch.object(sys, "argv", argv):
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as context:
                    sunojump.cli_main()
        self.assertEqual(context.exception.code, 2)
        self.assertIn("cannot be combined", stderr.getvalue())

    def test_cli_refuses_c2pa_source_before_creating_output(self):
        manifest = b"\x00\x00\x00\x18jumbcli-policy"
        chunk = (
            b"C2PA"
            + struct.pack("<I", len(manifest))
            + manifest
            + (b"\x00" if len(manifest) & 1 else b"")
        )
        body = b"WAVE" + chunk
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "signed.wav"
            output_dir = temp_path / "output"
            input_path.write_bytes(
                b"RIFF" + struct.pack("<I", len(body)) + body
            )
            argv = [
                "sunojump.py",
                "--input",
                str(input_path),
                "--output",
                str(output_dir),
            ]
            with mock.patch.object(sys, "argv", argv):
                with redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as context:
                        sunojump.cli_main()

            self.assertFalse(output_dir.exists())

        self.assertEqual(context.exception.code, 2)
        self.assertIn(
            "--c2pa-policy allow-removal",
            stderr.getvalue(),
        )

    def test_explicit_pass_enable_disable_and_conflicts_are_unambiguous(self):
        parser = sunojump._build_cli_parser()
        args = parser.parse_args([
            "--input",
            "unused.wav",
            "--enable-pass",
            "metadata",
            "--disable-pass",
            "pitch",
        ])
        config = sunojump._apply_cli_overrides(
            sunojump.PRESETS["Extreme"],
            args,
        )
        self.assertTrue(config["strip_metadata"])
        self.assertFalse(config["pitch_enabled"])

        conflict = parser.parse_args([
            "--input",
            "unused.wav",
            "--pitch",
            "1.0",
            "--disable-pass",
            "pitch",
        ])
        with self.assertRaises(ConfigurationError):
            sunojump._apply_cli_overrides(
                sunojump.PRESETS["Moderate"],
                conflict,
            )

    def test_invalid_explicit_preset_exits_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            preset = temp_path / "invalid.json"
            preset.write_text(
                json.dumps({
                    "schema_version": sunojump.PRESET_SCHEMA_VERSION,
                    "params": {"pitch_enabled": "false"},
                }),
                encoding="utf-8",
            )
            output = temp_path / "must-not-exist"
            stderr = io.StringIO()
            argv = [
                "sunojump.py",
                "--input",
                str(temp_path / "missing.wav"),
                "--preset-file",
                str(preset),
                "--output",
                str(output),
            ]
            with mock.patch.object(sys, "argv", argv):
                with redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as context:
                        sunojump.cli_main()
        self.assertEqual(context.exception.code, 2)
        self.assertIn("invalid configuration", stderr.getvalue())
        self.assertFalse(output.exists())

    def test_invalid_explicit_profile_exits_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            profile = temp_path / "invalid-profile.json"
            profile.write_text(
                json.dumps({
                    "schema_version": sunojump.PROFILE_SCHEMA_VERSION,
                    "preset": "gentle",
                    "overrides": {"unknown_pass": True},
                }),
                encoding="utf-8",
            )
            output = temp_path / "must-not-exist"
            stderr = io.StringIO()
            argv = [
                "sunojump.py",
                "--input",
                str(temp_path / "missing.wav"),
                "--profile",
                str(profile),
                "--output",
                str(output),
            ]
            with mock.patch.object(sys, "argv", argv):
                with redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as context:
                        sunojump.cli_main()
        self.assertEqual(context.exception.code, 2)
        self.assertIn("invalid configuration in profile", stderr.getvalue())
        self.assertFalse(output.exists())

    def test_negative_seed_exits_before_input_or_output_access(self):
        stderr = io.StringIO()
        argv = [
            "sunojump.py",
            "--input",
            "missing.wav",
            "--seed",
            "-1",
        ]
        with mock.patch.object(sys, "argv", argv):
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as context:
                    sunojump.cli_main()

        self.assertEqual(context.exception.code, 2)
        self.assertIn("--seed must be a non-negative integer", stderr.getvalue())


class CustomSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _settings(path):
        settings = QSettings(str(path), QSettings.Format.IniFormat)
        settings.clear()
        return settings

    def test_custom_values_round_trip_through_qsettings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "session.ini"
            first_settings = self._settings(settings_path)
            first = sunojump.MainWindow(settings=first_settings)
            first.param_rows["pitch_range"].set_value(2.15)
            first.param_rows["pitch_range"].set_enabled_check(False)
            first.param_rows["noise_level"].set_value(-43.0)
            first.meta_check.setChecked(False)
            first.preview_offset_spin.setValue(123.5)
            first.worker_count_spin.setValue(3)
            self.assertEqual(first.preset_combo.currentText(), "Custom")
            expected = first._get_params()
            first._save_session_state()
            saved = json.loads(
                first_settings.value("session/config_json")
            )
            self.assertNotIn("c2pa_policy", saved["params"])
            first.close()

            second_settings = QSettings(
                str(settings_path),
                QSettings.Format.IniFormat,
            )
            second = sunojump.MainWindow(settings=second_settings)
            try:
                self.assertEqual(
                    second.preset_combo.currentText(),
                    "Custom",
                )
                self.assertEqual(second._get_params(), expected)
                self.assertEqual(second.preview_offset_spin.value(), 123.5)
                self.assertEqual(second.worker_count_spin.value(), 3)
            finally:
                second.close()

    def test_invalid_custom_session_falls_back_to_extreme(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir) / "session.ini")
            settings.setValue("session/preset", "Custom")
            settings.setValue(
                "session/config_json",
                json.dumps({
                    "schema_version": (
                        sunojump.PRESET_SCHEMA_VERSION + 1
                    ),
                    "params": {},
                }),
            )
            settings.sync()
            window = sunojump.MainWindow(settings=settings)
            try:
                self.assertEqual(
                    window.preset_combo.currentText(),
                    "Extreme",
                )
                self.assertIn(
                    "restored Extreme instead",
                    window.log_box.toPlainText(),
                )
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
