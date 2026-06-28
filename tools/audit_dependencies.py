#!/usr/bin/env python3
"""Audit SunoJump release dependencies against known vulnerability databases."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK_FILE = ROOT / "requirements-lock.txt"


def audit_command(extra_args: list[str] | None = None) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pip_audit",
        "--requirement",
        str(LOCK_FILE),
        "--no-deps",
        "--disable-pip",
        "--strict",
        "--progress-spinner",
        "off",
    ]
    if extra_args:
        command.extend(extra_args)
    return command


def main(argv: list[str] | None = None) -> int:
    if not LOCK_FILE.exists():
        print(f"Missing release lock: {LOCK_FILE}", file=sys.stderr)
        return 2

    try:
        import pip_audit  # noqa: F401
    except ImportError:
        print(
            "pip-audit is required. Install dev tools with: "
            "python -m pip install -r requirements-dev.txt",
            file=sys.stderr,
        )
        return 2

    return subprocess.run(audit_command(argv or [])).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
