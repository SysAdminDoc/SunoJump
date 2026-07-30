#!/usr/bin/env python3
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
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
            self.assertEqual(first.preset_combo.currentText(), "Custom")
            expected = first._get_params()
            first._save_session_state()
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
