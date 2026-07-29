"""Typed local verifier results for SunoJump evidence surfaces."""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from enum import Enum

import numpy as np


VERIFIER_SCHEMA_VERSION = 1
MIN_CONSTELLATION_SECONDS = 2.0
MAX_CONSTELLATION_SECONDS = 30.0
MIN_CONSTELLATION_LANDMARKS = 20
NEAR_SILENCE_PEAK = 1e-6


class VerifierState(str, Enum):
    MEASURED = "measured"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True)
class VerifierResult:
    adapter: str
    adapter_version: str
    metric: str
    state: VerifierState
    value: float | None = None
    unit: str | None = None
    reason: str | None = None
    coverage: dict[str, int | float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.state, VerifierState):
            raise ValueError("verifier state must be a VerifierState")
        if not self.adapter or not self.adapter_version or not self.metric:
            raise ValueError("verifier results require adapter, version, and metric")
        if self.state is VerifierState.MEASURED:
            if self.value is None or not np.isfinite(self.value):
                raise ValueError("measured verifier results require a finite value")
            if not self.unit or not self.coverage:
                raise ValueError("measured verifier results require unit and coverage")
        elif self.value is not None:
            raise ValueError("unavailable/error verifier results cannot carry a value")
        if self.state is not VerifierState.MEASURED and not self.reason:
            raise ValueError("unavailable/error verifier results require a reason")

    @property
    def available(self) -> bool:
        return self.state is not VerifierState.UNAVAILABLE

    def to_dict(self) -> dict:
        payload = {
            "schema_version": VERIFIER_SCHEMA_VERSION,
            "adapter": self.adapter,
            "adapter_version": self.adapter_version,
            "metric": self.metric,
            "state": self.state.value,
            "coverage": self.coverage,
        }
        if self.state is VerifierState.MEASURED:
            payload["value"] = round(float(self.value), 2)
            payload["unit"] = self.unit
        else:
            payload["reason"] = self.reason
        return payload


class VerifierAdapter:
    name = "base"
    version = "0"
    metric = "unknown"

    def is_available(self) -> bool:
        return False

    def unavailable(self, reason: str, coverage: dict | None = None) -> VerifierResult:
        return VerifierResult(
            adapter=self.name,
            adapter_version=self.version,
            metric=self.metric,
            state=VerifierState.UNAVAILABLE,
            reason=reason,
            coverage=coverage or {},
        )

    def score(self, original, processed, sr: int) -> VerifierResult:
        return self.unavailable("not_implemented")


