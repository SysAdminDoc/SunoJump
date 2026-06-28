# Research - SunoJump

## Executive Summary
SunoJump is a Python/PyQt6 desktop and CLI tool that applies an 11-pass, non-uniform audio transform pipeline to reduce acoustic-fingerprint matches while keeping creator-owned tracks usable. Verified from `README.md` and `sunojump.py`, its strongest current shape is the local-first processing engine plus preview/compare workflow; the highest-value direction is making every bypass claim measurable, reproducible, and failure-honest across multiple fingerprint families. Top opportunities: fail closed when enabled passes fail, prevent batch output overwrites, harden frozen builds and dependency installation, add accessibility metadata, add persistent diagnostics, make codec/output behavior explicit, and prepare strings for localization after the UI stabilizes.

## Product Map
- Core workflows: drag/drop batch processing, preset tuning, 30-second render preview, four-preset comparison, CLI directory/file processing.
- User personas: creators re-uploading their own generated tracks as templates; technical users benchmarking fingerprint changes; batch users processing folders with reproducible seeds.
- Platforms and distribution: Python 3.9+ source on Windows/macOS/Linux; PyInstaller Windows executable; optional ffmpeg for lossy re-encode; optional PyQt6 Multimedia for playback.
- Key integrations and data flows: local audio input via `soundfile`, metadata stripping through `mutagen`, DSP through `numpy`/`scipy.signal`, optional ffmpeg MP3 encode/decode, GUI logs in `QTextEdit`, preview artifacts in a temp directory.

## Competitive Landscape
- geeknik/ai-audio-fingerprint-remover: closest OSS peer focused on AI-audio fingerprint/watermark removal; learn from its explicit layered-pass framing and user expectation management; avoid unverifiable universal-removal claims.
- dejavu, audfprint, and adblockradio/stream-audio-fingerprint: Wang/Shazam-style landmark engines; learn from simple before/after match fixtures and database-backed regression tests; avoid coupling SunoJump to one landmark implementation.
- Panako and Olaf: event-point/Gabor-style fingerprint systems; learn from testing against different transform sensitivities; avoid assuming current constellation overlap covers all detector families.
- Chromaprint/AcoustID and pyacoustid: widely used fingerprinting CLI/API ecosystem; learn from minimal command-line ergonomics and stable fingerprints; avoid cloud lookups by default because user audio should stay local.
- AddictedCS/soundfingerprinting: mature .NET fingerprinting library with LSH/search model; learn confidence scoring and indexed corpora; avoid adding a heavyweight runtime unless isolated behind an optional verifier adapter.
- ACRCloud, Audible Magic, Pex, and BMAT: commercial recognition stacks emphasize confidence, monitoring history, API results, and reports; learn result provenance and audit exports; avoid making third-party upload checks the default.
- Audacity and DAW-style workflows: users already understand preview, spectrograms, macros, and codec selection; learn transparent A/B inspection and export controls; avoid hiding destructive processing behind a single success message.
- C2PA/Digimarc provenance and watermarking: provenance/watermark ecosystems are moving toward standardized metadata and durable watermark signals; learn to distinguish metadata removal, acoustic fingerprint movement, and provenance claims; avoid promising removal of content-based identity.

## Security, Privacy, and Reliability
- Verified risk: `_bootstrap()` in `sunojump.py:10` silently shells `sys.executable -m pip install` before imports, and `SunoJump.spec` has no runtime hook or frozen guard. This conflicts with normal Python packaging and risks frozen-app relaunch behavior on Windows.
- Verified risk: `AudioProcessor.process()` catches every enabled pass failure at `sunojump.py:624-660`, logs a warning, skips the pass, then can still save/report success. For this product, a partial transform should not look like a validated bypass.
- Verified risk: `ProcessWorker.run()` and `cli_main()` write outputs as `<stem>_sj<ext>` at `sunojump.py:1793` and `sunojump.py:3341`; two different input folders with the same stem can overwrite each other in one batch.
- Verified risk: GUI errors mostly go to the in-app log; there is no persistent crash/run log for users to attach when processing fails after the window closes.
- Verified risk: dependencies are loose lower bounds in `requirements.txt`, which is useful for libraries but weak for a distributed desktop app that needs reproducible builds and security review.
- Privacy posture is strong because processing is local by default; preserve that by making any commercial/cloud verifier explicit, opt-in, and documented.

