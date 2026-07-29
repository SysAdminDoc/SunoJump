"""Bounded inspection and process-isolated decoding for untrusted audio."""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO


HEADER_INSPECTION_BYTES = 1024 * 1024
NPY_OVERHEAD_BYTES = 64 * 1024
WAVE_FORMAT_IMA_ADPCM = 0x0011
WAVE_FORMAT_EXTENSIBLE = 0xFFFE
MIN_LIBSNDFILE_VERSION = (1, 2, 2)

_CONTAINERS_BY_EXTENSION = {
    ".wav": {"wav"},
    ".flac": {"flac"},
    ".ogg": {"ogg"},
    ".opus": {"ogg"},
    ".aif": {"aiff"},
    ".aiff": {"aiff"},
    ".mp3": {"mp3"},
}


class DecodeCancelled(RuntimeError):
    """Raised when a caller cancels while the decoder process is active."""


@dataclass(frozen=True)
class DecodeLimits:
    max_input_bytes: int
    max_decoded_bytes: int
    max_channels: int
    max_sample_rate: int
    max_duration_seconds: float
    timeout_seconds: float
    worker_memory_bytes: int
    header_bytes: int = HEADER_INSPECTION_BYTES


def validate_libsndfile_version(version: str) -> tuple[int, int, int]:
    """Fail closed for unknown or older native decoder builds."""
    match = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)", str(version))
    if match is None:
        raise ValueError(f"cannot verify libsndfile runtime version: {version!r}")
    parsed = tuple(int(value) for value in match.groups())
    if parsed < MIN_LIBSNDFILE_VERSION:
        required = ".".join(str(value) for value in MIN_LIBSNDFILE_VERSION)
        raise ValueError(
            f"libsndfile {version} is below the required runtime floor {required}"
        )
    return parsed


def _is_ircam_header(header: bytes) -> bool:
    if len(header) < 4:
        return False
    return (
        header[0] == 0x64
        and header[1] == 0xA3
        and header[3] == 0x00
    ) or (
        header[0] == 0x00
        and header[2] == 0xA3
        and header[3] == 0x64
    )


def _is_mp3_header(header: bytes) -> bool:
    if header.startswith(b"ID3"):
        return True
    if len(header) < 2 or header[0] != 0xFF:
        return False
    # MPEG sync (11 bits), with reserved version/layer combinations excluded.
    return (
        header[1] & 0xE0 == 0xE0
        and header[1] & 0x18 != 0x08
        and header[1] & 0x06 != 0x00
    )


def _inspect_wave_header(header: bytes) -> dict[str, object]:
    if len(header) < 12:
        raise ValueError("unsupported or malformed WAV header")
    if header[:4] not in {b"RIFF", b"RF64", b"BW64"} or header[8:12] != b"WAVE":
        raise ValueError("unsupported or malformed WAV container")

    offset = 12
    while offset + 8 <= len(header):
        chunk_id = header[offset:offset + 4]
        chunk_size = struct.unpack_from("<I", header, offset + 4)[0]
        payload_start = offset + 8
        payload_end = payload_start + chunk_size
        if payload_end < payload_start:
            raise ValueError("malformed WAV chunk size")

        if chunk_id == b"fmt ":
            if chunk_size < 16 or payload_end > len(header):
                raise ValueError("WAV fmt chunk is malformed or exceeds inspection limit")
            fmt = header[payload_start:payload_end]
            format_tag = struct.unpack_from("<H", fmt, 0)[0]
            if format_tag == WAVE_FORMAT_IMA_ADPCM:
                raise ValueError(
                    "WAV IMA ADPCM is disabled because the bundled native decoder "
                    "is not proven patched"
                )
            subformat_tag = None
            if format_tag == WAVE_FORMAT_EXTENSIBLE:
                if len(fmt) < 40:
                    raise ValueError("malformed WAVE_FORMAT_EXTENSIBLE header")
                subformat_tag = struct.unpack_from("<H", fmt, 24)[0]
                if subformat_tag == WAVE_FORMAT_IMA_ADPCM:
                    raise ValueError(
                        "WAV extensible IMA ADPCM is disabled because the bundled "
                        "native decoder is not proven patched"
                    )
            return {
                "container": "wav",
                "wave_format_tag": format_tag,
                "wave_subformat_tag": subformat_tag,
            }

        if chunk_id == b"data":
            raise ValueError("WAV data chunk appears before required fmt chunk")
        if payload_end > len(header):
            raise ValueError("WAV fmt chunk was not found within inspection limit")
        offset = payload_end + (chunk_size & 1)

    raise ValueError("WAV fmt chunk was not found within inspection limit")


