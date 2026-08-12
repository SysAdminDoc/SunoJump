#!/usr/bin/env python3
"""Report release-lock, native-runtime, DSP, and security compatibility."""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_LOCK = ROOT / "requirements-lock.txt"
BUILD_LOCK = ROOT / "requirements-build-lock.txt"
DIRECT_REQUIREMENTS = ROOT / "requirements.txt"
BASELINE_FILE = ROOT / "tools" / "compatibility_baseline.json"
LOCK_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^\s]+)"
)
REQUIREMENT_NAME_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)")


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_lock(path: Path) -> dict[str, str]:
    packages = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        match = LOCK_PATTERN.match(raw_line.strip())
        if not match:
            continue
        name = normalize_name(match.group("name"))
        if name in packages:
            raise ValueError(f"duplicate package in {path.name}: {name}")
        packages[name] = match.group("version")
    if not packages:
        raise ValueError(f"no pinned packages found in {path}")
    return packages


def direct_requirement_names(path: Path = DIRECT_REQUIREMENTS) -> set[str]:
    names = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = REQUIREMENT_NAME_PATTERN.match(line)
        if match:
            names.add(normalize_name(match.group(1)))
    return names


def installed_versions() -> dict[str, str]:
    installed = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            installed[normalize_name(name)] = distribution.version
    return installed


def package_records(
    expected: dict[str, str],
    installed: dict[str, str],
) -> list[dict]:
    records = []
    for name, expected_version in sorted(expected.items()):
        actual = installed.get(name)
        status = (
            "missing"
            if actual is None
            else "match"
            if actual == expected_version
            else "mismatch"
        )
        records.append({
            "name": name,
            "expected": expected_version,
            "installed": actual,
            "status": status,
        })
    return records


def audit_command(extra_args: list[str] | None = None) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pip_audit",
        "--requirement",
        str(RUNTIME_LOCK),
        "--no-deps",
        "--disable-pip",
        "--strict",
        "--progress-spinner",
        "off",
        "--format",
        "json",
    ]
    if extra_args:
        command.extend(extra_args)
    return command


def security_report() -> dict:
    try:
        import pip_audit  # noqa: F401
    except ImportError:
        return {
            "status": "error",
            "vulnerability_count": None,
            "vulnerabilities": [],
            "message": (
                "pip-audit is required; install requirements-dev.txt"
            ),
        }
    completed = subprocess.run(
        audit_command(),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "vulnerability_count": None,
            "vulnerabilities": [],
            "message": (
                completed.stderr.strip()
                or "pip-audit did not return valid JSON"
            ),
        }
    vulnerabilities = []
    for dependency in payload.get("dependencies", []):
        for vulnerability in dependency.get("vulns", []):
            vulnerabilities.append({
                "package": dependency.get("name"),
                "version": dependency.get("version"),
                "id": vulnerability.get("id"),
                "fix_versions": vulnerability.get("fix_versions", []),
            })
    if completed.returncode not in {0, 1}:
        status = "error"
    else:
        status = "vulnerable" if vulnerabilities else "clean"
    return {
        "status": status,
        "vulnerability_count": len(vulnerabilities),
        "vulnerabilities": vulnerabilities,
        "message": completed.stderr.strip(),
    }


