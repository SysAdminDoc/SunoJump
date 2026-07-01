"""Optional local verifier adapter interface for SunoJump.

Defines a stable protocol and result schema for fingerprint verifiers.
The built-in constellation scorer wraps the existing hash-overlap heuristic.
External adapters (Dejavu, Chromaprint, Panako, etc.) can be added without
hard dependency failures.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field


@dataclass
class VerifierResult:
    adapter: str
    available: bool
    before_score: float | None = None
    after_score: float | None = None
    delta: float | None = None
    offset_samples: int | None = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "adapter": self.adapter,
            "available": self.available,
        }
        if self.before_score is not None:
            d["before_score"] = round(self.before_score, 2)
        if self.after_score is not None:
            d["after_score"] = round(self.after_score, 2)
        if self.delta is not None:
            d["delta"] = round(self.delta, 2)
        if self.offset_samples is not None:
            d["offset_samples"] = self.offset_samples
        if self.error:
            d["error"] = self.error
        if self.metadata:
            d["metadata"] = self.metadata
        return d


class VerifierAdapter:
    name: str = "base"

    def is_available(self) -> bool:
        return False

    def score(self, original, processed, sr: int) -> VerifierResult:
        return VerifierResult(adapter=self.name, available=False,
                              error="not implemented")


class ConstellationVerifier(VerifierAdapter):
    name = "constellation"

    def __init__(self, processor=None):
        self._processor = processor

    def is_available(self) -> bool:
        return self._processor is not None

    def score(self, original, processed, sr: int) -> VerifierResult:
        if not self.is_available():
            return VerifierResult(adapter=self.name, available=False,
                                  error="no AudioProcessor instance")
        try:
            n = min(len(original), len(processed))
            match_pct = self._processor._compute_constellation_match(
                original[:n], processed[:n], sr,
            )
            return VerifierResult(
                adapter=self.name,
                available=True,
                before_score=100.0,
                after_score=match_pct,
                delta=100.0 - match_pct,
            )
        except Exception as e:
            return VerifierResult(adapter=self.name, available=True,
                                  error=str(e))


EXTERNAL_ADAPTERS = {
    "chromaprint": "verifiers_chromaprint",
    "dejavu": "verifiers_dejavu",
    "panako": "verifiers_panako",
}


def discover_adapters(processor=None) -> list[VerifierAdapter]:
    adapters: list[VerifierAdapter] = [ConstellationVerifier(processor)]
    for name, module_name in EXTERNAL_ADAPTERS.items():
        try:
            mod = importlib.import_module(module_name)
            adapter_cls = getattr(mod, "Adapter", None)
            if adapter_cls and callable(adapter_cls):
                adapters.append(adapter_cls())
        except ImportError:
            pass
    return adapters


def run_all(adapters: list[VerifierAdapter], original, processed,
            sr: int) -> list[VerifierResult]:
    results = []
    for adapter in adapters:
        if adapter.is_available():
            results.append(adapter.score(original, processed, sr))
        else:
            results.append(VerifierResult(
                adapter=adapter.name, available=False,
                error=f"{adapter.name} not available",
            ))
    return results