def _detect_container(header: bytes) -> tuple[str, dict[str, object]]:
    if _is_ircam_header(header):
        raise ValueError(
            "IRCAM audio is disabled because the bundled native decoder is not "
            "proven patched"
        )
    if header.startswith((b"RIFF", b"RF64", b"BW64")):
        details = _inspect_wave_header(header)
        return "wav", details
    if header.startswith(b"fLaC"):
        return "flac", {"container": "flac"}
    if header.startswith(b"OggS"):
        return "ogg", {"container": "ogg"}
    if len(header) >= 12 and header.startswith(b"FORM") and header[8:12] in {
        b"AIFF",
        b"AIFC",
    }:
        return "aiff", {"container": "aiff"}
    if _is_mp3_header(header):
        return "mp3", {"container": "mp3"}
    raise ValueError("unsupported or malformed audio container header")


def inspect_audio_stream(
    source: BinaryIO,
    extension: str,
    *,
    max_header_bytes: int = HEADER_INSPECTION_BYTES,
) -> dict[str, object]:
    """Inspect at most *max_header_bytes* without invoking a native decoder."""
    if max_header_bytes < 64:
        raise ValueError("audio header inspection limit is too small")
    normalized_extension = extension.lower()
    expected = _CONTAINERS_BY_EXTENSION.get(normalized_extension)
    if expected is None:
        raise ValueError(
            f"unsupported audio format: {normalized_extension or '(none)'}"
        )

    source.seek(0)
    header = source.read(max_header_bytes)
    source.seek(0)
    container, details = _detect_container(header)
    if container not in expected:
        raise ValueError(
            f"audio container mismatch: {normalized_extension} file contains "
            f"{container}"
        )
    return details


def inspect_audio_path(
    input_path: str | os.PathLike[str],
    *,
    max_input_bytes: int,
    max_header_bytes: int = HEADER_INSPECTION_BYTES,
) -> dict[str, object]:
    """Validate file identity/size and inspect its bounded header."""
    path = Path(input_path)
    if path.suffix.lower() not in _CONTAINERS_BY_EXTENSION:
        raise ValueError(f"unsupported audio format: {path.suffix or '(none)'}")
    try:
        stat = path.stat()
    except OSError as exc:
        raise ValueError(f"cannot access input file: {exc}") from exc
    if not path.is_file():
        raise ValueError("input path is not a file")
    if stat.st_size <= 0:
        raise ValueError("empty audio file")
    if stat.st_size > max_input_bytes:
        raise ValueError(
            f"input file is too large ({stat.st_size} bytes > "
            f"{max_input_bytes} bytes)"
        )
    try:
        with path.open("rb") as source:
            details = inspect_audio_stream(
                source,
                path.suffix,
                max_header_bytes=max_header_bytes,
            )
    except OSError as exc:
        raise ValueError(f"cannot inspect input file: {exc}") from exc
    details.update({"input_bytes": stat.st_size})
    return details


