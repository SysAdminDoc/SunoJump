# Changelog

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
