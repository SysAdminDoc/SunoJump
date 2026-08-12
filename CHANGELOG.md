# Changelog

## Unreleased
- **Responsive queue discovery.** Recursive folder discovery now runs in a cancellable background worker with counted progress, bounded scan-error reporting, deterministic deduplication, and retained partial results after cancellation.
- **Actionable queue states.** Empty queues disable processing and explain how to begin; preview and preset comparison name their selected target; failed and partial rows expose typed reasons directly in the queue.
- **One-click failed retry.** Retry Failed now reuses the latest batch manifest when available, while retaining manifest selection as the recovery fallback.
- **Regression coverage.** Added offscreen GUI and worker tests for empty queues, background discovery, cancellation, target labeling, visible failure summaries, and recent-batch retry.
- **Dependency/native compatibility lane.** Expanded the dependency audit into a machine-readable direct/transitive/build/native drift, DSP-golden, security, and rollback report; added Python 3.11/3.12 source CI plus an exact-lock CPython 3.12 release contract.
- **Lock-coupled upgrade baseline.** Added a schema-versioned record binding both lock hashes to tested native versions, deterministic generated-audio output, supported lanes, and an explicit rollback commit; stale baselines now block release builds.
- **Runtime-native evidence.** Qt reporting now distinguishes the loaded runtime version from the compile target, and release artifacts include the compatibility baseline and provenance evidence.
- **Localized GUI and CLI text.** Added Qt-compatible JSON catalogs with region/language/English fallback, plural forms, runtime `--locale`/`SUNOJUMP_LOCALE` selection, and catalog-controlled right-to-left layout.
- **Pseudo-localization coverage.** The bundled `qps-ploc` catalog now drives offscreen GUI, status, error, CLI-help, responsive-layout, screenshot, and frozen-package tests instead of hand-edited sample labels.
- **Evidence-first A/B regression suite.** Added deterministic CC0 44.1/48 kHz stereo music cues, fixed-seed renders, unrelated-cue negative controls, schema-versioned per-detector score/coverage/alignment evidence, and local-only pass/fail thresholds.
- **Objective quality contract.** Added ITU-R BS.1770-5 gated loudness and oversampled true-peak measurements plus an optional hash-identified Google ViSQOL audio-mode CLI adapter that reports missing tools as unavailable.
- **Optional CUDA FFT backend.** Source runs can dispatch every STFT/ISTFT-heavy pass to a lazily loaded PyTorch CUDA runtime through `--compute` or `SUNOJUMP_COMPUTE_BACKEND`, with strict and fallback modes.
- **GPU correctness and artifact gates.** CUDA selection must pass an on-device SciPy parity vector before rendering; fixed-seed CPU/Torch contract tests cover the full FFT-heavy pass chain, while frozen builds exclude Torch and enforce a 250 MiB executable ceiling.
- **Compute provenance.** Replay sidecar schema v3 records backend/library/device/CUDA evidence and marks accelerated renders dependency-sensitive across GPU environments.
- **Bounded long-file rendering.** Full renders now decode into caller-owned NPY memory maps and automatically switch large payloads to disk-backed, overlap-blended chunks across the complete pass pipeline instead of retaining the whole source and every pass in RAM.
- **Streaming evidence and safety.** Replay sidecar schema v4 records decode/processing strategies, chunk geometry, and per-chunk pass traces; temporary disk capacity, cancellation, atomic output promotion, and fail-closed cleanup are enforced throughout the streaming path.
- **Streaming regression coverage.** Added caller-owned chunked-decode, bounded multi-chunk stereo, mono metadata-only, configuration, provenance, and pass-failure cleanup tests (251 total tests).
- **Parallel file rendering.** GUI and CLI batches now use an operator-bounded 1–8 thread pool (default 2), retain deterministic result ordering, prefix interleaved logs by queue job, and reserve collision-free outputs safely across concurrent renders.
- **Per-file progress.** Queue rows display progress for their stable job IDs while the batch bar aggregates all active and completed files; CLI batches emit independently labeled progress checkpoints.
- **Concurrent batch durability.** Batch-manifest attempts and terminal evidence are serialized through a re-entrant lock, preserving every distinct job update under parallel completion and cancellation.
- **Per-file A/B winners.** Saving the currently playing comparison preset now records a bounded per-file winner, restores it when that source is re-added, annotates the queue row, and applies the winner to Process All.
- **Private comparison history.** The 200-entry settings history keys files by a normalized-path SHA-256 and stores only preset/timestamp metadata, never the source path itself; malformed or future history schemas fail empty.
- **Arbitrary preview offsets.** The Monitor panel now selects any 0.1-second start point for Preview and A/B/C/D Compare clips; isolated decoding seeks before bounded reads, original playback aligns to the rendered segment, and the offset persists between sessions.
- **Offset replay evidence.** Preview sidecars record the exact source start frame and actual offset, while out-of-range offsets fail closed without creating output.
- **Watch-folder CLI.** `--watch DIRECTORY` continuously accepts stable supported top-level files, processes up to the selected worker count, and writes validated outputs to `DIRECTORY/output` by default until Ctrl+C.
- **Safe watch ingestion.** Size/mtime stabilization prevents partial-copy reads, unchanged files are deduplicated while modifications can be reprocessed, each accepted drop receives an atomic one-job manifest, and output cannot equal the watched directory.
- **Per-file queue presets.** Selected GUI queue rows can use any built-in preset or an independent snapshot of the current controls while unassigned rows continue to follow the batch settings.
- **Per-file recovery state.** Batch manifest schema v2 stores optional validated per-job configurations and display names, migrates v1 manifests in memory, and restores assignments for resumed parallel work.
- **Composable CLI profiles.** Added `--profile <json>` for a versioned built-in-preset plus sparse-overrides document; explicit value/pass flags apply last, while ambiguous configuration sources and operation-level policy keys fail closed.

