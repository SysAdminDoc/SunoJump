# SunoJump Roadmap

Roadmap for SunoJump - the 10-pass audio-fingerprint masking pipeline with non-uniform segment-based transforms. Focus: higher bypass rates, faster processing, and more robust CLI/batch workflows.

## Planned Features

### Pipeline quality
- Non-uniform-segment pitch + tempo coupling (keep beat alignment while still varying the fingerprint)
- Per-band spectral perturbation strength slider (sub-bass, low-mids, presence, air)
- Watermark-band scanning pre-pass - auto-detect candidate watermark bands per file before perturbing
- Dynamic EQ with LUFS-preserving gain staging (don't silently change perceived loudness)
- Psychoacoustic masking-aware noise injection (stay below masking thresholds)
- STFT window-size sweep per-segment (1024 / 2048 / 4096 with random cut points)

### Detection-bypass validation
- Self-test harness: run the output through an open reimplementation of constellation fingerprinting (dejavu, Panako) and report match probability before/after
- Integration harness for Suno's public detection API (if exposed) as an optional post-check
- A/B regression suite - a set of reference tracks that must retain quality threshold (PEAQ/PESQ) AND reduce detection match below a threshold

### Performance
- GPU path via CuPy/PyTorch for FFT-heavy passes
- Streaming / chunked rendering for long files with bounded RAM (currently chunked, tighten guarantees)
- Parallel-per-file worker pool with per-file progress

### Presets & customization
- Preset marketplace (shareable JSON) with community ratings
- Preset A/B/C/D Compare UI enhancements - save the winner per-file back into history
- Per-genre presets (electronic, vocal-heavy, acoustic, orchestral) tuned not to ruin instrumentation
- Preview at arbitrary offsets (not just first 30 seconds)

### Batch & CLI
- Watch-folder mode: drop a file, get a processed file in output/
- GUI queue view with per-file presets
- `--profile <json>` flag composes preset plus overrides
- Sidecar JSON written per output describing exactly which parameters + seed were used
- Exit codes: 0 = success, 1 = partial, 2 = all failed

### Auditing
- Before/after spectrogram side-by-side PNG export per file
- Modification-strength metric expanded to per-pass contribution breakdown
- Before/after LUFS + true-peak report
- Crest-factor and stereo-width delta report

## Competitive Research

- **NoFingerprint / Audio Fingerprint Detector** tools - many use single-pass transforms; the non-uniform-segment approach is the differentiator. Keep emphasizing that in README and add measurable bypass-rate data per preset.
- **Sonic Isolation** and watermark-removal research from academic papers - incorporate learnings about chirp watermarks and echo-hide watermarks (different families need different countermeasures).
- **Audacity macros** - users sometimes chain EQ + reverb + pitch manually for the same goal; publish a sample Audacity macro that approximates SunoJump Gentle so users can A/B.
- **dejavu** (open fingerprinting lib) - the natural tool to benchmark against; wire it into the validation harness.

## Nice-to-Haves

- Built-in Suno uploader - paste cookie/session, pipeline renders then uploads with suno-fileupload + checks for re-detection
- Per-pass waveform/spectrogram preview in GUI
- History panel of past runs with one-click reproduce using saved seed
- Portable build target: single-file exe <= 50 MB with stripped torch/numpy
- Pluggable pass SDK so users can write a `.py` that inserts a new pass into the pipeline
- VST/CLAP plugin wrapper for use inside a DAW as an effect chain
- Discord bot that accepts file upload + preset and returns processed file (self-host)

## Open-Source Research (Round 2)

### Related OSS Projects
- https://github.com/geeknik/ai-audio-fingerprint-remover — Closest peer: AI-gen watermark/fingerprint/metadata stripper with layered passes including "human imperfection injection" final pass
- https://github.com/adblockradio/stream-audio-fingerprint — Landmark-algorithm impl (Shazam 2003 paper port); reference for what you're trying to perturb
- https://github.com/JorenSix/Panako — Olaf / Panako acoustic fingerprinting; Gabor-transform-based, different perturbation sensitivity than classic Shazam
- https://github.com/AddictedCS/soundfingerprinting — .NET fingerprinting lib, useful for a built-in "fingerprint distance meter" to score effectiveness per-pass
- https://github.com/itspoma/audio-fingerprint-identifying-python — Python Shazam-clone, good for integration tests
- https://github.com/exdsgift/FrequencyFingerprint-Algorithm — Has robustness tests against white noise / clipping / pitch-shift — direct validation harness
- https://github.com/EgemenErin/ShazamAlgorithm — Recordable waveform + DB add flow, reference for a built-in "detect before/after" tester
- https://github.com/topics/audio-fingerprinting — Topic hub

### Features to Borrow
- Built-in before/after fingerprint-distance meter using Panako + AddictedCS — show user how much the signature moved (SunoJump's killer feature that competitors skip)
- Human-imperfection final pass — mouth-click micro-transients, breath hiss, subtle mic-handling noise (geeknik ai-audio-fingerprint-remover)
- Per-pass A/B spectrogram diff view (you already do pipeline; surface each pass's spectrogram delta in the GUI)
- Robustness test harness — run masked output through noise/clip/pitch-shift and measure recognition degradation (exdsgift test notebook methodology)
- Gabor-transform-aware pass targeting Panako-style fingerprints, not just classic Shazam (Panako/Olaf)
- "Content fingerprint" disclaimer in UI (geeknik explicitly notes tools cannot defeat content-based FP, only watermarks — important expectation-setting)

### Patterns & Architectures Worth Studying
- Layered-pass pipeline where each pass has declared effects-model AND a cost-estimate (quality loss, fingerprint shift) surfaced as a graph (geeknik layered design + your 10-pass pipeline)
- Seed-reproducible runs (already on roadmap) + "replay trace" JSON logging which parameters each segment got — critical for A/B and user trust
- Integrated fingerprinting-system simulator using OSS Shazam clones to self-test every build before release (`python audio-fingerprint-identifying` + Panako as CI suite)
- Plugin pass SDK backed by a `Pass(ABC)` interface with `process(audio, ctx) -> audio`, `estimated_quality_cost`, `estimated_fingerprint_shift` — users drop `.py` into `passes/`
- Streaming-mode pipeline (chunked) for files >10 min so RAM stays flat and VST/CLAP wrapper becomes trivial
