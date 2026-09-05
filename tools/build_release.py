#!/usr/bin/env python3
"""Build and prove an unsigned SunoJump release in an isolated toolchain."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from typing import NamedTuple
import uuid
import venv
import wave
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_LOCK = ROOT / "requirements-lock.txt"
BUILD_LOCK = ROOT / "requirements-build-lock.txt"
SPEC_FILE = ROOT / "SunoJump.spec"
DIST_DIR = ROOT / "dist"
LICENSE_TOOL = ROOT / "tools" / "audit_licenses.py"
COMPATIBILITY_BASELINE = ROOT / "tools" / "compatibility_baseline.json"
CYCLONEDX_SCHEMA = "https://cyclonedx.org/schema/bom-1.7.schema.json"
MIN_RELEASE_EXECUTABLE_BYTES = 1024 * 1024
MAX_RELEASE_EXECUTABLE_BYTES = 250 * 1024 * 1024
LOCK_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^\s]+)"
    r"\s+--hash=sha256:(?P<sha256>[a-f0-9]{64})$"
)
FORBIDDEN_BUNDLE_NAMES = {
    "cupy",
    "ipython",
    "lark",
    "matplotlib",
    "numba",
    "pandas",
    "playwright",
    "pytest",
    "torch",
    "webview",
    "yt_dlp",
}
REQUIRED_SOURCE_FILES = {
    Path("audio_quality.py"),
    Path("assets/sunojump-mark.png"),
    Path("assets/sunojump.ico"),
    Path("assets/version_info.txt"),
    Path("batch_manifest.py"),
    Path("c2pa_provenance.py"),
    Path("compute_backend.py"),
    Path("config_schema.py"),
    Path("locales/en.json"),
    Path("locales/qps-ploc.json"),
    Path("localization.py"),
    Path("requirements-build-lock.txt"),
    Path("render_results.py"),
    Path("safe_audio.py"),
    Path("safe_audio_worker.py"),
    Path("tools/compatibility_baseline.json"),
    Path("tools/dsp_golden.py"),
    Path("tools/run_ab_regression.py"),
    Path("tools/generate_brand_assets.py"),
    Path("tools/smoke_accessibility.ps1"),
    Path("verifiers.py"),
    Path("verifiers_visqol.py"),
}


class ReleaseError(RuntimeError):
    """Release gate failure."""


class LockEntry(NamedTuple):
    name: str
    version: str
    sha256: str

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.name)


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_hashed_lock(path: Path) -> list[LockEntry]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReleaseError(f"missing release lock {path}: {exc}") from exc
    entries = []
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = LOCK_PATTERN.fullmatch(line)
        if match is None:
            raise ReleaseError(
                f"{path.name}:{line_number} is not exactly pinned and hashed"
            )
        entries.append(LockEntry(**match.groupdict()))
    if not entries:
        raise ReleaseError(f"{path.name} contains no packages")
    normalized = [entry.normalized_name for entry in entries]
    if len(normalized) != len(set(normalized)):
        raise ReleaseError(f"{path.name} contains duplicate packages")
    return entries


def load_compatibility_baseline() -> dict:
    try:
        payload = json.loads(
            COMPATIBILITY_BASELINE.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(
            f"invalid compatibility baseline: {exc}"
        ) from exc
    if payload.get("schema_version") != 1:
        raise ReleaseError("unsupported compatibility baseline schema")
    for lock in (RUNTIME_LOCK, BUILD_LOCK):
        expected = payload.get("locks", {}).get(lock.name, {}).get("sha256")
        actual = sha256_file(lock)
        if expected != actual:
            raise ReleaseError(
                f"compatibility baseline is stale for {lock.name}; "
                "record tested compatibility, native versions, and a "
                "rollback point before building"
            )
    rollback = payload.get("rollback", {})
    if not re.fullmatch(r"[0-9a-f]{40}", rollback.get("git_commit", "")):
        raise ReleaseError("compatibility baseline lacks a rollback commit")
    if not payload.get("native_runtime") or not payload.get("dsp_golden"):
        raise ReleaseError(
            "compatibility baseline lacks native or DSP golden evidence"
        )
    return payload


def validate_native_compatibility(
    native_runtime: dict,
    compatibility_baseline: dict,
) -> None:
    expected = compatibility_baseline["native_runtime"]
    mismatches = {
        name: {
            "expected": expected.get(name),
            "actual": native_runtime.get(name),
        }
        for name in ("libsndfile", "qt6")
        if expected.get(name) != native_runtime.get(name)
    }
    if mismatches:
        raise ReleaseError(
            f"native runtime differs from compatibility baseline: {mismatches}"
        )


def sha256_file(path: Path, *, retry_seconds: float = 30.0) -> str:
    deadline = time.monotonic() + retry_seconds
    while True:
        hasher = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(block)
            return hasher.hexdigest()
        except OSError as exc:
            if getattr(exc, "winerror", None) != 5:
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def generate_sha256sums(artifacts: list[Path], output: Path) -> None:
    missing = [artifact for artifact in artifacts if not artifact.is_file()]
    if missing:
        names = ", ".join(str(path) for path in missing)
        raise ReleaseError(f"cannot hash missing release artifacts: {names}")
    lines = [
        f"{sha256_file(artifact)}  {artifact.name}"
        for artifact in sorted(artifacts, key=lambda item: item.name.lower())
    ]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def source_version() -> str:
    text = (ROOT / "sunojump.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION = "([^"]+)"$', text, re.MULTILINE)
    if match is None:
        raise ReleaseError("sunojump.py does not declare VERSION")
    return match.group(1)


def _release_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
    })
    return environment


def validate_build_host() -> None:
    if os.name != "nt":
        raise ReleaseError("the release target is Windows x64")
    if sys.version_info[:2] != (3, 12):
        raise ReleaseError(
            "release builds require CPython 3.12 "
            f"(found {platform.python_version()})"
        )
    if platform.machine().upper() not in {"AMD64", "X86_64"}:
        raise ReleaseError(
            f"release builds require x64 (found {platform.machine()})"
        )


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run_streaming(command: list[str], *, timeout: int, cwd: Path = ROOT) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=_release_environment(),
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise ReleaseError(
            f"command failed with exit code {result.returncode}: "
            f"{' '.join(command)}"
        )


def install_isolated_toolchain(venv_dir: Path) -> Path:
    print(
        "Creating isolated CPython 3.12 virtual environment...",
        flush=True,
    )
    venv.EnvBuilder(with_pip=True, clear=True, symlinks=False).create(venv_dir)
    python = _venv_python(venv_dir)
    if not python.is_file():
        raise ReleaseError("virtual environment did not create python.exe")
    print("Installing only hashed runtime and build inputs...", flush=True)
    _run_streaming(
        [
            str(python),
            "-m",
            "pip",
            "--isolated",
            "install",
            "--require-hashes",
            "--only-binary=:all:",
            "--no-compile",
            "--no-cache-dir",
            "-r",
            str(RUNTIME_LOCK),
            "-r",
            str(BUILD_LOCK),
        ],
        timeout=900,
    )
    return python


def query_installed_distributions(python: Path) -> dict[str, dict[str, str]]:
    script = (
        "import importlib.metadata as m,json;"
        "print(json.dumps({"
        "d.metadata['Name']:{'version':d.version,'name':d.metadata['Name']}"
        " for d in m.distributions()}))"
    )
    result = subprocess.run(
        [str(python), "-I", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=_release_environment(),
    )
    if result.returncode != 0:
        raise ReleaseError(
            f"cannot inventory isolated environment: {result.stderr.strip()}"
        )
    raw = json.loads(result.stdout)
    return {
        normalize_name(name): {
            "name": record["name"],
            "version": record["version"],
        }
        for name, record in raw.items()
    }


def validate_installed_distributions(
    installed: dict[str, dict[str, str]],
    runtime_lock: list[LockEntry],
    build_lock: list[LockEntry],
) -> None:
    expected = {
        entry.normalized_name: entry.version
        for entry in runtime_lock + build_lock
    }
    allowed_bootstrap = {"pip"}
    unexpected = sorted(set(installed) - set(expected) - allowed_bootstrap)
    missing = sorted(set(expected) - set(installed))
    wrong = sorted(
        f"{name}: expected {version}, found {installed[name]['version']}"
        for name, version in expected.items()
        if name in installed and installed[name]["version"] != version
    )
    if unexpected or missing or wrong:
        raise ReleaseError(
            "isolated distribution mismatch; "
            f"unexpected={unexpected}, missing={missing}, wrong={wrong}"
        )


def _run_capture(
    command: list[str],
    *,
    timeout: int = 120,
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess:
    deadline = time.monotonic() + min(45, timeout)
    while True:
        try:
            return subprocess.run(
                command,
                cwd=cwd,
                env=_release_environment(),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except OSError as exc:
            if getattr(exc, "winerror", None) != 5:
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def validate_release_executable_size(size_bytes: int) -> None:
    if size_bytes < MIN_RELEASE_EXECUTABLE_BYTES:
        raise ReleaseError(
            f"release executable is unexpectedly small: {size_bytes} bytes"
        )
    if size_bytes > MAX_RELEASE_EXECUTABLE_BYTES:
        raise ReleaseError(
            "release executable exceeds the artifact-size gate: "
            f"{size_bytes} > {MAX_RELEASE_EXECUTABLE_BYTES} bytes"
        )


def verify_executable(
    executable: Path,
    expected_version: str,
    build_started_ns: int,
    *,
    runner=_run_capture,
) -> dict:
    ready_deadline = time.monotonic() + 45
    while True:
        try:
            if not executable.is_file():
                raise ReleaseError(
                    f"release executable is missing: {executable}"
                )
            stat = executable.stat()
            with executable.open("rb") as handle:
                handle.read(2)
            break
        except OSError as exc:
            if getattr(exc, "winerror", None) != 5:
                raise
            if time.monotonic() >= ready_deadline:
                raise ReleaseError(
                    f"release executable stayed locked: {executable}"
                )
            time.sleep(0.25)
    if stat.st_mtime_ns < build_started_ns:
        raise ReleaseError(
            f"release executable is stale: {executable.name} predates this build"
        )
    validate_release_executable_size(stat.st_size)

    version_result = runner([str(executable), "--version"], timeout=120)
    expected_line = f"SunoJump v{expected_version}"
    if version_result.returncode != 0:
        raise ReleaseError(
            f"release executable --version failed: "
            f"{version_result.stderr.strip()}"
        )
    version_lines = [
        line.strip()
        for line in version_result.stdout.splitlines()
        if line.strip()
    ]
    if version_lines != [expected_line]:
        raise ReleaseError(
            f"wrong executable version: expected {expected_line!r}, "
            f"found {version_lines!r}"
        )

    help_result = runner([str(executable), "--help"], timeout=120)
    if help_result.returncode != 0:
        raise ReleaseError(
            f"release executable --help failed: {help_result.stderr.strip()}"
        )
    help_text = help_result.stdout
    if "usage:" not in help_text.lower() or "--native-runtime" not in help_text:
        raise ReleaseError("release executable --help is incomplete")
    return {
        "version": expected_line,
        "version_exit_code": version_result.returncode,
        "help_exit_code": help_result.returncode,
        "help_has_usage": True,
        "sha256": sha256_file(executable),
        "bytes": stat.st_size,
    }


def query_native_runtime(executable: Path) -> dict:
    result = _run_capture([str(executable), "--native-runtime"], timeout=120)
    if result.returncode != 0:
        raise ReleaseError(
            f"release executable --native-runtime failed: {result.stderr.strip()}"
        )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseError("native runtime report is not valid JSON") from exc
    if not str(report.get("runtime_gate", "")).startswith("pass"):
        raise ReleaseError(
            f"native runtime gate failed: {report.get('runtime_gate')}"
        )
    for key in ("python", "numpy", "scipy", "soundfile", "libsndfile", "pyqt6", "qt6"):
        if not report.get(key):
            raise ReleaseError(f"native runtime report is missing {key}")
    return report


def _write_fixture(path: Path, sample_rate: int = 8000) -> None:
    frames = bytearray()
    for index in range(sample_rate * 2):
        sample = int(
            0.25
            * 32767
            * math.sin(2.0 * math.pi * 440.0 * index / sample_rate)
        )
        frames.extend(struct.pack("<h", sample))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(frames)


def smoke_render_executable(executable: Path, version: str, root: Path) -> dict:
    fixture = root / "fixture.wav"
    output_dir = root / "rendered"
    _write_fixture(fixture)
    result = _run_capture(
        [
            str(executable),
            "-i",
            str(fixture),
            "-o",
            str(output_dir),
            "-p",
            "gentle",
            "--seed",
            "17",
            "--result-format",
            "json",
        ],
        timeout=240,
        cwd=ROOT,
    )
    if result.returncode != 0:
        raise ReleaseError(
            "fixture render failed: "
            f"stdout={result.stdout[-2000:]!r} stderr={result.stderr[-2000:]!r}"
        )
    output = output_dir / "fixture_sj.wav"
    sidecar = output_dir / "fixture_sj.sidecar.json"
    if not output.is_file() or not sidecar.is_file():
        raise ReleaseError("fixture render did not produce audio and sidecar")
    with wave.open(str(output), "rb") as handle:
        shape = {
            "sample_rate_hz": handle.getframerate(),
            "channels": handle.getnchannels(),
            "frames": handle.getnframes(),
        }
        sample_bytes = handle.readframes(handle.getnframes())
    if (
        shape["sample_rate_hz"] != 8000
        or shape["channels"] != 1
        or shape["frames"] <= 0
        or not any(sample_bytes)
    ):
        raise ReleaseError(f"fixture output shape/content is invalid: {shape}")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    if payload.get("sunojump_version") != version:
        raise ReleaseError("fixture sidecar has the wrong application version")
    if payload.get("output_sha256") != sha256_file(output):
        raise ReleaseError("fixture sidecar output hash does not match audio")
    try:
        result_payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseError(
            f"fixture CLI result is not valid JSON: {exc}"
        ) from exc
    if (
        result_payload.get("schema_id") != "com.sunojump.cli-results"
        or result_payload.get("state") != "succeeded"
        or len(result_payload.get("results", [])) != 1
        or result_payload["results"][0].get("state") != "succeeded"
    ):
        raise ReleaseError(
            "fixture CLI did not report a schema-versioned succeeded job "
            "and batch"
        )
    return {
        "exit_code": result.returncode,
        "output_sha256": sha256_file(output),
        "sidecar_sha256": sha256_file(sidecar),
        **shape,
    }


def smoke_gui_executable(executable: Path, version: str) -> dict:
    if os.name != "nt":
        raise ReleaseError("GUI smoke currently requires Windows")
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    executable_key = os.path.normcase(str(executable.resolve()))
    toolhelp_process = 0x00000002
    process_query = 0x1000
    process_terminate = 0x0001
    synchronize = 0x00100000
    still_active = 259
    invalid_handle = ctypes.c_void_p(-1).value

    class ProcessEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessEntry32),
    ]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessEntry32),
    ]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL

    def process_path(process_id):
        handle = kernel32.OpenProcess(process_query, False, process_id)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                buffer,
                ctypes.byref(size),
            ):
                return None
            return os.path.normcase(os.path.realpath(buffer.value))
        finally:
            kernel32.CloseHandle(handle)

    def matching_process_ids():
        snapshot = kernel32.CreateToolhelp32Snapshot(toolhelp_process, 0)
        if not snapshot or snapshot == invalid_handle:
            return set()
        matching = set()
        try:
            entry = ProcessEntry32()
            entry.dwSize = ctypes.sizeof(entry)
            has_entry = kernel32.Process32FirstW(
                snapshot,
                ctypes.byref(entry),
            )
            while has_entry:
                process_id = entry.th32ProcessID
                if process_id and process_path(process_id) == executable_key:
                    matching.add(process_id)
                has_entry = kernel32.Process32NextW(
                    snapshot,
                    ctypes.byref(entry),
                )
        finally:
            kernel32.CloseHandle(snapshot)
        return matching

    def terminate_matching_processes():
        deadline = time.monotonic() + 5
        while True:
            matching = matching_process_ids()
            if not matching:
                return
            for process_id in matching:
                handle = kernel32.OpenProcess(
                    process_terminate | synchronize,
                    False,
                    process_id,
                )
                if handle:
                    try:
                        kernel32.TerminateProcess(handle, 2)
                        kernel32.WaitForSingleObject(handle, 5000)
                    finally:
                        kernel32.CloseHandle(handle)
            if time.monotonic() >= deadline:
                return
            time.sleep(0.1)

    launch_deadline = time.monotonic() + 45
    while True:
        try:
            process = subprocess.Popen(
                [str(executable)],
                cwd=ROOT,
                env=_release_environment(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(
                f"  GUI process launched (pid {process.pid}); waiting for window...",
                flush=True,
            )
            break
        except OSError as exc:
            if getattr(exc, "winerror", None) != 5:
                raise
            if time.monotonic() >= launch_deadline:
                raise ReleaseError(
                    "foreground GUI executable stayed locked for 45 seconds"
                )
            time.sleep(0.25)
    expected_title = f"SunoJump v{version}"
    found = {
        "handle": None,
        "title": None,
        "process_id": None,
    }

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(handle, _lparam):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        if (
            process_path(process_id.value) != executable_key
            or not user32.IsWindowVisible(handle)
        ):
            return True
        length = user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        if buffer.value:
            found["handle"] = handle
            found["title"] = buffer.value
            found["process_id"] = process_id.value
            return False
        return True

    deadline = time.monotonic() + 45
    child_handle = None
    try:
        while time.monotonic() < deadline:
            found["handle"] = None
            found["title"] = None
            found["process_id"] = None
            user32.EnumWindows(callback, 0)
            if found["handle"]:
                break
            time.sleep(0.2)
        if not found["handle"]:
            raise ReleaseError(
                f"foreground GUI did not expose a visible window; "
                f"launcher_exit={process.poll()}"
            )
        if found["title"] != expected_title:
            raise ReleaseError(
                f"foreground GUI title mismatch: {found['title']!r}"
            )
        print(f"  Visible GUI window: {found['title']}", flush=True)
        child_handle = kernel32.OpenProcess(
            process_query | process_terminate | synchronize,
            False,
            found["process_id"],
        )
        if not child_handle:
            raise ReleaseError("cannot open the foreground GUI child process")
        user32.ShowWindow(found["handle"], 9)
        foreground_requested = bool(user32.SetForegroundWindow(found["handle"]))
        time.sleep(0.75)
        if not user32.PostMessageW(found["handle"], 0x0010, 0, 0):
            raise ReleaseError("cannot post WM_CLOSE to the foreground GUI")
        wait_result = kernel32.WaitForSingleObject(child_handle, 15000)
        if wait_result != 0:
            raise ReleaseError("foreground GUI child did not close after WM_CLOSE")
        exit_code = wintypes.DWORD(still_active)
        if not kernel32.GetExitCodeProcess(child_handle, ctypes.byref(exit_code)):
            raise ReleaseError("cannot read the foreground GUI child exit code")
        if exit_code.value != 0:
            raise ReleaseError(
                f"foreground GUI child exited with code {exit_code.value}"
            )
        try:
            launcher_exit = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            launcher_exit = process.poll()
        if launcher_exit not in {None, 0}:
            raise ReleaseError(
                f"foreground GUI launcher exited with code {launcher_exit}"
            )
        print("  Foreground GUI closed cleanly.", flush=True)
        return {
            "visible_window": True,
            "window_title": found["title"],
            "window_process_id_is_child": found["process_id"] != process.pid,
            "foreground_requested": foreground_requested,
            "exit_code": exit_code.value,
        }
    finally:
        if child_handle:
            kernel32.CloseHandle(child_handle)
        terminate_matching_processes()


def read_analysis_entries(analysis_file: Path) -> list[dict[str, str]]:
    if not analysis_file.is_file():
        raise ReleaseError(f"PyInstaller analysis manifest missing: {analysis_file}")
    try:
        payload = ast.literal_eval(analysis_file.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        raise ReleaseError("cannot parse PyInstaller analysis manifest") from exc
    if not isinstance(payload, tuple) or len(payload) < 20:
        raise ReleaseError("unexpected PyInstaller analysis manifest structure")
    sections = {
        "script": payload[13],
        "python-module": payload[14],
        "binary": payload[15],
        "data": payload[18],
        "dependency": payload[19],
    }
    entries = []
    for section, values in sections.items():
        for value in values:
            if not isinstance(value, tuple) or len(value) < 2:
                continue
            entries.append({
                "logical_name": str(value[0]).replace("\\", "/"),
                "source": str(value[1]),
                "type": str(value[2]) if len(value) > 2 else section,
                "section": section,
            })
    if not entries:
        raise ReleaseError("PyInstaller analysis manifest contains no entries")
    return entries


def _site_packages_for(python: Path) -> Path:
    result = _run_capture(
        [
            str(python),
            "-I",
            "-c",
            "import json,site; print(json.dumps(site.getsitepackages()))",
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise ReleaseError("cannot locate isolated site-packages")
    paths = [Path(path) for path in json.loads(result.stdout)]
    for path in paths:
        if path.name.lower() == "site-packages" and path.is_dir():
            return path.resolve()
    raise ReleaseError("isolated site-packages directory is missing")


def _distribution_file_map(site_packages: Path) -> dict[str, str]:
    mapping = {}
    for dist in importlib.metadata.distributions(path=[str(site_packages)]):
        name = normalize_name(dist.metadata["Name"])
        for relative in dist.files or []:
            try:
                located = dist.locate_file(relative).resolve()
            except OSError:
                continue
            mapping[os.path.normcase(str(located))] = name
    return mapping


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def build_artifact_inventory(
    analysis_file: Path,
    site_packages: Path,
    runtime_lock: list[LockEntry],
    build_lock: list[LockEntry],
) -> dict:
    raw_entries = read_analysis_entries(analysis_file)
    file_map = _distribution_file_map(site_packages)
    runtime_names = {entry.normalized_name for entry in runtime_lock}
    build_names = {entry.normalized_name for entry in build_lock}
    package_counts = Counter()
    section_counts = Counter()
    type_counts = Counter()
    forbidden = []
    unmapped_site_sources = []
    build_only_bundled = []
    entries = []

    forbidden_patterns = {
        name: re.compile(
            rf"(^|[./\\]){re.escape(name)}([./\\]|$)",
            re.IGNORECASE,
        )
        for name in FORBIDDEN_BUNDLE_NAMES
    }
    for raw in raw_entries:
        logical_name = raw["logical_name"]
        source_text = raw["source"]
        distribution = None
        origin = "system-or-standard-library"
        source_path = Path(source_text)
        try:
            resolved_source = source_path.resolve()
        except OSError:
            resolved_source = source_path
        if source_text and _is_relative_to(resolved_source, site_packages):
            source_key = os.path.normcase(str(resolved_source))
            distribution = file_map.get(source_key)
            if distribution is None:
                unmapped_site_sources.append(logical_name)
                origin = "unmapped-site-packages"
            elif distribution in runtime_names:
                origin = "runtime-package"
                package_counts[distribution] += 1
            elif distribution in build_names:
                origin = "build-tool"
                if distribution != "pyinstaller":
                    build_only_bundled.append({
                        "logical_name": logical_name,
                        "distribution": distribution,
                    })
            elif distribution == "pip":
                origin = "bootstrap-tool"
                build_only_bundled.append({
                    "logical_name": logical_name,
                    "distribution": distribution,
                })
            else:
                origin = "undeclared-package"
                build_only_bundled.append({
                    "logical_name": logical_name,
                    "distribution": distribution,
                })
        elif source_text and _is_relative_to(resolved_source, ROOT):
            origin = "sunojump-source"

        searchable = f"{logical_name} {source_text}"
        for name, pattern in forbidden_patterns.items():
            if pattern.search(searchable):
                forbidden.append({
                    "logical_name": logical_name,
                    "package": name,
                })
        section_counts[raw["section"]] += 1
        type_counts[raw["type"]] += 1
        entries.append({
            "logical_name": logical_name,
            "type": raw["type"],
            "section": raw["section"],
            "origin": origin,
            **({"distribution": distribution} if distribution else {}),
        })

    if forbidden or unmapped_site_sources or build_only_bundled:
        raise ReleaseError(
            "artifact inventory rejected the build; "
            f"forbidden={forbidden[:10]}, "
            f"unmapped_site_sources={unmapped_site_sources[:10]}, "
            f"build_only_or_undeclared={build_only_bundled[:10]}"
        )
    runtime_packages = []
    for entry in runtime_lock:
        runtime_packages.append({
            "name": entry.name,
            "normalized_name": entry.normalized_name,
            "version": entry.version,
            "wheel_sha256": entry.sha256,
            "analysis_entry_count": package_counts[entry.normalized_name],
        })
    return {
        "schema_version": 1,
        "source": "PyInstaller Analysis-00.toc",
        "entry_count": len(entries),
        "section_counts": dict(sorted(section_counts.items())),
        "type_counts": dict(sorted(type_counts.items())),
        "runtime_packages": runtime_packages,
        "undeclared_packages": [],
        "forbidden_entries": [],
        "build_only_package_entries": [],
        "entries": entries,
    }


def _git_value(args: list[str], default: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else default


def git_source_state() -> dict:
    status = _git_value(["status", "--porcelain=v1"], "")
    return {
        "commit": _git_value(["rev-parse", "HEAD"], "unknown"),
        "commit_timestamp": _git_value(["show", "-s", "--format=%cI", "HEAD"], "unknown"),
        "dirty": bool(status),
    }


def source_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseError("git ls-files failed")
    relative_paths = {
        Path(raw.decode("utf-8"))
        for raw in result.stdout.split(b"\0")
        if raw
    }
    relative_paths.update(REQUIRED_SOURCE_FILES)
    paths = []
    for relative in sorted(relative_paths, key=lambda path: path.as_posix()):
        absolute = ROOT / relative
        if absolute.is_file():
            paths.append(relative)
    missing = sorted(
        str(relative)
        for relative in REQUIRED_SOURCE_FILES
        if not (ROOT / relative).is_file()
    )
    if missing:
        raise ReleaseError(f"required source files are missing: {missing}")
    return paths


def source_tree_sha256(relative_paths: list[Path]) -> str:
    hasher = hashlib.sha256()
    for relative in relative_paths:
        hasher.update(relative.as_posix().encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(bytes.fromhex(sha256_file(ROOT / relative)))
        hasher.update(b"\0")
    return hasher.hexdigest()


def create_source_archive(
    output: Path,
    version: str,
    relative_paths: list[Path],
) -> None:
    prefix = f"SunoJump-{version}"
    fixed_timestamp = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for relative in relative_paths:
            info = zipfile.ZipInfo(
                f"{prefix}/{relative.as_posix()}",
                fixed_timestamp,
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (ROOT / relative).read_bytes())


def _license_map(inventory: list[dict]) -> dict[str, dict]:
    return {
        normalize_name(record["package"]): record
        for record in inventory
    }


def _runtime_dependency_graph(
    site_packages: Path,
    runtime_lock: list[LockEntry],
) -> dict[str, list[str]]:
    runtime_names = {entry.normalized_name for entry in runtime_lock}
    graph = {}
    for dist in importlib.metadata.distributions(path=[str(site_packages)]):
        name = normalize_name(dist.metadata["Name"])
        if name not in runtime_names:
            continue
        dependencies = set()
        for requirement in dist.requires or []:
            match = re.match(r"\s*([A-Za-z0-9_.-]+)", requirement)
            if match:
                dependency = normalize_name(match.group(1))
                if dependency in runtime_names:
                    dependencies.add(dependency)
        graph[name] = sorted(dependencies)
    return graph


def generate_cyclonedx_sbom(
    output: Path,
    version: str,
    executable_sha256: str,
    runtime_lock: list[LockEntry],
    license_inventory: list[dict],
    dependency_graph: dict[str, list[str]],
    native_runtime: dict,
    timestamp: str,
) -> None:
    licenses = _license_map(license_inventory)
    app_ref = f"pkg:github/SysAdminDoc/SunoJump@v{version}"
    components = []
    for entry in runtime_lock:
        record = licenses[entry.normalized_name]
        component_ref = f"pkg:pypi/{entry.normalized_name}@{entry.version}"
        components.append({
            "type": "library",
            "bom-ref": component_ref,
            "name": entry.name,
            "version": entry.version,
            "purl": component_ref,
            "hashes": [{"alg": "SHA-256", "content": entry.sha256}],
            "licenses": [{
                "license": {"id": record["reviewed_license"]},
            }],
            "externalReferences": [{
                "type": "vcs",
                "url": record["source_url"],
            }],
            "properties": [{
                "name": "sunojump:distribution",
                "value": record["distribution"],
            }],
        })
    native_properties = [
        {
            "name": f"sunojump:native:{name}",
            "value": str(value),
        }
        for name, value in sorted(native_runtime.items())
        if isinstance(value, (str, int, float, bool))
    ]
    dependencies = [{
        "ref": app_ref,
        "dependsOn": [
            f"pkg:pypi/{entry.normalized_name}@{entry.version}"
            for entry in runtime_lock
        ],
    }]
    entry_by_name = {
        entry.normalized_name: entry for entry in runtime_lock
    }
    for entry in runtime_lock:
        dependencies.append({
            "ref": f"pkg:pypi/{entry.normalized_name}@{entry.version}",
            "dependsOn": [
                f"pkg:pypi/{dependency}@{entry_by_name[dependency].version}"
                for dependency in dependency_graph.get(entry.normalized_name, [])
            ],
        })
    serial_seed = f"{version}:{executable_sha256}"
    payload = {
        "$schema": CYCLONEDX_SCHEMA,
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, serial_seed)}",
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "tools": {
                "components": [{
                    "type": "application",
                    "name": "PyInstaller",
                    "version": next(
                        entry.version
                        for entry in parse_hashed_lock(BUILD_LOCK)
                        if entry.normalized_name == "pyinstaller"
                    ),
                }],
            },
            "component": {
                "type": "application",
                "bom-ref": app_ref,
                "name": "SunoJump",
                "version": version,
                "hashes": [{
                    "alg": "SHA-256",
                    "content": executable_sha256,
                }],
                "licenses": [{
                    "license": {"id": "GPL-3.0-only"},
                }],
                "purl": app_ref,
                "externalReferences": [{
                    "type": "vcs",
                    "url": "https://github.com/SysAdminDoc/SunoJump",
                }],
                "properties": [
                    {
                        "name": "sunojump:source-license",
                        "value": "MIT",
                    },
                    {
                        "name": "sunojump:code-signing",
                        "value": "not-performed",
                    },
                    *native_properties,
                ],
            },
        },
        "components": components,
        "dependencies": dependencies,
    }
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_json(path: Path, payload: dict | list) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _copy_release_tree(stage: Path, destination: Path) -> None:
    resolved_destination = destination.resolve()
    expected_destination = DIST_DIR.resolve()
    if resolved_destination != expected_destination:
        raise ReleaseError(
            "refusing to replace anything except the declared release "
            f"directory: {expected_destination}"
        )
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(stage, destination)


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description=(
            "Build, inventory, and smoke-test the unsigned Windows release "
            "from hashed inputs in a temporary virtual environment."
        )
    )


def main(argv: list[str] | None = None) -> int:
    _parser().parse_args(argv)
    try:
        validate_build_host()
        version = source_version()
        runtime_lock = parse_hashed_lock(RUNTIME_LOCK)
        build_lock = parse_hashed_lock(BUILD_LOCK)
        compatibility_baseline = load_compatibility_baseline()
        timestamp = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        source_state = git_source_state()
        relative_sources = source_files()
        source_digest = source_tree_sha256(relative_sources)

        with tempfile.TemporaryDirectory(prefix="sunojump-release-") as temp:
            temp_root = Path(temp)
            venv_dir = temp_root / "venv"
            work_dir = temp_root / "pyinstaller-work"
            pyinstaller_dist = temp_root / "pyinstaller-dist"
            stage = temp_root / "release"
            smoke_dir = temp_root / "smoke"
            stage.mkdir()
            smoke_dir.mkdir()

            python = install_isolated_toolchain(venv_dir)
            installed = query_installed_distributions(python)
            validate_installed_distributions(
                installed,
                runtime_lock,
                build_lock,
            )

            print("Building unsigned executable with PyInstaller...", flush=True)
            build_started_ns = time.time_ns()
            _run_streaming(
                [
                    str(python),
                    "-m",
                    "PyInstaller",
                    "--clean",
                    "--noconfirm",
                    "--log-level",
                    "INFO",
                    "--distpath",
                    str(pyinstaller_dist),
                    "--workpath",
                    str(work_dir),
                    str(SPEC_FILE),
                ],
                timeout=1200,
            )
            executable = pyinstaller_dist / "SunoJump.exe"
            print(
                "Proving --version and --help against the fresh executable...",
                flush=True,
            )
            cli_smoke = verify_executable(
                executable,
                version,
                build_started_ns,
            )
            print("Capturing native runtime evidence...", flush=True)
            native_runtime = query_native_runtime(executable)
            validate_native_compatibility(
                native_runtime,
                compatibility_baseline,
            )
            print(
                "Rendering a generated fixture through the executable...",
                flush=True,
            )
            render_smoke = smoke_render_executable(
                executable,
                version,
                smoke_dir,
            )
            print("Launching and closing the foreground GUI...", flush=True)
            gui_smoke = smoke_gui_executable(executable, version)

            print(
                "Auditing the actual PyInstaller analysis inventory...",
                flush=True,
            )
            site_packages = _site_packages_for(python)
            artifact_inventory = build_artifact_inventory(
                work_dir / "SunoJump" / "Analysis-00.toc",
                site_packages,
                runtime_lock,
                build_lock,
            )

            release_executable = stage / "SunoJump.exe"
            shutil.copy2(executable, release_executable)
            shutil.copy2(RUNTIME_LOCK, stage / RUNTIME_LOCK.name)
            shutil.copy2(BUILD_LOCK, stage / BUILD_LOCK.name)
            shutil.copy2(
                COMPATIBILITY_BASELINE,
                stage / COMPATIBILITY_BASELINE.name,
            )

            source_archive_name = f"SunoJump-v{version}-source.zip"
            create_source_archive(
                stage / source_archive_name,
                version,
                relative_sources,
            )

            print(
                "Generating license, notice, and source-routing artifacts...",
                flush=True,
            )
            _run_streaming(
                [
                    str(python),
                    str(LICENSE_TOOL),
                    "--lock",
                    str(RUNTIME_LOCK),
                    "--write-inventory",
                    "--inventory",
                    str(stage / "license-inventory.json"),
                    "--notices",
                    str(stage / "THIRD_PARTY_NOTICES.txt"),
                    "--source-routing",
                    str(stage / "SOURCE_ROUTING.txt"),
                    "--app-version",
                    version,
                    "--source-archive",
                    source_archive_name,
                    "--source-commit",
                    source_state["commit"],
                ],
                timeout=120,
            )
            license_inventory = json.loads(
                (stage / "license-inventory.json").read_text(encoding="utf-8")
            )

            write_json(stage / "artifact-inventory.json", artifact_inventory)
            write_json(stage / "native-versions.json", native_runtime)
            dependency_graph = _runtime_dependency_graph(
                site_packages,
                runtime_lock,
            )
            generate_cyclonedx_sbom(
                stage / "sbom.cdx.json",
                version,
                cli_smoke["sha256"],
                runtime_lock,
                license_inventory,
                dependency_graph,
                native_runtime,
                timestamp,
            )

            provenance = {
                "schema_version": 1,
                "generated_at": timestamp,
                "application": {
                    "name": "SunoJump",
                    "version": version,
                },
                "source": {
                    **source_state,
                    "tree_sha256": source_digest,
                    "file_count": len(relative_sources),
                    "source_archive": source_archive_name,
                },
                "target": {
                    "os": "Windows",
                    "architecture": "x86_64",
                    "python": platform.python_version(),
                },
                "isolation": {
                    "temporary_virtual_environment": True,
                    "require_hashes": True,
                    "only_binary": True,
                    "user_site_disabled": True,
                    "installed_distributions": installed,
                },
                "dependency_locks": {
                    RUNTIME_LOCK.name: sha256_file(RUNTIME_LOCK),
                    BUILD_LOCK.name: sha256_file(BUILD_LOCK),
                },
                "compatibility": {
                    "baseline_sha256": sha256_file(
                        COMPATIBILITY_BASELINE
                    ),
                    "rollback": compatibility_baseline["rollback"],
                    "native_runtime": compatibility_baseline[
                        "native_runtime"
                    ],
                    "dsp_golden": compatibility_baseline["dsp_golden"],
                },
                "packaging": {
                    "tool": "PyInstaller",
                    "version": installed["pyinstaller"]["version"],
                    "one_file": True,
                    "unsigned": True,
                    "code_signing": "not-performed",
                    "upx": False,
                },
                "artifact": {
                    "name": release_executable.name,
                    "sha256": sha256_file(release_executable),
                    "bytes": release_executable.stat().st_size,
                },
                "inventory": {
                    "entry_count": artifact_inventory["entry_count"],
                    "undeclared_packages": [],
                    "forbidden_entries": [],
                },
                "native_runtime": native_runtime,
                "smoke_tests": {
                    "cli": cli_smoke,
                    "fixture_render": render_smoke,
                    "foreground_gui": gui_smoke,
                },
            }
            write_json(stage / "build-provenance.json", provenance)

            artifacts = [
                path
                for path in stage.iterdir()
                if path.is_file() and path.name != "SHA256SUMS"
            ]
            generate_sha256sums(artifacts, stage / "SHA256SUMS")
            _copy_release_tree(stage, DIST_DIR)

        final_executable = DIST_DIR / "SunoJump.exe"
        final_verification = verify_executable(
            final_executable,
            version,
            0,
        )
        expected_artifacts = {
            "SunoJump.exe",
            "SHA256SUMS",
            "SOURCE_ROUTING.txt",
            "THIRD_PARTY_NOTICES.txt",
            "artifact-inventory.json",
            "build-provenance.json",
            "compatibility_baseline.json",
            "license-inventory.json",
            "native-versions.json",
            "requirements-build-lock.txt",
            "requirements-lock.txt",
            "sbom.cdx.json",
            f"SunoJump-v{version}-source.zip",
        }
        actual_artifacts = {
            path.name for path in DIST_DIR.iterdir() if path.is_file()
        }
        if actual_artifacts != expected_artifacts:
            raise ReleaseError(
                "final release artifact set mismatch; "
                f"missing={sorted(expected_artifacts - actual_artifacts)}, "
                f"unexpected={sorted(actual_artifacts - expected_artifacts)}"
            )
        print(
            f"Unsigned release proved: {final_executable} "
            f"({final_verification['bytes']} bytes)"
        )
        return 0
    except (
        ReleaseError,
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as exc:
        print(f"Release build failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