## Architecture Assessment
- `sunojump.py` is over 3,300 lines and contains bootstrap, DSP engine, workers, widgets, GUI layout, playback, and CLI. The next maintainable boundary is a small `processing/` engine module plus `gui/` and `cli/` facades, after the reliability fixes land.
- `AudioProcessor.process()` needs a structured result model rather than boolean success plus log strings, so GUI/CLI can distinguish cancellation, read failure, pass failure, save failure, and verifier results.
- `ProcessWorker`, `PreviewWorker`, and `PresetCompareWorker` duplicate output-path and processor setup logic; pull path planning and parameter snapshots into one helper before adding sidecar traces or watch-folder mode.
- Tests in `tests/test_audio_processor.py` cover DSP primitives well, but there are no CLI integration tests, output-collision tests, frozen-build smoke tests, accessibility checks, or error-path tests for failed passes.
- UI controls have tooltips but no `setAccessibleName`/`setAccessibleDescription` coverage, and focus order/state announcements are not tested.

## Rejected Ideas
- Hosted multi-user processing service: rejected for now because the local-private workflow is a core privacy advantage; source: commercial recognition APIs and current local-only architecture.
- Guaranteed content-fingerprint removal: rejected because robust fingerprint systems intentionally survive many transforms; source: Wang/Shazam, Panako/Olaf, Chromaprint.
- Default cloud upload verification: rejected because it sends private creator audio to third parties; source: ACRCloud/Audible Magic/Pex/BMAT API models.
- Mobile-first app: rejected until desktop validation and packaging are stable; current pipeline depends on desktop file workflows, ffmpeg, and heavy FFT processing.
- DRM/copyright circumvention tooling: rejected because the README's stated use is creator-owned false-positive recovery, and feature work should reinforce that boundary.

## Sources
OSS:
- https://github.com/geeknik/ai-audio-fingerprint-remover
- https://github.com/worldveil/dejavu
- https://github.com/dpwe/audfprint
- https://github.com/adblockradio/stream-audio-fingerprint
- https://github.com/JorenSix/Panako
- https://github.com/JorenSix/Olaf
- https://github.com/acoustid/chromaprint
- https://github.com/beetbox/pyacoustid
- https://github.com/AddictedCS/soundfingerprinting
- https://github.com/itspoma/audio-fingerprint-identifying-python

Commercial:
- https://www.acrcloud.com/music-recognition/
- https://www.audiblemagic.com/
- https://pex.com/
- https://www.bmat.com/
- https://www.digimarc.com/

Research and Standards:
- https://www.ee.columbia.edu/~dpwe/papers/Wang03-shazam.pdf
- https://transactions.ismir.net/articles/10.5334/tismir.116
- https://c2pa.org/specifications/

Dependencies and Packaging:
- https://docs.scipy.org/doc/scipy/release/1.16.0-notes.html
- https://python-soundfile.readthedocs.io/en/latest/
- https://pypi.org/project/PyQt6/
- https://mutagen.readthedocs.io/en/latest/changelog.html
- https://pyinstaller.org/en/stable/CHANGES.html

Security:
- https://github.com/advisories?query=scipy
- https://github.com/advisories?query=libsndfile
- https://github.com/advisories?query=PyQt6

## Open Questions
None that block prioritization. Suno-specific detector access remains an external dependency, but the roadmap can proceed with local OSS verifier adapters first.