def native_runtime_report() -> dict:
    report = {
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    try:
        import soundfile

        report["soundfile"] = str(soundfile.__version__)
        report["libsndfile"] = str(soundfile.__libsndfile_version__)
    except Exception as exc:  # pragma: no cover - import failure is report data
        report["soundfile_error"] = str(exc)
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, qVersion

        report["pyqt6"] = str(PYQT_VERSION_STR)
        report["qt6"] = str(qVersion())
        report["qt6_compile_target"] = str(QT_VERSION_STR)
    except Exception as exc:  # pragma: no cover - import failure is report data
        report["qt_error"] = str(exc)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        completed = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        report["ffmpeg"] = (
            completed.stdout.splitlines()[0]
            if completed.stdout.splitlines()
            else "unavailable"
        )
    else:
        report["ffmpeg"] = "unavailable (optional)"
    return report


def load_baseline() -> dict:
    payload = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported compatibility baseline schema")
    return payload


def baseline_report(baseline: dict) -> dict:
    lock_status = []
    for path in (RUNTIME_LOCK, BUILD_LOCK):
        expected = baseline.get("locks", {}).get(path.name, {}).get("sha256")
        actual = sha256_file(path)
        lock_status.append({
            "path": path.name,
            "expected_sha256": expected,
            "actual_sha256": actual,
            "status": "match" if expected == actual else "mismatch",
        })
    return {
        "status": (
            "current"
            if all(item["status"] == "match" for item in lock_status)
            else "stale"
        ),
        "locks": lock_status,
        "recorded_at": baseline.get("recorded_at"),
        "target": baseline.get("target", {}),
        "rollback": baseline.get("rollback", {}),
    }


def native_drift_report(actual: dict, baseline: dict) -> list[dict]:
    expected = baseline.get("native_runtime", {})
    records = []
    for name in ("libsndfile", "qt6"):
        expected_version = expected.get(name)
        actual_version = actual.get(name)
        records.append({
            "name": name,
            "expected": expected_version,
            "installed": actual_version,
            "status": (
                "missing"
                if actual_version is None
                else "match"
                if actual_version == expected_version
                else "mismatch"
            ),
        })
    return records


def golden_report(baseline: dict) -> dict:
    from dsp_golden import render_golden

    _rendered, actual = render_golden()
    expected = baseline.get("dsp_golden", {})
    differences = {
        key: {"expected": expected.get(key), "actual": actual.get(key)}
        for key in sorted(set(expected) | set(actual))
        if expected.get(key) != actual.get(key)
    }
    return {
        "status": "match" if not differences else "mismatch",
        "expected": expected,
        "actual": actual,
        "differences": differences,
    }


def build_report(
    *,
    include_security: bool = True,
    include_golden: bool = True,
) -> dict:
    baseline = load_baseline()
    runtime_lock = parse_lock(RUNTIME_LOCK)
    build_lock = parse_lock(BUILD_LOCK)
    direct_names = direct_requirement_names()
    installed = installed_versions()
    direct = {
        name: version
        for name, version in runtime_lock.items()
        if name in direct_names
    }
    transitive = {
        name: version
        for name, version in runtime_lock.items()
        if name not in direct_names
    }
    native = native_runtime_report()
    report = {
        "schema_version": 1,
        "baseline": baseline_report(baseline),
        "environment": {
            "python": platform.python_version(),
            "executable": sys.executable,
        },
        "direct_dependencies": package_records(direct, installed),
        "transitive_dependencies": package_records(transitive, installed),
        "build_dependencies": package_records(build_lock, installed),
        "native_runtime": native,
        "native_drift": native_drift_report(native, baseline),
        "security": (
            security_report()
            if include_security
            else {"status": "skipped"}
        ),
        "dsp_golden": (
            golden_report(baseline)
            if include_golden
            else {"status": "skipped"}
        ),
    }
    drift_groups = (
        report["direct_dependencies"],
        report["transitive_dependencies"],
        report["build_dependencies"],
        report["native_drift"],
    )
    report["drift_count"] = sum(
        item["status"] != "match"
        for group in drift_groups
        for item in group
    )
    hard_failure = (
        report["baseline"]["status"] != "current"
        or report["security"]["status"] in {"error", "vulnerable"}
        or report["dsp_golden"]["status"] == "mismatch"
    )
    report["status"] = (
        "fail"
        if hard_failure
        else "warning"
        if report["drift_count"]
        else "pass"
    )
    return report


def _drift_count(records: list[dict]) -> int:
    return sum(record["status"] != "match" for record in records)


def print_human_report(report: dict) -> None:
    print(f"Compatibility status: {report['status']}")
    print(f"Baseline: {report['baseline']['status']}")
    for label, key in (
        ("Direct runtime", "direct_dependencies"),
        ("Transitive runtime", "transitive_dependencies"),
        ("Build", "build_dependencies"),
        ("Native", "native_drift"),
    ):
        records = report[key]
        print(
            f"{label} drift: {_drift_count(records)}/{len(records)}"
        )
        for record in records:
            if record["status"] != "match":
                print(
                    f"  {record['name']}: expected {record['expected']}, "
                    f"installed {record['installed'] or 'missing'}"
                )
    print(f"DSP golden: {report['dsp_golden']['status']}")
    security = report["security"]
    print(
        "Security: "
        f"{security['status']}"
        + (
            f" ({security.get('vulnerability_count')} vulnerabilities)"
            if security.get("vulnerability_count") is not None
            else ""
        )
    )
    rollback = report["baseline"].get("rollback", {})
    print(
        "Rollback point: "
        f"{rollback.get('git_commit', 'missing')} "
        f"(v{rollback.get('version', 'unknown')})"
    )


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description=(
            "Report direct, transitive, build, native, DSP-golden, and "
            "security compatibility against the release baseline."
        )
    )
    command.add_argument(
        "--json",
        action="store_true",
        help="Print the complete machine-readable report",
    )
    command.add_argument(
        "--output",
        type=Path,
        help="Also write the JSON report to this path",
    )
    command.add_argument(
        "--require-lock-match",
        action="store_true",
        help="Fail when the current environment differs from either lock",
    )
    command.add_argument(
        "--skip-security",
        action="store_true",
        help="Skip the network-backed vulnerability query",
    )
    command.add_argument(
        "--skip-golden",
        action="store_true",
        help="Skip the deterministic DSP compatibility render",
    )
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    for required in (
        RUNTIME_LOCK,
        BUILD_LOCK,
        DIRECT_REQUIREMENTS,
        BASELINE_FILE,
    ):
        if not required.exists():
            print(f"Missing compatibility input: {required}", file=sys.stderr)
            return 2
    try:
        report = build_report(
            include_security=not args.skip_security,
            include_golden=not args.skip_golden,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Compatibility report failed: {exc}", file=sys.stderr)
        return 2
    if args.output:
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human_report(report)
    if report["status"] == "fail":
        return 1
    if args.require_lock_match and report["drift_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