def _validate_audio_info(info: object, preview_seconds: float | None, limits: DecodeLimits) -> dict[str, object]:
    frames = int(getattr(info, "frames", 0) or 0)
    samplerate = int(getattr(info, "samplerate", 0) or 0)
    channels = int(getattr(info, "channels", 0) or 0)
    if frames <= 0:
        raise ValueError("empty audio file")
    if samplerate <= 0:
        raise ValueError("invalid sample rate")
    if samplerate > limits.max_sample_rate:
        raise ValueError(
            f"sample rate too high ({samplerate} Hz > "
            f"{limits.max_sample_rate} Hz)"
        )
    if channels <= 0:
        raise ValueError("invalid channel count")
    if channels > limits.max_channels:
        raise ValueError(
            f"too many channels ({channels} > {limits.max_channels})"
        )

    duration = frames / float(samplerate)
    preview_requested = preview_seconds is not None and preview_seconds > 0
    if not preview_requested and duration > limits.max_duration_seconds:
        raise ValueError(
            f"audio duration too long ({duration / 60.0:.1f} min > "
            f"{limits.max_duration_seconds / 60.0:.1f} min)"
        )
    read_frames = frames
    if preview_requested:
        read_frames = min(
            frames,
            max(1, int(float(preview_seconds) * samplerate)),
        )
    decoded_bytes = read_frames * channels * 8
    if decoded_bytes > limits.max_decoded_bytes:
        raise ValueError(
            f"decoded audio would exceed memory guardrail "
            f"({decoded_bytes} bytes > {limits.max_decoded_bytes} bytes)"
        )
    return {
        "frames": frames,
        "samplerate": samplerate,
        "channels": channels,
        "duration": duration,
        "read_frames": read_frames,
        "decoded_bytes": decoded_bytes,
        "format": str(getattr(info, "format", "") or ""),
        "subtype": str(getattr(info, "subtype", "") or ""),
        "endian": str(getattr(info, "endian", "") or ""),
    }


_WINDOWS_JOB_HANDLE = None


def _apply_worker_memory_limit(limit_bytes: int) -> None:
    if limit_bytes <= 0:
        raise ValueError("decoder memory limit must be positive")
    if os.name != "nt":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
        return

    import ctypes
    from ctypes import wintypes

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x00000100 | 0x00002000
    info.ProcessMemoryLimit = limit_bytes
    if not kernel32.SetInformationJobObject(
        handle,
        9,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise ctypes.WinError(error)
    if not kernel32.AssignProcessToJobObject(
        handle,
        kernel32.GetCurrentProcess(),
    ):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise ctypes.WinError(error)

    global _WINDOWS_JOB_HANDLE
    _WINDOWS_JOB_HANDLE = handle


def _write_worker_result(result_path: str, payload: dict[str, object]) -> None:
    Path(result_path).write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )


