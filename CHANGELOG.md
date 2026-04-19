# Changelog

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
