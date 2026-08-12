"""Lazy FFT compute backends with a CPU-safe frozen-release default."""
from __future__ import annotations

import importlib
import os

import numpy as np
from scipy import signal


COMPUTE_BACKEND_ENV = "SUNOJUMP_COMPUTE_BACKEND"
COMPUTE_BACKEND_CHOICES = ("cpu", "auto", "cuda")
_FFT_CONTRACT_CACHE = {}


class ComputeBackendError(RuntimeError):
    """Raised when an explicitly requested compute backend is unavailable."""


class ScipyCpuBackend:
    name = "scipy-cpu"
    accelerated = False

    def __init__(self, *, requested: str = "cpu", fallback_reason: str | None = None):
        self.requested = requested
        self.fallback_reason = fallback_reason

    @property
    def evidence(self) -> dict:
        payload = {
            "requested": self.requested,
            "selected": self.name,
            "accelerated": False,
            "library": "scipy",
        }
        if self.fallback_reason:
            payload["fallback_reason"] = self.fallback_reason
        return payload

    def stft(self, samples, sample_rate=1.0, *, nperseg, noverlap):
        return signal.stft(
            samples,
            sample_rate,
            nperseg=nperseg,
            noverlap=noverlap,
        )

    def istft(self, spectrum, sample_rate=1.0, *, nperseg, noverlap):
        return signal.istft(
            spectrum,
            sample_rate,
            nperseg=nperseg,
            noverlap=noverlap,
        )


class TorchFftBackend:
    """Torch STFT/ISTFT with SciPy's default boundary/scaling semantics."""

    accelerated = True

    def __init__(self, device: str = "cuda", *, requested: str = "cuda"):
        try:
            torch = importlib.import_module("torch")
        except ImportError as exc:
            raise ComputeBackendError(
                "PyTorch is not installed; use --compute cpu or install a "
                "CUDA-enabled PyTorch source environment"
            ) from exc
        if device == "cuda" and not torch.cuda.is_available():
            raise ComputeBackendError(
                "CUDA was requested but this PyTorch runtime has no usable CUDA device"
            )
        self._torch = torch
        self.device = torch.device(device)
        self.requested = requested
        self.name = f"torch-{self.device.type}"
        self.accelerated = self.device.type == "cuda"
        self.correctness_gate = None

    @property
    def evidence(self) -> dict:
        torch = self._torch
        payload = {
            "requested": self.requested,
            "selected": self.name,
            "accelerated": self.accelerated,
            "library": "torch",
            "library_version": str(torch.__version__),
            "device": str(self.device),
            "cuda_runtime": str(torch.version.cuda or "unavailable"),
        }
        if self.device.type == "cuda":
            payload["device_name"] = torch.cuda.get_device_name(self.device)
        if self.correctness_gate is not None:
            payload["correctness_gate"] = self.correctness_gate
        return payload

    def _window(self, nperseg: int):
        return self._torch.hann_window(
            nperseg,
            periodic=True,
            dtype=self._torch.float64,
            device=self.device,
        )

    def stft(self, samples, sample_rate=1.0, *, nperseg, noverlap):
        torch = self._torch
        hop = nperseg - noverlap
        if hop <= 0:
            raise ValueError("STFT overlap must be smaller than its window")
        values = torch.as_tensor(
            np.asarray(samples, dtype=np.float64),
            dtype=torch.float64,
            device=self.device,
        )
        if values.ndim != 1:
            raise ValueError("FFT backend STFT expects one-dimensional audio")
        boundary = nperseg // 2
        values = torch.nn.functional.pad(values, (boundary, boundary))
        remainder = (values.numel() - nperseg) % hop
        if remainder:
            values = torch.nn.functional.pad(values, (0, hop - remainder))
        window = self._window(nperseg)
        frames = values.unfold(0, nperseg, hop)
        spectrum = torch.fft.rfft(frames * window, n=nperseg, dim=1)
        spectrum = spectrum.transpose(0, 1) / torch.sum(window)
        frequencies = torch.fft.rfftfreq(
            nperseg,
            d=1.0 / float(sample_rate),
            device=self.device,
        )
        times = torch.arange(
            frames.shape[0],
            dtype=torch.float64,
            device=self.device,
        ) * (hop / float(sample_rate))
        return (
            frequencies.detach().cpu().numpy(),
            times.detach().cpu().numpy(),
            spectrum.detach().cpu().numpy(),
        )

    def istft(self, spectrum, sample_rate=1.0, *, nperseg, noverlap):
        torch = self._torch
        hop = nperseg - noverlap
        if hop <= 0:
            raise ValueError("ISTFT overlap must be smaller than its window")
        values = torch.as_tensor(
            np.asarray(spectrum, dtype=np.complex128),
            dtype=torch.complex128,
            device=self.device,
        )
        if values.ndim != 2:
            raise ValueError("FFT backend ISTFT expects a 2D spectrum")
        window = self._window(nperseg)
        frames = torch.fft.irfft(
            values.transpose(0, 1) * torch.sum(window),
            n=nperseg,
            dim=1,
        )
        frame_count = frames.shape[0]
        output_length = nperseg + hop * max(0, frame_count - 1)
        columns = (frames * window).transpose(0, 1).unsqueeze(0)
        output = torch.nn.functional.fold(
            columns,
            output_size=(1, output_length),
            kernel_size=(1, nperseg),
            stride=(1, hop),
        ).reshape(-1)
        norm_columns = (
            window.square()[:, None]
            .expand(nperseg, frame_count)
            .unsqueeze(0)
        )
        norm = torch.nn.functional.fold(
            norm_columns,
            output_size=(1, output_length),
            kernel_size=(1, nperseg),
            stride=(1, hop),
        ).reshape(-1)
        valid = norm > 1e-10
        output[valid] /= norm[valid]
        boundary = nperseg // 2
        output = output[boundary:-boundary] if boundary else output
        times = torch.arange(
            output.numel(),
            dtype=torch.float64,
            device=self.device,
        ) / float(sample_rate)
        return (
            times.detach().cpu().numpy(),
            output.detach().cpu().numpy(),
        )


