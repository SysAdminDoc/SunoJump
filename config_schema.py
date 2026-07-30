"""Typed validation for render, preset, session, and CLI configuration."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

from c2pa_provenance import C2PA_POLICIES


CONFIG_SCHEMA_VERSION = 1
OUTPUT_FORMATS = frozenset({"wav", "flac", "ogg", "mp3", "m4a"})


class ConfigurationError(ValueError):
    """Configuration data is structurally invalid or unsafe to apply."""


@dataclass(frozen=True, slots=True)
class NumberField:
    key: str
    enabled_key: str
    minimum: float
    maximum: float
    default: float
    default_enabled: bool


NUMBER_FIELDS = (
    NumberField(
        "spectral_strength",
        "spectral_enabled",
        0.0,
        1.0,
        0.30,
        True,
    ),
    NumberField(
        "spectral_sub_bass_strength",
        "spectral_sub_bass_enabled",
        0.0,
        1.0,
        0.30,
        True,
    ),
    NumberField(
        "spectral_low_mids_strength",
        "spectral_low_mids_enabled",
        0.0,
        1.0,
        0.30,
        True,
    ),
    NumberField(
        "spectral_presence_strength",
        "spectral_presence_enabled",
        0.0,
        1.0,
        0.30,
        True,
    ),
    NumberField(
        "spectral_air_strength",
        "spectral_air_enabled",
        0.0,
        1.0,
        0.30,
        True,
    ),
    NumberField(
        "dynamic_eq_amount",
        "dynamic_eq_enabled",
        0.0,
        1.0,
        0.20,
        True,
    ),
    NumberField(
        "pitch_range",
        "pitch_enabled",
        0.0,
        5.0,
        0.80,
        True,
    ),
    NumberField(
        "tempo_range",
        "tempo_enabled",
        0.0,
        0.15,
        0.05,
        True,
    ),
    NumberField(
        "phase_amount",
        "phase_enabled",
        0.0,
        1.0,
        0.30,
        True,
    ),
    NumberField(
        "stereo_shift",
        "stereo_enabled",
        0.0,
        0.5,
        0.10,
        True,
    ),
    NumberField(
        "noise_level",
        "noise_enabled",
        -70.0,
        -30.0,
        -50.0,
        True,
    ),
    NumberField(
        "dynamics_amount",
        "dynamics_enabled",
        0.0,
        1.0,
        0.20,
        True,
    ),
    NumberField(
        "humanize_amount",
        "humanize_enabled",
        0.0,
        1.0,
        0.30,
        True,
    ),
    NumberField(
        "reencode_bitrate",
        "reencode_enabled",
        96.0,
        320.0,
        192.0,
        False,
    ),
)
NUMBER_FIELDS_BY_KEY = {field.key: field for field in NUMBER_FIELDS}
BOOLEAN_DEFAULTS = {
    "strip_metadata": True,
    "spectral_scan_enabled": True,
    **{
        field.enabled_key: field.default_enabled
        for field in NUMBER_FIELDS
    },
}
REQUIRED_CONFIG_KEYS = frozenset(
    {*BOOLEAN_DEFAULTS, *NUMBER_FIELDS_BY_KEY}
)


def default_render_config() -> dict[str, bool | float]:
    """Return the complete schema defaults used for legacy partial presets."""
    return {
        **BOOLEAN_DEFAULTS,
        **{
            field.key: field.default
            for field in NUMBER_FIELDS
        },
    }


def _validate_mapping(
    raw: Mapping[str, object],
    *,
    allow_output_format: bool,
) -> dict[str, bool | float | str]:
    known = set(REQUIRED_CONFIG_KEYS)
    known.add("c2pa_policy")
    if allow_output_format:
        known.add("output_format")
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigurationError(
            f"unknown configuration key(s): {', '.join(unknown)}"
        )

    validated: dict[str, bool | float | str] = {}
    for key, value in raw.items():
        if key in BOOLEAN_DEFAULTS:
            if type(value) is not bool:
                raise ConfigurationError(
                    f"{key} must be true or false, not "
                    f"{type(value).__name__}"
                )
            validated[key] = value
            continue
        if key in NUMBER_FIELDS_BY_KEY:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigurationError(
                    f"{key} must be a finite number, not "
                    f"{type(value).__name__}"
                )
            number = float(value)
            field = NUMBER_FIELDS_BY_KEY[key]
            if not math.isfinite(number):
                raise ConfigurationError(f"{key} must be finite")
            if number < field.minimum or number > field.maximum:
                raise ConfigurationError(
                    f"{key} must be between {field.minimum:g} and "
                    f"{field.maximum:g}; found {number:g}"
                )
            validated[key] = number
            continue
        if key == "output_format":
            if not isinstance(value, str):
                raise ConfigurationError("output_format must be a string")
            output_format = value.strip().lower()
            if output_format not in OUTPUT_FORMATS:
                raise ConfigurationError(
                    f"unsupported output_format: {value!r}"
                )
            validated[key] = output_format
            continue
        if key == "c2pa_policy":
            if not isinstance(value, str):
                raise ConfigurationError("c2pa_policy must be a string")
            policy = value.strip().lower()
            if policy not in C2PA_POLICIES:
                raise ConfigurationError(
                    f"unsupported c2pa_policy: {value!r}"
                )
            validated[key] = policy
    return validated


def validate_render_config(
    raw: Mapping[str, object],
    *,
    base: Mapping[str, object] | None = None,
    require_complete: bool = False,
    allow_output_format: bool = True,
) -> dict[str, bool | float | str]:
    """Validate and normalize render configuration without coercing types."""
    if not isinstance(raw, Mapping):
        raise ConfigurationError(
            f"configuration must be an object, not {type(raw).__name__}"
        )
    validated: dict[str, bool | float | str] = {}
    if base is not None:
        if not isinstance(base, Mapping):
            raise ConfigurationError("base configuration must be an object")
        validated.update(
            _validate_mapping(
                base,
                allow_output_format=allow_output_format,
            )
        )
    validated.update(
        _validate_mapping(
            raw,
            allow_output_format=allow_output_format,
        )
    )
    if require_complete:
        missing = sorted(REQUIRED_CONFIG_KEYS - set(validated))
        if missing:
            raise ConfigurationError(
                f"missing configuration key(s): {', '.join(missing)}"
            )
    return validated
