"""Objective audio measurements used by regression and reporting paths."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import lfilter, resample_poly


BS1770_VERSION = "ITU-R BS.1770-5"
ABSOLUTE_GATE_LUFS = -70.0
RELATIVE_GATE_LU = -10.0
BLOCK_SECONDS = 0.400
BLOCK_STEP_SECONDS = 0.100
MEASUREMENT_CHUNK_FRAMES = 65536


@dataclass(frozen=True)
class AudioQualityMeasurement:
    standard: str
    sample_rate_hz: int
    channels: int
    duration_seconds: float
    integrated_lufs: float | None
    true_peak_dbtp: float | None
    true_peak_oversample: int

    def to_dict(self) -> dict:
        return {
            "standard": self.standard,
            "sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
            "duration_seconds": round(self.duration_seconds, 6),
            "integrated_lufs": (
                round(self.integrated_lufs, 4)
                if self.integrated_lufs is not None
                else None
            ),
            "true_peak_dbtp": (
                round(self.true_peak_dbtp, 4)
                if self.true_peak_dbtp is not None
                else None
            ),
            "true_peak_oversample": self.true_peak_oversample,
        }


def _as_channels(audio) -> np.ndarray:
    values = np.asarray(audio, dtype=np.float64)
    if values.ndim == 1:
        values = values[:, np.newaxis]
    if values.ndim != 2:
        raise ValueError("audio must be a one- or two-dimensional array")
    return values


def _iter_chunks(values, chunk_frames=MEASUREMENT_CHUNK_FRAMES):
    for start in range(0, values.shape[0], chunk_frames):
        chunk = np.asarray(
            values[start:start + chunk_frames],
            dtype=np.float64,
        )
        if not np.all(np.isfinite(chunk)):
            raise ValueError("audio must contain only finite samples")
        yield start, chunk


def _k_weighting_coefficients(sample_rate: int):
    if sample_rate <= 0:
        raise ValueError("sample rate must be positive")

    shelf_frequency = 1681.974450955533
    shelf_gain_db = 3.999843853973347
    shelf_q = 0.7071752369554196
    shelf_k = np.tan(np.pi * shelf_frequency / sample_rate)
    shelf_vh = 10.0 ** (shelf_gain_db / 20.0)
    shelf_vb = shelf_vh ** 0.4996667741545416
    shelf_a0 = 1.0 + shelf_k / shelf_q + shelf_k ** 2
    shelf_b = np.array([
        shelf_vh + shelf_vb * shelf_k / shelf_q + shelf_k ** 2,
        2.0 * (shelf_k ** 2 - shelf_vh),
        shelf_vh - shelf_vb * shelf_k / shelf_q + shelf_k ** 2,
    ]) / shelf_a0
    shelf_a = np.array([
        1.0,
        2.0 * (shelf_k ** 2 - 1.0) / shelf_a0,
        (1.0 - shelf_k / shelf_q + shelf_k ** 2) / shelf_a0,
    ])

    high_pass_frequency = 38.13547087602444
    high_pass_q = 0.5003270373238773
    high_pass_k = np.tan(np.pi * high_pass_frequency / sample_rate)
    high_pass_a0 = 1.0 + high_pass_k / high_pass_q + high_pass_k ** 2
    high_pass_b = np.array([1.0, -2.0, 1.0]) / high_pass_a0
    high_pass_a = np.array([
        1.0,
        2.0 * (high_pass_k ** 2 - 1.0) / high_pass_a0,
        (1.0 - high_pass_k / high_pass_q + high_pass_k ** 2)
        / high_pass_a0,
    ])
    return (shelf_b, shelf_a), (high_pass_b, high_pass_a)


def _integrated_loudness(values: np.ndarray, sample_rate: int) -> float | None:
    block_samples = int(round(BLOCK_SECONDS * sample_rate))
    step_samples = int(round(BLOCK_STEP_SECONDS * sample_rate))
    if values.shape[0] < block_samples or block_samples <= 0 or step_samples <= 0:
        return None

    filters = _k_weighting_coefficients(sample_rate)
    states = [
        np.zeros((max(len(numerator), len(denominator)) - 1, values.shape[1]))
        for numerator, denominator in filters
    ]
    channel_weights = np.ones(values.shape[1], dtype=np.float64)
    if values.shape[1] >= 5:
        channel_weights[3:5] = 1.41
    powers = []
    power_buffer = np.empty(0, dtype=np.float64)
    buffer_start = 0
    next_block_start = 0
    received = 0
    for _, chunk in _iter_chunks(values):
        filtered = chunk
        for index, (numerator, denominator) in enumerate(filters):
            filtered, states[index] = lfilter(
                numerator,
                denominator,
                filtered,
                axis=0,
                zi=states[index],
            )
        sample_power = np.sum(
            filtered ** 2 * channel_weights[np.newaxis, :],
            axis=1,
        )
        power_buffer = np.concatenate((power_buffer, sample_power))
        received += len(sample_power)
        while next_block_start + block_samples <= received:
            local_start = next_block_start - buffer_start
            block = power_buffer[local_start:local_start + block_samples]
            powers.append(float(np.mean(block)))
            next_block_start += step_samples
        discard = next_block_start - buffer_start
        if discard > 0:
            power_buffer = power_buffer[discard:]
            buffer_start = next_block_start
    block_powers = np.asarray(powers, dtype=np.float64)
    valid = block_powers > 0.0
    block_loudness = np.full(block_powers.shape, -np.inf)
    block_loudness[valid] = (
        -0.691 + 10.0 * np.log10(block_powers[valid])
    )

    absolute = block_loudness >= ABSOLUTE_GATE_LUFS
    if not np.any(absolute):
        return None
    preliminary = -0.691 + 10.0 * np.log10(np.mean(block_powers[absolute]))
    gated = absolute & (block_loudness >= preliminary + RELATIVE_GATE_LU)
    if not np.any(gated):
        return None
    return float(-0.691 + 10.0 * np.log10(np.mean(block_powers[gated])))


def _true_peak(values: np.ndarray, sample_rate: int) -> tuple[float | None, int]:
    if values.size == 0:
        return None, 4
    if sample_rate <= 48000:
        factor = 4
    elif sample_rate <= 96000:
        factor = 2
    else:
        factor = 1
    peak = 0.0
    overlap = 64 if factor > 1 else 0
    for start, chunk in _iter_chunks(values):
        if factor > 1:
            source_start = max(0, start - overlap)
            source_stop = min(
                values.shape[0],
                start + len(chunk) + overlap,
            )
            expanded = np.asarray(
                values[source_start:source_stop],
                dtype=np.float64,
            )
            oversampled = resample_poly(expanded, factor, 1, axis=0)
            central_start = (start - source_start) * factor
            central_stop = central_start + len(chunk) * factor
            oversampled = oversampled[central_start:central_stop]
        else:
            oversampled = chunk
        if oversampled.size:
            peak = max(peak, float(np.max(np.abs(oversampled))))
    if peak <= 0.0:
        return None, factor
    return float(20.0 * np.log10(peak)), factor


def measure_bs1770(audio, sample_rate: int) -> AudioQualityMeasurement:
    """Measure gated integrated loudness and oversampled true peak."""
    if sample_rate <= 0:
        raise ValueError("sample rate must be positive")
    values = _as_channels(audio)
    loudness = _integrated_loudness(values, sample_rate)
    true_peak, factor = _true_peak(values, sample_rate)
    return AudioQualityMeasurement(
        standard=BS1770_VERSION,
        sample_rate_hz=int(sample_rate),
        channels=int(values.shape[1]),
        duration_seconds=values.shape[0] / float(sample_rate),
        integrated_lufs=loudness,
        true_peak_dbtp=true_peak,
        true_peak_oversample=factor,
    )
