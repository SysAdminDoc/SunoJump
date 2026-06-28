# SunoJump Roadmap

Roadmap for SunoJump - the 11-pass audio-fingerprint masking pipeline with non-uniform segment-based transforms. Focus: higher bypass rates, faster processing, and more robust CLI/batch workflows.

## Planned Features

### Detection-bypass validation
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
- Layered-pass pipeline where each pass has declared effects-model AND a cost-estimate (quality loss, fingerprint shift) surfaced as a graph (geeknik layered design + your 11-pass pipeline)
- Seed-reproducible runs (already on roadmap) + "replay trace" JSON logging which parameters each segment got — critical for A/B and user trust
- Integrated fingerprinting-system simulator using OSS Shazam clones to self-test every build before release (`python audio-fingerprint-identifying` + Panako as CI suite)
- Plugin pass SDK backed by a `Pass(ABC)` interface with `process(audio, ctx) -> audio`, `estimated_quality_cost`, `estimated_fingerprint_shift` — users drop `.py` into `passes/`
- Streaming-mode pipeline (chunked) for files >10 min so RAM stays flat and VST/CLAP wrapper becomes trivial

## Research-Driven Additions

- [ ] P0 - Fail closed when enabled processing passes fail
  Why: A skipped enabled pass can currently still produce a successful output, which makes bypass and quality logs untrustworthy.
  Evidence: `sunojump.py:624-660`; commercial recognizers and OSS verifier engines all depend on honest pass/result reporting.
  Touches: `AudioProcessor.process`, `ProcessWorker.run`, `PreviewWorker.run`, `PresetCompareWorker.run`, `cli_main`, `tests/test_audio_processor.py`
  Acceptance: an exception in any enabled pass marks that render failed, GUI rows show FAILED, CLI returns a non-zero exit, logs name the failed pass, and tests cover one injected pass failure.
  Complexity: M

- [ ] P0 - Prevent same-stem batch output overwrites
  Why: Files from different folders with the same stem can silently write the same `<stem>_sj.<ext>` output path.
  Evidence: `sunojump.py:1793`, `sunojump.py:3341`
  Touches: `ProcessWorker.run`, `cli_main`, output-path helper tests
  Acceptance: batch and CLI runs generate collision-free paths or preserve relative subfolders, warn before replacing an existing file, and tests prove two `song.wav` inputs both survive.
  Complexity: S

- [ ] P0 - Harden frozen builds and remove runtime dependency installs
  Why: The entry script shells `sys.executable -m pip install` before imports, while PyInstaller builds need explicit frozen guards and `freeze_support`.
  Evidence: `sunojump.py:8-41`, `SunoJump.spec`, Python stack memory, PyInstaller changelog
  Touches: `sunojump.py`, `SunoJump.spec`, `requirements.txt`, release build notes
  Acceptance: dependencies install only through documented setup/build steps, frozen execution never invokes pip, `multiprocessing.freeze_support()` is protected by a runtime hook, and a packaged Windows smoke test launches one process.
  Complexity: S

- [ ] P1 - Add persistent run diagnostics and exportable failure reports
  Why: The GUI log disappears on close, leaving users without the parameter, version, codec, traceback, and environment context needed to reproduce failures.
  Evidence: `sunojump.py:2494-2502`, `sunojump.py:2809-2812`; ACRCloud/Audible Magic/Pex/BMAT emphasize auditable recognition/report history.
  Touches: `MainWindow._log`, worker result signals, `AudioProcessor.process`, CLI logging path
  Acceptance: each run writes a timestamped local log with app version, inputs, output paths, preset/seed, pass outcomes, dependency/ffmpeg availability, and full exception details; GUI has an Open Log action.
  Complexity: M

- [ ] P1 - Add accessibility names, descriptions, and focus-order tests
  Why: Tooltips exist, but screen-reader-facing names/descriptions and verified focus order are absent from the PyQt controls.
  Evidence: `sunojump.py` has no `setAccessibleName` or `setAccessibleDescription`; Qt/PyQt6 accessibility APIs
  Touches: `MainWindow`, `DropListWidget`, `ParamRow`, compare/preview controls, GUI smoke test harness
  Acceptance: all interactive controls expose accessible names/descriptions, disabled states explain the reason, tab order follows the visual workflow, and an automated smoke check asserts the main controls have names.
  Complexity: M

- [ ] P2 - Make codec and output-format behavior explicit
  Why: Users can input MP3/Opus/AIFF but can only output WAV/FLAC/OGG, while lossy re-encode is an internal MP3 round trip rather than an export choice.
  Evidence: `SUPPORTED_FORMATS` and CLI `choices=['wav','flac','ogg']` in `sunojump.py`; Audacity export UX; SoundFile and ffmpeg documentation
  Touches: format selection UI, `cli_main`, `AudioProcessor.process`, ffmpeg availability checks, README after implementation
  Acceptance: UI/CLI distinguish processing format from optional lossy pass, unsupported codec choices fail early with actionable messages, and optional MP3/M4A export is available only when ffmpeg supports it.
  Complexity: M

- [ ] P2 - Add a dependency lock and local security-audit command
  Why: A desktop binary needs reproducible dependency resolution and a repeatable way to review scipy/libsndfile/PyQt parser and media stack advisories.
  Evidence: loose lower bounds in `requirements.txt`; GitHub advisories for scipy/libsndfile/PyQt6; dependency changelogs
  Touches: dependency files, build script/docs, release checklist
  Acceptance: repo has a lock/constraints file for release builds, a local audit command exits non-zero on known vulnerable resolved packages, and the app still supports normal source setup from `requirements.txt`.
  Complexity: S

- [ ] P3 - Prepare GUI text for localization
  Why: Creator tools are shared across language communities, and the current UI hardcodes user-visible strings throughout `sunojump.py`.
  Evidence: hardcoded labels/buttons/log strings in `MainWindow`, `ParamRow`, and `cli_main`; Qt Linguist/QTranslator support
  Touches: GUI string constants, CLI help text, translation catalog scaffolding
  Acceptance: user-facing GUI strings are routed through one translation helper/catalog, English remains the default, and no behavior or layout changes occur.
  Complexity: M
