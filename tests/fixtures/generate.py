#!/usr/bin/env python3
"""Generate license-safe test fixtures from the manifest."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

FIXTURES_DIR = Path(__file__).resolve().parent
MANIFEST = FIXTURES_DIR / "manifest.json"


def _generate_sine(spec):
    sr = spec["sample_rate"]
    dur = spec["duration_sec"]
    amp = spec.get("amplitude", 0.25)
    freq = spec["frequency_hz"]
    t = np.arange(int(sr * dur), dtype=np.float64) / sr
    mono = amp * np.sin(2.0 * np.pi * freq * t)
    if spec["channels"] == 2:
        return np.column_stack([mono, mono]), sr
    return mono, sr


def _generate_chord(spec):
    sr = spec["sample_rate"]
    dur = spec["duration_sec"]
    amp = spec.get("amplitude", 0.20)
    freqs = spec["frequencies_hz"]
    t = np.arange(int(sr * dur), dtype=np.float64) / sr
    mono = sum(amp * np.sin(2.0 * np.pi * f * t) for f in freqs) / len(freqs)
    if spec["channels"] == 2:
        return np.column_stack([mono, mono]), sr
    return mono, sr


def _generate_sweep(spec):
    sr = spec["sample_rate"]
    dur = spec["duration_sec"]
    amp = spec.get("amplitude", 0.25)
    f0 = spec["freq_start_hz"]
    f1 = spec["freq_end_hz"]
    t = np.arange(int(sr * dur), dtype=np.float64) / sr
    phase = 2.0 * np.pi * (f0 * t + (f1 - f0) / (2.0 * dur) * t ** 2)
    mono = amp * np.sin(phase)
    if spec["channels"] == 2:
        return np.column_stack([mono, mono]), sr
    return mono, sr


def _generate_pink_noise(spec):
    sr = spec["sample_rate"]
    dur = spec["duration_sec"]
    amp = spec.get("amplitude", 0.15)
    n = int(sr * dur)
    rng = np.random.default_rng(42)
    white = rng.standard_normal(n)
    pink = np.zeros(n)
    b0 = b1 = b2 = b3 = b4 = b5 = b6 = 0.0
    for i in range(n):
        w = white[i]
        b0 = 0.99886 * b0 + w * 0.0555179
        b1 = 0.99332 * b1 + w * 0.0750759
        b2 = 0.96900 * b2 + w * 0.1538520
        b3 = 0.86650 * b3 + w * 0.3104856
        b4 = 0.55000 * b4 + w * 0.5329522
        b5 = -0.7616 * b5 - w * 0.0168980
        pink[i] = b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362
        b6 = w * 0.115926
    peak = np.max(np.abs(pink))
    if peak > 0:
        pink = pink * (amp / peak)
    if spec["channels"] == 2:
        return np.column_stack([pink, pink]), sr
    return pink, sr


GENERATORS = {
    "sine": _generate_sine,
    "chord": _generate_chord,
    "sweep": _generate_sweep,
    "pink_noise": _generate_pink_noise,
}


def generate_all():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for spec in manifest["generated_fixtures"]:
        name = spec["name"]
        gen = GENERATORS.get(spec["generator"])
        if gen is None:
            print(f"  Skip: unknown generator {spec['generator']}", file=sys.stderr)
            continue
        audio, sr = gen(spec)
        path = FIXTURES_DIR / f"{name}.wav"
        sf.write(str(path), audio, sr, subtype="PCM_16")
        print(f"  Generated: {path.name} ({audio.shape})")
    return True


if __name__ == "__main__":
    generate_all()