## v1.6.1 (2026-07-01)
- **Clear Logs tooltip fix.** Tooltip now accurately says "Delete all persistent run logs" instead of falsely claiming it keeps the last 30.
- **Lossy re-encode encoder guard.** The internal MP3 re-encode pass now checks for `libmp3lame` availability before attempting the encode, matching the export path behavior.
- **Sidecar schema constant.** Sidecar JSON `schema_version` now uses the `PRESET_SCHEMA_VERSION` constant instead of a hardcoded `1`.
- **Windows path redaction.** Log path redaction now handles case-insensitive Windows paths correctly.
- **CLI help accuracy.** `--output` help text corrected from "Output file or directory" to "Output directory" to match actual behavior.
- **Screenshot tool exit code.** `capture_screenshot.py` now exits non-zero on version mismatch and only saves the screenshot on success.
- **Test mock safety.** Fixed ffmpeg failure test mock that created a stray `-encoders` file in the working directory.
- **Dead code removal.** Removed unused `sha256sums_command` function and unused `json` import from release tooling.
- **Verifier discovery robustness.** `discover_adapters` now catches all exceptions from external adapter modules, not just `ImportError`.
- **Deferred import promoted.** Moved `hashlib` import from method body to module level.
- **Regression coverage.** Added tests for path redaction, log retention cap enforcement, and lossy reencode missing-encoder guard (79 total tests).

## v1.6.0 (2026-06-30)
- **Release license compliance gate.** Added `tools/audit_licenses.py` that inventories all release dependency licenses, flags unreviewed copyleft packages, and blocks release packaging when license evidence is missing.
- **License inventory export.** `--write-inventory` flag emits a machine-readable `license-inventory.json` for release verification.
- **README legal section.** Documents GPL/LGPL runtime dependencies and source vs binary distribution strategy.
- **Python 3.11+ version guard.** Aligned documented support matrix with actual dependency requirements; added runtime guard that exits with a clear message on unsupported Python versions.
- **ffmpeg encoder probe.** MP3/M4A export now validates that the required codec (`libmp3lame`/`aac`) is available before rendering, preventing wasted full-file renders. Diagnostics log detected encoder support.
- **Segment-level replay sidecar.** Every output now writes a `.sidecar.json` with app version, input SHA-256, seed, enabled passes, params, dependency versions, and per-segment pitch/tempo/coupled decisions.
- **Metadata strip reporting.** `_strip_metadata()` now inventories before/after tag families, logs removed and retained metadata, and surfaces a provenance disclaimer when strip fails.
- **Preset schema versioning.** Saved presets include `schema_version`; unknown future versions fail with an actionable upgrade message; migration framework ready for future parameter changes.
- **GUI session persistence.** Window geometry, output directory, output format, preset, and browse directories are saved via QSettings and restored on next launch.
- **CLI `--help`/`--version`.** Both now work without launching the GUI or requiring `--input`.
- **Regression coverage.** Added tests for license audit, Python version guard, ffmpeg encoder probe, sidecar trace, preset schema migration, and preset schema version.

