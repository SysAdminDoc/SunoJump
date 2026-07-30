#!/usr/bin/env python3
"""Audit and emit release license/source-routing artifacts."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK_FILE = ROOT / "requirements-lock.txt"
INVENTORY_FILE = ROOT / "license-inventory.json"
LOCK_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^\s]+)"
    r"\s+--hash=sha256:(?P<sha256>[a-f0-9]{64})$"
)

REVIEWED_PACKAGES: dict[str, dict[str, str]] = {
    "cffi": {
        "license": "MIT",
        "distribution": "bundled",
        "source_url": "https://github.com/python-cffi/cffi",
    },
    "mutagen": {
        "license": "GPL-2.0-or-later",
        "distribution": "bundled",
        "source_url": "https://github.com/quodlibet/mutagen",
        "note": "Mutagen is bundled; the combined executable is distributed under GPL-3.0 terms.",
    },
    "numpy": {
        "license": "BSD-3-Clause",
        "distribution": "bundled",
        "source_url": "https://github.com/numpy/numpy",
    },
    "pycparser": {
        "license": "BSD-3-Clause",
        "distribution": "bundled",
        "source_url": "https://github.com/eliben/pycparser",
    },
    "pyqt6": {
        "license": "GPL-3.0-only",
        "distribution": "bundled",
        "source_url": "https://www.riverbankcomputing.com/software/pyqt/download",
        "note": "PyQt6 is bundled under GPL-3.0; the combined executable is distributed under GPL-3.0 terms.",
    },
    "pyqt6-qt6": {
        "license": "LGPL-3.0-only",
        "distribution": "bundled-dynamic-libraries",
        "source_url": "https://download.qt.io/official_releases/qt/6.11/6.11.1/submodules/",
        "note": "Qt 6 shared libraries are dynamically loaded from the PyInstaller extraction directory; exact versions and upstream source are recorded.",
    },
    "pyqt6-sip": {
        "license": "SIP",
        "distribution": "bundled",
        "source_url": "https://www.riverbankcomputing.com/software/sip/download",
    },
    "scipy": {
        "license": "BSD-3-Clause",
        "distribution": "bundled",
        "source_url": "https://github.com/scipy/scipy",
    },
    "soundfile": {
        "license": "BSD-3-Clause",
        "distribution": "bundled",
        "source_url": "https://github.com/bastibe/python-soundfile",
        "note": "The wheel also contains libsndfile and its codec libraries; exact native versions are recorded separately.",
    },
    "typing-extensions": {
        "license": "PSF-2.0",
        "distribution": "bundled",
        "source_url": "https://github.com/python/typing_extensions",
    },
}

COPYLEFT_PREFIXES = ("GPL", "AGPL", "LGPL", "MPL", "EUPL", "CDDL", "EPL")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_lock(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def parse_lock(path: Path) -> list[dict[str, str]]:
    entries = []
    for line_number, line in enumerate(_parse_lock(path), start=1):
        match = LOCK_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError(
                f"{path.name}: unpinned or unhashed entry {line_number}: {line}"
            )
        entries.append(match.groupdict())
    if not entries:
        raise ValueError(f"{path.name}: lock is empty")
    normalized = [_normalize(entry["name"]) for entry in entries]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{path.name}: duplicate package entries")
    return entries


def _detect_license(package_name: str, expected_version: str) -> str:
    try:
        dist = importlib.metadata.distribution(package_name)
    except importlib.metadata.PackageNotFoundError:
        return "NOT-INSTALLED"
    if dist.version != expected_version:
        return f"VERSION-MISMATCH:{dist.version}"
    meta = dist.metadata
    license_field = meta.get("License-Expression") or meta.get("License") or ""
    license_field = license_field.strip()
    if license_field and license_field != "UNKNOWN" and len(license_field) <= 200:
        return license_field
    classifiers = meta.get_all("Classifier") or []
    for classifier in classifiers:
        if "License ::" in classifier:
            return classifier.split(" :: ")[-1]
    return "UNKNOWN"


def _is_copyleft(license_str: str) -> bool:
    upper = license_str.upper()
    return any(
        upper.startswith(prefix) or f" {prefix}" in upper
        for prefix in COPYLEFT_PREFIXES
    )


def build_inventory(lock_file: Path = LOCK_FILE) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    inventory: list[dict] = []
    try:
        locked = parse_lock(lock_file)
    except (OSError, ValueError) as exc:
        return [], [str(exc)]

    for entry in locked:
        name = entry["name"]
        version = entry["version"]
        normalized = _normalize(name)
        reviewed = REVIEWED_PACKAGES.get(normalized)
        detected = _detect_license(name, version)
        record = {
            "package": name,
            "version": version,
            "wheel_sha256": entry["sha256"],
            "detected_license": detected,
        }
        if reviewed is None:
            errors.append(
                f"UNREVIEWED: {name}=={version} (detected: {detected})"
            )
            record["status"] = "UNREVIEWED"
        else:
            record.update({
                "reviewed_license": reviewed["license"],
                "distribution": reviewed["distribution"],
                "source_url": reviewed["source_url"],
                "status": "REVIEWED",
            })
            if reviewed.get("note"):
                record["note"] = reviewed["note"]
            if detected.startswith(("NOT-INSTALLED", "VERSION-MISMATCH")):
                record["installed_metadata_status"] = detected

        license_to_check = (
            reviewed["license"] if reviewed is not None else detected
        )
        if _is_copyleft(license_to_check):
            record["copyleft"] = True
            if reviewed is None or not reviewed.get("note"):
                errors.append(
                    f"COPYLEFT-NOTE-MISSING: {name}=={version} "
                    f"({license_to_check})"
                )
        inventory.append(record)
    return inventory, errors


def write_notices(inventory: list[dict], output: Path) -> None:
    lines = [
        "SunoJump Third-Party Notices",
        "",
        "The unsigned SunoJump executable bundles the components below.",
        "SunoJump source is MIT-licensed; the combined executable is distributed",
        "under GPL-3.0 terms because it bundles PyQt6.",
        "",
    ]
    for record in inventory:
        lines.extend([
            f"{record['package']} {record['version']}",
            f"  License: {record.get('reviewed_license', 'UNREVIEWED')}",
            f"  Distribution: {record.get('distribution', 'unknown')}",
            f"  Source: {record.get('source_url', 'unavailable')}",
        ])
        if record.get("note"):
            lines.append(f"  Note: {record['note']}")
        lines.append("")
    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_source_routing(
    inventory: list[dict],
    output: Path,
    app_version: str,
    source_archive: str,
    source_commit: str,
) -> None:
    lines = [
        "SunoJump Source Routing",
        "",
        f"Application version: {app_version}",
        f"Source commit: {source_commit}",
        f"Corresponding SunoJump source archive: {source_archive}",
        "Repository: https://github.com/SysAdminDoc/SunoJump",
        "",
        "Upstream component source:",
    ]
    for record in inventory:
        lines.append(
            f"- {record['package']} {record['version']}: "
            f"{record.get('source_url', 'unavailable')}"
        )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def audit(
    write_inventory: bool = False,
    *,
    lock_file: Path | None = None,
    inventory_file: Path | None = None,
) -> tuple[int, list[str]]:
    selected_lock = Path(lock_file) if lock_file is not None else LOCK_FILE
    inventory, errors = build_inventory(selected_lock)
    if write_inventory and inventory:
        output = (
            Path(inventory_file)
            if inventory_file is not None
            else INVENTORY_FILE
        )
        output.write_text(
            json.dumps(inventory, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if not inventory and errors:
        return 2, errors
    return (1 if errors else 0), errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=LOCK_FILE)
    parser.add_argument("--write-inventory", action="store_true")
    parser.add_argument("--inventory", type=Path, default=INVENTORY_FILE)
    parser.add_argument("--notices", type=Path)
    parser.add_argument("--source-routing", type=Path)
    parser.add_argument("--app-version", default="unknown")
    parser.add_argument("--source-archive", default="unavailable")
    parser.add_argument("--source-commit", default="unknown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    inventory, errors = build_inventory(args.lock)
    code = 2 if not inventory and errors else (1 if errors else 0)
    for error in errors:
        print(f"  FAIL: {error}", file=sys.stderr)
    if code:
        print(
            f"License audit FAILED: {len(errors)} issue(s) found.",
            file=sys.stderr,
        )
        return code

    if args.write_inventory:
        args.inventory.write_text(
            json.dumps(inventory, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"License inventory written: {args.inventory}")
    if args.notices:
        write_notices(inventory, args.notices)
        print(f"Third-party notices written: {args.notices}")
    if args.source_routing:
        write_source_routing(
            inventory,
            args.source_routing,
            args.app_version,
            args.source_archive,
            args.source_commit,
        )
        print(f"Source routing written: {args.source_routing}")
    print("License audit passed: all release dependencies reviewed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
