#!/usr/bin/env python3
"""Audit release dependency licenses and block packaging when unreviewed."""
from __future__ import annotations

import importlib.metadata
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_FILE = ROOT / "requirements-lock.txt"
INVENTORY_FILE = ROOT / "license-inventory.json"

REVIEWED_PACKAGES: dict[str, dict[str, str]] = {
    "cffi": {
        "license": "MIT",
        "distribution": "source-and-binary",
    },
    "mutagen": {
        "license": "GPL-2.0-or-later",
        "distribution": "source-only-install",
        "note": "Users install via pip; not statically linked into SunoJump code",
    },
    "numpy": {
        "license": "BSD-3-Clause",
        "distribution": "source-and-binary",
    },
    "pycparser": {
        "license": "BSD-3-Clause",
        "distribution": "source-and-binary",
    },
    "pyqt6": {
        "license": "GPL-3.0-only",
        "distribution": "source-only-install",
        "note": "PyQt6 is GPL; binary redistribution requires GPL compliance for the combined work",
    },
    "pyqt6-qt6": {
        "license": "LGPL-3.0",
        "distribution": "source-and-binary",
        "note": "Qt6 runtime libraries are LGPL; dynamically linked",
    },
    "pyqt6-sip": {
        "license": "SIP-License",
        "distribution": "source-and-binary",
        "note": "Permissive SIP license for the bindings layer",
    },
    "scipy": {
        "license": "BSD-3-Clause",
        "distribution": "source-and-binary",
    },
    "soundfile": {
        "license": "BSD-3-Clause",
        "distribution": "source-and-binary",
    },
}

COPYLEFT_PREFIXES = ("GPL", "AGPL", "LGPL", "MPL", "EUPL", "CDDL", "EPL")


def _parse_lock(path: Path) -> list[str]:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def _normalize(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def _detect_license(package_name: str) -> str:
    try:
        meta = importlib.metadata.metadata(package_name)
    except importlib.metadata.PackageNotFoundError:
        return "UNKNOWN"
    license_field = meta.get("License-Expression") or meta.get("License") or ""
    if license_field.strip() and license_field.strip() != "UNKNOWN":
        return license_field.strip()
    classifiers = meta.get_all("Classifier") or []
    for c in classifiers:
        if "License ::" in c:
            parts = c.split(" :: ")
            return parts[-1] if len(parts) >= 3 else c
    return "UNKNOWN"


def _is_copyleft(license_str: str) -> bool:
    upper = license_str.upper()
    return any(upper.startswith(p) or f" {p}" in upper for p in COPYLEFT_PREFIXES)


def audit(write_inventory: bool = False) -> tuple[int, list[str]]:
    if not LOCK_FILE.exists():
        return 2, [f"Missing release lock: {LOCK_FILE}"]

    locked = _parse_lock(LOCK_FILE)
    errors: list[str] = []
    inventory: list[dict[str, str]] = []

    for entry in locked:
        name = entry.split("==")[0]
        version = entry.split("==")[1] if "==" in entry else "?"
        normalized = _normalize(name)
        detected = _detect_license(name)

        record = {
            "package": name,
            "version": version,
            "detected_license": detected,
        }

        reviewed = REVIEWED_PACKAGES.get(normalized)
        if reviewed is None:
            errors.append(
                f"UNREVIEWED: {name}=={version} (detected: {detected}) — "
                "add to REVIEWED_PACKAGES in tools/audit_licenses.py"
            )
            record["status"] = "UNREVIEWED"
        else:
            record["reviewed_license"] = reviewed["license"]
            record["distribution"] = reviewed["distribution"]
            if reviewed.get("note"):
                record["note"] = reviewed["note"]
            record["status"] = "REVIEWED"

        if _is_copyleft(detected):
            record["copyleft"] = True
            if reviewed is None:
                errors.append(
                    f"COPYLEFT-UNREVIEWED: {name}=={version} has copyleft "
                    f"license ({detected}) — requires explicit review"
                )

        inventory.append(record)

    if write_inventory:
        INVENTORY_FILE.write_text(
            json.dumps(inventory, indent=2) + "\n", encoding="utf-8"
        )

    return (1 if errors else 0), errors


def main(argv: list[str] | None = None) -> int:
    args = argv or []
    write = "--write-inventory" in args

    code, errors = audit(write_inventory=write)

    if code == 2:
        for e in errors:
            print(e, file=sys.stderr)
        return 2

    for e in errors:
        print(f"  FAIL: {e}", file=sys.stderr)

    if code == 0:
        print("License audit passed: all release dependencies reviewed.")
        if write:
            print(f"Inventory written to {INVENTORY_FILE}")
    else:
        print(
            f"\nLicense audit FAILED: {len(errors)} issue(s) found.\n"
            "Review and update REVIEWED_PACKAGES in tools/audit_licenses.py.",
            file=sys.stderr,
        )

    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