## v1.5.16 (2026-06-30)
- **Atomic final exports.** Final audio writes now target same-directory temporary files and promote with `os.replace()` only after the write, metadata strip, and cancellation checks succeed.
- **Failure cleanup.** Direct `soundfile` write errors, ffmpeg encoder failures, and late cancellation remove temporary outputs without leaving partial final artifacts.
- **Regression coverage.** Added atomic-output tests for direct write failure, ffmpeg failure, and cancellation after save.

## v1.5.15 (2026-06-30)
- **Input preflight guardrails.** Audio files are validated for supported extension, non-empty content, readable headers, channel count, sample rate, duration, container size, and decoded memory cost before full decode.
- **Bounded preview reads.** Preview renders now read only the requested preview window instead of decoding the full track before trimming.
- **Regression coverage.** Added malformed, oversized, unsupported, excessive-channel, excessive-sample-rate, decoded-memory, and preview-read preflight tests.

## v1.5.14 (2026-06-28)
- **Release dependency lock.** Added a pinned runtime dependency closure for release and PyInstaller builds while keeping normal source installs on `requirements.txt`.
- **Local dependency audit.** Added `tools/audit_dependencies.py`, a `pip-audit` wrapper that audits the pinned lock without dependency resolution and exits non-zero on known vulnerable packages.
- **Regression coverage.** Added tests that enforce runtime/dev dependency separation and lock-file pinning.

## v1.5.13 (2026-06-28)
- **Explicit codec export.** Output format handling now distinguishes direct WAV/FLAC/OGG export from ffmpeg-backed MP3/M4A export.
- **Fail-early codec guardrails.** CLI and processing paths fail with actionable messages when MP3/M4A export is requested without ffmpeg.
- **Regression coverage.** Added output-format mapping and ffmpeg-missing fail-closed tests.

## v1.5.12 (2026-06-28)
- **Accessible GUI controls.** Primary buttons, inputs, checkboxes, queue, log, and processing sliders now expose screen-reader names and descriptions.
- **Focus-order contract.** The main window now defines and tests a visual workflow tab order from queue controls through render controls.
- **Regression coverage.** Added a PyQt GUI accessibility smoke test.

## v1.5.11 (2026-06-28)
- **Persistent run diagnostics.** GUI batch, preview, compare, and CLI runs now write timestamped local logs with version, environment, inputs, output paths, preset/seed, parameters, pass results, and full tracebacks.
- **Open Log action.** The Session Log panel now exposes the latest persistent log for quick support/export workflows.
- **Regression coverage.** Added diagnostics log-format tests.

## v1.5.10 (2026-06-28)
- **Frozen-build hardening.** Startup now calls `multiprocessing.freeze_support()` before application imports and the PyInstaller spec includes a matching runtime hook.
- **No runtime dependency installs.** Source runs now fail with an actionable `pip install -r requirements.txt` message instead of trying to install packages at launch.
- **Packaging hygiene.** The PyInstaller spec is tracked so release builds use the hardened runtime hook consistently.

## v1.5.9 (2026-06-28)
- **Collision-free output names.** Batch and CLI renders now avoid overwriting same-stem files or existing outputs by assigning numbered `_sj_2`, `_sj_3`, ... suffixes.
- **Regression coverage.** Added tests for duplicate input stems and existing output files.

