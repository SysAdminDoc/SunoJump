<img width="1536" height="448" alt="banner" src="banner.png" />

<br>

![Version](https://img.shields.io/badge/version-1.6.1-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

# SunoJump

Local-first audio variation and evidence tool for material you own or are authorized to modify. SunoJump applies a seedable transform pipeline, supports preview and comparison, and records local signal-change evidence so you can audit each render.

<img width="1844" height="1375" alt="SunoJump main window" src="screenshot.png" />


## How It Works

SunoJump applies an 11-pass processing pipeline with **non-uniform segment-based transforms**. Each segment can receive different processing parameters, producing controlled spectral, temporal, phase, stereo, noise, dynamics, and codec variation.

### Use and Evidence Contract

- Use SunoJump only with audio you own or are authorized to modify.
- Built-in metrics are experimental, local, adapter/version-scoped measurements of the current input and output.
- A signal-change or landmark-overlap value does not predict or guarantee any platform, recognition, moderation, or detector outcome.
- SunoJump does not upload or resubmit audio, call platform APIs, or optimize against a platform response.

### Processing Pipeline

| # | Pass | What It Does |
|---|------|-------------|
| 1 | **Metadata Strip** | Removes tags recognized by Mutagen; signed provenance may be affected |
| 2 | **Spectral Perturbation** | Scans stable narrowband candidates, then perturbs frequency magnitudes with per-band controls and randomized STFT window sweeps |
| 3 | **Dynamic EQ** | Time-varying multiband EQ with LUFS-like gain matching |
| 4 | **Pitch Micro-Shift** | Non-uniform pitch warping across random segments |
| 5 | **Tempo Micro-Variation** | Coupled in-segment timing drift that keeps segment boundaries beat-aligned |
| 6 | **Phase Scrambling** | Randomizes phase relationships in STFT domain |
| 7 | **Stereo Manipulation** | Mid-side processing to alter stereo field |
| 8 | **Noise Injection** | Adds masking-aware shaped pink noise below local spectral thresholds |
| 9 | **Dynamics Modification** | Per-frame random gain variation to break statistical patterns |
| 10 | **Humanization** | Wow/flutter, dynamic breathing, micro noise floor |
| 11 | **Lossy Re-encode** | Controlled MP3 encode/decode round trip (requires ffmpeg) |

### Key Differentiator: Non-Uniform Processing

Unlike a flat whole-track transform, SunoJump splits audio into variable-length segments and applies **different transform parameters to each segment**. Preview, comparison, run logs, and sidecars make those changes inspectable.

## Installation

### Windows (recommended) -- prebuilt executable
Download `SunoJump.exe` from the [latest release](https://github.com/SysAdminDoc/SunoJump/releases/latest). No Python install required. Double-click to launch.

### From source (any platform)
```bash
git clone https://github.com/SysAdminDoc/SunoJump.git
cd SunoJump

# Install dependencies
python -m pip install -r requirements.txt

# Run
python sunojump.py
```

### Requirements
- Python 3.11+
- ffmpeg (optional, for Lossy Re-encode pass)
- PyQt6 Multimedia (optional, for in-app preview playback; usually bundled)

Install Python dependencies before running from source; the packaged executable already includes them.

### Release Dependency Audit
```bash
# Install local audit tooling, then scan the release lock
python -m pip install -r requirements-dev.txt
python tools/audit_dependencies.py
python tools/audit_licenses.py
```

### Reproducing and Verifying the Windows Release

On Windows x64 with CPython 3.12, the release command creates a temporary virtual environment, installs only the exact wheels in the two hashed locks, builds an unsigned one-file executable, and proves its CLI, fixture-render, and foreground-GUI paths:

```bash
python tools/build_release.py
```

The gate fails on a missing, stale, or wrong-version executable; undeclared bundled packages; incomplete license/source records; or failed smoke tests. It does not perform code signing. `dist/` receives the executable, `SHA256SUMS`, CycloneDX 1.7 SBOM, actual PyInstaller inventory, native-version report, license notices/source routing, build provenance, both locks, and the corresponding source archive. Use `sha256sum -c SHA256SUMS` from inside the downloaded artifact directory to verify the set.

## Features

- **11-pass audio processing pipeline** — metadata strip, spectral perturbation, dynamic EQ, pitch/tempo micro-shift, phase scrambling, stereo manipulation, noise injection, dynamics, humanization, lossy re-encode
- **LUFS-preserving Dynamic EQ** — reshapes band energy without silently changing perceived loudness
- **Psychoacoustic noise shaping** — keeps injected pink noise lower in quiet regions and under louder spectral masks
- **Narrowband candidate scan** — locates stable spectral candidates per file and targets them during spectral perturbation
- **STFT window sweeps** — varies 1024/2048/4096-point windows across randomized spectral segments
- **Per-band spectral controls** — tune sub-bass, low-mids, presence, and air perturbation independently
- **Coupled non-uniform pitch/tempo processing** — varies pitch and timing together while keeping segment boundaries aligned
- **4 built-in presets** — Gentle, Moderate, Aggressive, Extreme + Custom
- **Per-pass toggles and strength sliders** — fine-grained control
- **Render Preview** — hear a 30-second sample with your current settings before committing to full-file processing
- **Compare Presets** — one click renders a 20-second sample per preset so you can A/B/C/D audition all four, then apply your favorite
- **In-app A/B playback** — play original and processed side-by-side without leaving the app
- **Accessible GUI controls** — primary controls and pass sliders expose screen-reader names, descriptions, and a tested focus order
- **Explicit codec export** — WAV/FLAC/OGG export directly, with MP3/M4A export enabled when ffmpeg is available
- **Contained input decoding** — bounded pure-Python container inspection runs before a time/memory/output-capped decoder process; mismatched containers, IRCAM, and WAV IMA ADPCM are rejected before libsndfile
- **Validated atomic output saves** — decodes same-folder temporary renders and checks finite/non-silent samples, duration, rate, channels, path mapping, and SHA-256 before promotion
- **Persistent run diagnostics** — every GUI and CLI run writes a local log with environment, parameters, paths, pass results, and tracebacks
- **Scoped local evidence** — reports `sunojump.signal_change v1` plus typed `measured`, `unavailable`, or `error` results from experimental `sunojump.constellation v1`
- **Truthful terminal states** — jobs and batches report `succeeded`, `partial`, `failed`, or `cancelled` with stable error codes; only an all-success batch reaches 100%
- **Reproducible output** — optional `--seed` for bit-identical runs (useful for testing and diffing)
- **Batch queue** — drag/drop multiple files, reorder them, and process them sequentially
- **Custom preset save/load** — export your tuned settings to JSON, share, or reuse
- **Guarded in-memory processing** — input size/decode limits are enforced; the Humanization pass processes long audio in chunks
- **Open Output** — one-click to output folder in your file manager
- **Versioned signal-change metric** — inspect a sample-domain difference value with explicit scope

## Usage

### GUI Mode
```bash
python sunojump.py
```

1. Drop audio files into the file list (or click Browse)
2. Select a preset or customize individual parameters
3. (Optional) Click **Render Preview** to process the first 30 seconds of the selected file so you can hear the result before committing; adjust settings and re-render as needed
4. Click **Process All** to render every file in the list to the output directory with `_sj` suffix

### CLI Mode
```bash
# Basic usage with preset
python sunojump.py -i song.wav -p aggressive

# Custom parameters
python sunojump.py -i song.wav --pitch 1.5 --phase 0.5 --spectral 0.4

# Batch process a directory
python sunojump.py -i ./my_songs/ -o ./output/ -p moderate -f flac

# With lossy re-encode
python sunojump.py -i song.wav -p aggressive --reencode 128
```

#### CLI Options
| Flag | Description | Default |
|------|-------------|---------|
| `-i, --input` | Input file or directory | (required) |
| `-o, --output` | Output directory | `~/Desktop/SunoJump_Output` |
| `-p, --preset` | gentle, moderate, aggressive, extreme | moderate |
| `-f, --format` | wav, flac, ogg, mp3, m4a (mp3/m4a require ffmpeg) | wav |
| `--preset-file` | Path to custom JSON preset (overrides `-p`) | none |
| `--no-spectral-scan`, `--no-watermark-scan` | Disable the local narrowband candidate scan (`--no-watermark-scan` is retained for compatibility) | enabled |
| `--spectral` | Spectral perturbation (0.0-1.0) | preset |
| `--spectral-sub-bass` | Sub-bass spectral perturbation (0.0-1.0) | preset |
| `--spectral-low-mids` | Low-mids spectral perturbation (0.0-1.0) | preset |
| `--spectral-presence` | Presence-band spectral perturbation (0.0-1.0) | preset |
| `--spectral-air` | Air-band spectral perturbation (0.0-1.0) | preset |
| `--dynamic-eq` | Dynamic EQ amount (0.0-1.0) | preset |
| `--pitch` | Pitch micro-shift in semitones (0.0-5.0) | preset |
| `--tempo` | Tempo variation (0.0-0.15) | preset |
| `--phase` | Phase scrambling (0.0-1.0) | preset |
| `--stereo` | Stereo manipulation (0.0-0.5) | preset |
| `--noise` | Noise level in dB (-70 to -30) | preset |
| `--dynamics` | Dynamics amount (0.0-1.0) | preset |
| `--humanize` | Humanization amount (0.0-1.0) | preset |
| `--reencode` | Lossy re-encode bitrate (96-320) | disabled |
| `--seed` | Integer for deterministic random generator (same seed = same output) | random |
| `--native-runtime` | Print machine-readable decoder version and containment policy, then exit | n/a |

Use `Save...` in the GUI to export the current settings, then pass the resulting `.json` to `--preset-file` on the CLI to reproduce the same configuration across runs.

CLI exit status is `0` only when every job succeeds, `1` when a batch has a usable output but is partial, and `2` when no job produces a usable output. Human-readable per-file and batch summaries include stable error codes. Sidecars include the validated output shape, peak, decoder, byte count, input/output SHA-256 hashes, and whether those hashes differ.

## Presets

| Preset | Pitch | Spectral | Phase | Noise | Use Case |
|--------|-------|----------|-------|-------|----------|
| **Gentle** | 0.3 st | 0.10 | 0.10 | -60 dB | Lowest transform intensity |
| **Moderate** | 0.8 st | 0.30 | 0.30 | -50 dB | Moderate transform intensity |
| **Aggressive** | 1.5 st | 0.50 | 0.50 | -45 dB | High transform intensity |
| **Extreme (default)** | 3.0 st | 0.70 | 0.70 | -40 dB | Highest transform intensity; audition before use |

Preset names describe transform intensity, not effectiveness. Use Preview or Compare Presets and choose the lowest intensity that meets your audible and analytical goals.

## Signal Change Metric

After processing, SunoJump reports `sunojump.signal_change v1`, a normalized sample-domain difference derived from signal-to-difference ratio:

- **0-25%** — low sample-domain change
- **25-50%** — moderate sample-domain change
- **50-75%** — high sample-domain change
- **75-100%** — very high sample-domain change; audition output quality

This metric is not calibrated to any external service or recognition system. Sidecars record the adapter name, version, unit, and scope.

## Local Landmark Evidence

`sunojump.constellation v1` compares up to 30 seconds of local landmark hashes. A measured result includes adapter/version, percentage, sample coverage, duration, and landmark counts. Silence, near-silence, clips shorter than two seconds, and inputs with too few landmarks report `unavailable` without a numeric value; adapter failures report `error`. These states appear identically in GUI/CLI logs and sidecar JSON and do not imply a platform outcome.

## Supported Formats

**Input:** WAV, MP3, FLAC, OGG, AIFF, Opus

For native-decoder safety, IRCAM payloads and WAV IMA ADPCM (including the extensible subtype) are not accepted. The file extension must match the detected container.

**Output:** WAV (24-bit), FLAC, OGG Vorbis, MP3, M4A/AAC

## Release Licensing

SunoJump's own source code is MIT-licensed. Runtime dependencies include copyleft-licensed packages:

| Package | License | Notes |
|---------|---------|-------|
| PyQt6 | GPL-3.0-only | Bundled GUI toolkit |
| mutagen | GPL-2.0-or-later | Bundled metadata tag library |
| PyQt6-Qt6 | LGPL-3.0-only | Bundled shared Qt runtime |

**Source installs:** users install dependencies themselves via `pip install -r requirements.txt`. SunoJump source is MIT.

**Binary releases (PyInstaller):** the combined executable bundles GPL-licensed components. The binary as a whole must comply with GPL-3.0 terms. Source code is always available in this repository.

Run `python tools/audit_licenses.py` to verify the reviewed runtime inventory. Release packaging also inventories the frozen executable and fails if build-only, undeclared, or unreviewed components appear.

## License

MIT
