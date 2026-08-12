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


def _midi_frequency(note):
    return 440.0 * (2.0 ** ((note - 69.0) / 12.0))


def _generate_music(spec):
    """Generate a deterministic stereo synth cue with harmony and drums."""
    sr = spec["sample_rate"]
    duration = spec["duration_sec"]
    sample_count = int(sr * duration)
    output = np.zeros((sample_count, 2), dtype=np.float64)
    rng = np.random.default_rng(spec["seed"])
    beat_seconds = 60.0 / spec.get("tempo_bpm", 120.0)
    beat_samples = int(round(beat_seconds * sr))
    progression = spec.get("progression", [0, 9, 5, 7])
    root_midi = spec.get("root_midi", 48)

    beat_index = 0
    for start in range(0, sample_count, beat_samples):
        stop = min(sample_count, start + beat_samples)
        length = stop - start
        local_time = np.arange(length, dtype=np.float64) / sr
        chord_root = root_midi + progression[(beat_index // 4) % len(progression)]
        chord_envelope = np.minimum(1.0, local_time / 0.015) * np.exp(
            -1.6 * local_time / beat_seconds
        )
        left = np.zeros(length, dtype=np.float64)
        right = np.zeros(length, dtype=np.float64)
        chord_intervals = spec.get("chord_intervals", [0, 4, 7, 12])
        for voice, interval in enumerate(chord_intervals):
            frequency = _midi_frequency(chord_root + interval)
            phase = 0.17 * voice + 0.03 * beat_index
            tone = np.sin(2.0 * np.pi * frequency * local_time + phase)
            tone += 0.18 * np.sin(
                2.0 * np.pi * frequency * 2.0 * local_time + phase / 2.0
            )
            pan = (voice - 1.5) / 4.0
            left += (1.0 - pan) * tone
            right += (1.0 + pan) * tone
        output[start:stop, 0] += 0.055 * chord_envelope * left
        output[start:stop, 1] += 0.055 * chord_envelope * right

        if spec.get("bass", True):
            bass = 0.12 * np.sin(
                2.0 * np.pi * _midi_frequency(chord_root - 12) * local_time
            ) * np.exp(-2.2 * local_time / beat_seconds)
            output[start:stop] += bass[:, np.newaxis]

        if spec.get("drums", True):
            kick_phase = 2.0 * np.pi * (
                95.0 * local_time - 35.0 * local_time ** 2
            )
            kick = 0.20 * np.sin(kick_phase) * np.exp(-18.0 * local_time)
            output[start:stop] += kick[:, np.newaxis]

            if beat_index % 4 in (1, 3):
                noise = rng.standard_normal(length)
                snare = 0.07 * noise * np.exp(-25.0 * local_time)
                output[start:stop, 0] += snare
                output[start:stop, 1] += np.roll(snare, min(11, length - 1))

            half = max(1, beat_samples // 2)
            for hat_start in range(0, length, half):
                hat_length = min(length - hat_start, int(0.08 * sr))
                if hat_length <= 1:
                    continue
                hat_time = np.arange(hat_length, dtype=np.float64) / sr
                noise = rng.standard_normal(hat_length + 1)
                bright_noise = np.diff(noise)
                hat = 0.025 * bright_noise * np.exp(-55.0 * hat_time)
                output[
                    start + hat_start:start + hat_start + hat_length,
                    0,
                ] += hat
                output[
                    start + hat_start:start + hat_start + hat_length,
                    1,
                ] -= hat
        beat_index += 1

    fade_samples = min(sample_count // 4, int(0.05 * sr))
    if fade_samples:
        fade = np.linspace(0.0, 1.0, fade_samples, endpoint=False)
        output[:fade_samples] *= fade[:, np.newaxis]
        output[-fade_samples:] *= fade[::-1, np.newaxis]
    peak = float(np.max(np.abs(output)))
    target_peak = float(spec.get("amplitude", 0.65))
    if peak > 0.0:
        output *= target_peak / peak
    return output, sr


GENERATORS = {
    "sine": _generate_sine,
    "chord": _generate_chord,
    "sweep": _generate_sweep,
    "pink_noise": _generate_pink_noise,
    "music": _generate_music,
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
