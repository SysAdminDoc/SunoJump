"""Optional Apache-2.0 Google ViSQOL audio-mode CLI adapter."""
from __future__ import annotations

import csv
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from scipy.signal import resample_poly
import soundfile as sf

from verifiers import (
    VerifierAdapter,
    VerifierResult,
    VerifierState,
    estimate_offset_seconds,
)


VISQOL_SAMPLE_RATE = 48000
VISQOL_BINARY_ENV = "VISQOL_BINARY"
VISQOL_TIMEOUT_SECONDS = 120


def _as_audio(values) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim == 1:
        array = array[:, np.newaxis]
    if array.ndim != 2 or not np.all(np.isfinite(array)):
        raise ValueError("ViSQOL inputs must be finite mono/stereo arrays")
    return array


def _resample(values: np.ndarray, sample_rate: int) -> np.ndarray:
    if sample_rate == VISQOL_SAMPLE_RATE:
        return values
    divisor = int(np.gcd(sample_rate, VISQOL_SAMPLE_RATE))
    return resample_poly(
        values,
        VISQOL_SAMPLE_RATE // divisor,
        sample_rate // divisor,
        axis=0,
    )


class Adapter(VerifierAdapter):
    name = "google.visqol_audio"
    version = "3-cli"
    metric = "mos_lqo"

    def __init__(self, binary_path: str | os.PathLike | None = None):
        requested = str(binary_path or os.environ.get(VISQOL_BINARY_ENV, "")).strip()
        self.binary_path = self._resolve_binary(requested)
        if self.binary_path:
            self.version = self._binary_version(self.binary_path)

    @staticmethod
    def _resolve_binary(requested: str) -> str | None:
        if not requested:
            return shutil.which("visqol")
        candidate = Path(requested).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        return shutil.which(requested)

    @staticmethod
    def _binary_version(binary_path: str) -> str:
        try:
            digest = hashlib.sha256(Path(binary_path).read_bytes()).hexdigest()
        except OSError:
            return "3-cli"
        return f"3-cli+sha256.{digest[:12]}"

    def is_available(self) -> bool:
        return self.binary_path is not None

    def _command(self) -> list[str]:
        if not self.binary_path:
            return []
        if Path(self.binary_path).suffix.lower() == ".py":
            return [sys.executable, self.binary_path]
        return [self.binary_path]

    @staticmethod
    def _parse_score(results_path: Path) -> float:
        with results_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            raise ValueError("ViSQOL results CSV did not contain a result row")
        normalized = {
            str(key).strip().lower().replace("-", "_"): value
            for key, value in rows[-1].items()
        }
        raw_value = normalized.get("moslqo", normalized.get("mos_lqo"))
        if raw_value is None:
            raise ValueError("ViSQOL results CSV did not contain MOS-LQO")
        value = float(raw_value)
        if not np.isfinite(value) or not 0.0 <= value <= 5.0:
            raise ValueError("ViSQOL MOS-LQO was outside the valid range")
        return value

    def score(self, original, processed, sr: int) -> VerifierResult:
        try:
            reference = _as_audio(original)
            degraded = _as_audio(processed)
        except (TypeError, ValueError) as exc:
            return VerifierResult(
                adapter=self.name,
                adapter_version=self.version,
                metric=self.metric,
                state=VerifierState.ERROR,
                reason=f"invalid_input:{type(exc).__name__}",
            )

        common_samples = min(reference.shape[0], degraded.shape[0])
        coverage = {
            "coverage_version": 1,
            "source_sample_rate_hz": int(sr),
            "visqol_sample_rate_hz": VISQOL_SAMPLE_RATE,
            "original_samples": int(reference.shape[0]),
            "processed_samples": int(degraded.shape[0]),
            "compared_samples": int(common_samples),
            "compared_seconds": (
                round(common_samples / float(sr), 6) if sr > 0 else 0.0
            ),
            "original_channels": int(reference.shape[1]),
            "processed_channels": int(degraded.shape[1]),
            "resampled": sr != VISQOL_SAMPLE_RATE,
        }
        offset = estimate_offset_seconds(reference, degraded, sr)
        if sr <= 0:
            return self.unavailable("invalid_sample_rate", coverage)
        if common_samples <= 0:
            return self.unavailable("empty_input", coverage)
        if not self.is_available():
            return VerifierResult(
                adapter=self.name,
                adapter_version=self.version,
                metric=self.metric,
                state=VerifierState.UNAVAILABLE,
                reason="visqol_binary_not_available",
                coverage=coverage,
                offset_seconds=offset,
            )

        try:
            reference = _resample(reference[:common_samples], sr)
            degraded = _resample(degraded[:common_samples], sr)
            common_48k = min(reference.shape[0], degraded.shape[0])
            reference = reference[:common_48k]
            degraded = degraded[:common_48k]
            with tempfile.TemporaryDirectory(prefix="sunojump-visqol-") as temp_dir:
                root = Path(temp_dir)
                reference_path = root / "reference.wav"
                degraded_path = root / "degraded.wav"
                results_path = root / "results.csv"
                sf.write(
                    reference_path,
                    reference,
                    VISQOL_SAMPLE_RATE,
                    subtype="PCM_16",
                )
                sf.write(
                    degraded_path,
                    degraded,
                    VISQOL_SAMPLE_RATE,
                    subtype="PCM_16",
                )
                command = self._command() + [
                    "--reference_file",
                    str(reference_path),
                    "--degraded_file",
                    str(degraded_path),
                    "--results_csv",
                    str(results_path),
                ]
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=VISQOL_TIMEOUT_SECONDS,
                    check=False,
                )
                if completed.returncode != 0:
                    raise RuntimeError(
                        f"visqol_exit_{completed.returncode}"
                    )
                value = self._parse_score(results_path)
        except Exception as exc:
            return VerifierResult(
                adapter=self.name,
                adapter_version=self.version,
                metric=self.metric,
                state=VerifierState.ERROR,
                reason=f"adapter_exception:{type(exc).__name__}",
                coverage=coverage,
                offset_seconds=offset,
            )

        return VerifierResult(
            adapter=self.name,
            adapter_version=self.version,
            metric=self.metric,
            state=VerifierState.MEASURED,
            value=value,
            unit="MOS-LQO",
            coverage=coverage,
            offset_seconds=offset,
        )
