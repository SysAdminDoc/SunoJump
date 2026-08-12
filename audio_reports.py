"""Bounded, dependency-light visual and numeric audio audit reports."""
from __future__ import annotations

import struct
import zlib

import numpy as np

from audio_quality import BS1770_VERSION, measure_bs1770


SPECTROGRAM_SCHEMA_ID = "com.sunojump.spectrogram-comparison"
SPECTROGRAM_SCHEMA_VERSION = 1
SPECTROGRAM_PANEL_WIDTH = 640
SPECTROGRAM_PANEL_HEIGHT = 400
SPECTROGRAM_DB_FLOOR = -100.0
SPECTROGRAM_DB_CEILING = 0.0
LOUDNESS_REPORT_SCHEMA_ID = "com.sunojump.loudness-comparison"
LOUDNESS_REPORT_SCHEMA_VERSION = 1
SIGNAL_REPORT_SCHEMA_ID = "com.sunojump.signal-statistics-comparison"
SIGNAL_REPORT_SCHEMA_VERSION = 1
SIGNAL_MEASUREMENT_CHUNK_FRAMES = 65536

_FONT = {
    " ": ("00000",) * 7,
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
}

_COLOR_STOPS = np.asarray(
    [
        (8, 10, 28),
        (38, 18, 72),
        (104, 25, 110),
        (184, 52, 83),
        (235, 111, 42),
        (249, 190, 56),
        (252, 248, 190),
    ],
    dtype=np.float64,
)


def _mono_segment(audio, start, stop):
    segment = np.asarray(audio[start:stop], dtype=np.float64)
    if segment.ndim == 2:
        segment = np.mean(segment, axis=1)
    return np.nan_to_num(segment, nan=0.0, posinf=0.0, neginf=0.0)


def _spectrogram_db(audio, sample_rate, width, height):
    frames = int(audio.shape[0])
    if frames < 64 or sample_rate <= 0:
        raise ValueError("spectrogram input requires at least 64 audio frames")
    n_fft = min(2048, 1 << (frames.bit_length() - 1))
    n_fft = max(64, n_fft)
    centers = np.linspace(0, max(0, frames - 1), width, dtype=np.int64)
    window = np.hanning(n_fft)
    amplitude_scale = max(np.sum(window) / 2.0, 1.0)
    maximum_hz = min(float(sample_rate) / 2.0, 20000.0)
    minimum_hz = min(20.0, maximum_hz)
    target_hz = np.geomspace(
        max(minimum_hz, 1.0),
        max(maximum_hz, 1.0),
        height,
    )
    frequency_bins = np.clip(
        np.rint(target_hz * n_fft / sample_rate).astype(np.int64),
        0,
        n_fft // 2,
    )
    db = np.empty((height, width), dtype=np.float32)
    batch_size = 64
    for batch_start in range(0, width, batch_size):
        batch_centers = centers[batch_start:batch_start + batch_size]
        segments = np.zeros((len(batch_centers), n_fft), dtype=np.float64)
        for index, center in enumerate(batch_centers):
            start = int(center) - n_fft // 2
            source_start = max(0, start)
            source_stop = min(frames, start + n_fft)
            destination_start = source_start - start
            segment = _mono_segment(audio, source_start, source_stop)
            segments[
                index,
                destination_start:destination_start + len(segment),
            ] = segment
        magnitude = np.abs(np.fft.rfft(segments * window, axis=1))
        magnitude /= amplitude_scale
        batch_db = 20.0 * np.log10(np.maximum(magnitude, 1e-12))
        db[:, batch_start:batch_start + len(batch_centers)] = (
            batch_db[:, frequency_bins].T[::-1]
        )
    return np.clip(db, SPECTROGRAM_DB_FLOOR, SPECTROGRAM_DB_CEILING)


