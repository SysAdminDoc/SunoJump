# Changelog

## v1.6.0 (2026-06-30)
- **Release license compliance gate.** Added `tools/audit_licenses.py` that inventories all release dependency licenses, flags unreviewed copyleft packages, and blocks release packaging when license evidence is missing.
- **License inventory export.** `--write-inventory` flag emits a machine-readable `license-inventory.json` for release verification.
- **README legal section.** Documents GPL/LGPL runtime dependencies and source vs binary distribution strategy.
- **Regression coverage.** Added license audit tests for lock coverage, copyleft detection, inventory generation, and distribution note enforcement.

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