def validate_fft_contract(backend) -> dict:
    """Compare backend STFT/ISTFT to SciPy before accelerated use."""
    evidence = backend.evidence
    cache_key = (
        evidence.get("library"),
        evidence.get("library_version"),
        evidence.get("device"),
        evidence.get("cuda_runtime"),
        evidence.get("device_name"),
    )
    if cache_key in _FFT_CONTRACT_CACHE:
        result = dict(_FFT_CONTRACT_CACHE[cache_key])
        if hasattr(backend, "correctness_gate"):
            backend.correctness_gate = result
        return result
    samples = (
        np.sin(np.arange(2053, dtype=np.float64) * 0.173)
        + 0.25 * np.cos(np.arange(2053, dtype=np.float64) * 0.071)
    )
    reference = ScipyCpuBackend()
    _, _, reference_spectrum = reference.stft(
        samples,
        48000,
        nperseg=512,
        noverlap=256,
    )
    _, _, backend_spectrum = backend.stft(
        samples,
        48000,
        nperseg=512,
        noverlap=256,
    )
    _, reference_output = reference.istft(
        reference_spectrum,
        48000,
        nperseg=512,
        noverlap=256,
    )
    _, backend_output = backend.istft(
        backend_spectrum,
        48000,
        nperseg=512,
        noverlap=256,
    )
    spectrum_max_error = float(np.max(np.abs(
        backend_spectrum - reference_spectrum
    )))
    output_max_error = float(np.max(np.abs(
        backend_output - reference_output
    )))
    tolerance = 1e-9
    if spectrum_max_error > tolerance or output_max_error > tolerance:
        raise ComputeBackendError(
            "FFT backend correctness gate failed: "
            f"spectrum={spectrum_max_error:.3g}, "
            f"output={output_max_error:.3g}, tolerance={tolerance:.3g}"
        )
    result = {
        "status": "pass",
        "reference": "scipy-cpu",
        "spectrum_max_abs_error": spectrum_max_error,
        "output_max_abs_error": output_max_error,
        "tolerance": tolerance,
    }
    if hasattr(backend, "correctness_gate"):
        backend.correctness_gate = result
    _FFT_CONTRACT_CACHE[cache_key] = dict(result)
    return result


def resolve_compute_backend(requested=None):
    if hasattr(requested, "stft") and hasattr(requested, "istft"):
        return requested
    name = str(
        requested
        if requested is not None
        else os.environ.get(COMPUTE_BACKEND_ENV, "cpu")
    ).strip().lower()
    if name not in COMPUTE_BACKEND_CHOICES:
        raise ComputeBackendError(
            f"unknown compute backend {name!r}; choose cpu, auto, or cuda"
        )
    if name == "cpu":
        return ScipyCpuBackend()
    if name == "cuda":
        backend = TorchFftBackend("cuda", requested=name)
        validate_fft_contract(backend)
        return backend
    try:
        backend = TorchFftBackend("cuda", requested=name)
        validate_fft_contract(backend)
        return backend
    except ComputeBackendError as exc:
        return ScipyCpuBackend(
            requested=name,
            fallback_reason=str(exc),
        )
