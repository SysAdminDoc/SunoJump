#!/usr/bin/env python3
import importlib.util
import hashlib
import json
import pathlib
import unittest
from unittest import mock


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
        self.assertIn('--format', command)
        self.assertIn('json', command)

    def test_compatibility_baseline_pins_locks_native_runtime_and_rollback(self):
        baseline = json.loads(
            (ROOT / 'tools' / 'compatibility_baseline.json').read_text(
                encoding='utf-8'
            )
        )
        self.assertEqual(baseline['schema_version'], 1)
        for name in ('requirements-lock.txt', 'requirements-build-lock.txt'):
            actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            self.assertEqual(baseline['locks'][name]['sha256'], actual)
        self.assertEqual(
            baseline['target']['source_python_lanes'],
            ['3.11', '3.12'],
        )
        self.assertEqual(baseline['target']['release_python'], '3.12')
        self.assertIn('libsndfile', baseline['native_runtime'])
        self.assertIn('qt6', baseline['native_runtime'])
        rollback = baseline['rollback']
        self.assertRegex(rollback['git_commit'], r'^[0-9a-f]{40}$')
        self.assertTrue(rollback['version'])
        self.assertTrue(rollback['reason'])

    def test_report_separates_direct_transitive_build_native_and_security(self):
        spec = importlib.util.spec_from_file_location(
            'audit_dependencies_report',
            ROOT / 'tools' / 'audit_dependencies.py',
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        runtime = module.parse_lock(ROOT / 'requirements-lock.txt')
        build = module.parse_lock(ROOT / 'requirements-build-lock.txt')
        installed = {**runtime, **build}
        baseline = module.load_baseline()
        native = {
            'python': '3.12.0',
            'platform': 'test',
            'libsndfile': baseline['native_runtime']['libsndfile'],
            'qt6': baseline['native_runtime']['qt6'],
            'ffmpeg': 'unavailable (optional)',
        }
        golden = baseline['dsp_golden']
        with (
            mock.patch.object(module, 'installed_versions', return_value=installed),
            mock.patch.object(module, 'native_runtime_report', return_value=native),
            mock.patch.object(
                module,
                'security_report',
                return_value={
                    'status': 'clean',
                    'vulnerability_count': 0,
                    'vulnerabilities': [],
                    'message': '',
                },
            ),
            mock.patch.object(
                module,
                'golden_report',
                return_value={
                    'status': 'match',
                    'expected': golden,
                    'actual': golden,
                    'differences': {},
                },
            ),
        ):
            report = module.build_report()

        self.assertEqual(report['status'], 'pass')
        self.assertEqual(report['drift_count'], 0)
        direct_names = {
            item['name'] for item in report['direct_dependencies']
        }
        self.assertIn('numpy', direct_names)
        self.assertNotIn('cffi', direct_names)
        transitive_names = {
            item['name'] for item in report['transitive_dependencies']
        }
        self.assertIn('cffi', transitive_names)
        build_names = {
            item['name'] for item in report['build_dependencies']
        }
        self.assertIn('pyinstaller', build_names)
        self.assertEqual(report['native_drift'][0]['status'], 'match')
        self.assertEqual(report['security']['status'], 'clean')

    def test_mismatched_environment_is_reported_as_drift(self):
        spec = importlib.util.spec_from_file_location(
            'audit_dependencies_drift',
            ROOT / 'tools' / 'audit_dependencies.py',
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        records = module.package_records(
            {'numpy': '2.2.6', 'scipy': '1.17.1'},
            {'numpy': '9.9.9'},
        )
        self.assertEqual(records[0]['status'], 'mismatch')
        self.assertEqual(records[1]['status'], 'missing')


if __name__ == '__main__':
    unittest.main()
