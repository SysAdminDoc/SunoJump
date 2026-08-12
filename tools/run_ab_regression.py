#!/usr/bin/env python3
"""Run the generated-corpus A/B detector and perceptual-quality contract."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

import numpy as np
from scipy.signal import resample_poly
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audio_quality import measure_bs1770  # noqa: E402
from render_results import RenderState  # noqa: E402
from sunojump import AudioProcessor, PRESETS  # noqa: E402
from verifiers import ConstellationVerifier, VerifierState  # noqa: E402
from verifiers_visqol import Adapter as VisqolAudioVerifier  # noqa: E402


REPORT_SCHEMA_VERSION = 1
FIXTURE_MANIFEST = ROOT / "tests" / "fixtures" / "manifest.json"
FIXTURE_GENERATOR = ROOT / "tests" / "fixtures" / "generate.py"
SCOPE_NOTICE = (
    "Local regression evidence only; detector and quality measurements make "
    "no external-platform inference."
)
MAX_LOUDNESS_DELTA_LU = 6.0
MAX_TRUE_PEAK_DBTP = 1.0


def _fixture_module():
    module_spec = importlib.util.spec_from_file_location(
        "sunojump_fixture_generator",
        FIXTURE_GENERATOR,
    )
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def _music_specs() -> list[dict]:
    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    return [
        fixture
        for fixture in manifest["generated_fixtures"]
        if fixture.get("generator") == "music"
    ]


def _resample_to(audio: np.ndarray, source_rate: int, target_rate: int):
    if source_rate == target_rate:
        return audio
    divisor = int(np.gcd(source_rate, target_rate))
    return resample_poly(
        audio,
        target_rate // divisor,
        source_rate // divisor,
        axis=0,
    )


def _quality_pair(original, processed, sample_rate: int) -> dict:
    before = measure_bs1770(original, sample_rate)
    after = measure_bs1770(processed, sample_rate)
    loudness_delta = (
        after.integrated_lufs - before.integrated_lufs
        if before.integrated_lufs is not None
        and after.integrated_lufs is not None
        else None
    )
    true_peak_delta = (
        after.true_peak_dbtp - before.true_peak_dbtp
        if before.true_peak_dbtp is not None
        and after.true_peak_dbtp is not None
        else None
    )
    return {
        "before": before.to_dict(),
        "after": after.to_dict(),
        "integrated_loudness_delta_lu": (
            round(loudness_delta, 4) if loudness_delta is not None else None
        ),
        "true_peak_delta_db": (
            round(true_peak_delta, 4) if true_peak_delta is not None else None
        ),
    }


def _detectors(
    original,
    processed,
    sample_rate: int,
    processor,
    visqol,
) -> list[dict]:
    return [
        ConstellationVerifier(processor).score(
            original,
            processed,
            sample_rate,
        ).to_dict(),
        visqol.score(original, processed, sample_rate).to_dict(),
    ]


def _validate_corpus(specs: list[dict]) -> list[str]:
    failures = []
    if {spec["sample_rate"] for spec in specs} != {44100, 48000}:
        failures.append("music corpus must cover 44.1 and 48 kHz")
    for spec in specs:
        if spec.get("channels") != 2:
            failures.append(f"{spec.get('name')} is not stereo")
        if not spec.get("regression_seed"):
            failures.append(f"{spec.get('name')} has no fixed regression seed")
        if spec.get("license") not in {"CC0-1.0", "MIT"}:
            failures.append(f"{spec.get('name')} is not redistributable")
    return failures


def run_suite(
    *,
    preset_name: str = "Moderate",
    visqol_binary: str | None = None,
    require_visqol: bool = False,
) -> dict:
    specs = _music_specs()
    failures = _validate_corpus(specs)
    generator = _fixture_module()
    visqol = VisqolAudioVerifier(visqol_binary)
    pairs = []
    originals = []

    with tempfile.TemporaryDirectory(prefix="sunojump-ab-regression-") as temp_dir:
        temp_root = Path(temp_dir)
        for spec in specs:
            original, sample_rate = generator.GENERATORS[spec["generator"]](spec)
            originals.append((spec, original, sample_rate))
            input_path = temp_root / f"{spec['name']}.wav"
            output_path = temp_root / f"{spec['name']}_b.wav"
            sf.write(input_path, original, sample_rate, subtype="PCM_24")
            params = dict(PRESETS[preset_name])
            params.update({
                "strip_metadata": False,
                "output_format": "wav",
                "reencode_enabled": False,
            })
            processor = AudioProcessor(
                params,
                log_fn=lambda _message: None,
                seed=int(spec["regression_seed"]),
            )
            render = processor.process(input_path, output_path)
            if render.state is not RenderState.SUCCEEDED:
                failures.append(
                    f"{spec['name']} render was {render.state.value}: "
                    f"{render.error_code} {render.message}"
                )
                continue
            processed, processed_rate = sf.read(output_path, dtype="float64")
            if processed_rate != sample_rate:
                failures.append(f"{spec['name']} changed sample rate")
                continue
            detectors = _detectors(
                original,
                processed,
                sample_rate,
                processor,
                visqol,
            )
            quality = _quality_pair(original, processed, sample_rate)
            constellation = detectors[0]
            if constellation["state"] != VerifierState.MEASURED.value:
                failures.append(f"{spec['name']} constellation was not measured")
            visqol_result = detectors[1]
            if (
                (require_visqol or visqol_binary)
                and visqol_result["state"] != VerifierState.MEASURED.value
            ):
                failures.append(f"{spec['name']} ViSQOL audio score unavailable")
            loudness_delta = quality["integrated_loudness_delta_lu"]
            if loudness_delta is None:
                failures.append(f"{spec['name']} loudness was not measurable")
            elif abs(loudness_delta) > MAX_LOUDNESS_DELTA_LU:
                failures.append(
                    f"{spec['name']} loudness delta {loudness_delta} LU exceeded "
                    f"{MAX_LOUDNESS_DELTA_LU} LU"
                )
            true_peak = quality["after"]["true_peak_dbtp"]
            if true_peak is None:
                failures.append(f"{spec['name']} true peak was not measurable")
            elif true_peak > MAX_TRUE_PEAK_DBTP:
                failures.append(
                    f"{spec['name']} true peak {true_peak} dBTP exceeded "
                    f"{MAX_TRUE_PEAK_DBTP} dBTP"
                )
            pairs.append({
                "fixture": spec["name"],
                "license": spec["license"],
                "sample_rate_hz": sample_rate,
                "channels": spec["channels"],
                "duration_seconds": spec["duration_sec"],
                "fixed_seed": spec["regression_seed"],
                "a": "generated reference",
                "b": f"{preset_name} render",
                "detectors": detectors,
                "quality": quality,
            })

        negative_controls = []
        if len(originals) >= 2:
            left_spec, left, left_rate = originals[0]
            right_spec, right, right_rate = originals[1]
            right = _resample_to(right, right_rate, left_rate)
            common = min(left.shape[0], right.shape[0])
            negative_processor = AudioProcessor({}, seed=0)
            detectors = _detectors(
                left[:common],
                right[:common],
                left_rate,
                negative_processor,
                visqol,
            )
            negative_constellation = detectors[0]
            if (
                negative_constellation["state"]
                != VerifierState.MEASURED.value
            ):
                failures.append("negative-control constellation was not measured")
            elif negative_constellation["value"] >= 50.0:
                failures.append("negative-control landmark overlap was not low")
            negative_controls.append({
                "a": left_spec["name"],
                "b": right_spec["name"],
                "relationship": "independently generated unrelated cues",
                "detectors": detectors,
            })

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "scope": SCOPE_NOTICE,
        "preset": preset_name,
        "quality_contract": {
            "perceptual_metric": "Google ViSQOL audio mode (optional CLI adapter)",
            "loudness_and_peak": "ITU-R BS.1770-5",
            "max_integrated_loudness_delta_lu": MAX_LOUDNESS_DELTA_LU,
            "max_true_peak_dbtp": MAX_TRUE_PEAK_DBTP,
            "visqol_required": bool(require_visqol or visqol_binary),
        },
        "pairs": pairs,
        "negative_controls": negative_controls,
        "summary": {
            "passed": not failures,
            "pair_count": len(pairs),
            "failure_count": len(failures),
            "failures": failures,
        },
    }


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run generated-music A/B detector and quality regressions.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--preset",
        choices=tuple(PRESETS),
        default="Moderate",
    )
    parser.add_argument(
        "--visqol-binary",
        help="Google ViSQOL CLI; also accepted through VISQOL_BINARY",
    )
    parser.add_argument(
        "--require-visqol",
        action="store_true",
        help="fail when the ViSQOL audio-mode score is unavailable",
    )
    options = parser.parse_args(argv)
    report = run_suite(
        preset_name=options.preset,
        visqol_binary=options.visqol_binary,
        require_visqol=options.require_visqol,
    )
    if options.output:
        _write_report(options.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