def _colorize(db):
    normalized = (
        (db - SPECTROGRAM_DB_FLOOR)
        / (SPECTROGRAM_DB_CEILING - SPECTROGRAM_DB_FLOOR)
    )
    positions = normalized * (len(_COLOR_STOPS) - 1)
    lower = np.floor(positions).astype(np.int64)
    upper = np.minimum(lower + 1, len(_COLOR_STOPS) - 1)
    fraction = (positions - lower)[..., np.newaxis]
    rgb = _COLOR_STOPS[lower] * (1.0 - fraction) + _COLOR_STOPS[upper] * fraction
    return np.rint(rgb).astype(np.uint8)


def _draw_text(canvas, x, y, text, *, scale=2, color=(238, 240, 246)):
    cursor = int(x)
    for character in str(text).upper():
        glyph = _FONT.get(character, _FONT[" "])
        for row, bits in enumerate(glyph):
            for column, bit in enumerate(bits):
                if bit == "1":
                    canvas[
                        y + row * scale:y + (row + 1) * scale,
                        cursor + column * scale:cursor + (column + 1) * scale,
                    ] = color
        cursor += 6 * scale


def _png_chunk(kind, payload):
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _encode_png(rgb):
    height, width, channels = rgb.shape
    if channels != 3:
        raise ValueError("PNG encoder requires RGB pixels")
    scanlines = b"".join(
        b"\x00" + np.ascontiguousarray(row).tobytes()
        for row in rgb
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(scanlines, level=9))
        + _png_chunk(b"IEND", b"")
    )