## v1.5.8 (2026-06-28)
- **Fail-closed processing.** Enabled processing pass failures now abort the render instead of silently saving a partially transformed file.
- **Lossy re-encode guard.** Enabling re-encode now fails the render when ffmpeg or the encode/decode round trip is unavailable.
- **Regression coverage.** Added a test proving an injected pass failure returns failure and leaves no output file.

## v1.5.7 (2026-06-27)
- **Constellation self-test harness.** Processing now estimates surviving Wang/Shazam-style landmark hashes and logs before/after landmark overlap alongside the detection-signature score.
- **Regression coverage.** Added tests proving identical audio retains a high constellation match while unrelated harmonic content drops sharply.

## v1.5.6 (2026-06-27)
- **STFT window-size sweep per spectral segment.** Spectral perturbation now jitters segment cut points and randomly selects 1024, 2048, or 4096 point STFT windows per segment when the input length supports them.
- **Regression coverage.** Added tests for window selection and explicit 4096-window perturbation.

## v1.5.5 (2026-06-27)
- **Psychoacoustic masking-aware noise injection.** Noise Injection now shapes pink noise under local spectral masks and caps the injected RMS so quiet regions are not over-noised.
- **Regression coverage.** Added a test verifying injected noise follows louder masked regions more than quiet regions while staying under the requested noise level.

## v1.5.4 (2026-06-27)
- **Dynamic EQ with loudness-preserving gain staging.** Added a multiband dynamic EQ pass that reshapes band energy, then matches LUFS-like integrated loudness back to the pre-pass reference.
- **Preset and CLI control.** Added Dynamic EQ amounts to built-in presets, preset JSON persistence, and `--dynamic-eq` for CLI runs.
- **Regression coverage.** Added tests verifying Dynamic EQ changes the waveform while preserving integrated loudness.

## v1.5.3 (2026-06-27)
- **Watermark-band scan pre-pass.** Spectral processing now auto-detects stable narrowband candidates per file and applies targeted perturbation to those bands.
- **Preset and CLI control.** Added a Watermark Scan toggle to the GUI, preset JSON persistence, and `--no-watermark-scan` for CLI runs.
- **Regression coverage.** Added tests for high-frequency candidate detection and candidate-band perturbation.

## v1.5.2 (2026-06-27)
- **Per-band spectral perturbation controls.** Added independent sub-bass, low-mids, presence, and air strengths with GUI sliders, preset JSON persistence, and CLI overrides.
- **Backward-compatible preset loading.** Older presets without band-specific keys continue to fall back to the global spectral strength.
- **Regression coverage.** Added tests for spectral band fallback, clamping, disabled-band handling, and targeted air-band perturbation.

## v1.5.1 (2026-06-27)
- **Coupled pitch/tempo micro-variation.** When both pitch and tempo passes are enabled, SunoJump now uses one shared non-uniform segment control curve so local timing drift and pitch shift move together while segment boundaries stay beat-aligned.
- **Regression coverage.** Added local unit tests for deterministic seeded coupled rendering, output length stability, finite samples, and fixed chunk endpoints.

## v1.5.0 (2026-04-19)
- **Premium GUI redesign.** Reworked the PyQt interface from a vertical utility stack into a two-column studio console with queue, monitor, session log, pipeline controls, destination, and render sections.
- **New visual system.** Added graphite surfaces, brass primary actions, clearer hover/disabled states, status pills, and icon-bearing controls for a more polished desktop-software feel.
- **Improved operational hierarchy.** Added a top status strip for file count, preset, output format, and render state so the app reads like an active production tool.
- **Scrollable processing controls.** Pipeline rows now live in a dedicated scrollable control surface to avoid cramped layouts while keeping every transform visible and easy to tune.
- **Clearer queue and render states.** File rows now show READY/RUNNING/DONE/FAILED states, and render status updates during processing, previewing, comparing, cancelling, and completion.
- **Destination layout fix.** Output format and directory controls were separated into cleaner rows so long paths remain readable.

