#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _requirements(path):
    return [
        line.strip()
        for line in path.read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.strip().startswith('#')
    ]


class DependencyAuditTests(unittest.TestCase):
    def test_release_lock_pins_runtime_dependency_closure(self):
        runtime_names = {
            line.split('>=', 1)[0].split('==', 1)[0].lower().replace('_', '-')
            for line in _requirements(ROOT / 'requirements.txt')
        }
        locked = _requirements(ROOT / 'requirements-lock.txt')
        locked_names = {
            line.split('==', 1)[0].lower().replace('_', '-')
            for line in locked
        }

        self.assertTrue(runtime_names.issubset(locked_names))
        self.assertIn('pyqt6-qt6', locked_names)
        self.assertIn('pyqt6-sip', locked_names)
        self.assertIn('cffi', locked_names)
        self.assertIn('pycparser', locked_names)
        self.assertTrue(all('==' in line and '>=' not in line for line in locked))

    def test_audit_tool_is_dev_only_and_uses_release_lock_without_resolution(self):
        runtime = '\n'.join(_requirements(ROOT / 'requirements.txt')).lower()
        dev = '\n'.join(_requirements(ROOT / 'requirements-dev.txt')).lower()
        self.assertNotIn('pip-audit', runtime)
        self.assertIn('pip-audit', dev)

        spec = importlib.util.spec_from_file_location(
            'audit_dependencies',
            ROOT / 'tools' / 'audit_dependencies.py',
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        command = module.audit_command()
        self.assertIn(str(ROOT / 'requirements-lock.txt'), command)
        self.assertIn('--no-deps', command)
        self.assertIn('--disable-pip', command)
        self.assertIn('--strict', command)


if __name__ == '__main__':
    unittest.main()
