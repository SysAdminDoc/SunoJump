"""Typed render and batch outcomes shared by the GUI and CLI."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import math


RESULT_SCHEMA_VERSION = 1


class RenderState(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RenderErrorCode(str, Enum):
    CANCELLED = "cancelled"
    INVALID_INPUT = "invalid_input"
    DECODE_FAILED = "decode_failed"
    EMPTY_AUDIO = "empty_audio"
    NO_PASSES_ENABLED = "no_passes_enabled"
    PASS_FAILED = "pass_failed"
    OUTPUT_MAPPING_INVALID = "output_mapping_invalid"
    ENCODER_UNAVAILABLE = "encoder_unavailable"
    OUTPUT_WRITE_FAILED = "output_write_failed"
    OUTPUT_DECODE_FAILED = "output_decode_failed"
    OUTPUT_NONFINITE = "output_nonfinite"
    OUTPUT_SILENT = "output_silent"
    OUTPUT_DURATION_MISMATCH = "output_duration_mismatch"
    OUTPUT_SAMPLE_RATE_MISMATCH = "output_sample_rate_mismatch"
    OUTPUT_CHANNEL_MISMATCH = "output_channel_mismatch"
    OUTPUT_HASH_FAILED = "output_hash_failed"
    SIDECAR_WRITE_FAILED = "sidecar_write_failed"
    OUTPUT_DIR_UNAVAILABLE = "output_dir_unavailable"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True)
class OutputValidation:
    input_sha256: str
    output_sha256: str
    output_bytes: int
    sample_rate_hz: int
    channels: int
    frames: int
    duration_seconds: float
    peak: float
    hashes_distinct: bool
    decoder: str

    def __post_init__(self) -> None:
        for name, value in (
            ("input_sha256", self.input_sha256),
            ("output_sha256", self.output_sha256),
        ):
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if self.output_bytes <= 0 or self.sample_rate_hz <= 0:
            raise ValueError("validated output must have bytes and a sample rate")
        if self.channels <= 0 or self.frames <= 0:
            raise ValueError("validated output must have channels and frames")
        if (
            not math.isfinite(self.duration_seconds)
            or not math.isfinite(self.peak)
            or self.duration_seconds <= 0
            or self.peak <= 0
        ):
            raise ValueError("validated output must have duration and non-silent samples")
        if not self.decoder:
            raise ValueError("validated output must name its decoder")

    def to_dict(self) -> dict:
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "input_sha256": self.input_sha256,
            "output_sha256": self.output_sha256,
            "output_bytes": self.output_bytes,
            "sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
            "frames": self.frames,
            "duration_seconds": round(self.duration_seconds, 6),
            "peak": round(self.peak, 9),
            "hashes_distinct": self.hashes_distinct,
            "decoder": self.decoder,
        }


@dataclass(frozen=True)
class RenderResult:
    state: RenderState
    input_path: str
    output_path: str | None = None
    error_code: RenderErrorCode | None = None
    message: str = ""
    elapsed_seconds: float = 0.0
    validation: OutputValidation | None = None
    effective_seed: int | None = None
    sidecar_path: str | None = None
    sidecar_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, RenderState):
            raise ValueError("render state must be a RenderState")
        if not self.input_path:
            raise ValueError("render result requires an input path")
        if self.state is RenderState.SUCCEEDED:
            if self.error_code is not None:
                raise ValueError("succeeded render cannot have an error code")
            if not self.output_path or self.validation is None:
                raise ValueError("succeeded render requires a validated output")
        else:
            if not isinstance(self.error_code, RenderErrorCode):
                raise ValueError("non-success render requires a typed error code")
        if self.state is RenderState.PARTIAL:
            if not self.output_path or self.validation is None:
                raise ValueError("partial render requires a validated usable output")
        elif self.state is not RenderState.SUCCEEDED:
            if self.output_path is not None or self.validation is not None:
                raise ValueError("failed/cancelled render cannot expose an output")
        if not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0:
            raise ValueError("elapsed time cannot be negative")
        if (
            self.effective_seed is not None
            and (
                isinstance(self.effective_seed, bool)
                or not isinstance(self.effective_seed, int)
                or self.effective_seed < 0
            )
        ):
            raise ValueError("effective seed must be a non-negative integer")
        if (self.sidecar_path is None) != (self.sidecar_sha256 is None):
            raise ValueError("sidecar path and hash must be supplied together")
        if self.sidecar_sha256 is not None:
            if (
                len(self.sidecar_sha256) != 64
                or any(ch not in "0123456789abcdef" for ch in self.sidecar_sha256)
            ):
                raise ValueError("sidecar_sha256 must be a lowercase SHA-256 digest")
            if not self.usable_output:
                raise ValueError("failed/cancelled render cannot expose a sidecar")

    @property
    def usable_output(self) -> bool:
        return self.state in {RenderState.SUCCEEDED, RenderState.PARTIAL}

    def __bool__(self) -> bool:
        return self.usable_output

    def to_dict(self) -> dict:
        payload = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "state": self.state.value,
            "input_path": self.input_path,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
        }
        if self.output_path is not None:
            payload["output_path"] = self.output_path
        if self.error_code is not None:
            payload["error_code"] = self.error_code.value
        if self.message:
            payload["message"] = self.message
        if self.validation is not None:
            payload["validation"] = self.validation.to_dict()
        if self.effective_seed is not None:
            payload["effective_seed"] = self.effective_seed
        if self.sidecar_path is not None:
            payload["sidecar_path"] = self.sidecar_path
            payload["sidecar_sha256"] = self.sidecar_sha256
        return payload


@dataclass(frozen=True)
class BatchResult:
    state: RenderState
    results: tuple[RenderResult, ...]
    elapsed_seconds: float

    @classmethod
    def from_results(
        cls,
        results: list[RenderResult] | tuple[RenderResult, ...],
        elapsed_seconds: float,
    ) -> "BatchResult":
        result_tuple = tuple(results)
        states = [result.state for result in result_tuple]
        if any(state is RenderState.CANCELLED for state in states):
            state = RenderState.CANCELLED
        elif states and all(state is RenderState.SUCCEEDED for state in states):
            state = RenderState.SUCCEEDED
        elif any(
            state in {RenderState.SUCCEEDED, RenderState.PARTIAL}
            for state in states
        ):
            state = RenderState.PARTIAL
        else:
            state = RenderState.FAILED
        return cls(
            state=state,
            results=result_tuple,
            elapsed_seconds=max(0.0, float(elapsed_seconds)),
        )

    @property
    def counts(self) -> dict[str, int]:
        counts = Counter(result.state.value for result in self.results)
        return {state.value: counts[state.value] for state in RenderState}

    @property
    def error_counts(self) -> dict[str, int]:
        counts = Counter(
            result.error_code.value
            for result in self.results
            if result.error_code is not None
        )
        return dict(sorted(counts.items()))

    def to_dict(self) -> dict:
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "state": self.state.value,
            "counts": self.counts,
            "error_counts": self.error_counts,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
            "results": [result.to_dict() for result in self.results],
        }


def format_render_result(result: RenderResult) -> str:
    text = f"Result: {result.state.value}"
    if result.error_code is not None:
        text += f" [{result.error_code.value}]"
    if result.message:
        text += f" — {result.message}"
    if result.validation is not None:
        text += f" — sha256:{result.validation.output_sha256[:12]}"
    if result.effective_seed is not None:
        text += f" — seed:{result.effective_seed}"
    if result.sidecar_sha256 is not None:
        text += f" — sidecar:{result.sidecar_sha256[:12]}"
    return text


def format_batch_result(result: BatchResult) -> str:
    counts = result.counts
    summary = ", ".join(
        f"{counts[state.value]} {state.value}"
        for state in RenderState
    )
    errors = ", ".join(
        f"{code}={count}" for code, count in result.error_counts.items()
    )
    error_text = f"; errors: {errors}" if errors else ""
    return (
        f"Batch {result.state.value}: {summary}{error_text} "
        f"({result.elapsed_seconds:.1f}s)"
    )