## v1.4.2 (2026-04-19)
- **Fix: CI uploaded Linux and macOS binaries with the same name, causing collision.** Both targets built `dist/SunoJump` and uploaded as asset "SunoJump", so the second run clobbered the first. Only one surviving binary per release. Workflow now copies each binary to a unique name (`SunoJump-Linux`, `SunoJump-macOS`) before upload. Windows was already unique via `.exe`.
- **Fix: Playback state race on source transitions.** The `QMediaPlayer.playbackStateChanged(Stopped)` signal fired after `stop()` could run after the UI had already set state for the new source, wiping the new state. Added a `_media_transitioning` flag bracketing the stop -> setSource -> play sequence to suppress the stale signal. Affects both `_toggle_play` (original/processed) and `_play_compare` (compare panel).
- **Fix: Compare panel could show stale content when selection became None.** Now hides cleanly when the list is empty or selection moves away from the file compare was rendered for.
- **Fix: Output directory creation errors silently stranded the UI.** ProcessWorker now catches the OSError from `os.makedirs`, logs it, and emits `all_done` so the UI re-enables controls instead of hanging on "processing".
- **Fix: closeEvent could hit deallocated widgets via queued Stopped signal.** Disconnect `playbackStateChanged` and `errorOccurred` from the player before calling `stop()` during shutdown.
- **README banner points to committed file.** `banner.png` in the repo root (committed alongside) is now referenced via relative path instead of a GitHub attachments CDN URL, so the README renders correctly in forks and offline viewers.

## v1.4.1 (2026-04-19)
- **Fix (regression): Phase vocoder pitch shifter produced a silence tail** on any segment with a positive semitone shift. The `_pv_time_stretch` implementation had inverted rate semantics -- calling it with the pitch factor gave output_length = input_length / factor^2 samples of real audio, padded to full length with zeros. Only positive shifts were affected (negative shifts worked because the resample step produced longer intermediate audio that was correctly trimmed). Verified fix: +12 st on a 3s 440 Hz tone now produces a full 3s at 880 Hz with uniform RMS instead of 1s signal + 2s silence.
- **Progress bar updates during Compare Presets and Render Preview** -- PresetCompareWorker and PreviewWorker now emit progress signals; the main window progress bar reflects render progress instead of sitting at 0 for 30s.
- **File list rows show "[processing...]" marker** while a file is being processed in a batch (previously just sat at the default text until completion).
- **Total time logged on batch completion** -- "All done (45.2s)" or "(1m 23s)" for longer runs.
- **CI workflow is now idempotent** -- creates the GitHub Release if it doesn't exist before uploading binaries, handling the race between `git push --tags` and manual `gh release create`. Also handles concurrent matrix-job creates gracefully.
- **Defense-in-depth guard on Render Preview** -- explicit check that compare_worker isn't running (UI already disables the button, but the slot now bails cleanly if somehow invoked concurrently).

## v1.4.0 (2026-04-19)
- **Phase vocoder pitch shifting** -- replaced time-warp-based pitch micro-shift with a proper phase vocoder implementation (pure scipy, no librosa dependency). Eliminates the audible warble at Extreme preset's ±3-semitone shifts while preserving the non-uniform per-segment randomization that breaks fingerprints. Tempo no longer shifts as a side-effect of pitch.
- **Non-uniform spectral perturbation** -- spectral perturbation now processes in 3-second segments with per-segment random seeds, breaking detectors that look for consistent spectral signatures across a whole track. Short files (<3s) still get a single pass to avoid STFT edge issues.
- **Compare Presets** -- new button renders a 20-second sample with each built-in preset (Gentle, Moderate, Aggressive, Extreme). A compare panel appears below the preview row with per-preset play/stop toggles and an "Apply Currently Playing" button that sets the selected preset as active. Swapping the file selection hides the stale panel automatically.
- **Detection-signature heuristic** -- audio is now scored on a 0-100 scale using four features common AI-music detectors exploit: spectral frame variance, high-frequency rolloff, phase evolution, and short-term dynamic variance. Pre- and post-processing scores are logged (e.g. `Detection signature: 61% -> 37% (down 24%)`) so users get a directional indicator beyond the existing SNR-based modification strength.
- **Reproducibility seed** -- `AudioProcessor(seed=N)` and CLI `--seed N` produce bit-identical output across runs. Verified: same seed -> max sample diff 0.0; different seeds -> significant divergence.
- **UI polish** -- larger default/minimum window (760x980 min 720x900) accommodates the new compare panel. Compare + Render Preview + Process All are mutually exclusive at the UI level (each disables the others while running). Playback state cleanly resets on player stop for both regular and compare modes.

