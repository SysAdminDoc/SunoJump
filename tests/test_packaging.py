#!/usr/bin/env python3
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class PythonVersionGuardTests(unittest.TestCase):
    def test_version_guard_rejects_below_3_11(self):
        lines = (ROOT / 'sunojump.py').read_text(encoding='utf-8').splitlines()
        joined = '\n'.join(lines[:15])
        self.assertIn('sys.version_info < (3, 11)', joined)
        self.assertIn('sys.exit(1)', joined)

    def test_readme_and_source_agree_on_python_floor(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertIn('Python 3.11+', readme)
        source = '\n'.join(
            (ROOT / 'sunojump.py').read_text(encoding='utf-8').splitlines()[:15]
        )
        self.assertIn('(3, 11)', source)


class FrozenBuildGuardTests(unittest.TestCase):
    def test_freeze_support_runs_before_application_imports(self):
        lines = (ROOT / 'sunojump.py').read_text(encoding='utf-8').splitlines()

        self.assertEqual(lines[3], 'import multiprocessing')
        self.assertEqual(lines[4], 'multiprocessing.freeze_support()')
        self.assertNotIn('def _bootstrap', '\n'.join(lines[:80]))
        self.assertNotIn('subprocess.check_call', '\n'.join(lines[:80]))

    def test_pyinstaller_spec_uses_freeze_support_runtime_hook(self):
        spec = (ROOT / 'SunoJump.spec').read_text(encoding='utf-8')
        hook = (ROOT / 'runtime_hooks' / 'freeze_support.py').read_text(encoding='utf-8')

        self.assertIn("runtime_hooks=['runtime_hooks/freeze_support.py']", spec)
        self.assertIn('multiprocessing.freeze_support()', hook)


if __name__ == '__main__':
    unittest.main()
