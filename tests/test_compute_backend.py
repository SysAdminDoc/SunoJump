#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest

import numpy as np

from compute_backend import (
    ComputeBackendError,
    ScipyCpuBackend,
    TorchFftBackend,
    resolve_compute_backend,
    validate_fft_contract,
)
from sunojump import AudioProcessor, _build_cli_parser


ROOT = Path(__file__).resolve().parents[1]


class ComputeBackendSelectionTests(unittest.TestCase):
    def test_cpu_is_the_release_safe_default(self):
        backend = resolve_compute_backend("cpu")

        self.assertEqual(backend.name, "scipy-cpu")
        self.assertFalse(backend.accelerated)
        self.assertEqual(backend.evidence["requested"], "cpu")

    def test_auto_is_explicit_about_acceleration_or_fallback(self):
        backend = resolve_compute_backend("auto")

        self.assertEqual(backend.evidence["requested"], "auto")
        if backend.accelerated:
            self.assertEqual(backend.name, "torch-cuda")
        else:
            self.assertEqual(backend.name, "scipy-cpu")
            self.assertIn("fallback_reason", backend.evidence)

    def test_unknown_backend_fails_closed(self):
        with self.assertRaises(ComputeBackendError):
            resolve_compute_backend("magic")

    def test_cli_exposes_bounded_compute_choices(self):
        args = _build_cli_parser().parse_args([
            "--input",
            "fixture.wav",
            "--compute",
            "auto",
        ])

        self.assertEqual(args.compute, "auto")

    def test_frozen_artifact_excludes_optional_torch_runtime(self):
        spec = (ROOT / "SunoJump.spec").read_text(encoding="utf-8")
        release = (ROOT / "tools" / "build_release.py").read_text(
            encoding="utf-8",
        )
        backend = (ROOT / "compute_backend.py").read_text(encoding="utf-8")

        self.assertIn("'torch'", spec)
        self.assertIn('"torch"', release)
        self.assertNotIn("import torch", backend)


@unittest.skipUnless(importlib.util.find_spec("torch"), "PyTorch not installed")
class TorchFftCorrectnessTests(unittest.TestCase):
    def test_runtime_correctness_gate_records_evidence(self):
        backend = TorchFftBackend("cpu", requested="test")

        result = validate_fft_contract(backend)

        self.assertEqual(result["status"], "pass")
        self.assertEqual(
            backend.evidence["correctness_gate"]["reference"],
            "scipy-cpu",
        )

    def test_torch_fft_contract_matches_scipy_on_cpu(self):
        rng = np.random.default_rng(20260812)
        audio = rng.normal(0.0, 0.1, 4097)
        scipy_backend = ScipyCpuBackend()
        torch_backend = TorchFftBackend("cpu", requested="test")

        scipy_f, scipy_t, scipy_z = scipy_backend.stft(
            audio,
            48000,
            nperseg=1024,
            noverlap=512,
        )
        torch_f, torch_t, torch_z = torch_backend.stft(
            audio,
            48000,
            nperseg=1024,
            noverlap=512,
        )
        scipy_out_t, scipy_output = scipy_backend.istft(
            scipy_z,
            48000,
            nperseg=1024,
            noverlap=512,
        )
        torch_out_t, torch_output = torch_backend.istft(
            torch_z,
            48000,
            nperseg=1024,
            noverlap=512,
        )

        np.testing.assert_allclose(torch_f, scipy_f, atol=0.0, rtol=0.0)
        np.testing.assert_allclose(torch_t, scipy_t, atol=1e-15, rtol=0.0)
        np.testing.assert_allclose(torch_z, scipy_z, atol=1e-12, rtol=1e-12)
        np.testing.assert_allclose(
            torch_out_t,
            scipy_out_t,
            atol=1e-15,
            rtol=0.0,
        )
        np.testing.assert_allclose(
            torch_output,
            scipy_output,
            atol=1e-11,
            rtol=1e-11,
        )

    def test_fft_heavy_passes_match_cpu_with_a_fixed_seed(self):
        sample_rate = 24000
        time_axis = np.arange(sample_rate * 2, dtype=np.float64) / sample_rate
        audio = np.column_stack((
            0.2 * np.sin(2.0 * np.pi * 440.0 * time_axis),
            0.18 * np.sin(2.0 * np.pi * 997.0 * time_axis + 0.2),
        ))
        params = {
            "spectral_strength": 0.3,
            "phase_amount": 0.2,
            "dynamic_eq_amount": 0.2,
        }
        cpu = AudioProcessor(params, seed=123, compute_backend="cpu")
        torch_cpu = AudioProcessor(
            params,
            seed=123,
            compute_backend=TorchFftBackend("cpu", requested="test"),
        )

        cpu_output = cpu._spectral_perturb(audio, sample_rate)
        cpu_output = cpu._dynamic_eq(cpu_output, sample_rate)
        cpu_output = cpu._phase_scramble(cpu_output, sample_rate)
        torch_output = torch_cpu._spectral_perturb(audio, sample_rate)
        torch_output = torch_cpu._dynamic_eq(torch_output, sample_rate)
        torch_output = torch_cpu._phase_scramble(torch_output, sample_rate)

        np.testing.assert_allclose(
            torch_output,
            cpu_output,
            atol=2e-11,
            rtol=2e-11,
        )


if __name__ == "__main__":
    unittest.main()