## v1.3.0 (2026-04-19)
- **Render Preview** -- new button in the Preview panel processes the first 30 seconds of the selected file with current settings, saves to a session temp directory, and auto-plays; lets you audition presets before committing compute to full-file processing
- `AudioProcessor.process()` gained an optional `preview_seconds` parameter that trims the input to the first N seconds before the pipeline runs
- New `PreviewWorker(QThread)` renders previews off the UI thread; mutually exclusive with batch processing (each blocks the other's button while running)
- Preview files carry a visible marker in the Preview label (`(preview: 30s)`) and the Play button relabels to "Play Preview" so users never confuse a sample with a full output
- `item_id`-based bookkeeping handles list mutations during render (removed/reordered items don't crash the done-handler)
- Preview temp directory auto-created on first use and cleaned up on app close
- Graceful degradation: Render Preview button disabled with tooltip when PyQt6 QtMultimedia module isn't available
- Clicking Process All while a preview is rendering is blocked (and vice versa) to keep resource use predictable

## v1.2.1 (2026-04-19)
- **Default preset changed to Extreme** -- real-world testing confirmed Extreme delivers the most consistent bypass results against Suno's detection; now the recommended starting point for all users
- README preset table and guidance updated to highlight Extreme as the recommended choice
- **Windows executable** (`SunoJump.exe`) now attached to every release -- no Python install required
- Added GitHub Actions CI workflow (`.github/workflows/build.yml`) for automated cross-platform builds on tag push (Windows / macOS / Linux) via PyInstaller

## v1.2.0 (2026-04-19)
- **In-app preview player** -- A/B compare original vs processed without leaving SunoJump; uses PyQt6 QMediaPlayer with graceful fallback if Multimedia module is unavailable
- **Custom preset save/load** -- export tuned settings to JSON and share/reuse them; known-keys allowlist on load prevents foreign-field contamination
- **CLI `--preset-file` flag** -- pass a saved preset JSON on the command line to reproduce GUI settings exactly
- **Open Output button** -- one-click jump to the output directory in the OS file manager (Windows/macOS/Linux via QDesktopServices); auto-creates the directory if missing
- **Drag-to-reorder file list** -- internal drag/drop for reordering, while still accepting external file drops; reorder auto-locked during processing
- **Memory-bounded humanization** -- long audio (>60s) is processed in 60-second chunks with shared modulation parameters for continuity; keeps peak memory bounded regardless of song length
- **Processed-file tracking per item** -- each list item stores its output path (UserRole+1) so the preview player can locate the result even after reorder
- **Improved CLI exit codes** -- returns 2 when any files fail processing, 0 on success, 1 on fatal setup errors
- **UI polish** -- drop-hint text, file-count indicator, tooltips on new buttons, disabled-state styles tightened
- **CLI preset-file validation** -- only known parameter keys accepted; malformed/foreign JSON fields silently ignored to prevent poisoning

## v1.1.0 (2026-04-19)
- **Fix: Preset selection immediately reverted to "Custom"** -- the most visible bug; presets now stay selected
- **Fix: File list manipulable during processing** -- Browse/Clear/Remove disabled while worker runs, prevents index corruption
- **Fix: Race condition in cancel** -- replaced mutable bool with threading.Event shared between worker and processor
- **Fix: Progress bar showed per-file not per-batch progress** -- now maps to overall batch completion percentage
- **Fix: Tempo slider showed raw decimal (0.05) instead of percentage (5.0%)** -- added display_factor scaling to ParamRow
- **Fix: Sliders stayed interactive when pass checkbox unchecked** -- now visually disabled (grayed out) when toggled off
- **Fix: Bootstrap failure was silent** -- now prints clear error message and install command on failure
- **Fix: STFT window size not power-of-2** -- added _nperseg_for() helper; improves FFT speed and reconstruction quality
- **Fix: ffmpeg availability checked every call** -- cached globally after first check
- **Fix: Dynamics pass had zipper noise** -- replaced per-frame hard gain with interpolated smooth gain curve
- **Fix: Humanization modified input array in-place** -- all passes now return new arrays, no aliasing risk
- **Fix: Stereo/noise passes mutated input** -- copied before modification for consistency
- **Fix: _compute_strength returned 100% on silence** -- now returns 0% when input is silence
- **Fix: meta_check not connected to param change** -- toggling it now correctly switches preset to Custom
- **Fix: ParamRow division by zero** -- guarded set_value when min_val == max_val
- **Fix: Path deduplication fragile on Windows** -- uses normcase+abspath for case-insensitive comparison
- **Fix: CLI --format shadowed Python builtin** -- renamed to dest='out_format'
- Added CLI parameter range validation with clamping and warnings
- Added "Remove" button for file list (multi-select support)
- Added file count label in file list header
- Added disabled-state styles for buttons and sliders
- Added -loglevel error to ffmpeg calls
- Added output directory auto-creation before save
- File dialog now remembers last browsed directory
- Progress bar resets to 100% on completion
- Removed unused imports (json, QMimeData, QSizePolicy)
- DropListWidget now supports ExtendedSelection for multi-select removal

## v1.0.0 (2026-04-18)
- Initial release
- 10-pass audio processing pipeline: metadata strip, spectral perturbation, pitch micro-shift, tempo micro-variation, phase scrambling, stereo manipulation, noise injection, dynamics modification, humanization, lossy re-encode
- Non-uniform segment-based processing to break constellation fingerprint patterns
- 4 presets: Gentle, Moderate, Aggressive, Extreme
- PyQt6 GUI with Catppuccin Mocha dark theme
- Drag-and-drop file input with batch processing
- Per-pass enable/disable toggles and strength sliders
- Modification strength metric with assessment
- CLI mode with full parameter control
- WAV/FLAC/OGG output formats
- Auto-installs dependencies on first run

## Roadmap archive — 2026-08-10 — ROADMAP.md

<details>
<summary>Original roadmap snapshot</summary>

```markdown
# SunoJump Roadmap

Roadmap for SunoJump — the 11-pass, local audio-analysis and transform pipeline. Focus: evidence-first diagnostics for rights-owned audio, preserved quality, truthful uncertainty, and recoverable desktop/CLI workflows.

## Planned Features

### Evidence and quality validation
- A/B regression suite with redistributable 44.1/48 kHz stereo music, per-detector score/coverage/offset, ViSQOL-audio or validated PEAQ, BS.1770-5 loudness/true peak, fixed seeds, negative controls, and no Suno-acceptance claim

### Performance
- GPU path via CuPy/PyTorch for FFT-heavy passes after correctness and artifact-size gates
- Streaming / chunked rendering for long files with bounded RAM (on 2026-07-29, only Humanization is chunked; the full file is otherwise decoded)
- Parallel-per-file worker pool with per-file progress

### Presets & customization
- Preset A/B/C/D Compare UI enhancements - save the winner per-file back into history
- Corpus-derived quality profiles for vocal/instrumental and dense/sparse material; do not ship genre labels until detector and perceptual results prove a meaningful difference
- Preview at arbitrary offsets (not just first 30 seconds)

### Batch & CLI
- Watch-folder mode: drop a file, get a processed file in output/
- GUI queue view with per-file presets
- `--profile <json>` flag composes preset plus overrides
- Exit codes: 0 = success, 1 = partial, 2 = all failed; add schema-versioned JSON/JSONL per-file results while keeping human logs on stderr

### Auditing
- Before/after spectrogram side-by-side PNG export per file
- Experimental modification metric expanded to named per-pass contributions without bypass/effectiveness labels
- Before/after ITU-R BS.1770-5 loudness + true-peak report
- Crest-factor and stereo-width delta report

## Competitive Research

- **Chromaprint 1.6.1 / pyacoustid** — add as a lightweight, test-only second fingerprint family with short-input and chunk-boundary fixtures.
- **Olaf / Panako** — exercise detector-side pitch/time normalization through optional test adapters; do not bundle AGPL components.
- **AudioMarkBench / RAW-Bench / OmniSealBench** — borrow declarative attacks, fixed seeds, raw JSON/CSV results, per-pass ablation, and perceptual-quality gates without importing their neural stacks.
- **Archived direct peers** — retain their analysis-only and backup patterns, but never treat changed hashes or “patterns suppressed” as detector evidence.

## Nice-to-Haves

- Per-pass waveform/spectrogram preview in GUI
- History panel with one-click reproduce, sidecar references, input/output hashes, and explicit user-recorded outcomes (`accepted`, `match_block`, `timeout`, `account_restriction`, `inconclusive`, `unknown`)
- Portable build target: single-file exe <= 50 MB with no undeclared torch/test/scientific packages; depends on the isolated release-chain item below
- Process-isolated pass SDK with a narrow typed protocol, permissions, time/memory limits, and explicit trust prompts; never import arbitrary `.py` files into the GUI process
- VST/CLAP plugin wrapper for use inside a DAW as an effect chain

## Research-Driven Additions

### P2

- [ ] P2 — Make diagnostics deletion, redaction, and retention trustworthy
  Why: Clear Logs is unconfirmed, ignores deletion failures, can delete the active log and immediately recreate it, and diagnostics retain absolute paths indefinitely.
  Evidence: `sunojump.py` `RunDiagnostics`, `_clear_all_logs`, `_start_run_log`; privacy expectations from ShazamKit/offline recognition products.
  Touches: diagnostics lifecycle, clear/export dialog, retention settings, redaction preview, GUI/CLI tests.
  Acceptance: active handles close before confirmed deletion; failures and remaining files are reported; users can set retention, preview redaction, export a support bundle, and identify every local log/history/sidecar location.
  Complexity: M

- [ ] P2 — Move discovery off the UI thread and make empty/error states actionable
  Why: recursive directory scans block the GUI, Process All is available with an empty queue, and render errors exist mainly in the session log.
  Evidence: `sunojump.py` folder discovery, `_current_selected_item`, processing controls, error logging; Qt worker patterns.
  Touches: cancellable scan worker, queue empty/loading/error UI, explicit preview target, failed-item summary/retry action, GUI tests.
  Acceptance: large recursive scans show determinate or counted progress and can cancel; empty queues disable processing and explain the next action; preview/compare identify the selected file; render failures show per-file reason and retry without requiring log inspection.
  Complexity: M

- [ ] P2 — Add a dependency and native-library upgrade compatibility lane
  Why: NumPy/SciPy/PyInstaller drift, SciPy's Python-floor/FFT changes, and hidden native wheel contents make ad hoc upgrades unsafe.
  Evidence: `requirements-lock.txt`; NumPy 2.2.6 and SciPy 1.18.0 release notes; PyInstaller 6.21 documentation; SoundFile/native CVEs.
  Touches: dependency-report tool, Python 3.11/3.12 test matrix, DSP golden fixtures, release inventory, upgrade documentation.
  Acceptance: a command reports direct/transitive/native drift and security status; supported Python lanes run deterministic DSP golden, GUI, and packaging smoke tests; every lock update records compatibility, native versions, and an explicit rollback point.
  Complexity: M

### P3

- [ ] P3 — Route UI and CLI strings through a localization catalog
  Why: GUI labels, tooltips, status text, and CLI help are still hardcoded, blocking future localization and consistent microcopy cleanup.
  Evidence: `sunojump.py` UI builders and CLI parser; Qt `QTranslator`/`QLocale`; 2026-07-29 source scan found no live localization catalog.
  Touches: `sunojump.py` UI builders, CLI parser setup, tests for representative strings.
  Acceptance: user-facing strings use a catalog compatible with Qt locale fallback and pluralization; English remains unchanged; pseudo-localized and RTL tests prove key GUI, status, error, and CLI strings resolve without clipping.
  Complexity: M
```

</details>
