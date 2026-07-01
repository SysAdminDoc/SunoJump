#!/usr/bin/env python3
"""Build release artifacts: SHA256SUMS and CycloneDX SBOM."""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_FILE = ROOT / "requirements-lock.txt"
DIST_DIR = ROOT / "dist"


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b''):
            hasher.update(block)
    return hasher.hexdigest()


def generate_sha256sums(artifacts: list[Path], output: Path) -> None:
    lines = []
    for artifact in artifacts:
        if not artifact.exists():
            print(f"  Warning: artifact not found: {artifact}", file=sys.stderr)
            continue
        digest = sha256_file(artifact)
        lines.append(f"{digest}  {artifact.name}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  SHA256SUMS written: {output}")


def sbom_command() -> list[str]:
    return [
        sys.executable,
        "-m",
        "pip_audit",
        "--requirement",
        str(LOCK_FILE),
        "--no-deps",
        "--disable-pip",
        "--format",
        "cyclonedx-json",
        "--output",
        str(DIST_DIR / "sbom.cdx.json"),
    ]


def generate_sbom() -> bool:
    try:
        import pip_audit  # noqa: F401
    except ImportError:
        print(
            "pip-audit is required for SBOM generation. Install with: "
            "python -m pip install -r requirements-dev.txt",
            file=sys.stderr,
        )
        return False

    cmd = sbom_command()
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"  SBOM generation failed: {result.stderr}", file=sys.stderr)
        return False
    print(f"  SBOM written: {DIST_DIR / 'sbom.cdx.json'}")
    return True


def main(argv: list[str] | None = None) -> int:
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    exe = DIST_DIR / "SunoJump.exe"
    lock = ROOT / "requirements-lock.txt"
    artifacts = [a for a in [exe, lock] if a.exists()]

    if not artifacts:
        print("No release artifacts found. Build the executable first.", file=sys.stderr)
        return 2

    generate_sha256sums(artifacts, DIST_DIR / "SHA256SUMS")
    sbom_ok = generate_sbom()

    if not sbom_ok:
        print("Warning: SBOM generation skipped or failed.", file=sys.stderr)
        return 1

    print("Release artifacts ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
