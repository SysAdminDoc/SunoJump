#!/usr/bin/env python3
"""Deterministic, generated-input DSP signature for compatibility gates."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sunojump import AudioProcessor, PRESETS  # noqa: E402


GOLDEN_SCHEMA_VERSION = 1
GOLDEN_SEED = 20260812
GOLDEN_SAMPLE_RATE = 24000
GOLDEN_DURATION_SECONDS = 1.5
GOLDEN_QUANTIZATION = 1_000_000


def generated_stereo_fixture() -> np.ndarray:
    sample_count = int(GOLDEN_SAMPLE_RATE * GOLDEN_DURATION_SECONDS)
    time_axis = np.arange(sample_count, dtype=np.float64) / GOLDEN_SAMPLE_RATE
    envelope = 0.72 + 0.28 * np.sin(2.0 * np.pi * 1.75 * time_axis) ** 2
    left = envelope * (
        0.19 * np.sin(2.0 * np.pi * 220.0 * time_axis)
        + 0.11 * np.sin(2.0 * np.pi * 997.0 * time_axis + 0.2)
        + 0.04 * np.sin(2.0 * np.pi * 5300.0 * time_axis)
    )
    right = envelope * (
        0.18 * np.sin(2.0 * np.pi * 220.0 * time_axis + 0.03)
        + 0.10 * np.sin(2.0 * np.pi * 1499.0 * time_axis + 0.4)
        + 0.035 * np.sin(2.0 * np.pi * 7100.0 * time_axis)
    )
    return np.column_stack((left, right))


def _quantized_sha256(audio: np.ndarray) -> str:
    quantized = np.rint(
        np.asarray(audio, dtype=np.float64) * GOLDEN_QUANTIZATION
    ).astype("<i8")
    return hashlib.sha256(quantized.tobytes(order="C")).hexdigest()


def render_golden() -> tuple[np.ndarray, dict]:
    audio = generated_stereo_fixture()
    params = dict(PRESETS["Moderate"])
    params["strip_metadata"] = False
    params["reencode_enabled"] = False
    processor = AudioProcessor(params, log_fn=lambda _message: None, seed=GOLDEN_SEED)
    processor._spectral_candidates = processor._scan_spectral_candidates(
        audio,
        GOLDEN_SAMPLE_RATE,
    )
    rendered = processor._spectral_perturb(audio, GOLDEN_SAMPLE_RATE)
    rendered = processor._dynamic_eq(rendered, GOLDEN_SAMPLE_RATE)
    rendered = processor._pitch_tempo_coupled_microvar(
        rendered,
        GOLDEN_SAMPLE_RATE,
    )
    rendered = processor._phase_scramble(rendered, GOLDEN_SAMPLE_RATE)
    rendered = processor._stereo_manipulate(rendered)
    rendered = processor._inject_noise(rendered, GOLDEN_SAMPLE_RATE)
    rendered = processor._modify_dynamics(rendered, GOLDEN_SAMPLE_RATE)
    rendered = processor._humanize(rendered, GOLDEN_SAMPLE_RATE)
    rendered = np.clip(rendered, -1.0, 1.0)
    signature = {
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "seed": GOLDEN_SEED,
        "sample_rate_hz": GOLDEN_SAMPLE_RATE,
        "duration_seconds": GOLDEN_DURATION_SECONDS,
        "shape": list(rendered.shape),
        "quantization": GOLDEN_QUANTIZATION,
        "input_sha256": _quantized_sha256(audio),
        "output_sha256": _quantized_sha256(rendered),
        "output_peak": round(float(np.max(np.abs(rendered))), 8),
        "output_rms": round(float(np.sqrt(np.mean(rendered ** 2))), 8),
        "spectral_candidate_count": len(processor._spectral_candidates),
    }
    return rendered, signature


def main() -> int:
    _rendered, signature = render_golden()
    print(json.dumps(signature, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