def _decode_worker(
    input_path: str,
    audio_path: str,
    result_path: str,
    preview_seconds: float | None,
    limits: DecodeLimits,
) -> None:
    try:
        _apply_worker_memory_limit(limits.worker_memory_bytes)
        import numpy as np
        import soundfile as sf

        validate_libsndfile_version(
            str(getattr(sf, "__libsndfile_version__", "unknown"))
        )
        path = Path(input_path)
        with path.open("rb") as source:
            file_stat = os.fstat(source.fileno())
            if file_stat.st_size <= 0:
                raise ValueError("empty audio file")
            if file_stat.st_size > limits.max_input_bytes:
                raise ValueError(
                    f"input file is too large ({file_stat.st_size} bytes > "
                    f"{limits.max_input_bytes} bytes)"
                )
            header = inspect_audio_stream(
                source,
                path.suffix,
                max_header_bytes=limits.header_bytes,
            )
            source.seek(0)
            info = sf.info(source)
            metadata = _validate_audio_info(info, preview_seconds, limits)
            source.seek(0)
            read_kwargs: dict[str, object] = {"dtype": "float64"}
            if preview_seconds is not None and preview_seconds > 0:
                read_kwargs["frames"] = metadata["read_frames"]
            audio, samplerate = sf.read(source, **read_kwargs)

        if int(samplerate) != metadata["samplerate"]:
            raise ValueError("decoder sample rate changed between inspection and read")
        if audio.size <= 0:
            raise ValueError("empty audio file")
        if audio.nbytes > limits.max_decoded_bytes:
            raise ValueError(
                f"decoded audio exceeded memory guardrail "
                f"({audio.nbytes} bytes > {limits.max_decoded_bytes} bytes)"
            )
        if not np.all(np.isfinite(audio)):
            raise ValueError("decoded audio contains non-finite samples")
        expected_channels = 1 if audio.ndim == 1 else int(audio.shape[1])
        if expected_channels != metadata["channels"]:
            raise ValueError("decoder channel count changed between inspection and read")

        np.save(audio_path, audio, allow_pickle=False)
        metadata["decoded_bytes"] = int(audio.nbytes)
        metadata["header"] = header
        metadata["soundfile_version"] = str(getattr(sf, "__version__", "unknown"))
        metadata["libsndfile_version"] = str(
            getattr(sf, "__libsndfile_version__", "unknown")
        )
        _write_worker_result(result_path, {"ok": True, "metadata": metadata})
    except BaseException as exc:
        try:
            _write_worker_result(
                result_path,
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        except OSError:
            pass


def worker_cli_main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        return 2
    try:
        request = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        limits = DecodeLimits(**request["limits"])
        _decode_worker(
            request["input_path"],
            request["audio_path"],
            request["result_path"],
            request.get("preview_seconds"),
            limits,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return 2
    return 0


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5.0)


def _decoder_command(request_path: str) -> list[str]:
    if bool(getattr(sys, "frozen", False)):
        return [sys.executable, "--safe-decode-worker", request_path]
    worker_script = Path(__file__).with_name("safe_audio_worker.py")
    return [sys.executable, str(worker_script), request_path]


def decode_audio_isolated(
    input_path: str | os.PathLike[str],
    preview_seconds: float | None,
    limits: DecodeLimits,
    *,
    cancel_event: object | None = None,
    _worker_command: list[str] | None = None,
) -> tuple[object, int, dict[str, object]]:
    """Decode in a capped child process and validate its bounded output."""
    if cancel_event is not None and bool(cancel_event.is_set()):
        raise DecodeCancelled("audio decode cancelled")

    with tempfile.TemporaryDirectory(prefix="sunojump-decode-") as temp_dir:
        audio_path = str(Path(temp_dir) / "audio.npy")
        result_path = str(Path(temp_dir) / "result.json")
        request_path = str(Path(temp_dir) / "request.json")
        Path(request_path).write_text(
            json.dumps(
                {
                    "input_path": str(input_path),
                    "audio_path": audio_path,
                    "result_path": result_path,
                    "preview_seconds": preview_seconds,
                    "limits": asdict(limits),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        command = list(_worker_command or _decoder_command(request_path))
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if os.name == "nt"
            else 0
        )
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        deadline = time.monotonic() + limits.timeout_seconds
        while process.poll() is None:
            if cancel_event is not None and bool(cancel_event.is_set()):
                _stop_process(process)
                raise DecodeCancelled("audio decode cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_process(process)
                raise ValueError(
                    f"audio decode exceeded {limits.timeout_seconds:.0f} second timeout"
                )
            time.sleep(min(0.05, remaining))

        result_file = Path(result_path)
        if not result_file.is_file():
            raise ValueError(
                f"isolated audio decoder exited without a result "
                f"(exit code {process.returncode})"
            )
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"isolated audio decoder returned invalid status: {exc}") from exc
        if not result.get("ok"):
            raise ValueError(str(result.get("error") or "isolated audio decode failed"))

        output = Path(audio_path)
        if not output.is_file():
            raise ValueError("isolated audio decoder returned no sample payload")
        if output.stat().st_size > limits.max_decoded_bytes + NPY_OVERHEAD_BYTES:
            raise ValueError("isolated audio decoder exceeded its output cap")

        import numpy as np

        audio = np.load(output, allow_pickle=False)
        if audio.nbytes > limits.max_decoded_bytes:
            raise ValueError("isolated audio decoder exceeded its decoded-memory cap")
        metadata = result.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("isolated audio decoder returned no metadata")
        return audio, int(metadata["samplerate"]), metadata