class ConstellationVerifier(VerifierAdapter):
    name = "sunojump.constellation"
    version = "1"
    metric = "landmark_overlap"

    def __init__(self, processor=None):
        self._processor = processor

    def is_available(self) -> bool:
        return self._processor is not None

    @staticmethod
    def _coverage(original, processed, sr: int) -> tuple[int, dict[str, int | float]]:
        original_samples = int(len(original))
        processed_samples = int(len(processed))
        common_samples = min(original_samples, processed_samples)
        compared_samples = min(
            common_samples,
            max(0, int(sr * MAX_CONSTELLATION_SECONDS)),
        )
        coverage = {
            "coverage_version": 1,
            "sample_rate_hz": int(sr),
            "original_samples": original_samples,
            "processed_samples": processed_samples,
            "common_samples": common_samples,
            "compared_samples": compared_samples,
            "compared_seconds": round(
                compared_samples / float(sr), 4
            ) if sr > 0 else 0.0,
            "original_fraction": round(
                compared_samples / float(original_samples), 6
            ) if original_samples else 0.0,
            "processed_fraction": round(
                compared_samples / float(processed_samples), 6
            ) if processed_samples else 0.0,
        }
        return compared_samples, coverage

    def score(self, original, processed, sr: int) -> VerifierResult:
        compared_samples, coverage = self._coverage(original, processed, sr)
        if not self.is_available():
            return self.unavailable("adapter_not_initialized", coverage)
        if sr <= 0:
            return self.unavailable("invalid_sample_rate", coverage)
        if compared_samples <= 0:
            return self.unavailable("empty_input", coverage)
        if coverage["compared_seconds"] < MIN_CONSTELLATION_SECONDS:
            return self.unavailable("input_too_short", coverage)

        original_view = np.asarray(original[:compared_samples])
        processed_view = np.asarray(processed[:compared_samples])
        original_peak = float(np.max(np.abs(original_view)))
        processed_peak = float(np.max(np.abs(processed_view)))
        coverage["original_peak"] = round(original_peak, 8)
        coverage["processed_peak"] = round(processed_peak, 8)
        if (
            original_peak < NEAR_SILENCE_PEAK
            or processed_peak < NEAR_SILENCE_PEAK
        ):
            return self.unavailable("near_silence", coverage)

        try:
            original_hashes = self._processor._constellation_hashes(
                original_view,
                sr,
            )
            processed_hashes = self._processor._constellation_hashes(
                processed_view,
                sr,
            )
            shared = original_hashes & processed_hashes
            coverage.update({
                "original_landmarks": len(original_hashes),
                "processed_landmarks": len(processed_hashes),
                "shared_landmarks": len(shared),
            })
            if (
                len(original_hashes) < MIN_CONSTELLATION_LANDMARKS
                or len(processed_hashes) < MIN_CONSTELLATION_LANDMARKS
            ):
                return self.unavailable("insufficient_landmarks", coverage)
            union = original_hashes | processed_hashes
            if not union:
                return self.unavailable("insufficient_landmarks", coverage)
            value = 100.0 * len(shared) / len(union)
            return VerifierResult(
                adapter=self.name,
                adapter_version=self.version,
                metric=self.metric,
                state=VerifierState.MEASURED,
                value=value,
                unit="percent",
                coverage=coverage,
            )
        except Exception as exc:
            return VerifierResult(
                adapter=self.name,
                adapter_version=self.version,
                metric=self.metric,
                state=VerifierState.ERROR,
                reason=f"adapter_exception:{type(exc).__name__}",
                coverage=coverage,
            )


def format_verifier_result(result: VerifierResult) -> str:
    label = f"{result.adapter} v{result.adapter_version}"
    seconds = float(result.coverage.get("compared_seconds", 0.0))
    if result.state is VerifierState.MEASURED:
        original = int(result.coverage.get("original_landmarks", 0))
        processed = int(result.coverage.get("processed_landmarks", 0))
        return (
            f"Local landmark overlap [{label}]: {result.value:.0f}% "
            f"({seconds:.2f}s; {original}/{processed} landmarks; "
            "experimental; no platform inference)"
        )
    return (
        f"Local landmark overlap [{label}]: {result.state.value} "
        f"({result.reason}; {seconds:.2f}s; experimental; "
        "no platform inference)"
    )


EXTERNAL_ADAPTERS = {
    "chromaprint": "verifiers_chromaprint",
    "dejavu": "verifiers_dejavu",
    "panako": "verifiers_panako",
}


def discover_adapters(processor=None) -> list[VerifierAdapter]:
    adapters: list[VerifierAdapter] = [ConstellationVerifier(processor)]
    for module_name in EXTERNAL_ADAPTERS.values():
        try:
            module = importlib.import_module(module_name)
            adapter_class = getattr(module, "Adapter", None)
            if adapter_class and callable(adapter_class):
                adapters.append(adapter_class())
        except Exception:
            pass
    return adapters


def run_all(
    adapters: list[VerifierAdapter],
    original,
    processed,
    sr: int,
) -> list[VerifierResult]:
    results = []
    for adapter in adapters:
        if adapter.is_available():
            results.append(adapter.score(original, processed, sr))
        else:
            results.append(adapter.unavailable("adapter_not_available"))
    return results
