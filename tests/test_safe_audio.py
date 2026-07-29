#!/usr/bin/env python3
import json
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
import soundfile as sf

import safe_audio
import sunojump


def _wave_bytes(format_payload: bytes, samples: bytes = b"\x00\x00") -> bytes:
    fmt_chunk = b"fmt " + struct.pack("<I", len(format_payload)) + format_payload
    data_chunk = b"data" + struct.pack("<I", len(samples)) + samples
    body = b"WAVE" + fmt_chunk + data_chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _pcm_wave_bytes() -> bytes:
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
    return _wave_bytes(fmt)


class HeaderInspectionTests(unittest.TestCase):
    def _inspect_bytes(self, suffix: str, payload: bytes):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / f"input{suffix}"
            path.write_bytes(payload)
            return safe_audio.inspect_audio_path(
                path,
                max_input_bytes=sunojump.MAX_INPUT_FILE_BYTES,
            )

    def test_pcm_wave_header_is_accepted(self):
        details = self._inspect_bytes(".wav", _pcm_wave_bytes())
        self.assertEqual(details["container"], "wav")
        self.assertEqual(details["wave_format_tag"], 1)

    def test_container_extension_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "container mismatch"):
            self._inspect_bytes(".wav", b"fLaC" + b"\x00" * 64)

    def test_ircam_header_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "IRCAM audio is disabled"):
            self._inspect_bytes(
                ".wav",
                bytes((0x64, 0xA3, 0x02, 0x00)) + b"\x00" * 64,
            )

    def test_wave_ima_adpcm_is_rejected(self):
        fmt = struct.pack("<HHIIHHHH", 0x0011, 1, 8000, 4055, 256, 4, 2, 505)
        with self.assertRaisesRegex(ValueError, "WAV IMA ADPCM is disabled"):
            self._inspect_bytes(".wav", _wave_bytes(fmt))

    def test_wave_extensible_ima_adpcm_is_rejected(self):
        wave_format_ex = struct.pack(
            "<HHIIHHH",
            0xFFFE,
            1,
            8000,
            16000,
            2,
            16,
            22,
        )
        extension = (
            struct.pack("<HI", 16, 0)
            + struct.pack("<IHH8s", 0x0011, 0, 0x0010, b"\x80\x00\x00\xaa\x00\x38\x9b\x71")
        )
        with self.assertRaisesRegex(ValueError, "extensible IMA ADPCM"):
            self._inspect_bytes(".wav", _wave_bytes(wave_format_ex + extension))

    def test_wave_header_search_is_bounded(self):
        declared_junk = safe_audio.HEADER_INSPECTION_BYTES
        payload = (
            b"RIFF"
            + struct.pack("<I", declared_junk + 4)
            + b"WAVEJUNK"
            + struct.pack("<I", declared_junk)
            + b"\x00" * (safe_audio.HEADER_INSPECTION_BYTES - 20)
        )
        with self.assertRaisesRegex(ValueError, "inspection limit"):
            self._inspect_bytes(".wav", payload)


class IsolatedDecodeTests(unittest.TestCase):
    def test_decode_returns_bounded_preview_and_native_evidence(self):
        samplerate = 8000
        t = np.arange(samplerate * 2, dtype=np.float64) / samplerate
        audio = 0.2 * np.sin(2.0 * np.pi * 440.0 * t)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "input.wav"
            sf.write(path, audio, samplerate)

            decoded, decoded_rate, metadata = safe_audio.decode_audio_isolated(
                path,
                0.5,
                sunojump._decode_limits(),
            )

        self.assertEqual(decoded_rate, samplerate)
        self.assertEqual(decoded.shape, (samplerate // 2,))
        self.assertLessEqual(decoded.nbytes, sunojump.MAX_DECODED_AUDIO_BYTES)
        self.assertEqual(metadata["libsndfile_version"], sf.__libsndfile_version__)
        self.assertEqual(metadata["header"]["container"], "wav")

    def test_decode_honors_cancellation_before_spawn(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(safe_audio.DecodeCancelled):
            safe_audio.decode_audio_isolated(
                "unused.wav",
                None,
                sunojump._decode_limits(),
                cancel_event=cancel,
            )

    def test_decode_worker_timeout_is_fail_closed(self):
        limits = replace(sunojump._decode_limits(), timeout_seconds=0.05)
        with self.assertRaisesRegex(ValueError, "exceeded .* timeout"):
            safe_audio.decode_audio_isolated(
                "unused.wav",
                None,
                limits,
                _worker_command=[
                    sys.executable,
                    "-c",
                    "import time; time.sleep(5)",
                ],
            )


class NativeRuntimeEvidenceTests(unittest.TestCase):
    def test_native_version_gate_rejects_unknown_and_old_builds(self):
        with self.assertRaisesRegex(ValueError, "cannot verify"):
            safe_audio.validate_libsndfile_version("unknown")
        with self.assertRaisesRegex(ValueError, "below the required"):
            safe_audio.validate_libsndfile_version("1.2.1")
        self.assertEqual(
            safe_audio.validate_libsndfile_version("1.2.2"),
            (1, 2, 2),
        )

    def test_runtime_report_names_native_version_and_containment(self):
        report = sunojump._native_runtime_report()
        self.assertEqual(report["libsndfile"], sf.__libsndfile_version__)
        self.assertEqual(report["runtime_gate"], "pass-with-contained-formats")
        self.assertEqual(report["decode_isolation"], "spawned-process")
        self.assertIn("WAV IMA ADPCM", report["blocked_native_formats"])

    def test_native_runtime_cli_is_machine_readable(self):
        result = subprocess.run(
            [sys.executable, "sunojump.py", "--native-runtime"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["libsndfile"], sf.__libsndfile_version__)


if __name__ == "__main__":
    unittest.main()