def render_spectrogram_comparison_png(
    before,
    after,
    sample_rate,
    *,
    panel_width=SPECTROGRAM_PANEL_WIDTH,
    panel_height=SPECTROGRAM_PANEL_HEIGHT,
):
    """Return a deterministic side-by-side dBFS spectrogram PNG and metadata."""
    if panel_width < 64 or panel_height < 64:
        raise ValueError("spectrogram panels must be at least 64 by 64")
    before_db = _spectrogram_db(before, sample_rate, panel_width, panel_height)
    after_db = _spectrogram_db(after, sample_rate, panel_width, panel_height)
    margin_left = 52
    margin_top = 42
    margin_bottom = 44
    gap = 42
    margin_right = 18
    canvas_width = (
        margin_left + panel_width * 2 + gap + margin_right
    )
    canvas_height = margin_top + panel_height + margin_bottom
    canvas = np.full((canvas_height, canvas_width, 3), (14, 18, 24), dtype=np.uint8)
    first_x = margin_left
    second_x = margin_left + panel_width + gap
    canvas[margin_top:margin_top + panel_height, first_x:first_x + panel_width] = _colorize(before_db)
    canvas[margin_top:margin_top + panel_height, second_x:second_x + panel_width] = _colorize(after_db)
    divider_x = first_x + panel_width + gap // 2
    canvas[margin_top:margin_top + panel_height, divider_x:divider_x + 2] = (75, 82, 95)
    _draw_text(canvas, first_x, 12, "BEFORE", scale=3)
    _draw_text(canvas, second_x, 12, "AFTER", scale=3)
    _draw_text(canvas, first_x + panel_width // 2 - 24, margin_top + panel_height + 16, "TIME", scale=2)
    _draw_text(canvas, second_x + panel_width // 2 - 24, margin_top + panel_height + 16, "TIME", scale=2)
    maximum_hz = min(float(sample_rate) / 2.0, 20000.0)
    for frequency, label in ((20, "20"), (100, "100"), (1000, "1K"), (10000, "10K"), (20000, "20K")):
        if frequency > maximum_hz or maximum_hz <= 20:
            continue
        fraction = np.log(frequency / 20.0) / np.log(maximum_hz / 20.0)
        row = margin_top + panel_height - 1 - int(fraction * (panel_height - 1))
        canvas[row:row + 1, first_x:first_x + panel_width] = (210, 215, 225)
        canvas[row:row + 1, second_x:second_x + panel_width] = (210, 215, 225)
        _draw_text(canvas, 3, max(0, row - 6), label, scale=1)
    metadata = {
        "schema_id": SPECTROGRAM_SCHEMA_ID,
        "schema_version": SPECTROGRAM_SCHEMA_VERSION,
        "sample_rate_hz": int(sample_rate),
        "before_frames": int(before.shape[0]),
        "after_frames": int(after.shape[0]),
        "panel_width": int(panel_width),
        "panel_height": int(panel_height),
        "frequency_scale": "logarithmic_20hz_to_min_nyquist_20khz",
        "time_sampling": "uniform_window_centers_across_full_file",
        "window": "hann",
        "fft_size_max": 2048,
        "dbfs_floor": SPECTROGRAM_DB_FLOOR,
        "dbfs_ceiling": SPECTROGRAM_DB_CEILING,
        "shared_color_scale": True,
    }
    return _encode_png(canvas), metadata


def measure_loudness_comparison(before, after, sample_rate):
    """Return a schema-versioned before/after BS.1770-5 comparison."""
    before_measurement = measure_bs1770(before, sample_rate)
    after_measurement = measure_bs1770(after, sample_rate)

    def delta(before_value, after_value):
        if before_value is None or after_value is None:
            return None
        return round(after_value - before_value, 4)

    return {
        "schema_id": LOUDNESS_REPORT_SCHEMA_ID,
        "schema_version": LOUDNESS_REPORT_SCHEMA_VERSION,
        "standard": BS1770_VERSION,
        "before": before_measurement.to_dict(),
        "after": after_measurement.to_dict(),
        "integrated_loudness_delta_lu": delta(
            before_measurement.integrated_lufs,
            after_measurement.integrated_lufs,
        ),
        "true_peak_delta_db": delta(
            before_measurement.true_peak_dbtp,
            after_measurement.true_peak_dbtp,
        ),
    }


def _signal_statistics(audio, sample_rate):
    values = np.asarray(audio, dtype=np.float64)
    if values.ndim == 1:
        values = values[:, np.newaxis]
    if values.ndim != 2:
        raise ValueError("audio must be a one- or two-dimensional array")
    if sample_rate <= 0:
        raise ValueError("sample rate must be positive")
    if values.shape[0] == 0:
        raise ValueError("signal statistics require audio frames")
    peak = 0.0
    energy = 0.0
    sample_count = 0
    channel_peak = np.zeros(values.shape[1], dtype=np.float64)
    channel_energy = np.zeros(values.shape[1], dtype=np.float64)
    mid_energy = 0.0
    side_energy = 0.0
    left_energy = 0.0
    right_energy = 0.0
    cross_energy = 0.0
    for start in range(0, values.shape[0], SIGNAL_MEASUREMENT_CHUNK_FRAMES):
        chunk = np.asarray(
            values[start:start + SIGNAL_MEASUREMENT_CHUNK_FRAMES],
            dtype=np.float64,
        )
        if not np.all(np.isfinite(chunk)):
            raise ValueError("audio must contain only finite samples")
        absolute = np.abs(chunk)
        if chunk.size:
            peak = max(peak, float(np.max(absolute)))
            channel_peak = np.maximum(channel_peak, np.max(absolute, axis=0))
        squares = chunk ** 2
        energy += float(np.sum(squares))
        channel_energy += np.sum(squares, axis=0)
        sample_count += int(chunk.size)
        if values.shape[1] >= 2:
            left = chunk[:, 0]
            right = chunk[:, 1]
            mid = (left + right) / np.sqrt(2.0)
            side = (left - right) / np.sqrt(2.0)
            mid_energy += float(np.sum(mid ** 2))
            side_energy += float(np.sum(side ** 2))
            left_energy += float(np.sum(left ** 2))
            right_energy += float(np.sum(right ** 2))
            cross_energy += float(np.sum(left * right))

    def crest(peak_value, rms_value):
        if peak_value <= 0.0 or rms_value <= 0.0:
            return None
        return round(float(20.0 * np.log10(peak_value / rms_value)), 4)

    rms = np.sqrt(energy / sample_count) if sample_count else 0.0
    channel_rms = np.sqrt(channel_energy / values.shape[0])
    stereo_width_db = None
    correlation = None
    ratio_state = "unavailable"
    ratio_reason = "mono_input" if values.shape[1] < 2 else None
    if values.shape[1] >= 2 and mid_energy > 0.0 and side_energy > 0.0:
        stereo_width_db = round(
            10.0 * np.log10(side_energy / mid_energy),
            4,
        )
        ratio_state = "measured"
    elif values.shape[1] >= 2 and mid_energy <= 0.0:
        ratio_reason = "zero_mid_energy"
    elif values.shape[1] >= 2:
        ratio_reason = "zero_side_energy"
    correlation_denominator = np.sqrt(left_energy * right_energy)
    if values.shape[1] >= 2 and correlation_denominator > 0.0:
        correlation = round(
            float(np.clip(
                cross_energy / correlation_denominator,
                -1.0,
                1.0,
            )),
            6,
        )
    return {
        "sample_rate_hz": int(sample_rate),
        "channels": int(values.shape[1]),
        "frames": int(values.shape[0]),
        "duration_seconds": round(values.shape[0] / sample_rate, 6),
        "peak_amplitude": round(peak, 9),
        "rms_amplitude": round(float(rms), 9),
        "crest_factor_db": crest(peak, rms),
        "per_channel": [
            {
                "channel_index": index,
                "peak_amplitude": round(float(channel_peak[index]), 9),
                "rms_amplitude": round(float(channel_rms[index]), 9),
                "crest_factor_db": crest(
                    float(channel_peak[index]),
                    float(channel_rms[index]),
                ),
            }
            for index in range(values.shape[1])
        ],
        "stereo_width": {
            "state": "measured" if values.shape[1] >= 2 else "unavailable",
            "reason": None if values.shape[1] >= 2 else "mono_input",
            "channels_analyzed": [0, 1] if values.shape[1] >= 2 else [],
            "mid_side_ratio_state": ratio_state,
            "mid_side_ratio_reason": ratio_reason,
            "mid_side_energy_ratio_db": stereo_width_db,
            "interchannel_energy_correlation": correlation,
        },
    }


def measure_signal_statistics_comparison(before, after, sample_rate):
    """Return bounded before/after crest and stereo-width statistics."""
    before_measurement = _signal_statistics(before, sample_rate)
    after_measurement = _signal_statistics(after, sample_rate)

    def delta(before_value, after_value):
        if before_value is None or after_value is None:
            return None
        return round(after_value - before_value, 4)

    before_width = before_measurement["stereo_width"]
    after_width = after_measurement["stereo_width"]
    return {
        "schema_id": SIGNAL_REPORT_SCHEMA_ID,
        "schema_version": SIGNAL_REPORT_SCHEMA_VERSION,
        "definitions": {
            "crest_factor_db": "20*log10(absolute_peak/whole_signal_rms)",
            "stereo_width_db": "10*log10(side_energy/mid_energy)",
            "mid_side_scaling": "mid=(L+R)/sqrt(2), side=(L-R)/sqrt(2)",
            "correlation": "sum(L*R)/sqrt(sum(L^2)*sum(R^2))",
            "stereo_channel_policy": "first_left_right_pair",
        },
        "before": before_measurement,
        "after": after_measurement,
        "crest_factor_delta_db": delta(
            before_measurement["crest_factor_db"],
            after_measurement["crest_factor_db"],
        ),
        "stereo_width_delta_db": delta(
            before_width["mid_side_energy_ratio_db"],
            after_width["mid_side_energy_ratio_db"],
        ),
        "interchannel_correlation_delta": delta(
            before_width["interchannel_energy_correlation"],
            after_width["interchannel_energy_correlation"],
        ),
    }
