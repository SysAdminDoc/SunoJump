#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

from sunojump import APP_NAME, VERSION, RunDiagnostics


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
        import sunojump
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


if __name__ == '__main__':
    unittest.main()
