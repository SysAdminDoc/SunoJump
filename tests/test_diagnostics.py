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


if __name__ == '__main__':
    unittest.main()
