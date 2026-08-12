#!/usr/bin/env python3
"""SunoJump v1.6.1 - local audio variation and evidence tool."""

import multiprocessing
multiprocessing.freeze_support()

import sys
if sys.version_info < (3, 11):
    sys.stderr.write(
        f"SunoJump requires Python 3.11 or later (found {sys.version}).\n"
    )
    sys.exit(1)

if '--safe-decode-worker' in sys.argv:
    from safe_audio import worker_cli_main
    raise SystemExit(worker_cli_main(sys.argv[2:]))

# --- Imports ---
import os, json, argparse, copy, tempfile, shutil, threading, hashlib, time
import re, zipfile
import secrets, uuid
import platform, traceback
import subprocess
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
from config_schema import (
    CONFIG_SCHEMA_VERSION,
    NUMBER_FIELDS,
    NUMBER_FIELDS_BY_KEY,
    ConfigurationError,
    default_render_config,
    validate_render_config,
)
from audio_reports import (
    measure_loudness_comparison,
    render_spectrogram_comparison_png,
)
from batch_manifest import (
    BATCH_MANIFEST_SUFFIX,
    RETRY_POLICIES,
    BatchManifestError,
    BatchManifestStore,
    default_manifest_path,
)
from c2pa_provenance import (
    C2PA_POLICIES,
    C2PA_POLICY_ALLOW_REMOVAL,
    C2PA_POLICY_BLOCK,
    inspect_c2pa,
)
from safe_audio import (
    DECODE_CHUNK_FRAMES,
    DecodeCancelled,
    DecodeLimits,
    MIN_LIBSNDFILE_VERSION,
    decode_audio_isolated,
    decode_audio_isolated_to_path,
    inspect_audio_path,
    validate_libsndfile_version,
)
from render_results import (
    RESULT_SCHEMA_VERSION,
    BatchResult,
    OutputValidation,
    RenderErrorCode,
    RenderResult,
    RenderState,
    format_batch_result,
    format_render_result,
)
from verifiers import ConstellationVerifier, format_verifier_result

VERSION = "1.6.1"
APP_NAME = "SunoJump"
PRESET_SCHEMA_VERSION = CONFIG_SCHEMA_VERSION
PROFILE_SCHEMA_VERSION = 1
CLI_RESULTS_SCHEMA_ID = "com.sunojump.cli-results"
SIDECAR_SCHEMA_VERSION = 4
SIDECAR_SCHEMA_ID = "com.sunojump.replay-evidence"
SIDECAR_AUDIO_TAG = "SUNOJUMP_SIDECAR_PAYLOAD_SHA256"
SIGNAL_CHANGE_METRIC = {
    "adapter": "sunojump.signal_change",
    "version": "1",
    "unit": "percent",
    "scope": "sample-domain SNR-derived difference",
}
RIGHTS_ONLY_NOTICE = (
    "Use only with audio you own or are authorized to modify."
)
EVIDENCE_NOTICE = (
    "Local experimental metrics describe this render only; they do not "
    "predict or guarantee any platform, recognition, or detector outcome."
)
EVIDENCE_CONTRACT = {
    "rights_scope": "owned-or-authorized-audio-only",
    "metric_scope": "local-render-evidence-only",
    "platform_outcome_guaranteed": False,
    "upload_or_resubmission_automation": False,
}

try:
    import numpy as np
    import soundfile as sf
    import scipy
    from scipy import signal
    import mutagen
    from mutagen import File as MutagenFile
    from mutagen.id3 import TXXX
except ImportError as e:
    missing = getattr(e, 'name', None) or str(e)
    print(f"ERROR: Missing required Python dependency: {missing}", file=sys.stderr)
    print("Install dependencies with:  python -m pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

from compute_backend import (
    COMPUTE_BACKEND_CHOICES,
    ComputeBackendError,
    resolve_compute_backend,
)

try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QBoxLayout, QGridLayout,
        QPushButton, QLabel, QListWidget, QListWidgetItem,
        QComboBox, QLineEdit, QCheckBox, QSlider, QProgressBar,
        QTextEdit, QFileDialog, QAbstractItemView, QFrame, QSizePolicy,
        QStyle, QScrollArea, QSpinBox, QDoubleSpinBox,
        QMessageBox,
    )
    from PyQt6.QtCore import (
        PYQT_VERSION_STR,
        QT_VERSION_STR,
        QSettings,
        QSize,
        QThread,
        QUrl,
        Qt,
        qVersion,
        pyqtSignal,
    )
    from PyQt6.QtGui import (
        QDesktopServices,
        QDragEnterEvent,
        QDropEvent,
        QFont,
        QKeySequence,
    )
except ImportError as e:
    missing = getattr(e, 'name', None) or str(e)
    print(f"ERROR: Missing required GUI dependency: {missing}", file=sys.stderr)
    print("Install dependencies with:  python -m pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

from localization import (
    active_layout_direction,
    configure_locale,
    requested_locale_from_argv,
    tr as _tr,
    without_locale_args,
)

# Optional multimedia: only used for preview playback. Some Linux
# distros ship PyQt6 without the Multimedia module (separate package).
try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    _MULTIMEDIA_OK = True
except ImportError:
    QMediaPlayer = None
    QAudioOutput = None
    _MULTIMEDIA_OK = False

# --- Constants ---
C = {
    'base': '#0f1110', 'mantle': '#151716', 'crust': '#090a0a',
    'panel': '#191b1a', 'panel_alt': '#202321',
    'surface0': '#242825', 'surface1': '#343933', 'surface2': '#4a5149',
    'track': '#2a2e2b',
    'text': '#f4efe6', 'subtext': '#b9b2a6', 'overlay': '#82877f',
    'blue': '#75b7ff', 'green': '#7bd88f', 'red': '#ff6b6b',
    'yellow': '#e8c468', 'mauve': '#c9a4ff', 'peach': '#d8a24a',
    'teal': '#55d6be', 'lavender': '#d9d0c1',
    'accent': '#d8a24a', 'accent_soft': '#f2d08a',
    'stroke': '#2d322e',
}

SUPPORTED_FORMATS = {'.wav', '.mp3', '.flac', '.ogg', '.aiff', '.aif', '.opus'}
OUTPUT_EXTENSIONS = {
    'wav': '.wav',
    'flac': '.flac',
    'ogg': '.ogg',
    'mp3': '.mp3',
    'm4a': '.m4a',
}
FFMPEG_EXPORT_FORMATS = {'mp3', 'm4a'}

# Decode guardrails. SunoJump still processes audio in memory, so validate
# untrusted inputs before libsndfile decodes them into large float64 arrays.
MAX_INPUT_FILE_BYTES = 2 * 1024 ** 3
MAX_DECODED_AUDIO_BYTES = 1024 ** 3
MAX_AUDIO_CHANNELS = 8
MAX_AUDIO_SAMPLE_RATE = 384000
MAX_AUDIO_DURATION_SECONDS = 2 * 60 * 60
MAX_DECODE_WORKER_MEMORY_BYTES = MAX_DECODED_AUDIO_BYTES + 512 * 1024 ** 2
DECODE_TIMEOUT_SECONDS = 120.0
STREAMING_THRESHOLD_BYTES = 256 * 1024 ** 2
STREAMING_WORKING_SET_BYTES = 64 * 1024 ** 2
STREAMING_CHUNK_SECONDS = 20.0
STREAMING_OVERLAP_SECONDS = 1.0
DEFAULT_PARALLEL_FILE_WORKERS = 2
MAX_PARALLEL_FILE_WORKERS = 8
COMPARE_HISTORY_SCHEMA_VERSION = 1
MAX_COMPARE_HISTORY_ENTRIES = 200
COMPARE_HISTORY_SETTINGS_KEY = "compare/history_json"
WATCH_POLL_SECONDS = 0.5
WATCH_STABILITY_SECONDS = 1.0

# UserRole keys on QListWidgetItem
ROLE_INPUT = Qt.ItemDataRole.UserRole
ROLE_OUTPUT = Qt.ItemDataRole.UserRole + 1
ROLE_JOB_ID = Qt.ItemDataRole.UserRole + 2
ROLE_RESULT = Qt.ItemDataRole.UserRole + 3
ROLE_COMPARE_WINNER = Qt.ItemDataRole.UserRole + 4
ROLE_PREVIEW_OFFSET = Qt.ItemDataRole.UserRole + 5
ROLE_FILE_PRESET = Qt.ItemDataRole.UserRole + 6
QUEUE_PRESET_INHERIT = "Batch settings"
QUEUE_PRESET_CURRENT = "Current settings"

PRESETS = {
    'Gentle': {
        'strip_metadata': True,
        'spectral_scan_enabled': True,
        'spectral_enabled': True, 'spectral_strength': 0.10,
        'spectral_sub_bass_enabled': True, 'spectral_sub_bass_strength': 0.10,
        'spectral_low_mids_enabled': True, 'spectral_low_mids_strength': 0.10,
        'spectral_presence_enabled': True, 'spectral_presence_strength': 0.10,
        'spectral_air_enabled': True, 'spectral_air_strength': 0.10,
        'dynamic_eq_enabled': True, 'dynamic_eq_amount': 0.10,
        'pitch_enabled': True, 'pitch_range': 0.30,
        'tempo_enabled': True, 'tempo_range': 0.02,
        'phase_enabled': True, 'phase_amount': 0.10,
        'stereo_enabled': False, 'stereo_shift': 0.05,
        'noise_enabled': True, 'noise_level': -60.0,
        'dynamics_enabled': False, 'dynamics_amount': 0.10,
        'humanize_enabled': True, 'humanize_amount': 0.10,
        'reencode_enabled': False, 'reencode_bitrate': 256,
    },
    'Moderate': {
        'strip_metadata': True,
        'spectral_scan_enabled': True,
        'spectral_enabled': True, 'spectral_strength': 0.30,
        'spectral_sub_bass_enabled': True, 'spectral_sub_bass_strength': 0.30,
        'spectral_low_mids_enabled': True, 'spectral_low_mids_strength': 0.30,
        'spectral_presence_enabled': True, 'spectral_presence_strength': 0.30,
        'spectral_air_enabled': True, 'spectral_air_strength': 0.30,
        'dynamic_eq_enabled': True, 'dynamic_eq_amount': 0.20,
        'pitch_enabled': True, 'pitch_range': 0.80,
        'tempo_enabled': True, 'tempo_range': 0.05,
        'phase_enabled': True, 'phase_amount': 0.30,
        'stereo_enabled': True, 'stereo_shift': 0.10,
        'noise_enabled': True, 'noise_level': -50.0,
        'dynamics_enabled': True, 'dynamics_amount': 0.20,
        'humanize_enabled': True, 'humanize_amount': 0.30,
        'reencode_enabled': False, 'reencode_bitrate': 192,
    },
    'Aggressive': {
        'strip_metadata': True,
        'spectral_scan_enabled': True,
        'spectral_enabled': True, 'spectral_strength': 0.50,
        'spectral_sub_bass_enabled': True, 'spectral_sub_bass_strength': 0.50,
        'spectral_low_mids_enabled': True, 'spectral_low_mids_strength': 0.50,
        'spectral_presence_enabled': True, 'spectral_presence_strength': 0.50,
        'spectral_air_enabled': True, 'spectral_air_strength': 0.50,
        'dynamic_eq_enabled': True, 'dynamic_eq_amount': 0.30,
        'pitch_enabled': True, 'pitch_range': 1.50,
        'tempo_enabled': True, 'tempo_range': 0.08,
        'phase_enabled': True, 'phase_amount': 0.50,
        'stereo_enabled': True, 'stereo_shift': 0.20,
        'noise_enabled': True, 'noise_level': -45.0,
        'dynamics_enabled': True, 'dynamics_amount': 0.30,
        'humanize_enabled': True, 'humanize_amount': 0.50,
        'reencode_enabled': True, 'reencode_bitrate': 192,
    },
    'Extreme': {
        'strip_metadata': True,
        'spectral_scan_enabled': True,
        'spectral_enabled': True, 'spectral_strength': 0.70,
        'spectral_sub_bass_enabled': True, 'spectral_sub_bass_strength': 0.70,
        'spectral_low_mids_enabled': True, 'spectral_low_mids_strength': 0.70,
        'spectral_presence_enabled': True, 'spectral_presence_strength': 0.70,
        'spectral_air_enabled': True, 'spectral_air_strength': 0.70,
        'dynamic_eq_enabled': True, 'dynamic_eq_amount': 0.40,
        'pitch_enabled': True, 'pitch_range': 3.00,
        'tempo_enabled': True, 'tempo_range': 0.12,
        'phase_enabled': True, 'phase_amount': 0.70,
        'stereo_enabled': True, 'stereo_shift': 0.30,
        'noise_enabled': True, 'noise_level': -40.0,
        'dynamics_enabled': True, 'dynamics_amount': 0.50,
        'humanize_enabled': True, 'humanize_amount': 0.70,
        'reencode_enabled': True, 'reencode_bitrate': 128,
    },
}
PRESETS = {
    name: validate_render_config(
        params,
        require_complete=True,
        allow_output_format=False,
    )
    for name, params in PRESETS.items()
}
_CONFIG_DEFAULTS = default_render_config()
if PRESETS["Moderate"] != _CONFIG_DEFAULTS:
    raise RuntimeError("Moderate preset must match configuration defaults")

_PRESET_MIGRATIONS = {}
_PRESET_DOCUMENT_KEYS = {
    "name",
    "version",
    "schema_version",
    "params",
}
_PROFILE_DOCUMENT_KEYS = {
    "name",
    "schema_version",
    "preset",
    "overrides",
}


def _migrate_preset(data):
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"preset must be an object, not {type(data).__name__}"
        )
    data = copy.deepcopy(data)
    schema = data.get('schema_version', 0)
    if type(schema) is not int or schema < 0:
        raise ConfigurationError(
            "schema_version must be a non-negative integer"
        )
    params = data.get('params')
    if params is not None and not isinstance(params, dict):
        raise ConfigurationError("preset params must be an object")
    if isinstance(params, dict):
        if (
            'watermark_scan_enabled' in params
            and 'spectral_scan_enabled' not in params
        ):
            params['spectral_scan_enabled'] = params.pop(
                'watermark_scan_enabled'
            )
    elif (
        'watermark_scan_enabled' in data
        and 'spectral_scan_enabled' not in data
    ):
        data['spectral_scan_enabled'] = data.pop(
            'watermark_scan_enabled'
        )
    if schema > PRESET_SCHEMA_VERSION:
        raise ConfigurationError(
            f"Preset requires schema version {schema} but this SunoJump "
            f"(v{VERSION}) supports up to version {PRESET_SCHEMA_VERSION}. "
            f"Update SunoJump to load this preset."
        )
    while schema < PRESET_SCHEMA_VERSION:
        migrator = _PRESET_MIGRATIONS.get(schema)
        if migrator is None:
            break
        data = migrator(data)
        schema = data.get('schema_version', schema + 1)
    data['schema_version'] = PRESET_SCHEMA_VERSION
    return data


def _validate_preset_document(data):
    migrated = _migrate_preset(data)
    if "params" in migrated:
        unknown_document_keys = sorted(
            set(migrated) - _PRESET_DOCUMENT_KEYS
        )
        if unknown_document_keys:
            raise ConfigurationError(
                "unknown preset document key(s): "
                + ", ".join(unknown_document_keys)
            )
        raw_params = migrated["params"]
    else:
        raw_params = {
            key: value
            for key, value in migrated.items()
            if key not in {"name", "version", "schema_version"}
        }
        if not raw_params:
            raise ConfigurationError("preset file is missing params")

    name = migrated.get("name", "Custom")
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationError("preset name must be a non-empty string")
    source_version = migrated.get("version")
    if source_version is not None and not isinstance(source_version, str):
        raise ConfigurationError("preset version must be a string")
    params = validate_render_config(
        raw_params,
        base=_CONFIG_DEFAULTS,
        require_complete=True,
        allow_output_format=False,
    )
    return {
        "name": name.strip(),
        **({"version": source_version} if source_version is not None else {}),
        "schema_version": PRESET_SCHEMA_VERSION,
        "params": params,
    }


def _create_preset_document(name, params):
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationError("preset name must be a non-empty string")
    validated = validate_render_config(
        params,
        require_complete=True,
        allow_output_format=False,
    )
    return {
        "name": name.strip(),
        "version": VERSION,
        "schema_version": PRESET_SCHEMA_VERSION,
        "params": validated,
    }


def _validate_profile_document(data):
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"profile must be an object, not {type(data).__name__}"
        )
    unknown_keys = sorted(set(data) - _PROFILE_DOCUMENT_KEYS)
    if unknown_keys:
        raise ConfigurationError(
            "unknown profile document key(s): "
            + ", ".join(unknown_keys)
        )
    schema_version = data.get("schema_version")
    if type(schema_version) is not int or schema_version < 0:
        raise ConfigurationError(
            "profile schema_version must be a non-negative integer"
        )
    if schema_version > PROFILE_SCHEMA_VERSION:
        raise ConfigurationError(
            f"Profile requires schema version {schema_version} but this "
            f"SunoJump (v{VERSION}) supports up to version "
            f"{PROFILE_SCHEMA_VERSION}. Update SunoJump to load this profile."
        )
    if schema_version != PROFILE_SCHEMA_VERSION:
        raise ConfigurationError(
            f"unsupported profile schema version {schema_version}"
        )
    raw_preset = data.get("preset")
    if not isinstance(raw_preset, str) or not raw_preset.strip():
        raise ConfigurationError(
            "profile preset must name a built-in preset"
        )
    preset_name = raw_preset.strip().capitalize()
    if preset_name not in PRESETS:
        raise ConfigurationError(
            "profile preset must be one of: "
            + ", ".join(name.lower() for name in PRESETS)
        )
    overrides = data.get("overrides")
    if not isinstance(overrides, dict):
        raise ConfigurationError("profile overrides must be an object")
    operation_keys = sorted(
        set(overrides) & {"output_format", "c2pa_policy"}
    )
    if operation_keys:
        raise ConfigurationError(
            "profile cannot set operation-level key(s): "
            + ", ".join(operation_keys)
        )
    name = data.get("name", f"{preset_name} profile")
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationError("profile name must be a non-empty string")
    params = validate_render_config(
        overrides,
        base=PRESETS[preset_name],
        require_complete=True,
        allow_output_format=False,
    )
    return {
        "name": name.strip(),
        "schema_version": PROFILE_SCHEMA_VERSION,
        "preset": preset_name,
        "overrides": copy.deepcopy(overrides),
        "params": params,
    }


_PARAM_UI = {
    "spectral_strength": ("Spectral Perturbation", "", 2, 1.0),
    "spectral_sub_bass_strength": ("Sub-Bass Spectral", "", 2, 1.0),
    "spectral_low_mids_strength": ("Low-Mids Spectral", "", 2, 1.0),
    "spectral_presence_strength": ("Presence Spectral", "", 2, 1.0),
    "spectral_air_strength": ("Air Spectral", "", 2, 1.0),
    "dynamic_eq_amount": ("Dynamic EQ", "", 2, 1.0),
    "pitch_range": ("Pitch Micro-Shift", " st", 1, 1.0),
    "tempo_range": ("Tempo Micro-Variation", "%", 1, 100.0),
    "phase_amount": ("Phase Scrambling", "", 2, 1.0),
    "stereo_shift": ("Stereo Manipulation", "", 2, 1.0),
    "noise_level": ("Noise Injection", " dB", 0, 1.0),
    "dynamics_amount": ("Dynamics Modification", "", 2, 1.0),
    "humanize_amount": ("Humanization", "", 2, 1.0),
    "reencode_bitrate": ("Lossy Re-encode", " kbps", 0, 1.0),
}
PARAM_DEFS = [
    (
        field.key,
        _PARAM_UI[field.key][0],
        field.minimum,
        field.maximum,
        field.default,
        _PARAM_UI[field.key][1],
        _PARAM_UI[field.key][2],
        field.enabled_key,
        _PARAM_UI[field.key][3],
    )
    for field in NUMBER_FIELDS
]

DEFAULT_OUTPUT = str(Path.home() / 'Desktop' / 'SunoJump_Output')
PREVIEW_DURATION_SEC = 30.0  # length of preview clip generated by "Render Preview"
COMPARE_DURATION_SEC = 20.0  # length of each preset sample in Compare Presets mode

SPECTRAL_BANDS = (
    ('spectral_sub_bass', 20.0, 80.0, 0.30),
    ('spectral_low_mids', 120.0, 500.0, 0.18),
    ('spectral_presence', 2500.0, 6000.0, 0.24),
    ('spectral_air', 10000.0, None, 0.40),
)
SPECTRAL_SCAN_MAX_CANDIDATES = 5
DYNAMIC_EQ_BANDS = (
    (60.0, 180.0, 1.2),
    (180.0, 700.0, 1.0),
    (700.0, 2500.0, 0.9),
    (2500.0, 6500.0, 1.1),
    (6500.0, None, 1.3),
)

# --- Stylesheet ---
STYLE = f"""
QMainWindow {{
    background-color: {C['base']};
}}
QWidget {{
    color: {C['text']};
    font-family: 'Segoe UI', 'Arial', sans-serif;
}}
QWidget#appRoot {{
    background-color: {C['base']};
}}
QFrame#topBar {{
    background-color: {C['crust']};
    border: 1px solid {C['stroke']};
    border-radius: 8px;
}}
QFrame#panel {{
    background-color: {C['panel']};
    border: 1px solid {C['stroke']};
    border-radius: 8px;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QLabel {{
    background: transparent;
}}
QLabel#appTitle {{
    color: {C['text']};
    font-weight: bold;
}}
QLabel#appSubtitle {{
    color: {C['subtext']};
}}
QLabel#sectionTitle {{
    color: {C['text']};
    font-weight: bold;
}}
QLabel#sectionSubtitle {{
    color: {C['overlay']};
}}
QLabel#statusPill, QLabel#accentPill {{
    background-color: {C['surface0']};
    border: 1px solid {C['surface1']};
    border-radius: 8px;
    padding: 5px 10px;
    color: {C['subtext']};
    font-weight: bold;
}}
QLabel#accentPill {{
    background-color: {C['accent']};
    border-color: {C['accent']};
    color: {C['crust']};
}}
QLabel#countLabel {{
    color: {C['accent_soft']};
    font-weight: bold;
}}
QLabel#hintLabel {{
    color: {C['overlay']};
}}
QLabel#nowPlaying {{
    color: {C['subtext']};
    font-weight: 600;
}}
QGroupBox {{
    border: 1px solid {C['surface1']};
    border-radius: 8px;
    margin-top: 16px;
    padding-top: 20px;
    font-weight: bold;
    color: {C['lavender']};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}}
QPushButton {{
    background-color: {C['surface0']};
    border: 1px solid {C['surface1']};
    border-radius: 7px;
    padding: 7px 14px;
    color: {C['text']};
    font-weight: bold;
}}
QPushButton:hover {{
    background-color: {C['panel_alt']};
    border-color: {C['accent']};
}}
QPushButton:pressed {{
    background-color: {C['surface1']};
}}
QPushButton:focus {{
    border: 2px solid {C['accent_soft']};
}}
QPushButton:disabled {{
    background-color: {C['mantle']};
    color: {C['overlay']};
    border-color: {C['surface0']};
}}
QPushButton#processBtn {{
    background-color: {C['accent']};
    border-color: {C['accent']};
    color: {C['crust']};
    padding: 10px 24px;
}}
QPushButton#processBtn:hover {{
    background-color: {C['accent_soft']};
    border-color: {C['accent_soft']};
}}
QPushButton#processBtn:disabled {{
    background-color: {C['surface1']};
    color: {C['overlay']};
}}
QPushButton#cancelBtn {{
    background-color: rgba(255, 107, 107, 0.14);
    border-color: rgba(255, 107, 107, 0.45);
    color: {C['red']};
}}
QPushButton#iconButton {{
    padding: 7px 9px;
}}
QPushButton#compareButton {{
    padding: 6px 10px;
}}
QListWidget {{
    background-color: {C['crust']};
    border: 1px solid {C['surface1']};
    border-radius: 8px;
    padding: 6px;
    color: {C['text']};
}}
QListWidget::item {{
    padding: 8px 10px;
    border-radius: 6px;
}}
QListWidget::item:selected {{
    background-color: {C['surface0']};
    color: {C['text']};
}}
QListWidget::item:hover {{
    background-color: {C['mantle']};
}}
QListWidget:focus {{
    border: 2px solid {C['accent_soft']};
}}
QComboBox, QSpinBox {{
    background-color: {C['surface0']};
    border: 1px solid {C['surface1']};
    border-radius: 7px;
    padding: 7px 10px;
    color: {C['text']};
    min-width: 100px;
}}
QComboBox::drop-down {{
    border: none;
    padding-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {C['surface0']};
    border: 1px solid {C['surface1']};
    color: {C['text']};
    selection-background-color: {C['surface1']};
}}
QComboBox:focus, QSpinBox:focus {{
    border: 2px solid {C['accent_soft']};
}}
QLineEdit {{
    background-color: {C['crust']};
    border: 1px solid {C['surface1']};
    border-radius: 7px;
    padding: 7px 10px;
    color: {C['text']};
}}
QLineEdit:focus {{
    border: 2px solid {C['accent_soft']};
}}
QCheckBox {{
    color: {C['text']};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 5px;
    border: 1px solid {C['surface2']};
    background-color: {C['surface0']};
}}
QCheckBox::indicator:checked {{
    background-color: {C['accent']};
    border-color: {C['accent']};
}}
QCheckBox:focus {{
    color: {C['accent_soft']};
    background-color: {C['mantle']};
    border: 1px solid {C['accent_soft']};
    border-radius: 5px;
}}
QWidget#paramRow {{
    background-color: {C['surface0']};
    border: 1px solid transparent;
    border-radius: 8px;
}}
QWidget#paramRow:hover {{
    border-color: {C['surface1']};
}}
QLabel#paramName {{
    color: {C['text']};
    font-weight: bold;
}}
QLabel#paramValue {{
    color: {C['accent_soft']};
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-weight: bold;
}}
QSlider::groove:horizontal {{
    background: {C['track']};
    height: 6px;
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {C['accent_soft']};
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
}}
QSlider::sub-page:horizontal {{
    background: {C['accent']};
    border-radius: 3px;
}}
QSlider:focus {{
    background-color: {C['mantle']};
    border: 1px solid {C['accent_soft']};
    border-radius: 5px;
}}
QSlider::groove:horizontal:disabled {{
    background: {C['mantle']};
}}
QSlider::handle:horizontal:disabled {{
    background: {C['surface1']};
}}
QSlider::sub-page:horizontal:disabled {{
    background: {C['surface1']};
}}
QProgressBar {{
    background-color: {C['crust']};
    border: 1px solid {C['surface1']};
    border-radius: 8px;
    text-align: center;
    color: {C['subtext']};
    height: 24px;
    font-weight: bold;
}}
QProgressBar::chunk {{
    background-color: {C['teal']};
    border-radius: 7px;
}}
QTextEdit {{
    background-color: {C['crust']};
    border: 1px solid {C['surface1']};
    border-radius: 8px;
    padding: 8px;
    color: {C['subtext']};
    font-family: 'Cascadia Code', 'Consolas', monospace;
}}
QTextEdit:focus {{
    border: 2px solid {C['accent_soft']};
}}
QScrollBar:vertical {{
    background: {C['crust']};
    width: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical {{
    background: {C['surface1']};
    border-radius: 5px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
"""


# ============================================================
#  Helpers
# ============================================================
def _nperseg_for(length):
    """Compute STFT nperseg: power of 2, max 2048, returns 0 if too short."""
    n = min(2048, length // 4)
    if n < 64:
        return 0
    return 1 << (n.bit_length() - 1)


_ffmpeg_available = None
_ffmpeg_encoders: dict | None = None
_ffmpeg_version = None

def _check_ffmpeg():
    """Check ffmpeg availability once, cache result."""
    global _ffmpeg_available
    if _ffmpeg_available is None:
        try:
            subprocess.run(
                ['ffmpeg', '-version'], capture_output=True, check=True,
            )
            _ffmpeg_available = True
        except (FileNotFoundError, subprocess.CalledProcessError):
            _ffmpeg_available = False
    return _ffmpeg_available


def _ffmpeg_version_line():
    global _ffmpeg_version
    if _ffmpeg_version is not None:
        return _ffmpeg_version
    if not _check_ffmpeg():
        _ffmpeg_version = "unavailable"
        return _ffmpeg_version
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            text=True,
            check=True,
        )
        _ffmpeg_version = (
            (result.stdout or result.stderr or "unknown").splitlines()[0].strip()
            or "unknown"
        )
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        _ffmpeg_version = "unavailable"
    return _ffmpeg_version


FFMPEG_FORMAT_ENCODERS = {
    'mp3': 'libmp3lame',
    'm4a': 'aac',
}


def _probe_ffmpeg_encoders():
    """Probe which audio encoders ffmpeg supports. Cached after first call."""
    global _ffmpeg_encoders
    if _ffmpeg_encoders is not None:
        return _ffmpeg_encoders
    _ffmpeg_encoders = {}
    if not _check_ffmpeg():
        return _ffmpeg_encoders
    try:
        result = subprocess.run(
            ['ffmpeg', '-encoders'], capture_output=True, text=True, check=False,
        )
        lines = (result.stdout or '').splitlines()
        for fmt, encoder in FFMPEG_FORMAT_ENCODERS.items():
            _ffmpeg_encoders[fmt] = any(
                encoder in line for line in lines
            )
    except (FileNotFoundError, OSError):
        pass
    return _ffmpeg_encoders


def _ffmpeg_encoder_available(fmt):
    """Return True if the required encoder for *fmt* is present in ffmpeg."""
    encoders = _probe_ffmpeg_encoders()
    return encoders.get(str(fmt).lower(), False)


def _open_in_file_manager(path):
    """Open a directory in the OS file manager. Cross-platform."""
    if not os.path.isdir(path):
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))


def _open_file(path):
    if not os.path.isfile(path):
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))


def _set_accessibility(widget, name, description):
    name = _tr(name)
    description = _tr(description)
    widget.setAccessibleName(name)
    widget.setAccessibleDescription(description)
    if hasattr(widget, 'setToolTip') and not widget.toolTip():
        widget.setToolTip(description)


def _set_relative_font(widget, point_delta=0.0, *, bold=None):
    """Apply hierarchy without overriding the user's application font size."""
    base = QApplication.font()
    font = QFont(base)
    point_size = base.pointSizeF()
    if point_size > 0:
        font.setPointSizeF(max(1.0, point_size + point_delta))
    elif base.pixelSize() > 0:
        font.setPixelSize(max(1, round(base.pixelSize() + point_delta)))
    if bold is not None:
        font.setBold(bold)
    widget.setFont(font)


def _diagnostics_dir():
    if sys.platform.startswith('win'):
        root = os.environ.get('LOCALAPPDATA')
        if root:
            return Path(root) / APP_NAME / 'logs'
        return Path.home() / 'AppData' / 'Local' / APP_NAME / 'logs'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Logs' / APP_NAME
    root = os.environ.get('XDG_STATE_HOME')
    if root:
        return Path(root) / APP_NAME / 'logs'
    return Path.home() / '.local' / 'state' / APP_NAME / 'logs'


MIN_RETAINED_LOGS = 1
MAX_RETAINED_LOGS = 30
MAX_CONFIGURABLE_RETAINED_LOGS = 365
MAX_SUPPORT_LOG_BYTES = 5 * 1024 * 1024
SUPPORT_BUNDLE_SCHEMA_VERSION = 1
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:[A-Z]:[\\/]|\\\\)[^\"'\r\n]+"
)
_POSIX_ABSOLUTE_PATH = re.compile(
    r"(?<![:A-Za-z0-9_~])/(?!/)[^\"'\r\n]+"
)


def _new_diagnostics_path(prefix='run'):
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    return _diagnostics_dir() / f"{prefix}-{stamp}.log"


def _validated_log_retention(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("diagnostic retention must be an integer")
    if not MIN_RETAINED_LOGS <= value <= MAX_CONFIGURABLE_RETAINED_LOGS:
        raise ValueError(
            "diagnostic retention must be between "
            f"{MIN_RETAINED_LOGS} and {MAX_CONFIGURABLE_RETAINED_LOGS}"
        )
    return value


def _enforce_log_retention(
    max_logs=MAX_RETAINED_LOGS,
    *,
    protected_paths=(),
    log_dir=None,
):
    max_logs = _validated_log_retention(max_logs)
    log_dir = (
        Path(log_dir)
        if log_dir is not None
        else _diagnostics_dir()
    )
    report = {
        "limit": max_logs,
        "deleted": [],
        "failures": [],
        "remaining": [],
    }
    if not log_dir.is_dir():
        return report
    protected = {
        os.path.normcase(os.path.abspath(str(path)))
        for path in protected_paths
    }
    try:
        logs = sorted(
            log_dir.glob('*.log'),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
        )
    except OSError as exc:
        report["failures"].append({
            "name": "<log-directory>",
            "error": str(exc),
        })
        return report
    removable = [
        path
        for path in logs
        if os.path.normcase(os.path.abspath(str(path))) not in protected
    ]
    excess = max(0, len(logs) - max_logs)
    for oldest in removable[:excess]:
        try:
            oldest.unlink()
            report["deleted"].append(oldest.name)
        except OSError as exc:
            report["failures"].append({
                "name": oldest.name,
                "error": str(exc),
            })
    try:
        report["remaining"] = sorted(
            path.name for path in log_dir.glob('*.log')
        )
    except OSError as exc:
        report["failures"].append({
            "name": "<remaining-log-scan>",
            "error": str(exc),
        })
    return report


def _delete_diagnostic_logs(log_dir=None):
    log_dir = Path(log_dir) if log_dir is not None else _diagnostics_dir()
    report = {
        "found": 0,
        "deleted": [],
        "failures": [],
        "remaining": [],
    }
    if not log_dir.is_dir():
        return report
    try:
        logs = sorted(log_dir.glob('*.log'), key=lambda path: path.name)
    except OSError as exc:
        report["failures"].append({
            "name": "<log-directory>",
            "error": str(exc),
        })
        return report
    report["found"] = len(logs)
    for log_path in logs:
        try:
            log_path.unlink()
            report["deleted"].append(log_path.name)
        except OSError as exc:
            report["failures"].append({
                "name": log_path.name,
                "error": str(exc),
            })
    try:
        report["remaining"] = sorted(
            path.name for path in log_dir.glob('*.log')
        )
    except OSError as exc:
        report["failures"].append({
            "name": "<remaining-log-scan>",
            "error": str(exc),
        })
    return report


def _replace_path_literal(text, path, replacement):
    path = str(path)
    if not path:
        return text
    if not sys.platform.startswith('win'):
        return text.replace(path, replacement)
    pattern = re.compile(re.escape(path), re.IGNORECASE)
    return pattern.sub(lambda _match: replacement, text)


def _redact_diagnostic_text(text, path_aliases=()):
    text = "" if text is None else str(text)
    aliases = sorted(
        (
            (str(path), str(alias))
            for path, alias in path_aliases
            if str(path)
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for path, alias in aliases:
        text = _replace_path_literal(text, path, alias)
    text = _replace_path_literal(text, Path.home(), "~")
    text = _replace_path_literal(text, tempfile.gettempdir(), "<temp>")
    text = _WINDOWS_ABSOLUTE_PATH.sub("<absolute-path>", text)
    if not sys.platform.startswith('win'):
        text = _POSIX_ABSOLUTE_PATH.sub("<absolute-path>", text)
    return text


def _redact_home_paths(text):
    """Compatibility wrapper for the default diagnostic path redaction."""
    return _redact_diagnostic_text(text)


def _diagnostic_data_locations(
    output_dir,
    settings_location,
    preview_dir=None,
):
    output = Path(output_dir).expanduser()
    return (
        f"Run logs: {_diagnostics_dir()}",
        f"GUI settings: {settings_location}",
        (
            "Batch history/manifests: "
            f"{output}/*{BATCH_MANIFEST_SUFFIX}"
        ),
        f"Replay sidecars: {output}/*.sidecar.json",
        (
            "Preview audio: "
            f"{preview_dir or '<OS temporary directory; removed on clean close>'}"
        ),
        "Support bundles: user-selected .zip destination",
    )


def _read_support_log(path):
    size = path.stat().st_size
    truncated = size > MAX_SUPPORT_LOG_BYTES
    with path.open("rb") as handle:
        if truncated:
            handle.seek(-MAX_SUPPORT_LOG_BYTES, os.SEEK_END)
        payload = handle.read(MAX_SUPPORT_LOG_BYTES)
    text = payload.decode("utf-8", errors="replace")
    if truncated:
        text = "[Earlier log content omitted by support-bundle limit]\n" + text
    return text, truncated


def export_support_bundle(
    destination,
    *,
    redact=True,
    output_dir,
    settings_location,
    preview_dir=None,
    log_dir=None,
):
    destination = Path(destination)
    if destination.suffix.lower() != ".zip":
        raise ValueError("support bundle destination must end in .zip")
    if os.path.lexists(destination):
        raise FileExistsError(
            f"support bundle destination already exists: {destination}"
        )
    parent = destination.parent
    if not parent.is_dir():
        raise OSError(f"support bundle directory does not exist: {parent}")
    source_dir = Path(log_dir) if log_dir is not None else _diagnostics_dir()
    try:
        log_paths = (
            sorted(
                source_dir.glob("*.log"),
                key=lambda path: (path.stat().st_mtime_ns, path.name),
            )
            if source_dir.is_dir()
            else []
        )
    except OSError as exc:
        raise OSError(f"cannot enumerate diagnostic logs: {exc}") from exc

    archived_logs = []
    failures = []
    archive_payloads = []
    for index, log_path in enumerate(log_paths, start=1):
        archive_name = f"logs/{index:03d}-{log_path.name}"
        try:
            text, truncated = _read_support_log(log_path)
            if redact:
                text = _redact_diagnostic_text(text)
            encoded = text.encode("utf-8")
            archive_payloads.append((archive_name, encoded))
            archived_logs.append({
                "name": archive_name,
                "bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "truncated": truncated,
            })
        except OSError as exc:
            failures.append({
                "name": log_path.name,
                "error": _redact_diagnostic_text(str(exc)),
            })

    location_text = "\n".join(_diagnostic_data_locations(
        output_dir,
        settings_location,
        preview_dir,
    ))
    environment_text = "\n".join(_diagnostic_environment_lines()) + "\n"
    if redact:
        location_text = _redact_diagnostic_text(location_text)
        environment_text = _redact_diagnostic_text(environment_text)
    manifest = {
        "schema_version": SUPPORT_BUNDLE_SCHEMA_VERSION,
        "app": APP_NAME,
        "app_version": VERSION,
        "generated_at": (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "path_redaction": bool(redact),
        "included_logs": archived_logs,
        "read_failures": failures,
        "excluded_by_design": [
            "audio files",
            "batch manifests",
            "replay sidecars",
            "GUI settings contents",
        ],
    }
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=parent,
    )
    os.close(fd)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as bundle:
            bundle.writestr(
                "support-bundle.json",
                json.dumps(
                    manifest,
                    indent=2,
                    sort_keys=True,
                ) + "\n",
            )
            bundle.writestr("environment.txt", environment_text)
            bundle.writestr("data-locations.txt", location_text + "\n")
            for archive_name, payload in archive_payloads:
                bundle.writestr(archive_name, payload)
        with open(temporary, "rb+") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        _promote_file_no_replace(temporary, destination)
    except Exception:
        _remove_file_silent(temporary)
        raise
    return {
        "path": str(destination),
        "sha256": _sha256_file(destination),
        "included_logs": len(archived_logs),
        "read_failures": tuple(failures),
        "redacted": bool(redact),
    }


def _diagnostic_environment_lines():
    native = _native_runtime_report()
    return [
        f"App: {APP_NAME} v{VERSION}",
        f"Python: {sys.version.replace(os.linesep, ' ')}",
        f"Executable: {sys.executable}",
        f"Frozen: {bool(getattr(sys, 'frozen', False))}",
        f"Platform: {platform.platform()}",
        f"numpy: {np.__version__}",
        f"scipy: {scipy.__version__}",
        f"soundfile: {getattr(sf, '__version__', 'unknown')}",
        f"libsndfile: {native['libsndfile']}",
        "Decode policy: isolated process; bounded header, time, memory, and output; "
        "IRCAM/WAV IMA ADPCM disabled",
        f"mutagen: {getattr(mutagen, 'version_string', 'unknown')}",
        f"PyQt6: {PYQT_VERSION_STR}",
        f"Qt runtime: {qVersion()}",
        f"Qt compile target: {QT_VERSION_STR}",
        f"PyQt6 Multimedia: {'available' if _MULTIMEDIA_OK else 'missing'}",
        f"ffmpeg: {'available' if _check_ffmpeg() else 'missing'}"
        + (f" (encoders: {', '.join(k for k, v in _probe_ffmpeg_encoders().items() if v) or 'none'})" if _check_ffmpeg() else ""),
    ]


class RunDiagnostics:
    def __init__(
        self,
        prefix='run',
        path=None,
        redact=True,
        max_logs=MAX_RETAINED_LOGS,
    ):
        self.path = Path(path) if path is not None else _new_diagnostics_path(prefix)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._redact = bool(redact)
        self._closed = False
        self._path_aliases = []
        self.path.write_text('', encoding='utf-8')
        self.retention_report = _enforce_log_retention(
            _validated_log_retention(max_logs),
            protected_paths=(self.path,),
            log_dir=self.path.parent,
        )
        if self.retention_report["failures"]:
            failures = ", ".join(
                f"{item['name']}: {item['error']}"
                for item in self.retention_report["failures"]
            )
            self.write(f"Log retention warning: {failures}")

    @property
    def closed(self):
        return self._closed

    def close(self):
        with self._lock:
            self._closed = True

    def set_redaction(self, enabled):
        with self._lock:
            if self._closed:
                raise RuntimeError("diagnostic log is closed")
            self._redact = bool(enabled)

    def _register_sensitive_path(self, path, alias):
        raw = str(path)
        if raw and os.path.isabs(raw):
            self._path_aliases.append((raw, alias))

    def write(self, msg):
        text = '' if msg is None else str(msg)
        with self._lock:
            if self._closed:
                raise RuntimeError("diagnostic log is closed")
            if self._redact:
                text = _redact_diagnostic_text(
                    text,
                    self._path_aliases,
                )
            lines = text.splitlines() or ['']
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with self.path.open('a', encoding='utf-8') as f:
                for line in lines:
                    f.write(f"[{ts}] {line}\n")

    def write_header(self, mode, inputs, output_dir, params, preset_name=None, seed=None):
        self._register_sensitive_path(output_dir, "<output-dir>")
        for index, input_path in enumerate(inputs, start=1):
            self._register_sensitive_path(input_path, f"<input-{index}>")
        self.write(f"{APP_NAME} v{VERSION} run started")
        self.write(f"Rights scope: {RIGHTS_ONLY_NOTICE}")
        self.write(f"Evidence scope: {EVIDENCE_NOTICE}")
        self.write(
            "Path redaction: "
            f"{'enabled' if self._redact else 'disabled by explicit choice'}"
        )
        self.write(f"Mode: {mode}")
        self.write(f"Preset: {preset_name or 'Custom'}")
        if seed is None:
            seed_text = "generated per file; effective seed is logged with each result"
        else:
            seed_text = str(seed)
        self.write(f"Seed: {seed_text}")
        self.write(f"Output dir: {output_dir}")
        for i, input_path in enumerate(inputs, start=1):
            self.write(f"Input {i}: {input_path}")
        self.write("Environment:")
        for line in _diagnostic_environment_lines():
            self.write(f"  {line}")
        self.write("Parameters:")
        self.write(json.dumps(params, indent=2, sort_keys=True, default=str))


def _norm_output_path(path):
    return os.path.normcase(os.path.abspath(path))


def _sidecar_path_for_output(output_path):
    return Path(output_path).with_suffix('.sidecar.json')


def _spectrogram_path_for_output(output_path):
    path = Path(output_path)
    return path.with_suffix(".spectrogram.png")


def _loudness_report_path_for_output(output_path):
    path = Path(output_path)
    return path.with_suffix(".loudness.json")


def _reservation_path_for_output(output_path):
    path = Path(output_path)
    return path.with_name(f".{path.name}.sunojump-reservation")


def _output_candidate_is_occupied(candidate, used_paths):
    normalized = _norm_output_path(candidate)
    return (
        normalized in used_paths
        or os.path.lexists(candidate)
        or os.path.lexists(_sidecar_path_for_output(candidate))
        or os.path.lexists(_spectrogram_path_for_output(candidate))
        or os.path.lexists(_loudness_report_path_for_output(candidate))
        or os.path.lexists(_reservation_path_for_output(candidate))
    )


def _planned_output_path(input_path, output_dir, ext, used_paths=None):
    """Return an unreserved collision-free output path for compatibility."""
    used_paths = used_paths if used_paths is not None else set()
    stem = Path(input_path).stem
    base = os.path.join(output_dir, f"{stem}_sj{ext}")
    candidate = base
    counter = 2
    while _output_candidate_is_occupied(candidate, used_paths):
        candidate = os.path.join(output_dir, f"{stem}_sj_{counter}{ext}")
        counter += 1
    used_paths.add(_norm_output_path(candidate))
    return candidate, candidate != base


def _promote_file_no_replace(source_path, destination_path):
    """Atomically publish *source_path* without replacing an existing path."""
    source_path = os.path.abspath(source_path)
    destination_path = os.path.abspath(destination_path)
    try:
        os.link(source_path, destination_path)
    except FileExistsError:
        raise
    except OSError as link_error:
        if os.name != 'nt':
            raise OSError(
                "destination filesystem cannot atomically publish without "
                f"replacement: {link_error}"
            ) from link_error
        try:
            os.rename(source_path, destination_path)
            return
        except FileExistsError:
            raise
        except OSError as rename_error:
            raise OSError(
                "destination filesystem cannot atomically publish without "
                f"replacement: {rename_error}"
            ) from rename_error
    _remove_file_silent(source_path)
    _fsync_directory(os.path.dirname(destination_path) or os.getcwd())


class OutputReservation:
    """Cross-process ownership marker for one audio/sidecar destination pair."""

    def __init__(self, output_path, marker_path, token, renamed):
        self.output_path = str(output_path)
        self.marker_path = str(marker_path)
        self.token = token
        self.renamed = bool(renamed)
        self._released = False

    def _owns_marker(self):
        try:
            return Path(self.marker_path).read_text(
                encoding='utf-8',
            ).strip() == self.token
        except OSError:
            return False

    def promote(self, source_path):
        if self._released or not self._owns_marker():
            raise RuntimeError("output reservation ownership was lost")
        _promote_file_no_replace(source_path, self.output_path)

    def release(self):
        if self._released:
            return
        try:
            if self._owns_marker():
                os.remove(self.marker_path)
        except FileNotFoundError:
            pass
        finally:
            self._released = True


def _reserve_output_path(input_path, output_dir, ext, used_paths=None):
    """Reserve a collision-free audio/sidecar destination across processes."""
    used_paths = used_paths if used_paths is not None else set()
    stem = Path(input_path).stem
    base = os.path.join(output_dir, f"{stem}_sj{ext}")
    counter = 1
    while True:
        candidate = (
            base
            if counter == 1
            else os.path.join(output_dir, f"{stem}_sj_{counter}{ext}")
        )
        counter += 1
        if _output_candidate_is_occupied(candidate, used_paths):
            continue

        marker = _reservation_path_for_output(candidate)
        token = secrets.token_hex(32)
        try:
            fd = os.open(
                marker,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            continue
        try:
            os.write(fd, token.encode('ascii'))
            os.fsync(fd)
        except Exception:
            os.close(fd)
            _remove_file_silent(marker)
            raise
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

        reservation = OutputReservation(
            candidate,
            marker,
            token,
            candidate != base,
        )
        if (
            os.path.lexists(candidate)
            or os.path.lexists(_sidecar_path_for_output(candidate))
            or os.path.lexists(_spectrogram_path_for_output(candidate))
            or os.path.lexists(_loudness_report_path_for_output(candidate))
        ):
            reservation.release()
            continue
        used_paths.add(_norm_output_path(candidate))
        return reservation


def _output_extension(fmt):
    return OUTPUT_EXTENSIONS.get(str(fmt).lower(), '.wav')


def _format_requires_ffmpeg(fmt):
    return str(fmt).lower() in FFMPEG_EXPORT_FORMATS


def _available_output_formats():
    formats = ['wav', 'flac', 'ogg']
    if _check_ffmpeg():
        for fmt in ('mp3', 'm4a'):
            if _ffmpeg_encoder_available(fmt):
                formats.append(fmt)
    return formats


def _make_atomic_output_temp(output_path):
    """Create a same-directory temp output path that keeps the final extension."""
    final_path = os.path.abspath(output_path)
    dest_dir = os.path.dirname(final_path) or os.getcwd()
    os.makedirs(dest_dir, exist_ok=True)
    stem = Path(final_path).stem or APP_NAME.lower()
    ext = Path(final_path).suffix or '.tmp'
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{stem}.",
        suffix=f".tmp{ext}",
        dir=dest_dir,
    )
    os.close(fd)
    return tmp_path


def _remove_file_silent(path):
    if not path:
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _fsync_file(path):
    with open(path, 'r+b') as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path):
    if os.name == 'nt':
        return
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _canonical_payload_sha256(payload):
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
        default=str,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _sidecar_binding_payload(sidecar_core):
    return {
        key: value
        for key, value in sidecar_core.items()
        if key != "output_file"
    }


def _read_audio_sidecar_binding(path):
    audio_file = MutagenFile(path)
    if audio_file is None or audio_file.tags is None:
        return None
    tags = audio_file.tags
    module_name = type(tags).__module__
    if hasattr(tags, 'getall') and hasattr(tags, 'add'):
        for frame in tags.getall('TXXX'):
            if str(getattr(frame, 'desc', '')) == SIDECAR_AUDIO_TAG:
                values = getattr(frame, 'text', [])
                return str(values[0]) if values else None
        return None
    if module_name.startswith('mutagen.mp4'):
        values = tags.get(
            f'----:com.sunojump:{SIDECAR_AUDIO_TAG.lower()}',
            [],
        )
        if not values:
            return None
        value = values[0]
        return value.decode('ascii') if isinstance(value, bytes) else str(value)
    for key in tags.keys():
        if str(key).upper() == SIDECAR_AUDIO_TAG:
            value = tags[key]
            if isinstance(value, (list, tuple)):
                return str(value[0]) if value else None
            return str(value)
    return None


def _write_audio_sidecar_binding(path, payload_sha256):
    audio_file = MutagenFile(path)
    if audio_file is None:
        raise ValueError("encoded output has no supported metadata container")
    if audio_file.tags is None:
        audio_file.add_tags()
    tags = audio_file.tags
    module_name = type(tags).__module__
    if hasattr(tags, 'getall') and hasattr(tags, 'add'):
        tags.delall(f'TXXX:{SIDECAR_AUDIO_TAG}')
        tags.add(TXXX(
            encoding=3,
            desc=SIDECAR_AUDIO_TAG,
            text=[payload_sha256],
        ))
    elif module_name.startswith('mutagen.mp4'):
        tags[f'----:com.sunojump:{SIDECAR_AUDIO_TAG.lower()}'] = [
            payload_sha256.encode('ascii')
        ]
    else:
        tags[SIDECAR_AUDIO_TAG] = payload_sha256
    audio_file.save()
    if _read_audio_sidecar_binding(path) != payload_sha256:
        raise ValueError("audio-sidecar binding could not be verified")


def _write_json_atomic_no_replace(path, payload):
    destination = os.path.abspath(path)
    directory = os.path.dirname(destination) or os.getcwd()
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{Path(destination).stem}.",
        suffix=".tmp.json",
        dir=directory,
    )
    try:
        descriptor = fd
        fd = None
        with os.fdopen(
            descriptor,
            'w',
            encoding='utf-8',
            newline='\n',
        ) as handle:
            json.dump(
                payload,
                handle,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        _promote_file_no_replace(temp_path, destination)
        temp_path = None
        _fsync_directory(directory)
        return _sha256_file(destination)
    finally:
        if fd is not None:
            os.close(fd)
        _remove_file_silent(temp_path)


def _write_binary_atomic_no_replace(path, payload):
    destination = os.path.abspath(path)
    directory = os.path.dirname(destination) or os.getcwd()
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{Path(destination).stem}.",
        suffix=".tmp.bin",
        dir=directory,
    )
    try:
        descriptor = fd
        fd = None
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _promote_file_no_replace(temp_path, destination)
        temp_path = None
        _fsync_directory(directory)
        return _sha256_file(destination)
    finally:
        if fd is not None:
            os.close(fd)
        _remove_file_silent(temp_path)


OUTPUT_VALIDATION_BLOCK_FRAMES = 65536
OUTPUT_VALIDATION_TIMEOUT_SECONDS = 120
OUTPUT_SILENCE_PEAK = 1e-7


class OutputValidationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _sha256_file(path):
    hasher = hashlib.sha256()
    try:
        with open(path, 'rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                hasher.update(block)
    except OSError as exc:
        raise OutputValidationError(
            RenderErrorCode.OUTPUT_HASH_FAILED,
            f"cannot hash {Path(path).name}: {exc}",
        ) from exc
    return hasher.hexdigest()


def _validate_output_mapping(input_path, output_path, fmt):
    input_real = os.path.normcase(os.path.realpath(os.path.abspath(input_path)))
    output_real = os.path.normcase(os.path.realpath(os.path.abspath(output_path)))
    same_existing_file = False
    try:
        same_existing_file = os.path.samefile(input_path, output_path)
    except (FileNotFoundError, OSError):
        pass
    if input_real == output_real or same_existing_file:
        raise OutputValidationError(
            RenderErrorCode.OUTPUT_MAPPING_INVALID,
            "input and output resolve to the same file",
        )
    expected_ext = _output_extension(fmt)
    actual_ext = Path(output_path).suffix.lower()
    if actual_ext != expected_ext:
        raise OutputValidationError(
            RenderErrorCode.OUTPUT_MAPPING_INVALID,
            f"{fmt.upper()} output must use the {expected_ext} extension",
        )


def _decode_ffmpeg_output_for_validation(encoded_path):
    validation_path = _make_atomic_output_temp(
        str(Path(encoded_path).with_suffix('.validation.wav'))
    )
    try:
        result = subprocess.run(
            [
                'ffmpeg', '-y', '-loglevel', 'error',
                '-i', encoded_path,
                '-map', '0:a:0',
                '-c:a', 'pcm_f32le',
                validation_path,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=OUTPUT_VALIDATION_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or 'ffmpeg decode failed').strip()
            raise OutputValidationError(
                RenderErrorCode.OUTPUT_DECODE_FAILED,
                f"encoded output cannot be decoded: {detail}",
            )
        return validation_path
    except subprocess.TimeoutExpired as exc:
        _remove_file_silent(validation_path)
        raise OutputValidationError(
            RenderErrorCode.OUTPUT_DECODE_FAILED,
            "encoded-output validation timed out",
        ) from exc
    except (FileNotFoundError, OSError) as exc:
        _remove_file_silent(validation_path)
        raise OutputValidationError(
            RenderErrorCode.OUTPUT_DECODE_FAILED,
            f"encoded-output validation could not start: {exc}",
        ) from exc
    except Exception:
        _remove_file_silent(validation_path)
        raise


def _inspect_generated_audio(path):
    peak = 0.0
    frames = 0
    try:
        with sf.SoundFile(path, mode='r') as handle:
            sample_rate = int(handle.samplerate)
            channels = int(handle.channels)
            while True:
                block = handle.read(
                    OUTPUT_VALIDATION_BLOCK_FRAMES,
                    dtype='float32',
                    always_2d=True,
                )
                if not len(block):
                    break
                if not np.all(np.isfinite(block)):
                    raise OutputValidationError(
                        RenderErrorCode.OUTPUT_NONFINITE,
                        "decoded output contains non-finite samples",
                    )
                frames += int(block.shape[0])
                peak = max(peak, float(np.max(np.abs(block))))
    except OutputValidationError:
        raise
    except Exception as exc:
        raise OutputValidationError(
            RenderErrorCode.OUTPUT_DECODE_FAILED,
            f"output decode failed: {exc}",
        ) from exc
    return sample_rate, channels, frames, peak


def _validate_render_output(
    input_path,
    encoded_path,
    output_path,
    fmt,
    expected_sample_rate,
    expected_channels,
    expected_frames,
):
    _validate_output_mapping(input_path, output_path, fmt)
    try:
        output_bytes = os.path.getsize(encoded_path)
    except OSError as exc:
        raise OutputValidationError(
            RenderErrorCode.OUTPUT_DECODE_FAILED,
            f"cannot inspect encoded output: {exc}",
        ) from exc
    if output_bytes <= 0:
        raise OutputValidationError(
            RenderErrorCode.OUTPUT_DECODE_FAILED,
            "encoded output is empty",
        )

    decoded_path = encoded_path
    decoder = "soundfile"
    validation_temp = None
    if _format_requires_ffmpeg(fmt):
        validation_temp = _decode_ffmpeg_output_for_validation(encoded_path)
        decoded_path = validation_temp
        decoder = "ffmpeg+soundfile"
    try:
        sample_rate, channels, frames, peak = _inspect_generated_audio(decoded_path)
    finally:
        _remove_file_silent(validation_temp)

    if sample_rate != int(expected_sample_rate):
        raise OutputValidationError(
            RenderErrorCode.OUTPUT_SAMPLE_RATE_MISMATCH,
            f"output sample rate {sample_rate} does not match {expected_sample_rate}",
        )
    if channels != int(expected_channels):
        raise OutputValidationError(
            RenderErrorCode.OUTPUT_CHANNEL_MISMATCH,
            f"output channels {channels} do not match {expected_channels}",
        )
    frame_tolerance = (
        max(2048, int(expected_sample_rate * 0.05))
        if _format_requires_ffmpeg(fmt)
        else 1
    )
    if abs(frames - int(expected_frames)) > frame_tolerance:
        expected_duration = expected_frames / float(expected_sample_rate)
        actual_duration = frames / float(sample_rate)
        raise OutputValidationError(
            RenderErrorCode.OUTPUT_DURATION_MISMATCH,
            f"output duration {actual_duration:.6f}s does not match "
            f"{expected_duration:.6f}s",
        )
    if peak < OUTPUT_SILENCE_PEAK:
        raise OutputValidationError(
            RenderErrorCode.OUTPUT_SILENT,
            f"output peak {peak:.3e} is below the non-silent threshold",
        )

    input_hash = _sha256_file(input_path)
    output_hash = _sha256_file(encoded_path)
    return OutputValidation(
        input_sha256=input_hash,
        output_sha256=output_hash,
        output_bytes=output_bytes,
        sample_rate_hz=sample_rate,
        channels=channels,
        frames=frames,
        duration_seconds=frames / float(sample_rate),
        peak=peak,
        hashes_distinct=input_hash != output_hash,
        decoder=decoder,
    )


def _humanize_bytes(n_bytes):
    value = float(n_bytes)
    for suffix in ('B', 'KB', 'MB', 'GB'):
        if value < 1024.0 or suffix == 'GB':
            return f"{value:.1f} {suffix}" if suffix != 'B' else f"{int(value)} B"
        value /= 1024.0


def _preflight_audio_input(input_path, preview_seconds=None):
    del preview_seconds
    return inspect_audio_path(
        input_path,
        max_input_bytes=MAX_INPUT_FILE_BYTES,
    )


def _decode_limits():
    return DecodeLimits(
        max_input_bytes=MAX_INPUT_FILE_BYTES,
        max_decoded_bytes=MAX_DECODED_AUDIO_BYTES,
        max_channels=MAX_AUDIO_CHANNELS,
        max_sample_rate=MAX_AUDIO_SAMPLE_RATE,
        max_duration_seconds=MAX_AUDIO_DURATION_SECONDS,
        timeout_seconds=DECODE_TIMEOUT_SECONDS,
        worker_memory_bytes=MAX_DECODE_WORKER_MEMORY_BYTES,
    )


def _native_runtime_report():
    libsndfile_version = str(
        getattr(sf, "__libsndfile_version__", "unknown")
    )
    try:
        validate_libsndfile_version(libsndfile_version)
        runtime_gate = "pass-with-contained-formats"
    except ValueError as exc:
        runtime_gate = f"fail: {exc}"
    return {
        "python": platform.python_version(),
        "numpy": str(np.__version__),
        "scipy": str(scipy.__version__),
        "soundfile": str(getattr(sf, "__version__", "unknown")),
        "libsndfile": libsndfile_version,
        "mutagen": str(getattr(mutagen, "version_string", "unknown")),
        "pyqt6": str(PYQT_VERSION_STR),
        "qt6": str(qVersion()),
        "qt6_compile_target": str(QT_VERSION_STR),
        "ffmpeg": _ffmpeg_version_line(),
        "minimum_libsndfile": ".".join(
            str(value) for value in MIN_LIBSNDFILE_VERSION
        ),
        "runtime_gate": runtime_gate,
        "decode_isolation": "spawned-process",
        "decode_strategy": "chunked-npy-memmap",
        "decode_chunk_frames": DECODE_CHUNK_FRAMES,
        "header_inspection_bytes": 1024 * 1024,
        "decode_timeout_seconds": DECODE_TIMEOUT_SECONDS,
        "decode_memory_bytes": MAX_DECODE_WORKER_MEMORY_BYTES,
        "decode_output_bytes": MAX_DECODED_AUDIO_BYTES,
        "streaming_threshold_bytes": STREAMING_THRESHOLD_BYTES,
        "streaming_working_set_bytes": STREAMING_WORKING_SET_BYTES,
        "streaming_chunk_seconds": STREAMING_CHUNK_SECONDS,
        "streaming_overlap_seconds": STREAMING_OVERLAP_SECONDS,
        "blocked_native_formats": [
            "IRCAM",
            "WAV IMA ADPCM",
            "WAV extensible IMA ADPCM",
        ],
    }


# ============================================================
#  Audio Processor
# ============================================================
class AudioProcessor:
    # Humanize pass processes long audio in chunks to bound peak memory.
    # Chunks are rendered with a shared modulation curve (continuous across
    # boundaries) so the output is indistinguishable from whole-file rendering.
    _HUMANIZE_CHUNK_SEC = 60.0

    def __init__(
        self,
        params,
        log_fn=None,
        progress_fn=None,
        cancel_event=None,
        seed=None,
        compute_backend=None,
        audit_options=None,
        streaming_threshold_bytes=STREAMING_THRESHOLD_BYTES,
        streaming_chunk_seconds=STREAMING_CHUNK_SECONDS,
    ):
        self.params = params
        self.log = log_fn or print
        self.progress = progress_fn or (lambda v: None)
        if seed is None:
            seed = secrets.randbits(64)
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a non-negative integer")
        self._seed = seed
        self.rng = np.random.default_rng(self._seed)
        self._compute_backend = resolve_compute_backend(compute_backend)
        self._audit_options = _validated_audit_options(audit_options)
        if streaming_threshold_bytes < 0:
            raise ValueError("streaming threshold must be non-negative")
        if streaming_chunk_seconds <= STREAMING_OVERLAP_SECONDS:
            raise ValueError("streaming chunk duration must exceed overlap")
        self._streaming_threshold_bytes = int(streaming_threshold_bytes)
        self._streaming_chunk_seconds = float(streaming_chunk_seconds)
        self._cancel_event = cancel_event or threading.Event()
        self._spectral_candidates = []
        self._trace = self._new_trace()
        self._decode_metadata = {}
        self._verifier_results = []
        self._source_provenance = None

    def _new_trace(self):
        return {
            "rng": {
                "algorithm": type(self.rng.bit_generator).__name__,
                "seed": self._seed,
            },
            "compute": self._compute_backend.evidence,
            "passes": {},
        }

    def _trace_random(self, pass_name, distributions, **details):
        trace = self._trace["passes"].setdefault(pass_name, {})
        trace.update({
            "rng_algorithm": type(self.rng.bit_generator).__name__,
            "effective_seed": self._seed,
            "distributions": distributions,
            **details,
        })
        return trace

    def cancel(self):
        self._cancel_event.set()

    def _is_cancelled(self):
        return self._cancel_event.is_set()

    def _write_spectrogram_artifact(
        self,
        output_path,
        before,
        after,
        sample_rate,
    ):
        if not self._audit_options["spectrogram"]:
            return ()
        self.log("Generating before/after spectrogram...")
        png, metadata = render_spectrogram_comparison_png(
            before,
            after,
            sample_rate,
        )
        artifact_path = _spectrogram_path_for_output(output_path)
        digest = _write_binary_atomic_no_replace(artifact_path, png)
        self.log(
            f"Spectrogram written atomically: {artifact_path.name} "
            f"(sha256:{digest[:12]})"
        )
        return ({
            "kind": "spectrogram_comparison",
            "path": str(artifact_path),
            "media_type": "image/png",
            "sha256": digest,
            "metadata": metadata,
        },)

    def _write_loudness_artifact(
        self,
        output_path,
        before,
        after,
        sample_rate,
    ):
        if not self._audit_options["loudness"]:
            return ()
        self.log("Measuring ITU-R BS.1770-5 loudness and true peak...")
        report = measure_loudness_comparison(
            before,
            after,
            sample_rate,
        )
        artifact_path = _loudness_report_path_for_output(output_path)
        digest = _write_json_atomic_no_replace(artifact_path, report)
        self.log(
            f"Loudness report written atomically: {artifact_path.name} "
            f"(sha256:{digest[:12]})"
        )
        return ({
            "kind": "loudness_comparison",
            "path": str(artifact_path),
            "media_type": "application/json",
            "sha256": digest,
            "metadata": report,
        },)

    # --- Main pipeline ---
    def process(
        self,
        input_path,
        output_path,
        preview_seconds=None,
        preview_offset_seconds=0.0,
    ):
        """Process audio file.

        If preview_seconds is set and > 0, only that duration beginning at
        preview_offset_seconds is loaded and processed. This keeps render
        time short enough for interactive preset A/B auditioning.
        """
        started_at = time.monotonic()
        input_path = str(input_path)
        output_path = str(output_path)
        fmt = self.params.get('output_format', 'wav').lower()
        decode_temp = None

        def finish(
            state,
            error_code=None,
            message="",
            validation=None,
            sidecar_path=None,
            sidecar_sha256=None,
            artifacts=(),
        ):
            nonlocal decode_temp
            usable = state in {RenderState.SUCCEEDED, RenderState.PARTIAL}
            result = RenderResult(
                state=state,
                input_path=input_path,
                output_path=output_path if usable else None,
                error_code=error_code,
                message=message,
                elapsed_seconds=time.monotonic() - started_at,
                validation=validation if usable else None,
                effective_seed=self._seed,
                sidecar_path=sidecar_path if usable else None,
                sidecar_sha256=sidecar_sha256 if usable else None,
                artifacts=artifacts if usable else (),
            )
            if decode_temp is not None:
                try:
                    decode_temp.cleanup()
                except OSError:
                    pass
                decode_temp = None
            return result

        self.rng = np.random.default_rng(self._seed)
        self._trace = self._new_trace()
        self._verifier_results = []
        self._source_provenance = None
        self.log(f"Loading {Path(input_path).name}...")
        compute_evidence = self._compute_backend.evidence
        compute_message = (
            f"Compute backend: {compute_evidence['selected']}"
        )
        if compute_evidence.get("fallback_reason"):
            compute_message += " (auto fallback to CPU)"
        self.log(compute_message)
        self.log(f"  Effective seed: {self._seed}")

        try:
            _validate_output_mapping(input_path, output_path, fmt)
            if os.path.lexists(output_path):
                raise OutputValidationError(
                    RenderErrorCode.OUTPUT_WRITE_FAILED,
                    "destination already exists; it will not be replaced",
                )
            if os.path.lexists(_sidecar_path_for_output(output_path)):
                raise OutputValidationError(
                    RenderErrorCode.OUTPUT_WRITE_FAILED,
                    "sidecar destination already exists; it will not be replaced",
                )
        except OutputValidationError as exc:
            self.log(f"  Output mapping error: {exc}")
            return finish(RenderState.FAILED, exc.code, str(exc))

        try:
            _preflight_audio_input(input_path, preview_seconds)
        except ValueError as e:
            self.log(f"  Error: {e}")
            return finish(RenderState.FAILED, RenderErrorCode.INVALID_INPUT, str(e))

        self._source_provenance = inspect_c2pa(input_path)
        if self._source_provenance.failed:
            message = (
                "C2PA provenance inspection failed; source was not "
                f"processed: {self._source_provenance.message}"
            )
            self.log(f"  Error: {message}")
            return finish(
                RenderState.FAILED,
                RenderErrorCode.C2PA_INSPECTION_FAILED,
                message,
            )
        if self._source_provenance.present:
            store_summary = ", ".join(
                f"{store.location} sha256:{store.sha256[:12]}"
                for store in self._source_provenance.manifest_stores
            )
            self.log(
                "  C2PA source provenance detected "
                f"({self._source_provenance.status}): {store_summary}"
            )
            policy = self.params.get(
                "c2pa_policy",
                C2PA_POLICY_BLOCK,
            )
            if policy != C2PA_POLICY_ALLOW_REMOVAL:
                message = (
                    "source contains C2PA Content Credentials; processing "
                    "is blocked until removal/invalidation is explicitly "
                    "acknowledged"
                )
                self.log(f"  Error: {message}")
                return finish(
                    RenderState.FAILED,
                    RenderErrorCode.C2PA_POLICY_REQUIRED,
                    message,
                )
            self.log(
                "  C2PA policy acknowledged: the source remains unchanged; "
                "the transformed output will not carry or re-sign the source "
                "manifest. This is not evidence of acoustic-detector change."
            )
        else:
            self.log("  C2PA source provenance: not detected")

        try:
            if preview_seconds is not None and preview_seconds > 0:
                audio, sr, self._decode_metadata = decode_audio_isolated(
                    input_path,
                    preview_seconds,
                    _decode_limits(),
                    preview_offset_seconds=preview_offset_seconds,
                    cancel_event=self._cancel_event,
                )
                self._decode_metadata["processing_strategy"] = "in-memory-preview"
            else:
                decode_temp = tempfile.TemporaryDirectory(
                    prefix="sunojump-stream-"
                )
                decoded_path = Path(decode_temp.name) / "decoded.npy"
                sr, self._decode_metadata = decode_audio_isolated_to_path(
                    input_path,
                    None,
                    _decode_limits(),
                    decoded_path,
                    cancel_event=self._cancel_event,
                )
                decoded_bytes = int(
                    self._decode_metadata.get("decoded_bytes", 0)
                )
                if decoded_bytes > self._streaming_threshold_bytes:
                    return self._process_streaming_decoded(
                        input_path=input_path,
                        output_path=output_path,
                        decoded_path=decoded_path,
                        sample_rate=sr,
                        output_format=fmt,
                        finish=finish,
                    )
                audio = np.load(decoded_path, allow_pickle=False)
                self._decode_metadata["processing_strategy"] = "in-memory"
                decode_temp.cleanup()
                decode_temp = None
        except DecodeCancelled:
            self.log("Cancelled.")
            return finish(
                RenderState.CANCELLED,
                RenderErrorCode.CANCELLED,
                "decode cancelled",
            )
        except ValueError as e:
            self.log(f"  Error reading file: {e}")
            return finish(RenderState.FAILED, RenderErrorCode.DECODE_FAILED, str(e))
        self.log(
            "  Decoder: isolated "
            f"libsndfile {self._decode_metadata.get('libsndfile_version', 'unknown')}"
        )

        if audio.size == 0:
            self.log("  Error: empty audio file")
            return finish(
                RenderState.FAILED,
                RenderErrorCode.EMPTY_AUDIO,
                "empty audio file",
            )

        # Trim to preview length if requested
        if preview_seconds and preview_seconds > 0:
            max_samples = int(preview_seconds * sr)
            if audio.ndim == 1:
                audio = audio[:max_samples] if len(audio) > max_samples else audio
            else:
                audio = audio[:max_samples] if audio.shape[0] > max_samples else audio
            offset = float(
                self._decode_metadata.get("preview_offset_seconds", 0.0)
            )
            actual_duration = audio.shape[0] / sr
            self.log(
                f"  Preview mode: {offset:.1f}s–"
                f"{offset + actual_duration:.1f}s "
                f"({actual_duration:.1f}s actual)"
            )

        mono = audio.ndim == 1
        if mono:
            audio = audio[:, np.newaxis]

        original = audio.copy()

        # Build pass list
        pitch_enabled = self.params.get('pitch_enabled')
        tempo_enabled = self.params.get('tempo_enabled')
        coupled_pitch_tempo = pitch_enabled and tempo_enabled
        pass_names = []
        if self.params.get('strip_metadata', True):
            pass_names.append('Metadata Strip')
        if self.params.get('spectral_enabled'):
            if self.params.get(
                'spectral_scan_enabled',
                self.params.get('watermark_scan_enabled', True),
            ):
                pass_names.append('Narrowband Candidate Scan')
            pass_names.append('Spectral Perturbation')
        if self.params.get('dynamic_eq_enabled'):
            pass_names.append('Dynamic EQ')
        if coupled_pitch_tempo:
            pass_names.append('Coupled Pitch/Tempo Micro-Variation')
        else:
            if pitch_enabled:
                pass_names.append('Pitch Micro-Shift')
            if tempo_enabled:
                pass_names.append('Tempo Micro-Variation')
        if self.params.get('phase_enabled'):
            pass_names.append('Phase Scrambling')
        if self.params.get('stereo_enabled') and not mono:
            pass_names.append('Stereo Manipulation')
        if self.params.get('noise_enabled'):
            pass_names.append('Noise Injection')
        if self.params.get('dynamics_enabled'):
            pass_names.append('Dynamics Modification')
        if self.params.get('humanize_enabled'):
            pass_names.append('Humanization')
        if self.params.get('reencode_enabled'):
            pass_names.append('Lossy Re-encode')

        total = len(pass_names)
        if total == 0:
            self.log("No passes enabled.")
            return finish(
                RenderState.FAILED,
                RenderErrorCode.NO_PASSES_ENABLED,
                "no passes enabled",
            )

        for i, name in enumerate(pass_names):
            if self._is_cancelled():
                self.log("Cancelled.")
                return finish(
                    RenderState.CANCELLED,
                    RenderErrorCode.CANCELLED,
                    "cancelled before pass execution completed",
                )

            self.log(f"  Pass {i+1}/{total}: {name}...")
            self.progress(int((i / total) * 90))

            try:
                if name == 'Metadata Strip':
                    pass  # applied on save
                elif name == 'Narrowband Candidate Scan':
                    self._spectral_candidates = self._scan_spectral_candidates(audio, sr)
                    if self._spectral_candidates:
                        bands = self._format_spectral_candidates(self._spectral_candidates)
                        self.log(f"    Candidate bands: {bands}")
                    else:
                        self.log("    Candidate bands: none")
                elif name == 'Spectral Perturbation':
                    audio = self._spectral_perturb(audio, sr)
                elif name == 'Dynamic EQ':
                    audio = self._dynamic_eq(audio, sr)
                elif name == 'Coupled Pitch/Tempo Micro-Variation':
                    audio = self._pitch_tempo_coupled_microvar(audio, sr)
                elif name == 'Pitch Micro-Shift':
                    audio = self._pitch_microshift(audio, sr)
                elif name == 'Tempo Micro-Variation':
                    audio = self._tempo_microvar(audio, sr)
                elif name == 'Phase Scrambling':
                    audio = self._phase_scramble(audio, sr)
                elif name == 'Stereo Manipulation':
                    audio = self._stereo_manipulate(audio)
                elif name == 'Noise Injection':
                    audio = self._inject_noise(audio, sr)
                elif name == 'Dynamics Modification':
                    audio = self._modify_dynamics(audio, sr)
                elif name == 'Humanization':
                    audio = self._humanize(audio, sr)
                elif name == 'Lossy Re-encode':
                    audio = self._lossy_reencode(audio, sr, mono)
            except Exception as e:
                self.log(f"    Error: {name} failed ({e}); render aborted")
                self.log(traceback.format_exc().rstrip())
                return finish(
                    RenderState.FAILED,
                    RenderErrorCode.PASS_FAILED,
                    f"{name}: {e}",
                )

        audio = np.clip(audio, -1.0, 1.0)

        # Compute local evidence before the output commit point.
        self.progress(90)
        orig_ch = original[:, 0]
        proc_ch = audio[:, 0]
        n = min(len(orig_ch), len(proc_ch))
        try:
            strength = self._compute_signal_change(orig_ch[:n], proc_ch[:n])
            metric_label = (
                f"{SIGNAL_CHANGE_METRIC['adapter']} "
                f"v{SIGNAL_CHANGE_METRIC['version']}"
            )
            self.log(f"Signal change [{metric_label}]: {strength:.0f}%")
            if strength < 25:
                self.log("  Low sample-domain change")
            elif strength < 50:
                self.log("  Moderate sample-domain change")
            elif strength < 75:
                self.log("  High sample-domain change")
            else:
                self.log("  Very high sample-domain change -- audition output quality")
            self.log(f"  Scope: {EVIDENCE_NOTICE}")

            verifier_result = ConstellationVerifier(self).score(
                orig_ch[:n],
                proc_ch[:n],
                sr,
            )
            self._verifier_results = [verifier_result.to_dict()]
            self.log(format_verifier_result(verifier_result))
        except Exception as exc:
            self.log(f"  Evidence error: {exc}")
            self.log(traceback.format_exc().rstrip())
            return finish(
                RenderState.FAILED,
                RenderErrorCode.UNEXPECTED,
                f"evidence computation failed: {exc}",
            )

        # Encode to a same-directory temp file, validate it, then promote.
        self.log(f"Saving {Path(output_path).name}...")
        self.progress(92)

        save_audio = audio[:, 0] if mono else audio
        tmp_output = None
        validation = None
        sidecar_core = None
        sidecar_payload_sha256 = None
        try:
            if self._is_cancelled():
                self.log("Cancelled.")
                return finish(
                    RenderState.CANCELLED,
                    RenderErrorCode.CANCELLED,
                    "cancelled before output encoding",
                )
            tmp_output = _make_atomic_output_temp(output_path)
            if _format_requires_ffmpeg(fmt):
                if not _check_ffmpeg():
                    message = f"{fmt.upper()} export requires ffmpeg in PATH"
                    self.log(f"  Save error: {message}")
                    return finish(
                        RenderState.FAILED,
                        RenderErrorCode.ENCODER_UNAVAILABLE,
                        message,
                    )
                if not _ffmpeg_encoder_available(fmt):
                    encoder = FFMPEG_FORMAT_ENCODERS.get(fmt, fmt)
                    message = (
                        f"ffmpeg lacks {encoder} encoder for {fmt.upper()} export"
                    )
                    self.log(f"  Save error: {message}")
                    return finish(
                        RenderState.FAILED,
                        RenderErrorCode.ENCODER_UNAVAILABLE,
                        message,
                    )
                self._export_with_ffmpeg(save_audio, sr, tmp_output, fmt)
            elif fmt == 'flac':
                sf.write(tmp_output, save_audio, sr, format='FLAC')
            elif fmt == 'ogg':
                sf.write(tmp_output, save_audio, sr, format='OGG', subtype='VORBIS')
            else:
                sf.write(tmp_output, save_audio, sr, subtype='PCM_24')

            if self.params.get('strip_metadata', True):
                self._strip_metadata(tmp_output)

            pre_binding_validation = _validate_render_output(
                input_path=input_path,
                encoded_path=tmp_output,
                output_path=output_path,
                fmt=fmt,
                expected_sample_rate=sr,
                expected_channels=1 if mono else int(audio.shape[1]),
                expected_frames=int(audio.shape[0]),
            )
            sidecar_core = self._build_sidecar_core(
                input_path,
                output_path,
                sr,
                pass_names,
                strength,
                pre_binding_validation.input_sha256,
                fmt,
            )
            sidecar_payload_sha256 = _canonical_payload_sha256(
                _sidecar_binding_payload(sidecar_core)
            )
            _write_audio_sidecar_binding(
                tmp_output,
                sidecar_payload_sha256,
            )
            _fsync_file(tmp_output)

            self.progress(96)
            validation = _validate_render_output(
                input_path=input_path,
                encoded_path=tmp_output,
                output_path=output_path,
                fmt=fmt,
                expected_sample_rate=sr,
                expected_channels=1 if mono else int(audio.shape[1]),
                expected_frames=int(audio.shape[0]),
            )
            self.log(
                "  Output validated: "
                f"{validation.frames} frames, {validation.sample_rate_hz} Hz, "
                f"{validation.channels} channel(s), "
                f"sha256:{validation.output_sha256[:12]}"
            )
            self.log(
                "  Replay evidence bound: "
                f"sidecar payload sha256:{sidecar_payload_sha256[:12]}"
            )

            if self._is_cancelled():
                self.log("Cancelled.")
                return finish(
                    RenderState.CANCELLED,
                    RenderErrorCode.CANCELLED,
                    "cancelled before validated output promotion",
                )

            _promote_file_no_replace(tmp_output, output_path)
            tmp_output = None
        except OutputValidationError as exc:
            self.log(f"  Output validation failed [{exc.code.value}]: {exc}")
            return finish(RenderState.FAILED, exc.code, str(exc))
        except Exception as e:
            self.log(f"  Save error: {e}")
            self.log(traceback.format_exc().rstrip())
            return finish(
                RenderState.FAILED,
                RenderErrorCode.OUTPUT_WRITE_FAILED,
                str(e),
            )
        finally:
            _remove_file_silent(tmp_output)

        self.progress(99)
        sidecar_evidence = self._write_sidecar(
            output_path,
            validation,
            sidecar_core,
            sidecar_payload_sha256,
        )
        if not sidecar_evidence:
            return finish(
                RenderState.PARTIAL,
                RenderErrorCode.SIDECAR_WRITE_FAILED,
                "validated audio was promoted, but its sidecar could not be written",
                validation,
            )

        artifacts = ()
        try:
            artifacts += self._write_spectrogram_artifact(
                output_path,
                original,
                audio,
                sr,
            )
            artifacts += self._write_loudness_artifact(
                output_path,
                original,
                audio,
                sr,
            )
        except Exception as exc:
            self.log(f"  Audit artifact export failed: {exc}")
            self.log(traceback.format_exc().rstrip())
            return finish(
                RenderState.PARTIAL,
                RenderErrorCode.AUDIT_ARTIFACT_FAILED,
                f"validated audio and sidecar exist, but audit artifact "
                f"export failed: {exc}",
                validation,
                sidecar_path=sidecar_evidence["path"],
                sidecar_sha256=sidecar_evidence["sha256"],
                artifacts=artifacts,
            )

        self.progress(100)
        return finish(
            RenderState.SUCCEEDED,
            validation=validation,
            sidecar_path=sidecar_evidence["path"],
            sidecar_sha256=sidecar_evidence["sha256"],
            artifacts=artifacts,
        )

    @staticmethod
    def _close_stream_map(mapped):
        mmap_handle = getattr(mapped, "_mmap", None)
        if mmap_handle is not None:
            mmap_handle.close()

    def _stream_pass_names(self, mono):
        names = []
        if self.params.get("strip_metadata", True):
            names.append("Metadata Strip")
        if self.params.get("spectral_enabled"):
            if self.params.get(
                "spectral_scan_enabled",
                self.params.get("watermark_scan_enabled", True),
            ):
                names.append("Narrowband Candidate Scan")
            names.append("Spectral Perturbation")
        if self.params.get("dynamic_eq_enabled"):
            names.append("Dynamic EQ")
        pitch_enabled = self.params.get("pitch_enabled")
        tempo_enabled = self.params.get("tempo_enabled")
        if pitch_enabled and tempo_enabled:
            names.append("Coupled Pitch/Tempo Micro-Variation")
        else:
            if pitch_enabled:
                names.append("Pitch Micro-Shift")
            if tempo_enabled:
                names.append("Tempo Micro-Variation")
        if self.params.get("phase_enabled"):
            names.append("Phase Scrambling")
        if self.params.get("stereo_enabled") and not mono:
            names.append("Stereo Manipulation")
        if self.params.get("noise_enabled"):
            names.append("Noise Injection")
        if self.params.get("dynamics_enabled"):
            names.append("Dynamics Modification")
        if self.params.get("humanize_enabled"):
            names.append("Humanization")
        if self.params.get("reencode_enabled"):
            names.append("Lossy Re-encode")
        return names

    def _stream_apply_pass(self, name, audio, sample_rate, mono):
        if name == "Spectral Perturbation":
            return self._spectral_perturb(audio, sample_rate)
        if name == "Dynamic EQ":
            return self._dynamic_eq(audio, sample_rate)
        if name == "Coupled Pitch/Tempo Micro-Variation":
            return self._pitch_tempo_coupled_microvar(audio, sample_rate)
        if name == "Pitch Micro-Shift":
            return self._pitch_microshift(audio, sample_rate)
        if name == "Tempo Micro-Variation":
            return self._tempo_microvar(audio, sample_rate)
        if name == "Phase Scrambling":
            return self._phase_scramble(audio, sample_rate)
        if name == "Stereo Manipulation":
            return self._stereo_manipulate(audio)
        if name == "Noise Injection":
            return self._inject_noise(audio, sample_rate)
        if name == "Dynamics Modification":
            return self._modify_dynamics(audio, sample_rate)
        if name == "Humanization":
            return self._humanize(audio, sample_rate)
        if name == "Lossy Re-encode":
            return self._lossy_reencode(audio, sample_rate, mono)
        raise ValueError(f"unsupported streaming pass: {name}")

    @staticmethod
    def _stream_pass_trace_key(name):
        return {
            "Spectral Perturbation": "spectral_perturbation",
            "Dynamic EQ": "dynamic_eq",
            "Coupled Pitch/Tempo Micro-Variation": "coupled_pitch_tempo",
            "Pitch Micro-Shift": "pitch_microshift",
            "Tempo Micro-Variation": "tempo_microvar",
            "Phase Scrambling": "phase_scramble",
            "Stereo Manipulation": "stereo_manipulation",
            "Noise Injection": "noise_injection",
            "Dynamics Modification": "dynamics_modification",
            "Humanization": "humanization",
            "Lossy Re-encode": "lossy_reencode",
        }.get(name)

    @staticmethod
    def _stream_chunk_view(source, start, end):
        block = np.asarray(source[start:end], dtype=np.float64)
        if block.ndim == 1:
            block = block[:, np.newaxis]
        return np.array(block, dtype=np.float64, copy=True)

    def _stream_scan_candidates(self, source, sample_rate, chunk_samples):
        candidates = []
        for start in range(0, source.shape[0], chunk_samples):
            if self._is_cancelled():
                raise DecodeCancelled("streaming render cancelled")
            chunk = self._stream_chunk_view(
                source,
                start,
                min(source.shape[0], start + chunk_samples),
            )
            candidates.extend(
                self._scan_spectral_candidates(chunk, sample_rate)
            )
        selected = []
        for candidate in sorted(
            candidates,
            key=lambda item: item["score"],
            reverse=True,
        ):
            if len(selected) >= SPECTRAL_SCAN_MAX_CANDIDATES:
                break
            if any(
                candidate["low_hz"] <= item["high_hz"]
                and candidate["high_hz"] >= item["low_hz"]
                for item in selected
            ):
                continue
            selected.append(candidate)
        return selected

    def _stream_transform_pass(
        self,
        source,
        target_path,
        weights_path,
        name,
        sample_rate,
        mono,
        chunk_samples,
        overlap_samples,
        pass_index,
        pass_total,
    ):
        frame_count = int(source.shape[0])
        channels = 1 if source.ndim == 1 else int(source.shape[1])
        target_path.unlink(missing_ok=True)
        weights_path.unlink(missing_ok=True)
        target = np.lib.format.open_memmap(
            target_path,
            mode="w+",
            dtype=np.float64,
            shape=(frame_count, channels),
        )
        weights = np.lib.format.open_memmap(
            weights_path,
            mode="w+",
            dtype=np.float32,
            shape=(frame_count,),
        )
        target[:] = 0.0
        weights[:] = 0.0
        step = max(1, chunk_samples - overlap_samples)
        starts = [0]
        while starts[-1] + chunk_samples < frame_count:
            starts.append(starts[-1] + step)
        trace_key = self._stream_pass_trace_key(name)
        chunk_traces = []
        try:
            for chunk_index, start in enumerate(starts):
                if self._is_cancelled():
                    raise DecodeCancelled("streaming render cancelled")
                end = min(frame_count, start + chunk_samples)
                chunk = self._stream_chunk_view(source, start, end)
                processed = np.asarray(
                    self._stream_apply_pass(name, chunk, sample_rate, mono),
                    dtype=np.float64,
                )
                if processed.ndim == 1:
                    processed = processed[:, np.newaxis]
                if processed.shape != chunk.shape:
                    raise ValueError(
                        f"{name} changed streaming chunk shape from "
                        f"{chunk.shape} to {processed.shape}"
                    )
                window = np.ones(end - start, dtype=np.float64)
                fade = min(overlap_samples, len(window) // 2)
                if start > 0 and fade:
                    window[:fade] = np.linspace(0.0, 1.0, fade)
                if end < frame_count and fade:
                    window[-fade:] = np.linspace(1.0, 0.0, fade)
                target[start:end] += processed * window[:, np.newaxis]
                weights[start:end] += window.astype(np.float32)
                chunk_record = {
                    "index": chunk_index,
                    "start": int(start),
                    "end": int(end),
                }
                if trace_key:
                    chunk_record["trace"] = copy.deepcopy(
                        self._trace["passes"].get(trace_key, {})
                    )
                chunk_traces.append(chunk_record)
                fraction = (chunk_index + 1) / max(1, len(starts))
                self.progress(int(((pass_index + fraction) / pass_total) * 88))

            for start in range(0, frame_count, chunk_samples):
                end = min(frame_count, start + chunk_samples)
                denominator = np.maximum(
                    np.asarray(weights[start:end], dtype=np.float64),
                    1e-8,
                )
                target[start:end] = (
                    np.asarray(target[start:end])
                    / denominator[:, np.newaxis]
                )
            target.flush()
            if trace_key:
                self._trace["passes"][trace_key] = {
                    "streaming": True,
                    "chunk_samples": int(chunk_samples),
                    "overlap_samples": int(overlap_samples),
                    "chunks": chunk_traces,
                }
            return target
        except Exception:
            self._close_stream_map(target)
            raise
        finally:
            self._close_stream_map(weights)
            weights_path.unlink(missing_ok=True)

    def _stream_signal_change(
        self,
        original,
        processed,
        chunk_samples,
    ):
        signal_sum = 0.0
        difference_sum = 0.0
        sample_count = 0
        for start in range(0, original.shape[0], chunk_samples):
            end = min(original.shape[0], start + chunk_samples)
            original_block = np.asarray(original[start:end])
            original_channel = (
                original_block
                if original_block.ndim == 1
                else original_block[:, 0]
            )
            processed_block = np.asarray(processed[start:end])
            processed_channel = (
                processed_block
                if processed_block.ndim == 1
                else processed_block[:, 0]
            )
            processed_channel = np.clip(processed_channel, -1.0, 1.0)
            signal_sum += float(np.sum(original_channel ** 2))
            difference_sum += float(np.sum(
                (original_channel - processed_channel) ** 2
            ))
            sample_count += len(original_channel)
        if sample_count == 0 or signal_sum / sample_count < 1e-12:
            return 0.0
        signal_power = signal_sum / sample_count
        difference_power = difference_sum / sample_count + 1e-12
        snr = 10.0 * np.log10(signal_power / difference_power)
        return max(0.0, min(100.0, (40.0 - snr) * 2.5))

    def _export_wav_with_ffmpeg(self, wav_input, output_path, fmt):
        bitrate = max(
            96,
            min(320, int(self.params.get("reencode_bitrate", 192))),
        )
        if fmt == "mp3":
            codec_args = ["-codec:a", "libmp3lame", "-b:a", f"{bitrate}k"]
        elif fmt == "m4a":
            codec_args = ["-codec:a", "aac", "-b:a", f"{bitrate}k"]
        else:
            raise ValueError(f"Unsupported ffmpeg export format: {fmt}")
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(wav_input),
                "-vn",
                *codec_args,
                str(output_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "ffmpeg failed").strip()
            raise RuntimeError(detail)

    def _write_streamed_audio(
        self,
        source,
        sample_rate,
        output_path,
        output_format,
        mono,
        chunk_samples,
        temp_root,
    ):
        channels = (
            1
            if mono or source.ndim == 1
            else int(source.shape[1])
        )
        write_path = output_path
        if _format_requires_ffmpeg(output_format):
            write_path = Path(temp_root) / "stream-export.wav"
            write_path.unlink(missing_ok=True)
            container = "WAV"
            subtype = "PCM_24"
        elif output_format == "flac":
            container = "FLAC"
            subtype = None
        elif output_format == "ogg":
            container = "OGG"
            subtype = "VORBIS"
        else:
            container = "WAV"
            subtype = "PCM_24"
        options = {
            "mode": "w",
            "samplerate": sample_rate,
            "channels": channels,
            "format": container,
        }
        if subtype is not None:
            options["subtype"] = subtype
        with sf.SoundFile(str(write_path), **options) as destination:
            for start in range(0, source.shape[0], chunk_samples):
                if self._is_cancelled():
                    raise DecodeCancelled("streaming render cancelled")
                end = min(source.shape[0], start + chunk_samples)
                block = np.clip(
                    np.asarray(source[start:end], dtype=np.float64),
                    -1.0,
                    1.0,
                )
                if block.ndim == 1:
                    destination.write(block)
                else:
                    destination.write(block[:, 0] if mono else block)
        if _format_requires_ffmpeg(output_format):
            self._export_wav_with_ffmpeg(
                write_path,
                output_path,
                output_format,
            )

    def _process_streaming_decoded(
        self,
        *,
        input_path,
        output_path,
        decoded_path,
        sample_rate,
        output_format,
        finish,
    ):
        tracked_maps = []

        def track(mapped):
            tracked_maps.append(mapped)
            return mapped

        def close_map(mapped):
            tracked_maps[:] = [
                item for item in tracked_maps if item is not mapped
            ]
            self._close_stream_map(mapped)

        def stream_finish(*args, **kwargs):
            for mapped in list(reversed(tracked_maps)):
                close_map(mapped)
            return finish(*args, **kwargs)

        try:
            original = track(np.load(
                decoded_path,
                allow_pickle=False,
                mmap_mode="r",
            ))
            if original.size == 0:
                return stream_finish(
                    RenderState.FAILED,
                    RenderErrorCode.EMPTY_AUDIO,
                    "empty audio file",
                )
            mono = original.ndim == 1
            channels = 1 if mono else int(original.shape[1])
            frames = int(original.shape[0])
            target_samples = max(
                1024,
                STREAMING_WORKING_SET_BYTES // max(8, channels * 8 * 6),
            )
            chunk_samples = min(
                int(self._streaming_chunk_seconds * sample_rate),
                target_samples,
            )
            chunk_samples = max(1024, min(frames, chunk_samples))
            overlap_samples = min(
                int(STREAMING_OVERLAP_SECONDS * sample_rate),
                chunk_samples // 4,
            )
            decoded_bytes = int(self._decode_metadata.get("decoded_bytes", 0))
            disk_required = (
                decoded_bytes * 3
                + frames * 4
                + STREAMING_WORKING_SET_BYTES
            )
            disk_free = shutil.disk_usage(decoded_path.parent).free
            if disk_free < disk_required:
                return stream_finish(
                    RenderState.FAILED,
                    RenderErrorCode.OUTPUT_WRITE_FAILED,
                    "insufficient temporary disk space for bounded streaming "
                    f"({disk_free} bytes free; {disk_required} required)",
                )
            self.log(
                "  Processing mode: bounded streaming "
                f"({_humanize_bytes(chunk_samples * channels * 8)} chunks, "
                f"{overlap_samples / sample_rate:.2f}s overlap)"
            )
            self.log(
                "  Decoder: isolated "
                "libsndfile "
                f"{self._decode_metadata.get('libsndfile_version', 'unknown')}"
            )
            self._decode_metadata["processing_strategy"] = (
                "bounded-overlap-memmap"
            )
            self._decode_metadata["processing_chunk_samples"] = chunk_samples
            self._decode_metadata["processing_overlap_samples"] = (
                overlap_samples
            )
            self._trace["streaming"] = {
                "enabled": True,
                "threshold_bytes": self._streaming_threshold_bytes,
                "chunk_samples": chunk_samples,
                "overlap_samples": overlap_samples,
                "temporary_disk_required_bytes": disk_required,
            }

            pass_names = self._stream_pass_names(mono)
            if not pass_names:
                return stream_finish(
                    RenderState.FAILED,
                    RenderErrorCode.NO_PASSES_ENABLED,
                    "no passes enabled",
                )
            current = original
            transform_names = [
                name
                for name in pass_names
                if name not in {
                    "Metadata Strip",
                    "Narrowband Candidate Scan",
                }
            ]
            transform_index = 0
            for index, name in enumerate(pass_names):
                if self._is_cancelled():
                    return stream_finish(
                        RenderState.CANCELLED,
                        RenderErrorCode.CANCELLED,
                        "streaming render cancelled",
                    )
                self.log(f"  Pass {index + 1}/{len(pass_names)}: {name}...")
                if name == "Metadata Strip":
                    continue
                if name == "Narrowband Candidate Scan":
                    self._spectral_candidates = self._stream_scan_candidates(
                        current,
                        sample_rate,
                        chunk_samples,
                    )
                    if self._spectral_candidates:
                        self.log(
                            "    Candidate bands: "
                            + self._format_spectral_candidates(
                                self._spectral_candidates
                            )
                        )
                    else:
                        self.log("    Candidate bands: none")
                    continue
                target_path = decoded_path.parent / (
                    f"pass-{transform_index % 2}.npy"
                )
                weights_path = decoded_path.parent / "weights.npy"
                transformed = self._stream_transform_pass(
                    current,
                    target_path,
                    weights_path,
                    name,
                    sample_rate,
                    mono,
                    chunk_samples,
                    overlap_samples,
                    transform_index,
                    max(1, len(transform_names)),
                )
                track(transformed)
                if current is not original:
                    close_map(current)
                current = transformed
                transform_index += 1

            strength = self._stream_signal_change(
                original,
                current,
                chunk_samples,
            )
            self.progress(90)
            metric_label = (
                f"{SIGNAL_CHANGE_METRIC['adapter']} "
                f"v{SIGNAL_CHANGE_METRIC['version']}"
            )
            self.log(f"Signal change [{metric_label}]: {strength:.0f}%")
            self.log(f"  Scope: {EVIDENCE_NOTICE}")
            evidence_frames = min(
                frames,
                int(sample_rate * 30.0),
                chunk_samples,
            )
            original_evidence = np.asarray(original[:evidence_frames])
            if original_evidence.ndim == 2:
                original_evidence = original_evidence[:, 0]
            processed_evidence = np.asarray(current[:evidence_frames])
            if processed_evidence.ndim == 2:
                processed_evidence = processed_evidence[:, 0]
            processed_evidence = np.clip(processed_evidence, -1.0, 1.0)
            verifier_result = ConstellationVerifier(self).score(
                original_evidence,
                processed_evidence,
                sample_rate,
            )
            self._verifier_results = [verifier_result.to_dict()]
            self.log(format_verifier_result(verifier_result))

            self.log(f"Saving {Path(output_path).name}...")
            self.progress(92)
            tmp_output = None
            try:
                if _format_requires_ffmpeg(output_format):
                    if not _check_ffmpeg():
                        return stream_finish(
                            RenderState.FAILED,
                            RenderErrorCode.ENCODER_UNAVAILABLE,
                            f"{output_format.upper()} export requires ffmpeg in PATH",
                        )
                    if not _ffmpeg_encoder_available(output_format):
                        return stream_finish(
                            RenderState.FAILED,
                            RenderErrorCode.ENCODER_UNAVAILABLE,
                            f"ffmpeg lacks encoder for {output_format.upper()} export",
                        )
                tmp_output = _make_atomic_output_temp(output_path)
                self._write_streamed_audio(
                    current,
                    sample_rate,
                    tmp_output,
                    output_format,
                    mono,
                    chunk_samples,
                    decoded_path.parent,
                )
                if self.params.get("strip_metadata", True):
                    self._strip_metadata(tmp_output)
                pre_binding_validation = _validate_render_output(
                    input_path=input_path,
                    encoded_path=tmp_output,
                    output_path=output_path,
                    fmt=output_format,
                    expected_sample_rate=sample_rate,
                    expected_channels=channels,
                    expected_frames=frames,
                )
                sidecar_core = self._build_sidecar_core(
                    input_path,
                    output_path,
                    sample_rate,
                    pass_names,
                    strength,
                    pre_binding_validation.input_sha256,
                    output_format,
                )
                sidecar_payload_sha256 = _canonical_payload_sha256(
                    _sidecar_binding_payload(sidecar_core)
                )
                _write_audio_sidecar_binding(
                    tmp_output,
                    sidecar_payload_sha256,
                )
                _fsync_file(tmp_output)
                self.progress(96)
                validation = _validate_render_output(
                    input_path=input_path,
                    encoded_path=tmp_output,
                    output_path=output_path,
                    fmt=output_format,
                    expected_sample_rate=sample_rate,
                    expected_channels=channels,
                    expected_frames=frames,
                )
                if self._is_cancelled():
                    return stream_finish(
                        RenderState.CANCELLED,
                        RenderErrorCode.CANCELLED,
                        "cancelled before validated output promotion",
                    )
                _promote_file_no_replace(tmp_output, output_path)
                tmp_output = None
            except DecodeCancelled:
                return stream_finish(
                    RenderState.CANCELLED,
                    RenderErrorCode.CANCELLED,
                    "streaming render cancelled",
                )
            except OutputValidationError as exc:
                return stream_finish(
                    RenderState.FAILED,
                    exc.code,
                    str(exc),
                )
            except Exception as exc:
                self.log(traceback.format_exc().rstrip())
                return stream_finish(
                    RenderState.FAILED,
                    RenderErrorCode.OUTPUT_WRITE_FAILED,
                    str(exc),
                )
            finally:
                _remove_file_silent(tmp_output)

            self.progress(99)
            sidecar_evidence = self._write_sidecar(
                output_path,
                validation,
                sidecar_core,
                sidecar_payload_sha256,
            )
            if not sidecar_evidence:
                return stream_finish(
                    RenderState.PARTIAL,
                    RenderErrorCode.SIDECAR_WRITE_FAILED,
                    "validated audio was promoted, but its sidecar could not be written",
                    validation,
                )
            artifacts = ()
            try:
                artifacts += self._write_spectrogram_artifact(
                    output_path,
                    original,
                    current,
                    sample_rate,
                )
                artifacts += self._write_loudness_artifact(
                    output_path,
                    original,
                    current,
                    sample_rate,
                )
            except Exception as exc:
                self.log(f"  Audit artifact export failed: {exc}")
                self.log(traceback.format_exc().rstrip())
                return stream_finish(
                    RenderState.PARTIAL,
                    RenderErrorCode.AUDIT_ARTIFACT_FAILED,
                    f"validated audio and sidecar exist, but audit artifact "
                    f"export failed: {exc}",
                    validation,
                    sidecar_path=sidecar_evidence["path"],
                    sidecar_sha256=sidecar_evidence["sha256"],
                    artifacts=artifacts,
                )
            self.progress(100)
            return stream_finish(
                RenderState.SUCCEEDED,
                validation=validation,
                sidecar_path=sidecar_evidence["path"],
                sidecar_sha256=sidecar_evidence["sha256"],
                artifacts=artifacts,
            )
        except DecodeCancelled:
            return stream_finish(
                RenderState.CANCELLED,
                RenderErrorCode.CANCELLED,
                "streaming render cancelled",
            )
        except Exception as exc:
            self.log(f"  Streaming error: {exc}")
            self.log(traceback.format_exc().rstrip())
            return stream_finish(
                RenderState.FAILED,
                RenderErrorCode.PASS_FAILED,
                str(exc),
            )

    def _replay_report(self, fmt):
        nondeterministic_dependencies = []
        compute_evidence = self._compute_backend.evidence
        if compute_evidence.get("accelerated"):
            nondeterministic_dependencies.append({
                "dependency": (
                    f"{compute_evidence.get('library', 'GPU')}/"
                    f"{compute_evidence.get('cuda_runtime', 'CUDA')}"
                ),
                "reason": (
                    "GPU FFT results depend on the exact device, driver, "
                    "compute library, and CUDA runtime"
                ),
            })
        if fmt == 'ogg':
            nondeterministic_dependencies.append({
                "dependency": "libsndfile/libvorbis Ogg muxer",
                "reason": (
                    "Ogg stream serial numbers are generated per encode, so "
                    "container bytes can differ with identical decoded audio"
                ),
            })
        if self.params.get('reencode_enabled'):
            nondeterministic_dependencies.append({
                "dependency": "ffmpeg/libmp3lame",
                "reason": (
                    "the lossy intermediate depends on the exact ffmpeg and "
                    "libmp3lame build"
                ),
            })
        if _format_requires_ffmpeg(fmt):
            encoder = FFMPEG_FORMAT_ENCODERS.get(fmt, fmt)
            nondeterministic_dependencies.append({
                "dependency": f"ffmpeg/{encoder}",
                "reason": (
                    "container and codec bytes depend on the exact ffmpeg "
                    "encoder build"
                ),
            })
        return {
            "status": (
                "dependency_sensitive"
                if nondeterministic_dependencies
                else "byte_reproducible_same_environment"
            ),
            "effective_seed": self._seed,
            "rng_algorithm": type(self.rng.bit_generator).__name__,
            "required_environment": _native_runtime_report(),
            "compute_backend": compute_evidence,
            "nondeterministic_dependencies": nondeterministic_dependencies,
        }

    def _build_sidecar_core(
        self,
        input_path,
        output_path,
        sr,
        pass_names,
        strength,
        input_sha256,
        fmt,
    ):
        return {
            "schema_id": SIDECAR_SCHEMA_ID,
            "schema_version": SIDECAR_SCHEMA_VERSION,
            "sunojump_version": VERSION,
            "seed": self._seed,
            "input_file": Path(input_path).name,
            "input_sha256": input_sha256,
            "output_file": Path(output_path).name,
            "sample_rate": sr,
            "enabled_passes": pass_names,
            "evidence_contract": EVIDENCE_CONTRACT,
            "metrics": {
                "signal_change": {
                    **SIGNAL_CHANGE_METRIC,
                    "value": round(strength, 1),
                },
            },
            "verifiers": self._verifier_results,
            "params": {
                k: v for k, v in self.params.items()
                if not callable(v)
            },
            "environment": {
                "numpy": np.__version__,
                "scipy": scipy.__version__,
                "soundfile": getattr(sf, '__version__', 'unknown'),
                "libsndfile": getattr(sf, '__libsndfile_version__', 'unknown'),
                "decode_policy": _native_runtime_report(),
                "mutagen": getattr(mutagen, 'version_string', 'unknown'),
                "ffmpeg": _ffmpeg_version_line(),
                "compute_backend": self._compute_backend.evidence,
            },
            "decode": self._decode_metadata,
            "source_provenance": self._source_provenance_report(),
            "rng": self._trace.get("rng", {}),
            "passes": self._trace.get("passes", {}),
            "streaming": self._trace.get("streaming", {"enabled": False}),
            "replay": self._replay_report(fmt),
        }

    def _source_provenance_report(self):
        inspection = self._source_provenance
        if inspection is None:
            return {
                "status": "not_inspected",
                "policy": self.params.get(
                    "c2pa_policy",
                    C2PA_POLICY_BLOCK,
                ),
            }
        report = inspection.to_dict()
        report.update({
            "policy": self.params.get(
                "c2pa_policy",
                C2PA_POLICY_BLOCK,
            ),
            "decision": (
                "explicitly_acknowledged_output_without_source_credentials"
                if inspection.present
                else "no_source_manifest_detected"
            ),
            "source_file_modified": False,
            "source_manifest_copied_to_output": False,
            "acoustic_detector_evidence": False,
            "output_effect": (
                "Transformed output is re-encoded without copying or "
                "re-signing source Content Credentials."
                if inspection.present
                else "No source Content Credentials were located."
            ),
        })
        return report

    def _write_sidecar(
        self,
        output_path,
        validation,
        sidecar_core,
        sidecar_payload_sha256,
    ):
        sidecar = {
            **sidecar_core,
            "output_sha256": validation.output_sha256,
            "output_validation": validation.to_dict(),
            "binding": {
                "algorithm": "sha256",
                "audio_file_sha256": validation.output_sha256,
                "audio_tag": SIDECAR_AUDIO_TAG,
                "sidecar_payload_sha256": sidecar_payload_sha256,
                "sidecar_payload_scope": (
                    "canonical-json-sidecar-core-excluding-output-file"
                ),
            },
        }
        sidecar_path = _sidecar_path_for_output(output_path)
        try:
            sidecar_sha256 = _write_json_atomic_no_replace(
                sidecar_path,
                sidecar,
            )
            self.log(
                f"Sidecar written atomically: {sidecar_path.name} "
                f"(sha256:{sidecar_sha256[:12]})"
            )
            return {
                "path": str(sidecar_path),
                "sha256": sidecar_sha256,
            }
        except Exception as e:
            self.log(f"  Warning: sidecar write failed: {e}")
            return False

    def _trace_segments(self, pass_name, segments):
        trace = self._trace["passes"].get(pass_name)
        if trace is None:
            trace = self._trace_random(
                pass_name,
                ["uniform segment control or factor"],
            )
        trace.update({
            "segment_count": len(segments),
            "segments": segments,
        })

    # --- Metadata ---
    def _strip_metadata(self, filepath):
        removed = []
        retained = []
        tag_type = "unknown"
        try:
            f = MutagenFile(filepath)
            if f is None:
                self.log("    Metadata: no recognized tag container")
                self._trace["passes"]["metadata_strip"] = {
                    "status": "no_container",
                    "removed": [],
                    "retained": [],
                }
                return
            before_keys = sorted(set(f.keys())) if hasattr(f, 'keys') else []
            tag_type = type(f.tags).__name__ if f.tags else "none"
            f.delete()
            f.save()
            removed = before_keys
            if removed:
                self.log(f"    Metadata stripped: {len(removed)} tag(s) from {tag_type}")
            else:
                self.log(f"    Metadata: no tags present ({tag_type})")
        except Exception as e:
            retained = removed or ["unknown"]
            removed = []
            self.log(f"    Warning: metadata strip failed ({e})")
        self._trace["passes"]["metadata_strip"] = {
            "status": "ok" if not retained else "failed",
            "tag_type": tag_type,
            "removed": removed,
            "retained": retained,
        }
        if retained:
            self.log(
                "    Note: metadata stripping does not guarantee removal of "
                "acoustic fingerprints or signed provenance"
            )

    def _export_with_ffmpeg(self, audio, sr, output_path, fmt):
        tmp_dir = tempfile.mkdtemp(prefix='sunojump_export_')
        wav_in = os.path.join(tmp_dir, 'export.wav')
        bitrate = int(self.params.get('reencode_bitrate', 192))
        bitrate = max(96, min(320, bitrate))
        try:
            sf.write(wav_in, audio, sr, subtype='PCM_24')
            if fmt == 'mp3':
                codec_args = ['-codec:a', 'libmp3lame', '-b:a', f'{bitrate}k']
            elif fmt == 'm4a':
                codec_args = ['-codec:a', 'aac', '-b:a', f'{bitrate}k']
            else:
                raise ValueError(f"Unsupported ffmpeg export format: {fmt}")
            cmd = [
                'ffmpeg', '-y', '-loglevel', 'error', '-i', wav_in,
                '-vn', *codec_args, output_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or 'ffmpeg failed').strip()
                raise RuntimeError(detail)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # --- Narrowband spectral candidate scan pre-pass ---
    def _scan_spectral_candidates(self, audio, sr):
        if audio.shape[0] < 1024:
            return []

        mono = audio[:, 0] if audio.shape[1] == 1 else np.mean(audio, axis=1)
        n = len(mono)
        nperseg = min(4096, 1 << (n.bit_length() - 1))
        if nperseg < 512:
            return []
        noverlap = int(nperseg * 0.75)

        try:
            f, _, Zxx = self._compute_backend.stft(
                mono,
                sr,
                nperseg=nperseg,
                noverlap=noverlap,
            )
        except Exception:
            return []

        mag = np.abs(Zxx)
        if mag.shape[1] < 2:
            return []

        eps = 1e-12
        median_mag = np.median(mag, axis=1)
        mean_mag = np.mean(mag, axis=1)
        std_mag = np.std(mag, axis=1)

        kernel = min(51, (len(median_mag) // 2) * 2 - 1)
        if kernel < 5:
            return []
        floor = signal.medfilt(median_mag, kernel_size=kernel)
        floor = np.maximum(floor, eps)

        excess_db = 20.0 * np.log10(np.maximum(median_mag, eps) / floor)
        stability = 1.0 / (1.0 + (std_mag / np.maximum(mean_mag, eps)))

        focus = np.full_like(f, 0.75, dtype=np.float64)
        focus[(f >= 20.0) & (f <= 120.0)] = 1.25
        focus[f >= 8000.0] = 1.35

        score = excess_db * stability * focus
        valid = (f >= 20.0) & (f <= min(sr / 2.0, 20000.0)) & (excess_db >= 5.0)
        score = np.where(valid, score, -np.inf)

        candidates = []
        for idx in np.argsort(score)[::-1]:
            if len(candidates) >= SPECTRAL_SCAN_MAX_CANDIDATES:
                break
            if not np.isfinite(score[idx]) or score[idx] <= 2.0:
                break

            center = float(f[idx])
            half_width = max(25.0, center * 0.012)
            low_hz = max(20.0, center - half_width)
            high_hz = min(sr / 2.0, center + half_width)
            overlaps = any(
                low_hz <= existing['high_hz'] and high_hz >= existing['low_hz']
                for existing in candidates
            )
            if overlaps:
                continue

            candidates.append({
                'center_hz': center,
                'low_hz': low_hz,
                'high_hz': high_hz,
                'score': float(score[idx]),
            })

        return candidates

    def _format_spectral_candidates(self, candidates):
        labels = []
        for cand in candidates:
            center = cand['center_hz']
            if center >= 1000.0:
                labels.append(f"{center / 1000.0:.1f}kHz")
            else:
                labels.append(f"{center:.0f}Hz")
        return ", ".join(labels)

    # --- Spectral perturbation (non-uniform across segments) ---
    def _spectral_perturb(self, audio, sr):
        """Vary perturbation across independently randomized track segments."""
        strength = self.params.get('spectral_strength', 0.3)
        n = audio.shape[0]
        base_seg_samples = int(3.0 * sr)
        overlap = int(0.1 * sr)
        trace = self._trace_random(
            "spectral_perturbation",
            [
                "uniform segment duration 2.4-3.6 seconds",
                "uniform STFT window choice from 1024/2048/4096",
                "normal magnitude multiplier",
                "uniform band magnitude multiplier",
            ],
            strength=float(strength),
            channels=int(audio.shape[1]),
            segment_overlap_samples=overlap,
            candidate_bands=copy.deepcopy(self._spectral_candidates),
        )
        segments = []

        # Short audio: single pass (no segmentation benefit)
        if n <= base_seg_samples:
            nperseg = self._choose_spectral_window(n)
            segments.append({
                "start": 0,
                "end": int(n),
                "nperseg": int(nperseg),
            })
            trace["segment_count"] = 1
            trace["segments"] = segments
            result = np.zeros_like(audio)
            for ch in range(audio.shape[1]):
                result[:, ch] = self._spectral_perturb_ch(
                    audio[:, ch], sr, strength, nperseg=nperseg,
                )
            return result

        result = np.zeros_like(audio)
        weights = np.zeros(n)
        pos = 0
        while pos < n:
            seg_samples = int(self.rng.uniform(2.4, 3.6) * sr)
            end = min(pos + seg_samples, n)
            chunk = audio[pos:end]
            clen = end - pos
            nperseg = self._choose_spectral_window(clen)
            segments.append({
                "start": int(pos),
                "end": int(end),
                "nperseg": int(nperseg),
            })

            processed = np.zeros_like(chunk)
            for ch in range(chunk.shape[1]):
                processed[:, ch] = self._spectral_perturb_ch(
                    chunk[:, ch], sr, strength, nperseg=nperseg,
                )

            # Crossfade window to avoid seams
            win = np.ones(clen)
            fl = min(overlap, clen // 2)
            if pos > 0 and fl > 0:
                win[:fl] = np.linspace(0, 1, fl)
            if end < n and fl > 0:
                win[-fl:] = np.linspace(1, 0, fl)

            result[pos:end] += processed * win[:, np.newaxis]
            weights[pos:end] += win
            pos += max(1, clen - overlap)

        weights = np.maximum(weights, 1e-8)
        trace["segment_count"] = len(segments)
        trace["segments"] = segments
        return result / weights[:, np.newaxis]

    def _choose_spectral_window(self, length):
        valid = [w for w in (1024, 2048, 4096) if length >= w]
        if valid:
            return int(self.rng.choice(valid))
        return _nperseg_for(length)

    def _spectral_perturb_ch(self, channel, sr, strength, nperseg=None):
        nperseg = nperseg or _nperseg_for(len(channel))
        if nperseg == 0:
            return channel.copy()
        noverlap = nperseg // 2

        f, t, Zxx = self._compute_backend.stft(
            channel,
            sr,
            nperseg=nperseg,
            noverlap=noverlap,
        )
        mag = np.abs(Zxx)
        phase = np.angle(Zxx)

        noise = self.rng.normal(1.0, strength * 0.05, mag.shape)
        mag *= np.maximum(noise, 0.01)

        for key, low_hz, high_hz, depth in SPECTRAL_BANDS:
            band_strength = self._spectral_band_strength(key, strength)
            if band_strength <= 0.0:
                continue
            if high_hz is None:
                band_mask = f >= low_hz
            else:
                band_mask = (f >= low_hz) & (f <= high_hz)
            if np.any(band_mask):
                mag[band_mask] *= self.rng.uniform(
                    1.0 - band_strength * depth,
                    1.0 + band_strength * depth,
                    mag[band_mask].shape,
                )

        scan_strength = max(
            [strength] + [
                self._spectral_band_strength(key, strength)
                for key, _, _, _ in SPECTRAL_BANDS
            ],
        )
        if scan_strength > 0.0:
            for cand in self._spectral_candidates:
                band_mask = (f >= cand['low_hz']) & (f <= cand['high_hz'])
                if np.any(band_mask):
                    depth = min(0.55, 0.30 + cand['score'] * 0.01)
                    mag[band_mask] *= self.rng.uniform(
                        1.0 - scan_strength * depth,
                        1.0 + scan_strength * depth,
                        mag[band_mask].shape,
                    )

        Zxx_new = mag * np.exp(1j * phase)
        _, result = self._compute_backend.istft(
            Zxx_new,
            sr,
            nperseg=nperseg,
            noverlap=noverlap,
        )

        orig_len = len(channel)
        if len(result) > orig_len:
            result = result[:orig_len]
        elif len(result) < orig_len:
            result = np.pad(result, (0, orig_len - len(result)))
        return result

    def _spectral_band_strength(self, key, fallback):
        if not self.params.get(f'{key}_enabled', True):
            return 0.0
        try:
            value = float(self.params.get(f'{key}_strength', fallback))
        except (TypeError, ValueError):
            value = fallback
        return float(np.clip(value, 0.0, 1.0))

    # --- Dynamic EQ with loudness-preserving gain staging ---
    def _dynamic_eq(self, audio, sr):
        amount = self.params.get('dynamic_eq_amount', 0.2)
        self._trace_random(
            "dynamic_eq",
            ["normal per-band STFT-frame jitter, median filtered"],
            amount=float(amount),
            channels=int(audio.shape[1]),
            nperseg=int(_nperseg_for(audio.shape[0])),
            bands=[
                {
                    "low_hz": low_hz,
                    "high_hz": high_hz,
                    "max_db": max_db,
                }
                for low_hz, high_hz, max_db in DYNAMIC_EQ_BANDS
            ],
        )
        if amount < 0.001:
            return audio

        reference = audio.copy()
        result = np.zeros_like(audio)
        for ch in range(audio.shape[1]):
            result[:, ch] = self._dynamic_eq_ch(audio[:, ch], sr, amount)

        return self._match_lufs(result, reference, sr)

    def _dynamic_eq_ch(self, channel, sr, amount):
        nperseg = _nperseg_for(len(channel))
        if nperseg == 0:
            return channel.copy()
        noverlap = nperseg // 2

        f, _, Zxx = self._compute_backend.stft(
            channel,
            sr,
            nperseg=nperseg,
            noverlap=noverlap,
        )
        mag = np.abs(Zxx)
        phase = np.angle(Zxx)
        eps = 1e-12

        for low_hz, high_hz, max_db in DYNAMIC_EQ_BANDS:
            if high_hz is None:
                band_mask = f >= low_hz
            else:
                band_mask = (f >= low_hz) & (f < high_hz)
            if not np.any(band_mask):
                continue

            band_energy = np.mean(mag[band_mask], axis=0)
            median = np.median(band_energy) + eps
            relative_db = 20.0 * np.log10(np.maximum(band_energy, eps) / median)
            gain_db = -np.tanh(relative_db / 10.0) * max_db * amount

            if len(gain_db) > 2:
                jitter = self.rng.normal(0.0, 0.18 * amount, len(gain_db))
                kernel = min(9, (len(gain_db) // 2) * 2 - 1)
                if kernel >= 3:
                    jitter = signal.medfilt(jitter, kernel_size=kernel)
                gain_db += jitter

            gain = 10.0 ** (gain_db / 20.0)
            mag[band_mask] *= gain[np.newaxis, :]

        Zxx_new = mag * np.exp(1j * phase)
        _, result = self._compute_backend.istft(
            Zxx_new,
            sr,
            nperseg=nperseg,
            noverlap=noverlap,
        )
        if len(result) > len(channel):
            result = result[:len(channel)]
        elif len(result) < len(channel):
            result = np.pad(result, (0, len(channel) - len(result)))
        return result

    def _integrated_lufs(self, audio, sr):
        if audio.size == 0:
            return -np.inf
        if audio.ndim == 1:
            work = audio[:, np.newaxis]
        else:
            work = audio

        try:
            sos = signal.butter(2, 60.0, btype='highpass', fs=sr, output='sos')
            weighted = signal.sosfilt(sos, work, axis=0)
        except Exception:
            weighted = work

        mean_square = np.mean(np.square(weighted), axis=0)
        loudness_power = float(np.mean(mean_square))
        if loudness_power <= 1e-12:
            return -np.inf
        return -0.691 + 10.0 * np.log10(loudness_power)

    def _match_lufs(self, audio, reference, sr):
        ref_lufs = self._integrated_lufs(reference, sr)
        audio_lufs = self._integrated_lufs(audio, sr)
        if not np.isfinite(ref_lufs) or not np.isfinite(audio_lufs):
            return audio

        gain_db = float(np.clip(ref_lufs - audio_lufs, -6.0, 6.0))
        matched = audio * (10.0 ** (gain_db / 20.0))
        peak = float(np.max(np.abs(matched))) if matched.size else 0.0
        if peak > 0.98:
            matched = matched * (0.98 / peak)
        return matched

    # --- Coupled pitch + tempo micro-variation ---
    def _pitch_tempo_coupled_microvar(self, audio, sr):
        """Share one non-uniform segment curve across pitch and timing.

        Independent pitch and tempo passes can move transient emphasis in
        unrelated ways. This coupled path keeps every segment's start/end
        aligned, then applies a small in-segment timing warp and pitch shift
        from the same random control value so beats stay anchored while the
        rendered signal still varies across the track.
        """
        max_st = self.params.get('pitch_range', 0.8)
        max_var = self.params.get('tempo_range', 0.05)
        self._trace_random(
            "coupled_pitch_tempo",
            ["uniform segment control -1.0 to 1.0 with 0.15 floor"],
            pitch_range_semitones=float(max_st),
            tempo_range=float(max_var),
            segment_seconds=2.5,
            overlap_seconds=0.12,
        )
        if max_st < 0.001 and max_var < 0.001:
            return audio

        n = audio.shape[0]
        seg_samples = int(2.5 * sr)
        overlap = int(0.12 * sr)
        hop = max(1, seg_samples - overlap)

        segments = []
        if n < seg_samples:
            control = self._nonzero_segment_control()
            segments.append({"start": 0, "end": n, "control": round(control, 6)})
            self._trace_segments("coupled_pitch_tempo", segments)
            return self._apply_coupled_variation_chunk(audio, sr, control, max_st, max_var)

        result = np.zeros_like(audio)
        weights = np.zeros(n)
        pos = 0
        while pos < n:
            if self._is_cancelled():
                return audio

            end = min(pos + seg_samples, n)
            chunk = audio[pos:end]
            clen = end - pos
            if clen < int(0.25 * sr):
                win = np.ones(clen)
                if pos > 0:
                    fl = min(overlap, clen)
                    win[:fl] = np.linspace(0, 1, fl)
                result[pos:end] += chunk * win[:, np.newaxis]
                weights[pos:end] += win
                break

            control = self._nonzero_segment_control()
            segments.append({"start": pos, "end": end, "control": round(control, 6)})
            varied = self._apply_coupled_variation_chunk(chunk, sr, control, max_st, max_var)
            varied = self._fit_audio_length(varied, clen)

            win = np.ones(clen)
            fl = min(overlap, clen // 2)
            if pos > 0 and fl > 0:
                win[:fl] = np.linspace(0, 1, fl)
            if end < n and fl > 0:
                win[-fl:] = np.linspace(1, 0, fl)

            result[pos:end] += varied * win[:, np.newaxis]
            weights[pos:end] += win
            pos += hop

        self._trace_segments("coupled_pitch_tempo", segments)
        weights = np.maximum(weights, 1e-8)
        return result / weights[:, np.newaxis]

    def _nonzero_segment_control(self):
        control = float(self.rng.uniform(-1.0, 1.0))
        if abs(control) < 0.15:
            control = 0.15 if control >= 0 else -0.15
        return control

    def _apply_coupled_variation_chunk(self, chunk, sr, control, max_st, max_var):
        varied = chunk
        if max_var >= 0.001:
            varied = self._tempo_warp_aligned_chunk(varied, control * max_var)
        if max_st >= 0.001:
            semitones = control * max_st
            varied = self._pv_pitch_shift_multi(varied, sr, semitones)
        return self._fit_audio_length(varied, chunk.shape[0])

    def _tempo_warp_aligned_chunk(self, chunk, amount):
        n = chunk.shape[0]
        if n < 4 or abs(amount) < 0.0001:
            return chunk.copy()

        x_norm = np.linspace(0.0, 1.0, n, dtype=np.float64)
        # Endpoints remain fixed. The derivative varies about +/- amount,
        # creating local tempo drift without moving segment boundaries.
        displacement = (amount * n / (2.0 * np.pi)) * np.sin(2.0 * np.pi * x_norm)
        src_idx = np.arange(n, dtype=np.float64) + displacement
        np.clip(src_idx, 0.0, n - 1.0, out=src_idx)

        result = np.empty_like(chunk)
        x = np.arange(n, dtype=np.float64)
        for ch in range(chunk.shape[1]):
            result[:, ch] = np.interp(src_idx, x, chunk[:, ch])
        return result

    def _fit_audio_length(self, audio, length):
        if audio.shape[0] == length:
            return audio
        if audio.shape[0] > length:
            return audio[:length]
        pad = np.zeros((length - audio.shape[0], audio.shape[1]), dtype=audio.dtype)
        return np.concatenate([audio, pad])

    # --- Non-uniform pitch micro-shift (phase vocoder, preserves tempo) ---
    def _pitch_microshift(self, audio, sr):
        """Apply a different random pitch shift to each ~2.5s segment, using
        a phase-vocoder-based pitch shifter that preserves segment duration
        (so tempo isn't altered, unlike a raw time-warp approach). Avoids the
        audible warble that plain time-warping causes at large shifts (>1 st).
        """
        max_st = self.params.get('pitch_range', 0.8)
        self._trace_random(
            "pitch_microshift",
            ["uniform pitch shift within configured semitone range"],
            pitch_range_semitones=float(max_st),
            segment_seconds=2.5,
            overlap_seconds=0.12,
        )
        if max_st < 0.001:
            return audio

        n = audio.shape[0]
        seg_samples = int(2.5 * sr)
        overlap = int(0.12 * sr)  # 120ms crossfade -- generous for PV boundaries
        hop = max(1, seg_samples - overlap)

        segments = []
        if n < seg_samples:
            shift = float(self.rng.uniform(-max_st, max_st))
            segments.append({"start": 0, "end": n, "shift_st": round(shift, 6)})
            self._trace_segments("pitch_microshift", segments)
            return self._pv_pitch_shift_multi(audio, sr, shift)

        result = np.zeros_like(audio)
        weights = np.zeros(n)
        pos = 0
        while pos < n:
            end = min(pos + seg_samples, n)
            chunk = audio[pos:end]
            clen = end - pos
            if clen < int(0.25 * sr):
                win = np.ones(clen)
                if pos > 0:
                    fl = min(overlap, clen)
                    win[:fl] = np.linspace(0, 1, fl)
                result[pos:end] += chunk * win[:, np.newaxis]
                weights[pos:end] += win
                break

            shift = float(self.rng.uniform(-max_st, max_st))
            segments.append({"start": pos, "end": end, "shift_st": round(shift, 6)})
            shifted = self._pv_pitch_shift_multi(chunk, sr, shift)

            if shifted.shape[0] != clen:
                if shifted.shape[0] > clen:
                    shifted = shifted[:clen]
                else:
                    pad = np.zeros((clen - shifted.shape[0], shifted.shape[1]))
                    shifted = np.concatenate([shifted, pad])

            win = np.ones(clen)
            fl = min(overlap, clen // 2)
            if pos > 0 and fl > 0:
                win[:fl] = np.linspace(0, 1, fl)
            if end < n and fl > 0:
                win[-fl:] = np.linspace(1, 0, fl)

            result[pos:end] += shifted * win[:, np.newaxis]
            weights[pos:end] += win
            pos += hop

        self._trace_segments("pitch_microshift", segments)
        weights = np.maximum(weights, 1e-8)
        return result / weights[:, np.newaxis]

    # --- Phase vocoder primitives (pure scipy, no librosa dependency) ---
    def _pv_pitch_shift_multi(self, audio, sr, semitones):
        """Pitch shift a multi-channel (or mono) array preserving duration."""
        if abs(semitones) < 0.01:
            return audio.copy()
        if audio.ndim == 1:
            return self._pv_pitch_shift(audio, sr, semitones)
        out = np.zeros_like(audio)
        for ch in range(audio.shape[1]):
            out[:, ch] = self._pv_pitch_shift(audio[:, ch], sr, semitones)
        return out

    def _pv_pitch_shift(self, signal_1d, sr, semitones):
        """Pitch-shift a 1D signal preserving original length.

        Method: resample to alter pitch (which also alters duration), then
        phase-vocoder time-stretch back to the original length. The phase
        vocoder propagates phase by the measured instantaneous frequency
        rather than the bin frequency, avoiding the phasiness that naive
        magnitude interpolation produces.
        """
        n = len(signal_1d)
        if n < 256:
            return signal_1d.copy()

        factor = 2.0 ** (semitones / 12.0)

        # Step 1: resample -- pitch changes, length changes inversely
        intermediate_n = max(128, int(n / factor))
        try:
            pitched = signal.resample(signal_1d, intermediate_n)
        except Exception:
            return signal_1d.copy()

        # Step 2: PV time-stretch by `factor` so len returns to n
        stretched = self._pv_time_stretch(pitched, factor)

        # Step 3: length-correct (PV is approximate)
        if len(stretched) > n:
            stretched = stretched[:n]
        elif len(stretched) < n:
            stretched = np.pad(stretched, (0, n - len(stretched)))
        return stretched

    def _pv_time_stretch(self, signal_1d, rate, nperseg=2048):
        """Phase-vocoder time stretch of a 1D signal.

        `rate` is the stretch factor relative to the input:
          rate > 1  -> output is rate x LONGER
          rate < 1  -> output is rate x SHORTER
          rate = 1  -> unchanged length (frame-exact reconstruction)

        Reads each STFT frame at fractional positions, blends magnitudes
        linearly, and integrates phase from measured instantaneous
        frequency. Good quality for rates 0.5-2.0; we only use ~0.9-1.1
        in the per-segment pitch shifter.
        """
        if rate <= 0:
            return signal_1d.copy()
        if len(signal_1d) < nperseg:
            nperseg = max(64, 1 << (len(signal_1d).bit_length() - 1))
            if nperseg < 64:
                return signal_1d.copy()
        hop = nperseg // 4

        _, _, Z = self._compute_backend.stft(
            signal_1d,
            nperseg=nperseg,
            noverlap=nperseg - hop,
        )
        n_bins, n_frames = Z.shape
        if n_frames < 2:
            return signal_1d.copy()

        # Output frame count scales directly with stretch factor.
        n_out_frames = max(1, int(np.ceil(n_frames * rate)))
        # Bin-frequency phase advance per hop (rad)
        phi_advance = np.arange(n_bins) * 2.0 * np.pi * hop / nperseg

        Z_out = np.zeros((n_bins, n_out_frames), dtype=Z.dtype)
        phase_acc = np.angle(Z[:, 0])

        for i in range(n_out_frames):
            # Read fractional source frame so `rate` hops of output equal 1
            # hop of input movement. rate=2 -> each output frame advances
            # source by 0.5 -> stretching.
            step = i / rate
            idx = int(step)
            if idx >= n_frames - 1:
                break
            frac = step - idx
            col0 = Z[:, idx]
            col1 = Z[:, idx + 1]

            mag = (1.0 - frac) * np.abs(col0) + frac * np.abs(col1)
            Z_out[:, i] = mag * np.exp(1j * phase_acc)

            # Measured phase advance, wrap to [-pi, pi]
            dphase = np.angle(col1) - np.angle(col0) - phi_advance
            dphase = np.mod(dphase + np.pi, 2.0 * np.pi) - np.pi
            phase_acc = phase_acc + phi_advance + dphase

        _, result = self._compute_backend.istft(
            Z_out,
            nperseg=nperseg,
            noverlap=nperseg - hop,
        )
        return result

    # --- Non-uniform tempo micro-variation ---
    def _tempo_microvar(self, audio, sr):
        max_var = self.params.get('tempo_range', 0.05)
        self._trace_random(
            "tempo_microvar",
            ["uniform per-segment tempo factor within configured range"],
            tempo_range=float(max_var),
            segment_seconds=2.5,
        )
        if max_var < 0.001:
            return audio

        n = audio.shape[0]
        seg_samples = int(2.5 * sr)
        n_segments = max(1, n // seg_samples)

        factors = self.rng.uniform(1.0 - max_var, 1.0 + max_var, n_segments)

        segments = []
        seg_size = n / n_segments
        for i, f in enumerate(factors):
            segments.append({
                "start": int(i * seg_size),
                "end": int(min((i + 1) * seg_size, n)),
                "factor": round(float(f), 6),
            })
        self._trace_segments("tempo_microvar", segments)

        src = [0.0]
        dst = [0.0]
        for i, f in enumerate(factors):
            src.append((i + 1) * seg_size)
            dst.append(dst[-1] + seg_size * f)

        total_dst = dst[-1]
        if total_dst < 1e-8:
            return audio
        dst = [d * n / total_dst for d in dst]

        src_idx = np.interp(np.arange(n, dtype=np.float64), dst, src)
        src_idx = np.clip(src_idx, 0, n - 1)

        result = np.zeros_like(audio)
        x = np.arange(n, dtype=np.float64)
        for ch in range(audio.shape[1]):
            result[:, ch] = np.interp(src_idx, x, audio[:, ch])
        return result

    # --- Phase scrambling ---
    def _phase_scramble(self, audio, sr):
        amount = self.params.get('phase_amount', 0.3)
        self._trace_random(
            "phase_scramble",
            ["uniform phase offset -pi to pi per STFT bin/frame"],
            amount=float(amount),
            channels=int(audio.shape[1]),
            nperseg=int(_nperseg_for(audio.shape[0])),
        )
        result = np.zeros_like(audio)
        for ch in range(audio.shape[1]):
            result[:, ch] = self._phase_scramble_ch(audio[:, ch], sr, amount)
        return result

    def _phase_scramble_ch(self, channel, sr, amount):
        nperseg = _nperseg_for(len(channel))
        if nperseg == 0:
            return channel.copy()
        noverlap = nperseg // 2

        f, t, Zxx = self._compute_backend.stft(
            channel,
            sr,
            nperseg=nperseg,
            noverlap=noverlap,
        )
        mag = np.abs(Zxx)
        phase = np.angle(Zxx)

        phase_noise = self.rng.uniform(-np.pi, np.pi, phase.shape) * amount
        phase += phase_noise

        Zxx_new = mag * np.exp(1j * phase)
        _, result = self._compute_backend.istft(
            Zxx_new,
            sr,
            nperseg=nperseg,
            noverlap=noverlap,
        )

        orig_len = len(channel)
        if len(result) > orig_len:
            result = result[:orig_len]
        elif len(result) < orig_len:
            result = np.pad(result, (0, orig_len - len(result)))
        return result

    # --- Stereo manipulation ---
    def _stereo_manipulate(self, audio):
        if audio.shape[1] < 2:
            return audio
        shift = self.params.get('stereo_shift', 0.1)
        self._trace_random(
            "stereo_manipulation",
            ["normal side-channel noise"],
            shift=float(shift),
            noise_standard_deviation=float(shift * 0.01),
            samples=int(audio.shape[0]),
        )

        left = audio[:, 0].copy()
        right = audio[:, 1].copy()
        mid = (left + right) / 2.0
        side = (left - right) / 2.0

        side *= (1.0 + shift)
        side += self.rng.normal(0, shift * 0.01, len(side))

        result = audio.copy()
        result[:, 0] = mid + side
        result[:, 1] = mid - side
        return result

    # --- Noise injection ---
    def _inject_noise(self, audio, sr):
        level_db = self.params.get('noise_level', -50.0)
        level_lin = 10.0 ** (level_db / 20.0)
        self._trace_random(
            "noise_injection",
            ["normal white noise transformed by 1/sqrt(f) pink-noise filter"],
            level_db=float(level_db),
            channels=int(audio.shape[1]),
            samples_per_channel=int(audio.shape[0]),
            masking="STFT energy threshold plus 30ms envelope",
        )

        result = audio.copy()
        for ch in range(result.shape[1]):
            pink = self._pink_noise(result.shape[0])
            shaped = self._masking_aware_noise(audio[:, ch], pink, sr, level_lin)
            result[:, ch] += shaped
        return result

    def _pink_noise(self, n):
        white = self.rng.normal(0, 1, n)
        fft = np.fft.rfft(white)
        freqs = np.fft.rfftfreq(n)
        freqs[0] = 1.0
        fft /= np.sqrt(freqs)
        pink = np.fft.irfft(fft, n)
        peak = np.max(np.abs(pink))
        if peak < 1e-10:
            return pink
        return pink / peak

    def _masking_aware_noise(self, channel, noise, sr, level_lin):
        nperseg = _nperseg_for(len(channel))
        if nperseg == 0:
            return noise * level_lin
        noverlap = nperseg // 2

        try:
            f, t, audio_z = self._compute_backend.stft(
                channel,
                sr,
                nperseg=nperseg,
                noverlap=noverlap,
            )
            _, _, noise_z = self._compute_backend.stft(
                noise,
                sr,
                nperseg=nperseg,
                noverlap=noverlap,
            )
        except Exception:
            return noise * level_lin

        audio_mag = np.abs(audio_z)
        noise_mag = np.abs(noise_z) + 1e-12

        kernel_bins = min(17, max(3, (len(f) // 24) * 2 + 1))
        try:
            spread = signal.medfilt(audio_mag, kernel_size=(kernel_bins, 1))
        except Exception:
            spread = audio_mag

        masking_mag = np.maximum(audio_mag, spread)
        floor = level_lin * 0.02
        threshold = np.maximum(masking_mag * 0.10, floor)
        shaped_z = noise_z * np.minimum(1.0, threshold / noise_mag)

        _, shaped = self._compute_backend.istft(
            shaped_z,
            sr,
            nperseg=nperseg,
            noverlap=noverlap,
        )
        if len(shaped) > len(channel):
            shaped = shaped[:len(channel)]
        elif len(shaped) < len(channel):
            shaped = np.pad(shaped, (0, len(channel) - len(shaped)))

        env = self._masking_envelope(channel, sr)
        shaped = shaped * env

        target_rms = level_lin
        current_rms = self._rms(shaped)
        if current_rms > target_rms > 0:
            shaped = shaped * (target_rms / current_rms)
        return shaped

    def _masking_envelope(self, channel, sr):
        frame = max(1, int(0.03 * sr))
        kernel = np.ones(frame, dtype=np.float64) / frame
        energy = np.convolve(np.square(channel), kernel, mode='same')
        env = np.sqrt(np.maximum(energy, 0.0))
        ref = np.percentile(env, 95) if env.size else 0.0
        if ref <= 1e-12:
            return np.full_like(channel, 0.05)
        return np.clip(env / ref, 0.05, 1.0)

    def _rms(self, data):
        if data.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(data))))

    # --- Dynamics modification ---
    def _modify_dynamics(self, audio, sr):
        amount = self.params.get('dynamics_amount', 0.2)
        frame_size = max(1, int(0.03 * sr))
        n_frames = max(1, (audio.shape[0] + frame_size - 1) // frame_size)
        self._trace_random(
            "dynamics_modification",
            ["uniform frame gain"],
            amount=float(amount),
            frame_size_samples=int(frame_size),
            frame_count=int(n_frames),
            gain_min=float(1.0 - amount * 0.15),
            gain_max=float(1.0 + amount * 0.15),
        )

        gains = 1.0 + self.rng.uniform(-amount * 0.15, amount * 0.15, n_frames)

        frame_centers = np.arange(n_frames) * frame_size + frame_size // 2
        frame_centers = np.clip(frame_centers, 0, audio.shape[0] - 1)
        gain_curve = np.interp(np.arange(audio.shape[0]), frame_centers, gains)

        return audio * gain_curve[:, np.newaxis]

    # --- Humanization (chunked for memory efficiency on long audio) ---
    def _humanize(self, audio, sr):
        amount = self.params.get('humanize_amount', 0.3)
        n = audio.shape[0]

        # Pre-compute modulation parameters - shared across chunks for continuity
        mod_freq = self.rng.uniform(0.5, 3.0)
        phase0 = self.rng.uniform(0, 2.0 * np.pi)
        breath_freq = self.rng.uniform(0.1, 0.5)
        chunk = max(sr, int(self._HUMANIZE_CHUNK_SEC * sr))
        self._trace_random(
            "humanization",
            [
                "uniform modulation frequency 0.5-3.0 Hz",
                "uniform initial phase 0-2pi",
                "uniform breathing frequency 0.1-0.5 Hz",
                "normal micro-noise",
            ],
            amount=float(amount),
            modulation_frequency_hz=round(float(mod_freq), 9),
            initial_phase_radians=round(float(phase0), 9),
            breathing_frequency_hz=round(float(breath_freq), 9),
            micro_noise_standard_deviation=float(amount * 0.0008),
            chunk_samples=int(chunk),
            chunk_count=int(max(1, (n + chunk - 1) // chunk)),
        )
        w1 = 2.0 * np.pi * mod_freq / sr
        w2 = 2.0 * np.pi * mod_freq * 2.7 / sr
        wb = 2.0 * np.pi * breath_freq / sr
        phase1 = phase0 * 1.3

        # For short audio, one pass; for long audio, chunked
        if n <= chunk:
            return self._humanize_chunk(audio, 0, n, w1, w2, wb, phase0, phase1, amount, sr)

        result = np.empty_like(audio)
        pos = 0
        while pos < n:
            end = min(pos + chunk, n)
            result[pos:end] = self._humanize_chunk(
                audio[pos:end], pos, n, w1, w2, wb, phase0, phase1, amount, sr,
            )
            pos = end
        return result

    def _humanize_chunk(self, chunk_audio, offset, total_n, w1, w2, wb, phase0, phase1, amount, sr):
        """Apply humanization to a chunk. `offset` preserves modulation continuity."""
        c_n = chunk_audio.shape[0]
        # Absolute sample indices (not relative to chunk) - keeps modulation coherent
        abs_idx = np.arange(offset, offset + c_n, dtype=np.float64)

        # Wobble displacement in samples
        wobble = (amount * 0.001 * sr) * np.sin(w1 * abs_idx + phase0)
        wobble += (amount * 0.0005 * sr) * np.sin(w2 * abs_idx + phase1)

        # Time-warp indices (still in absolute frame)
        warp_idx = abs_idx + wobble
        del wobble

        # Clamp so we never read outside the chunk's local range. To support
        # proper continuity at chunk boundaries we'd need a small context
        # overlap; wobble is bounded by amount * 0.0015 * sr ~= 66 samples at
        # amount=1, so clamping within the chunk is visually lossless.
        np.clip(warp_idx, offset, offset + c_n - 1, out=warp_idx)

        # Convert back to chunk-relative indices for np.interp
        warp_idx -= offset
        local_idx = np.arange(c_n, dtype=np.float64)

        result = np.empty_like(chunk_audio)
        for ch in range(chunk_audio.shape[1]):
            result[:, ch] = np.interp(warp_idx, local_idx, chunk_audio[:, ch])
        del warp_idx

        # Breathing amplitude curve
        breathing = np.sin(wb * abs_idx)
        breathing *= amount * 0.03
        breathing += 1.0
        result *= breathing[:, np.newaxis]
        del breathing
        del abs_idx
        del local_idx

        # Micro noise floor
        result += self.rng.normal(0, amount * 0.0008, result.shape)
        return result

    # --- Lossy re-encode ---
    def _lossy_reencode(self, audio, sr, mono):
        bitrate = int(self.params.get('reencode_bitrate', 192))
        self._trace_random(
            "lossy_reencode",
            [],
            bitrate_kbps=bitrate,
            codec="libmp3lame",
            ffmpeg=_ffmpeg_version_line(),
            replay="dependency_sensitive",
        )

        if not _check_ffmpeg():
            raise RuntimeError("ffmpeg not found")
        if not _ffmpeg_encoder_available('mp3'):
            raise RuntimeError("ffmpeg lacks libmp3lame encoder for lossy re-encode")

        tmp_dir = tempfile.mkdtemp()
        try:
            wav_in = os.path.join(tmp_dir, 'in.wav')
            mp3_tmp = os.path.join(tmp_dir, 'tmp.mp3')
            wav_out = os.path.join(tmp_dir, 'out.wav')

            save = audio[:, 0] if mono else audio
            sf.write(wav_in, save, sr)

            subprocess.run(
                ['ffmpeg', '-y', '-loglevel', 'error', '-i', wav_in,
                 '-b:a', f'{bitrate}k', mp3_tmp],
                capture_output=True, check=True,
            )
            subprocess.run(
                ['ffmpeg', '-y', '-loglevel', 'error', '-i', mp3_tmp, wav_out],
                capture_output=True, check=True,
            )

            result, _ = sf.read(wav_out, dtype='float64')

            if result.ndim == 1:
                result = result[:, np.newaxis]
            if result.shape[1] < audio.shape[1]:
                result = np.column_stack([result] * audio.shape[1])
            elif result.shape[1] > audio.shape[1]:
                result = result[:, :audio.shape[1]]

            if result.shape[0] > audio.shape[0]:
                result = result[:audio.shape[0]]
            elif result.shape[0] < audio.shape[0]:
                pad = np.zeros((audio.shape[0] - result.shape[0], result.shape[1]))
                result = np.concatenate([result, pad])

            return result
        except Exception as e:
            raise RuntimeError(f"re-encode failed: {e}") from e
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # --- Sample-domain signal-change metric ---
    def _compute_signal_change(self, original, processed):
        sig_power = np.mean(original ** 2)
        if sig_power < 1e-12:
            return 0.0  # silence in, silence out
        diff = original - processed
        diff_power = np.mean(diff ** 2) + 1e-12
        snr = 10.0 * np.log10(sig_power / diff_power)
        return max(0.0, min(100.0, (40.0 - snr) * 2.5))

    def _constellation_hashes(self, mono, sr, max_seconds=30.0):
        if len(mono) < int(sr * 2):
            return set()
        max_samples = int(sr * max_seconds)
        work = mono[:max_samples].astype(np.float64, copy=False)
        peak = np.max(np.abs(work))
        if peak < 1e-9:
            return set()
        work = work / peak

        nperseg = min(2048, 1 << (len(work).bit_length() - 1))
        if nperseg < 512:
            return set()
        hop = nperseg // 4
        f, _, Zxx = self._compute_backend.stft(
            work,
            sr,
            nperseg=nperseg,
            noverlap=nperseg - hop,
        )
        mag = np.log1p(np.abs(Zxx))
        if mag.shape[1] < 4:
            return set()

        valid_freq = (f >= 40.0) & (f <= min(12000.0, sr / 2.0))
        valid_idx = np.flatnonzero(valid_freq)
        if len(valid_idx) == 0:
            return set()

        peaks = []
        bins_per_frame = min(6, len(valid_idx))
        for frame_idx in range(mag.shape[1]):
            column = mag[valid_idx, frame_idx]
            threshold = np.percentile(column, 82.0)
            top_local = np.argpartition(column, -bins_per_frame)[-bins_per_frame:]
            for local_idx in top_local:
                amp = column[local_idx]
                if amp >= threshold:
                    peaks.append((frame_idx, int(valid_idx[local_idx]), float(amp)))

        peaks.sort(key=lambda p: (p[0], -p[2]))
        hashes = set()
        for i, (t1, f1, _) in enumerate(peaks):
            pairs = 0
            for t2, f2, _ in peaks[i + 1:]:
                dt = t2 - t1
                if dt < 1:
                    continue
                if dt > 10:
                    break
                hashes.add((int(f1), int(f2), int(dt)))
                pairs += 1
                if pairs >= 3:
                    break
        return hashes


# ============================================================
#  Process Worker Thread
# ============================================================
def _finish_manifest_job(manifest_store, job_id, result):
    validation = result.validation
    manifest_store.finish_job(
        job_id,
        state=result.state.value,
        output_path=result.output_path,
        output_sha256=(
            validation.output_sha256 if validation is not None else None
        ),
        sidecar_path=result.sidecar_path,
        sidecar_sha256=result.sidecar_sha256,
        artifacts=result.artifacts,
        input_sha256=(
            validation.input_sha256 if validation is not None else None
        ),
        error_code=(
            result.error_code.value
            if result.error_code is not None
            else None
        ),
        message=result.message,
    )


def _manifest_failure_result(result, exc):
    if result.usable_output:
        return RenderResult(
            state=RenderState.PARTIAL,
            input_path=result.input_path,
            output_path=result.output_path,
            error_code=RenderErrorCode.MANIFEST_WRITE_FAILED,
            message=(
                "usable output exists, but batch state could not be "
                f"persisted: {exc}"
            ),
            elapsed_seconds=result.elapsed_seconds,
            validation=result.validation,
            effective_seed=result.effective_seed,
            sidecar_path=result.sidecar_path,
            sidecar_sha256=result.sidecar_sha256,
            artifacts=result.artifacts,
        )
    return RenderResult(
        state=RenderState.FAILED,
        input_path=result.input_path,
        error_code=RenderErrorCode.MANIFEST_WRITE_FAILED,
        message=f"batch state could not be persisted: {exc}",
        elapsed_seconds=result.elapsed_seconds,
        effective_seed=result.effective_seed,
    )


def _validated_parallel_workers(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("parallel worker count must be an integer")
    if not 1 <= value <= MAX_PARALLEL_FILE_WORKERS:
        raise ValueError(
            "parallel worker count must be between 1 and "
            f"{MAX_PARALLEL_FILE_WORKERS}"
        )
    return value


def _validated_audit_options(value=None):
    if value is None:
        return {"spectrogram": False, "loudness": False}
    if not isinstance(value, dict):
        raise ConfigurationError("audit options must be an object")
    unknown = sorted(set(value) - {"spectrogram", "loudness"})
    if unknown:
        raise ConfigurationError(
            "unknown audit option(s): " + ", ".join(unknown)
        )
    normalized = {
        "spectrogram": False,
        "loudness": False,
        **value,
    }
    for key, enabled in normalized.items():
        if not isinstance(enabled, bool):
            raise ConfigurationError(f"audit option {key} must be a boolean")
    return normalized


def _validated_manifest_jobs(jobs, batch_params):
    validated_jobs = []
    for job in jobs:
        normalized = dict(job)
        if isinstance(job.get("config"), dict):
            config = validate_render_config(
                job["config"],
                require_complete=True,
                allow_output_format=True,
            )
            for key in ("output_format", "c2pa_policy"):
                if config[key] != batch_params[key]:
                    raise ConfigurationError(
                        f"per-file {key} must match the batch configuration"
                    )
            normalized["config"] = config
        validated_jobs.append(normalized)
    return validated_jobs


def _run_batch_render_job(
    *,
    job_id,
    filepath,
    effective_seed,
    params,
    output_dir,
    used_outputs,
    output_lock,
    manifest_store,
    cancel_event,
    log_fn,
    progress_fn,
    started_fn,
    compute_backend=None,
    audit_options=None,
):
    if cancel_event.is_set():
        result = RenderResult(
            state=RenderState.CANCELLED,
            input_path=str(filepath),
            error_code=RenderErrorCode.CANCELLED,
            message="batch cancelled before this job started",
            effective_seed=effective_seed,
        )
    else:
        started_fn()
        reservation = None
        try:
            fmt = params.get("output_format", "wav").lower()
            ext = _output_extension(fmt)
            with output_lock:
                reservation = _reserve_output_path(
                    filepath,
                    output_dir,
                    ext,
                    used_outputs,
                )
            out_path = reservation.output_path
            if reservation.renamed:
                log_fn(
                    "Output name collision avoided: "
                    f"{Path(out_path).name}"
                )
            log_fn(f"Output path: {out_path}")
            if manifest_store is not None:
                manifest_store.begin_attempt(job_id, out_path)
            processor = AudioProcessor(
                params,
                log_fn=log_fn,
                progress_fn=progress_fn,
                cancel_event=cancel_event,
                seed=effective_seed,
                compute_backend=compute_backend,
                audit_options=audit_options,
            )
            result = processor.process(filepath, out_path)
            if not isinstance(result, RenderResult):
                raise TypeError("processor returned an untyped result")
        except BatchManifestError as exc:
            log_fn(f"Batch manifest update failed: {exc}")
            result = RenderResult(
                state=RenderState.FAILED,
                input_path=str(filepath),
                error_code=RenderErrorCode.MANIFEST_WRITE_FAILED,
                message=str(exc),
                effective_seed=effective_seed,
            )
        except Exception as exc:
            log_fn(f"Unexpected render failure: {exc}")
            log_fn(traceback.format_exc().rstrip())
            result = RenderResult(
                state=RenderState.FAILED,
                input_path=str(filepath),
                error_code=RenderErrorCode.UNEXPECTED,
                message=str(exc),
                effective_seed=effective_seed,
            )
        finally:
            if reservation is not None:
                reservation.release()

    if manifest_store is not None:
        try:
            _finish_manifest_job(manifest_store, job_id, result)
        except BatchManifestError as exc:
            log_fn(f"Batch manifest update failed: {exc}")
            result = _manifest_failure_result(result, exc)
    return result


class ProcessWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    file_progress = pyqtSignal(str, int)
    # Stable queue job IDs prevent stale results from attaching by row index.
    file_started = pyqtSignal(str)
    file_done = pyqtSignal(str, object)
    # all_done(BatchResult)
    all_done = pyqtSignal(object)

    def __init__(
        self,
        jobs,
        params,
        output_dir,
        manifest_store=None,
        max_workers=DEFAULT_PARALLEL_FILE_WORKERS,
        audit_options=None,
    ):
        super().__init__()
        self.jobs = []
        seeds = []
        job_params = []
        for index, job in enumerate(jobs):
            if isinstance(job, dict):
                job_id = job["id"]
                filepath = job["input_path"]
                seed = job.get("effective_seed")
                per_file_params = job.get("config")
            elif isinstance(job, (tuple, list)) and len(job) in {2, 3}:
                job_id, filepath = job[:2]
                seed = job[2] if len(job) == 3 else None
                per_file_params = None
            else:
                job_id = f"worker-{index}-{uuid.uuid4().hex}"
                filepath = job
                seed = None
                per_file_params = None
            self.jobs.append((str(job_id), str(filepath)))
            seeds.append(
                seed if seed is not None else secrets.randbits(64)
            )
            job_params.append(
                dict(per_file_params)
                if isinstance(per_file_params, dict)
                else dict(params)
            )
        self.files = [filepath for _, filepath in self.jobs]
        self.params = params
        self.job_params = job_params
        self.output_dir = output_dir
        self.manifest_store = manifest_store
        self.max_workers = _validated_parallel_workers(max_workers)
        self.audit_options = _validated_audit_options(audit_options)
        self._cancel_event = threading.Event()
        self.seeds = seeds

    def _record_manifest_result(self, job_id, result):
        if self.manifest_store is None:
            return result
        try:
            _finish_manifest_job(self.manifest_store, job_id, result)
            return result
        except BatchManifestError as exc:
            self.log_signal.emit(f"Batch manifest update failed: {exc}")
            return _manifest_failure_result(result, exc)

    def run(self):
        t_start = time.monotonic()
        results = []
        try:
            os.makedirs(self.output_dir, exist_ok=True)
        except OSError as e:
            message = f"cannot create output directory: {e}"
            self.log_signal.emit(message.capitalize())
            for idx, (job_id, filepath) in enumerate(self.jobs):
                result = RenderResult(
                    state=RenderState.FAILED,
                    input_path=str(filepath),
                    error_code=RenderErrorCode.OUTPUT_DIR_UNAVAILABLE,
                    message=message,
                    effective_seed=self.seeds[idx],
                )
                result = self._record_manifest_result(job_id, result)
                results.append(result)
                self.file_done.emit(job_id, result)
                self.log_signal.emit(format_render_result(result))
            self.all_done.emit(
                BatchResult.from_results(
                    results,
                    time.monotonic() - t_start,
                )
            )
            return
        n_files = len(self.files)
        if n_files == 0:
            self.all_done.emit(BatchResult.from_results([], 0.0))
            return
        used_outputs = set()
        output_lock = threading.Lock()
        events = queue.Queue()
        progress_by_index = [0] * n_files
        last_overall = -1

        def queue_event(kind, *payload):
            events.put((kind, payload))

        def dispatch_event(event):
            nonlocal last_overall
            kind, payload = event
            if kind == "log":
                self.log_signal.emit(payload[0])
            elif kind == "started":
                self.file_started.emit(payload[0])
            elif kind == "progress":
                idx, job_id, value = payload
                value = max(0, min(99, int(value)))
                progress_by_index[idx] = max(progress_by_index[idx], value)
                self.file_progress.emit(job_id, value)
                overall = min(99, sum(progress_by_index) // n_files)
                if overall != last_overall:
                    last_overall = overall
                    self.progress_signal.emit(overall)

        def drain_events():
            while True:
                try:
                    dispatch_event(events.get_nowait())
                except queue.Empty:
                    return

        worker_count = min(self.max_workers, n_files)
        self.log_signal.emit(
            f"Parallel file workers: {worker_count}"
        )
        results_by_index = [None] * n_files
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="sunojump-file",
        ) as executor:
            pending = {}
            for idx, (job_id, filepath) in enumerate(self.jobs):
                label = f"[{idx + 1}/{n_files} {Path(filepath).name}]"
                future = executor.submit(
                    _run_batch_render_job,
                    job_id=job_id,
                    filepath=filepath,
                    effective_seed=self.seeds[idx],
                    params=self.job_params[idx],
                    output_dir=self.output_dir,
                    used_outputs=used_outputs,
                    output_lock=output_lock,
                    manifest_store=self.manifest_store,
                    cancel_event=self._cancel_event,
                    log_fn=lambda message, prefix=label: queue_event(
                        "log", f"{prefix} {message}"
                    ),
                    progress_fn=lambda value, index=idx, jid=job_id: queue_event(
                        "progress", index, jid, value
                    ),
                    started_fn=lambda jid=job_id: queue_event(
                        "started", jid
                    ),
                    audit_options=self.audit_options,
                )
                pending[future] = (idx, job_id, filepath)

            while pending:
                try:
                    dispatch_event(events.get(timeout=0.02))
                except queue.Empty:
                    pass
                drain_events()
                for future in list(pending):
                    if not future.done():
                        continue
                    # A completed task has already queued all of its progress;
                    # deliver it before the terminal row update.
                    drain_events()
                    idx, job_id, filepath = pending.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = RenderResult(
                            state=RenderState.FAILED,
                            input_path=str(filepath),
                            error_code=RenderErrorCode.UNEXPECTED,
                            message=str(exc),
                            effective_seed=self.seeds[idx],
                        )
                        self.log_signal.emit(traceback.format_exc().rstrip())
                    results_by_index[idx] = result
                    progress_by_index[idx] = 100
                    overall = min(99, sum(progress_by_index) // n_files)
                    if overall != last_overall:
                        last_overall = overall
                        self.progress_signal.emit(overall)
                    self.log_signal.emit(format_render_result(result))
                    self.file_done.emit(job_id, result)

        drain_events()
        results = list(results_by_index)

        self.all_done.emit(
            BatchResult.from_results(results, time.monotonic() - t_start)
        )

    def cancel(self):
        self._cancel_event.set()


# ============================================================
#  Preview Worker Thread
# ============================================================
class PreviewWorker(QThread):
    """Renders a short clip of a single file for audition purposes."""

    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    # done(queue_job_id, preview_run_id, RenderResult)
    done = pyqtSignal(str, str, object)

    def __init__(
        self,
        input_path,
        params,
        temp_dir,
        job_id,
        run_id,
        duration_sec=PREVIEW_DURATION_SEC,
        offset_sec=0.0,
    ):
        super().__init__()
        self.input_path = str(input_path)
        self.params = params
        self.temp_dir = temp_dir
        self.job_id = str(job_id)
        self.run_id = str(run_id)
        self.duration_sec = duration_sec
        self.offset_sec = float(offset_sec)
        self._cancel_event = threading.Event()
        self.seed = secrets.randbits(64)

    def run(self):
        try:
            os.makedirs(self.temp_dir, exist_ok=True)
        except OSError as e:
            self.log_signal.emit(f"Preview: cannot create temp dir: {e}")
            self.done.emit(
                self.job_id,
                self.run_id,
                RenderResult(
                    state=RenderState.FAILED,
                    input_path=self.input_path,
                    error_code=RenderErrorCode.OUTPUT_DIR_UNAVAILABLE,
                    message=f"cannot create preview directory: {e}",
                    effective_seed=self.seed,
                ),
            )
            return

        stem = Path(self.input_path).stem
        # Disambiguate from any previous preview of the same file
        ts = datetime.now().strftime("%H%M%S%f")
        out_path = os.path.join(self.temp_dir, f"{stem}_preview_{ts}.wav")

        # Preview always writes WAV (fast, lossless, universally playable)
        # and skips lossy re-encode because the clip is already short.
        params = dict(self.params)
        params['output_format'] = 'wav'
        params['reencode_enabled'] = False

        processor = AudioProcessor(
            params,
            log_fn=lambda m: self.log_signal.emit(m),
            progress_fn=lambda v: self.progress_signal.emit(v),
            cancel_event=self._cancel_event,
            seed=self.seed,
        )
        self.log_signal.emit(f"Preview output path: {out_path}")
        try:
            result = processor.process(
                self.input_path,
                out_path,
                preview_seconds=self.duration_sec,
                preview_offset_seconds=self.offset_sec,
            )
            if not isinstance(result, RenderResult):
                raise TypeError("preview processor returned an untyped result")
            self.log_signal.emit(format_render_result(result))
        except Exception as e:
            self.log_signal.emit(f"Preview failed unexpectedly: {e}")
            self.log_signal.emit(traceback.format_exc().rstrip())
            result = RenderResult(
                state=RenderState.FAILED,
                input_path=self.input_path,
                error_code=RenderErrorCode.UNEXPECTED,
                message=str(e),
                effective_seed=self.seed,
            )
        self.done.emit(self.job_id, self.run_id, result)

    def cancel(self):
        self._cancel_event.set()


# ============================================================
#  Preset Compare Worker
# ============================================================
class PresetCompareWorker(QThread):
    """Renders one short sample per built-in preset so the user can A/B/C/D
    audition all four in one click. Results are written to the shared preview
    temp directory keyed by preset name."""

    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)  # 0-100 overall (across all presets)
    # preset_done(queue_job_id, compare_run_id, preset_name, RenderResult)
    preset_done = pyqtSignal(str, str, str, object)
    # all_done(queue_job_id, compare_run_id, BatchResult)
    all_done = pyqtSignal(str, str, object)

    def __init__(
        self,
        input_path,
        temp_dir,
        job_id,
        run_id,
        c2pa_policy=C2PA_POLICY_BLOCK,
        duration_sec=COMPARE_DURATION_SEC,
        offset_sec=0.0,
    ):
        super().__init__()
        self.input_path = str(input_path)
        self.temp_dir = temp_dir
        self.job_id = str(job_id)
        self.run_id = str(run_id)
        self.duration_sec = duration_sec
        self.offset_sec = float(offset_sec)
        if c2pa_policy not in C2PA_POLICIES:
            raise ValueError(f"unsupported C2PA policy: {c2pa_policy}")
        self.c2pa_policy = c2pa_policy
        self._cancel_event = threading.Event()
        self.preset_names = list(PRESETS.keys())
        self.seeds = {
            name: secrets.randbits(64) for name in self.preset_names
        }

    def run(self):
        started_at = time.monotonic()
        results = []
        n_presets = len(self.preset_names)
        try:
            os.makedirs(self.temp_dir, exist_ok=True)
        except OSError as e:
            self.log_signal.emit(f"Compare: cannot create temp dir: {e}")
            for name in self.preset_names:
                result = RenderResult(
                    state=RenderState.FAILED,
                    input_path=self.input_path,
                    error_code=RenderErrorCode.OUTPUT_DIR_UNAVAILABLE,
                    message=f"cannot create compare directory: {e}",
                    effective_seed=self.seeds[name],
                )
                results.append(result)
                self.preset_done.emit(
                    self.job_id,
                    self.run_id,
                    name,
                    result,
                )
            self.all_done.emit(
                self.job_id,
                self.run_id,
                BatchResult.from_results(
                    results,
                    time.monotonic() - started_at,
                ),
            )
            return

        stem = Path(self.input_path).stem
        ts = datetime.now().strftime("%H%M%S%f")

        for i, name in enumerate(self.preset_names):
            if self._cancel_event.is_set():
                for cancelled_name in self.preset_names[i:]:
                    result = RenderResult(
                        state=RenderState.CANCELLED,
                        input_path=self.input_path,
                        error_code=RenderErrorCode.CANCELLED,
                        message="compare cancelled before this preset started",
                        effective_seed=self.seeds[cancelled_name],
                    )
                    results.append(result)
                    self.preset_done.emit(
                        self.job_id,
                        self.run_id,
                        cancelled_name,
                        result,
                    )
                break
            self.log_signal.emit(f"Compare {i+1}/{n_presets}: {name}")

            # Map single-render progress (0-100) to overall (0-100)
            def sub_progress(v, _i=i, _n=n_presets):
                mapped = (_i * 100 + int(v)) // _n
                self.progress_signal.emit(min(99, mapped))

            params = dict(PRESETS[name])
            params['output_format'] = 'wav'
            params['reencode_enabled'] = False
            params['c2pa_policy'] = self.c2pa_policy

            out_path = os.path.join(
                self.temp_dir, f"{stem}_compare_{name}_{ts}.wav",
            )
            self.log_signal.emit(f"  Compare output path: {out_path}")

            proc = AudioProcessor(
                params,
                log_fn=lambda m: self.log_signal.emit(f"  {m}"),
                progress_fn=sub_progress,
                cancel_event=self._cancel_event,
                seed=self.seeds[name],
            )
            try:
                result = proc.process(
                    self.input_path,
                    out_path,
                    preview_seconds=self.duration_sec,
                    preview_offset_seconds=self.offset_sec,
                )
                if not isinstance(result, RenderResult):
                    raise TypeError("compare processor returned an untyped result")
                self.log_signal.emit(f"  {format_render_result(result)}")
            except Exception as e:
                self.log_signal.emit(f"  Compare {name} failed unexpectedly: {e}")
                self.log_signal.emit(traceback.format_exc().rstrip())
                result = RenderResult(
                    state=RenderState.FAILED,
                    input_path=self.input_path,
                    error_code=RenderErrorCode.UNEXPECTED,
                    message=str(e),
                    effective_seed=self.seeds[name],
                )
            results.append(result)
            self.preset_done.emit(
                self.job_id,
                self.run_id,
                name,
                result,
            )

        batch_result = BatchResult.from_results(
            results,
            time.monotonic() - started_at,
        )
        if batch_result.state is RenderState.SUCCEEDED:
            self.progress_signal.emit(100)
        self.all_done.emit(self.job_id, self.run_id, batch_result)

    def cancel(self):
        self._cancel_event.set()


# ============================================================
#  Custom Widgets
# ============================================================
class ResponsiveButton(QPushButton):
    """Push button that wraps translated text in compact layouts."""

    def __init__(self, text=""):
        self._responsive_source_text = str(text)
        self._responsive_compact = False
        super().__init__(text)

    def setText(self, text):
        self._responsive_source_text = str(text).replace("\n", " ")
        self._apply_responsive_text()

    def set_compact(self, compact):
        compact = bool(compact)
        if compact == self._responsive_compact:
            return
        self._responsive_compact = compact
        self._apply_responsive_text()

    def _apply_responsive_text(self):
        text = self._responsive_source_text
        if not self._responsive_compact or not text:
            super().setText(text)
            self.setMinimumHeight(0)
            self.updateGeometry()
            return
        metrics = self.fontMetrics()
        target_width = max(
            180,
            metrics.horizontalAdvance("M") * 22,
        )
        lines = []
        remaining = text.strip()
        while remaining:
            if metrics.horizontalAdvance(remaining) <= target_width:
                lines.append(remaining)
                break
            split_at = 1
            for index in range(1, len(remaining) + 1):
                if (
                    metrics.horizontalAdvance(remaining[:index])
                    > target_width
                ):
                    break
                split_at = index
            space_at = remaining.rfind(" ", 0, split_at + 1)
            if space_at > 0:
                split_at = space_at
            lines.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
        super().setText("\n".join(line for line in lines if line))
        line_count = len(self.text().splitlines())
        if line_count > 1:
            self.setMinimumHeight(
                line_count * self.fontMetrics().lineSpacing() + 20
            )
        else:
            self.setMinimumHeight(0)
        self.updateGeometry()

    def sizeHint(self):
        base = super().sizeHint()
        if not self._responsive_compact or "\n" not in self.text():
            return base
        metrics = self.fontMetrics()
        lines = self.text().splitlines()
        icon_width = (
            self.iconSize().width() + 8
            if not self.icon().isNull()
            else 0
        )
        width = (
            max(metrics.horizontalAdvance(line) for line in lines)
            + icon_width
            + 48
        )
        height = max(
            base.height(),
            len(lines) * metrics.lineSpacing() + 20,
            self.iconSize().height() + 20,
        )
        return QSize(width, height)

    def minimumSizeHint(self):
        return self.sizeHint()


class DropListWidget(QListWidget):
    """File list that accepts external file drops and allows internal reordering."""

    filesDropped = pyqtSignal(list)
    removeRequested = pyqtSignal()
    orderChanged = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def keyPressEvent(self, event):
        if event.key() in {
            Qt.Key.Key_Delete,
            Qt.Key.Key_Backspace,
        }:
            if self.selectedItems():
                self.removeRequested.emit()
            event.accept()
            return
        reorder_modifier = event.modifiers() & (
            Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.ControlModifier
        )
        if reorder_modifier and event.key() in {
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        }:
            direction = -1 if event.key() == Qt.Key.Key_Up else 1
            if self._move_selected(direction):
                label = "up" if direction < 0 else "down"
                self.orderChanged.emit(
                    f"Moved {len(self.selectedItems())} selected "
                    f"item(s) {label}."
                )
            event.accept()
            return
        super().keyPressEvent(event)

    def _move_selected(self, direction):
        selected = list(self.selectedItems())
        if not selected:
            return False
        row_items = sorted(
            (self.row(item), item)
            for item in selected
        )
        rows = [row for row, _item in row_items]
        if direction < 0:
            if rows[0] == 0:
                return False
            iterable = row_items
        else:
            if rows[-1] == self.count() - 1:
                return False
            iterable = reversed(row_items)
        current = self.currentItem()
        for row, item in iterable:
            moved = self.takeItem(row)
            self.insertItem(row + direction, moved)
        for item in selected:
            item.setSelected(True)
        if current is not None:
            self.setCurrentItem(current)
            self.scrollToItem(current)
        return True

    def dragEnterEvent(self, event: QDragEnterEvent):
        # Internal drag (reorder): accept through base class behavior
        if event.source() is self:
            super().dragEnterEvent(event)
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.source() is self:
            super().dragMoveEvent(event)
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        if event.source() is self:
            # Internal reorder - let Qt handle it
            super().dropEvent(event)
            return
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            self.filesDropped.emit(paths)


class ValueAnnouncingSlider(QSlider):
    """Slider whose accessible name/description include the displayed units."""

    def __init__(self, orientation):
        super().__init__(orientation)
        self._accessible_base_name = "Amount"
        self._accessible_instructions = ""
        self._accessible_value_text = ""

    def configure_accessibility(self, name, instructions):
        self._accessible_base_name = name
        self._accessible_instructions = instructions
        self._sync_accessibility()

    def set_accessible_value(self, value_text):
        self._accessible_value_text = value_text
        self._sync_accessibility()

    def _sync_accessibility(self):
        value = self._accessible_value_text or "not set"
        self.setAccessibleName(
            f"{self._accessible_base_name}: {value}"
        )
        self.setAccessibleDescription(
            f"Current value {value}. {self._accessible_instructions}"
        )
        self.setToolTip(
            f"{self._accessible_base_name}: {value}. "
            f"{self._accessible_instructions}"
        )


class ParamRow(QWidget):
    changed = pyqtSignal()

    def __init__(self, key, label, min_val, max_val, default, suffix='',
                 decimals=2, enabled_key='', display_factor=1.0):
        super().__init__()
        label = _tr(label)
        self.setObjectName("paramRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(38)
        self.key = key
        self.enabled_key = enabled_key
        self.min_val = min_val
        self.max_val = max_val
        self.decimals = decimals
        self.suffix = suffix
        self.display_factor = display_factor

        self._compact = None
        self._parent_compact = False
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(10, 8, 10, 8)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(6)

        self.check = QCheckBox()
        self.check.setChecked(True)
        self.check.setToolTip(f"Enable {label}")
        _set_accessibility(
            self.check,
            f"Enable {label}",
            f"Toggle the {label} processing pass.",
        )

        self._label = QLabel(label)
        self._label.setObjectName("paramName")
        self._label.setWordWrap(True)

        self.slider = ValueAnnouncingSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 200)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(10)
        self.slider.configure_accessibility(
            f"{label} amount",
            (
                f"Adjust {label} from "
                f"{self._format_display_value(min_val)} to "
                f"{self._format_display_value(max_val)}. "
                "Use Left and Right Arrow for fine changes or "
                "Page Up and Page Down for larger changes."
            ),
        )
        self._label.setBuddy(self.slider)

        self.val_label = QLabel()
        self.val_label.setObjectName("paramValue")
        self.val_label.setAlignment(
            Qt.AlignmentFlag.AlignTrailing
            | Qt.AlignmentFlag.AlignVCenter,
        )
        _set_accessibility(
            self.val_label,
            f"{label} displayed value",
            f"Displays the current {label} value with units.",
        )
        widest_value = max(
            self._format_display_value(min_val),
            self._format_display_value(max_val),
            key=len,
        )
        self.val_label.setMinimumWidth(
            self.val_label.fontMetrics().horizontalAdvance(widest_value)
            + 8
        )
        self._set_compact(False)

        self.slider.valueChanged.connect(self._update_label)
        self.check.stateChanged.connect(self._on_check_changed)
        self.set_value(default)
        self._update_label()

    def _format_display_value(self, value):
        display_value = value * self.display_factor
        return f"{display_value:.{self.decimals}f}{self.suffix}"

    def _set_compact(self, compact):
        if compact == self._compact:
            return
        self._compact = compact
        for widget in (
            self.check,
            self._label,
            self.slider,
            self.val_label,
        ):
            self._grid.removeWidget(widget)
        if compact:
            self._grid.addWidget(self.check, 0, 0)
            self._grid.addWidget(self._label, 0, 1, 1, 2)
            self._grid.addWidget(self.val_label, 0, 3)
            self._grid.addWidget(self.slider, 1, 1, 1, 3)
        else:
            self._grid.addWidget(self.check, 0, 0)
            self._grid.addWidget(self._label, 0, 1)
            self._grid.addWidget(self.slider, 0, 2)
            self._grid.addWidget(self.val_label, 0, 3)
        self._grid.setColumnStretch(0, 0)
        self._grid.setColumnStretch(1, 0)
        self._grid.setColumnStretch(2, 1)
        self._grid.setColumnStretch(3, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._parent_compact:
            self._set_compact(True)
            return
        self._update_compact_for_width()

    def _update_compact_for_width(self):
        metrics = self.fontMetrics()
        slider_width = max(150, metrics.horizontalAdvance("M") * 16)
        required_width = (
            self.check.sizeHint().width()
            + self._label.sizeHint().width()
            + slider_width
            + self.val_label.minimumWidth()
            + 60
        )
        if self._compact:
            required_width += max(
                40,
                metrics.horizontalAdvance("M") * 4,
            )
        self._set_compact(self.width() < required_width)

    def set_parent_compact(self, compact):
        self._parent_compact = bool(compact)
        if compact:
            self._set_compact(True)
        else:
            self._update_compact_for_width()

    def _on_check_changed(self, state):
        enabled = self.check.isChecked()
        self.slider.setEnabled(enabled)
        self.val_label.setEnabled(enabled)
        self.changed.emit()

    def _update_label(self):
        value_text = self._format_display_value(self.value())
        self.val_label.setText(value_text)
        self.slider.set_accessible_value(value_text)
        self.changed.emit()

    def value(self):
        return self.min_val + (self.max_val - self.min_val) * self.slider.value() / 200.0

    def set_value(self, v):
        v = max(self.min_val, min(self.max_val, v))
        val_range = self.max_val - self.min_val
        if val_range < 1e-12:
            pos = 0
        else:
            pos = round((v - self.min_val) / val_range * 200)
        self.slider.setValue(pos)

    def is_enabled(self):
        return self.check.isChecked()

    def set_enabled_check(self, b):
        self.check.setChecked(b)


# ============================================================
#  File Discovery Worker
# ============================================================
@dataclass(frozen=True)
class FileDiscoveryResult:
    paths: tuple[str, ...]
    scanned_entries: int
    unsupported_count: int
    errors: tuple[str, ...]
    error_count: int
    cancelled: bool


class FileDiscoveryWorker(QThread):
    """Discover supported audio without blocking the Qt event loop."""

    progress_signal = pyqtSignal(int, int, str)
    discovery_done = pyqtSignal(object)

    def __init__(self, paths, existing_paths=()):
        super().__init__()
        self.paths = tuple(str(path) for path in paths if str(path))
        self.existing_paths = {
            os.path.normcase(os.path.abspath(str(path)))
            for path in existing_paths
        }
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    def run(self):
        discovered = []
        seen = set(self.existing_paths)
        scanned_entries = 0
        unsupported_count = 0
        errors = []
        error_count = 0

        def remember_error(message):
            nonlocal error_count
            error_count += 1
            if len(errors) < 20:
                errors.append(str(message))

        def report(current_path):
            self.progress_signal.emit(
                scanned_entries,
                len(discovered),
                str(current_path),
            )

        def consider_file(file_path):
            nonlocal scanned_entries, unsupported_count
            if self._cancel_event.is_set():
                return
            scanned_entries += 1
            path = Path(file_path)
            if path.suffix.lower() not in SUPPORTED_FORMATS:
                unsupported_count += 1
                return
            normalized = os.path.normcase(os.path.abspath(str(path)))
            if normalized in seen:
                return
            seen.add(normalized)
            discovered.append(str(path.resolve()))
            if scanned_entries % 100 == 0:
                report(path)

        for raw_path in self.paths:
            if self._cancel_event.is_set():
                break
            path = Path(raw_path)
            try:
                if path.is_file():
                    consider_file(path)
                    continue
                if not path.is_dir():
                    remember_error(f"Not found or inaccessible: {path}")
                    continue

                walk_errors = []

                def on_walk_error(exc):
                    walk_errors.append(exc)

                for root, directories, files in os.walk(
                    path,
                    topdown=True,
                    onerror=on_walk_error,
                    followlinks=False,
                ):
                    if self._cancel_event.is_set():
                        break
                    directories.sort(key=str.casefold)
                    files.sort(key=str.casefold)
                    for name in files:
                        if self._cancel_event.is_set():
                            break
                        consider_file(Path(root) / name)
                    report(root)
                for exc in walk_errors:
                    remember_error(
                        f"Cannot scan {getattr(exc, 'filename', path)}: {exc}"
                    )
            except OSError as exc:
                remember_error(f"Cannot scan {path}: {exc}")

        report(self.paths[-1] if self.paths else "")
        self.discovery_done.emit(FileDiscoveryResult(
            paths=tuple(discovered),
            scanned_entries=scanned_entries,
            unsupported_count=unsupported_count,
            errors=tuple(errors),
            error_count=error_count,
            cancelled=self._cancel_event.is_set(),
        ))


# ============================================================
#  Main Window
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self, settings=None, locale_name=None):
        if locale_name is not None:
            configure_locale(locale_name, QApplication.instance())
        super().__init__()
        self.setLayoutDirection(active_layout_direction())
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        self.setMinimumSize(560, 360)
        self.resize(1180, 880)
        self.worker = None
        self.preview_worker = None
        self.compare_worker = None
        self.discovery_worker = None
        self._last_batch_manifest_path = None
        self._discovery_terminal_state = "Ready"
        self._preview_tempdir = None  # created lazily on first preview
        self._preview_job_id = None
        self._preview_run_id = None
        self._preview_terminal_state = "Failed"
        self._preview_offset_seconds = 0.0
        self._compare_results = {}  # preset_name -> path
        self._compare_job_id = None
        self._compare_run_id = None
        self._compare_terminal_state = "Failed"
        self._compare_offset_seconds = 0.0
        self._batch_terminal_state = "Failed"
        self._batch_result_received = False
        self._deferred_preview_cleanup = set()
        self._close_pending = False
        self._playing_compare_preset = None  # name of preset currently playing from compare
        # Suppresses stale StoppedState signals during source transitions
        # (player.stop() + setSource() + play() fires Stopped then Playing;
        # the Stopped handler would otherwise wipe _playing_source state
        # that _toggle_play() just set).
        self._media_transitioning = False
        self._applying_preset = False
        self._syncing_file_preset = False
        self._last_browse_dir = str(Path.home())
        self._last_preset_dir = str(Path.home())
        self._current_run_log = None
        self._settings = (
            settings
            if settings is not None
            else QSettings(APP_NAME, APP_NAME)
        )
        self._compare_history = self._load_compare_history()

        # Media player for preview (optional)
        self.player = None
        self.audio_output = None
        self._playing_source = None  # 'original' | 'processed' | None
        if _MULTIMEDIA_OK:
            self.player = QMediaPlayer()
            self.audio_output = QAudioOutput()
            self.player.setAudioOutput(self.audio_output)
            self.player.playbackStateChanged.connect(self._on_playback_state_changed)
            self.player.errorOccurred.connect(self._on_player_error)

        central = QWidget()
        self.setCentralWidget(central)
        shell = QVBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)

        self.main_scroller = QScrollArea()
        self.main_scroller.setObjectName("mainScroller")
        self.main_scroller.setWidgetResizable(True)
        self.main_scroller.setFrameShape(QFrame.Shape.NoFrame)
        self.main_scroller.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.main_scroller.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        shell.addWidget(self.main_scroller)

        self._content_root = QWidget()
        self._content_root.setObjectName("appRoot")
        self.main_scroller.setWidget(self._content_root)
        root = QVBoxLayout(self._content_root)
        root.setContentsMargins(18, 16, 18, 18)
        root.setSpacing(14)

        root.addWidget(self._build_header())

        self._workspace_widget = QWidget()
        self._workspace_layout = QBoxLayout(
            QBoxLayout.Direction.LeftToRight,
            self._workspace_widget,
        )
        self._workspace_layout.setContentsMargins(0, 0, 0, 0)
        self._workspace_layout.setSpacing(14)

        self._left_column = QWidget()
        left_col = QVBoxLayout(self._left_column)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(14)
        left_col.addWidget(self._build_files(), 5)
        left_col.addWidget(self._build_preview(), 0)
        left_col.addWidget(self._build_log(), 3)

        self._right_column = QWidget()
        right_col = QVBoxLayout(self._right_column)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(14)
        right_col.addWidget(self._build_settings(), 7)
        right_col.addWidget(self._build_output(), 0)
        right_col.addWidget(self._build_controls(), 0)

        self._workspace_layout.addWidget(self._left_column, 5)
        self._workspace_layout.addWidget(self._right_column, 6)
        root.addWidget(self._workspace_widget, 1)
        self._sync_header_stats()
        self._configure_accessibility()
        self._configure_shortcuts()
        self._configure_tab_order()
        self._restore_session_state()
        self._update_file_count()
        self._responsive_compact = None
        self._update_responsive_layout()

        if not self._session_restored_geometry:
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                x = (geo.width() - self.width()) // 2 + geo.x()
                y = (geo.height() - self.height()) // 2 + geo.y()
                self.move(x, y)

    @staticmethod
    def _compare_history_key(input_path):
        normalized = os.path.normcase(os.path.abspath(str(input_path)))
        return hashlib.sha256(
            normalized.encode("utf-8", errors="surrogatepass")
        ).hexdigest()

    def _load_compare_history(self):
        raw = self._settings.value(COMPARE_HISTORY_SETTINGS_KEY, "")
        if not isinstance(raw, str) or not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != COMPARE_HISTORY_SCHEMA_VERSION
            or not isinstance(payload.get("entries"), dict)
        ):
            return {}
        history = {}
        for key, entry in payload["entries"].items():
            if (
                isinstance(key, str)
                and len(key) == 64
                and all(ch in "0123456789abcdef" for ch in key)
                and isinstance(entry, dict)
                and entry.get("preset") in PRESETS
                and isinstance(entry.get("selected_at"), str)
            ):
                history[key] = {
                    "preset": entry["preset"],
                    "selected_at": entry["selected_at"],
                }
        return dict(sorted(
            history.items(),
            key=lambda pair: pair[1]["selected_at"],
            reverse=True,
        )[:MAX_COMPARE_HISTORY_ENTRIES])

    def _compare_winner_for_path(self, input_path):
        if not input_path:
            return None
        return self._compare_history.get(
            self._compare_history_key(input_path)
        )

    def _save_compare_winner(self, item, preset_name):
        if item is None or preset_name not in PRESETS:
            return False
        input_path = item.data(ROLE_INPUT)
        if not input_path:
            return False
        entry = {
            "preset": preset_name,
            "selected_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z"),
        }
        key = self._compare_history_key(input_path)
        self._compare_history[key] = entry
        retained = sorted(
            self._compare_history.items(),
            key=lambda pair: pair[1]["selected_at"],
            reverse=True,
        )[:MAX_COMPARE_HISTORY_ENTRIES]
        self._compare_history = dict(retained)
        payload = {
            "schema_version": COMPARE_HISTORY_SCHEMA_VERSION,
            "entries": self._compare_history,
        }
        self._settings.setValue(
            COMPARE_HISTORY_SETTINGS_KEY,
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
        )
        self._settings.sync()
        item.setData(ROLE_COMPARE_WINNER, entry)
        self._decorate_compare_winner_item(item)
        self._update_compare_history_label(item)
        return True

    def _decorate_compare_winner_item(self, item):
        entry = item.data(ROLE_COMPARE_WINNER)
        if not isinstance(entry, dict) or entry.get("preset") not in PRESETS:
            return
        input_path = item.data(ROLE_INPUT)
        text = item.text()
        if text.startswith("READY"):
            item.setText(
                f"READY    {Path(input_path).name}  "
                f"[A/B winner: {entry['preset']}]"
            )
        item.setToolTip(
            f"{input_path}\nLast A/B winner: {entry['preset']} "
            f"({entry['selected_at']})"
        )
        self._decorate_file_preset_item(item)

    def _update_compare_history_label(self, item=None):
        if not hasattr(self, "compare_history_label"):
            return
        if item is None:
            item = self._current_selected_item()
        entry = (
            item.data(ROLE_COMPARE_WINNER)
            if item is not None
            else None
        )
        if isinstance(entry, dict) and entry.get("preset") in PRESETS:
            self.compare_history_label.setText(
                _tr(
                    "Saved winner: {preset} ({when})",
                    preset=entry["preset"],
                    when=entry.get("selected_at", "unknown time"),
                )
            )
        else:
            self.compare_history_label.setText(
                _tr("No saved winner for this file")
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_workspace_layout"):
            self._update_responsive_layout(event.size().width() - 20)

    def showEvent(self, event):
        super().showEvent(event)
        self._update_responsive_layout()

    def _update_responsive_layout(self, available_width=None):
        viewport_width = available_width
        if viewport_width is None:
            viewport_width = (
                self.main_scroller.viewport().width()
                if hasattr(self, "main_scroller")
                else self.width()
            )
        if viewport_width < 100:
            viewport_width = self.width()
        em_width = max(8, self.fontMetrics().horizontalAdvance("M"))
        breakpoint = max(900, em_width * 80)
        compact = viewport_width < breakpoint
        if compact == getattr(self, "_responsive_compact", None):
            if compact:
                self._refresh_responsive_panel_minimums(True)
            return
        self._responsive_compact = compact
        direction = (
            QBoxLayout.Direction.TopToBottom
            if compact
            else QBoxLayout.Direction.LeftToRight
        )
        self._workspace_layout.setDirection(direction)
        self._workspace_layout.setStretch(0, 5 if not compact else 1)
        self._workspace_layout.setStretch(1, 6 if not compact else 1)
        if hasattr(self, "_header_layout"):
            self._header_layout.setDirection(
                QBoxLayout.Direction.TopToBottom
                if compact
                else QBoxLayout.Direction.LeftToRight
            )
            self._arrange_header_status(compact)
        self._arrange_adaptive_controls(compact)
        self._refresh_responsive_panel_minimums(compact)

    def _refresh_responsive_panel_minimums(self, compact):
        """Keep preferred child heights so compact grids never overlap."""
        for panel in self._content_root.findChildren(QFrame):
            if panel.objectName() != "panel":
                continue
            if (
                not compact
                and panel is not getattr(self, "_log_panel", None)
            ):
                panel.setMinimumHeight(0)
                continue
            layout = panel.layout()
            if layout is None:
                continue
            layout.invalidate()
            margins = layout.contentsMargins()
            preferred_height = margins.top() + margins.bottom()
            if layout.count() > 1:
                preferred_height += layout.spacing() * (layout.count() - 1)
            available_width = max(
                1,
                panel.width() - margins.left() - margins.right(),
            )
            for index in range(layout.count()):
                item = layout.itemAt(index)
                item_height = max(
                    item.minimumSize().height(),
                    item.sizeHint().height(),
                )
                if item.hasHeightForWidth():
                    item_height = max(
                        item_height,
                        item.heightForWidth(available_width),
                    )
                preferred_height += item_height
            panel.setMinimumHeight(preferred_height + 2)

    @staticmethod
    def _place_grid(layout, placements, stretch_columns=()):
        while layout.count():
            layout.takeAt(0)
        for column in range(8):
            layout.setColumnStretch(column, 0)
        for widget, row, column, row_span, column_span in placements:
            layout.addWidget(
                widget,
                row,
                column,
                row_span,
                column_span,
            )
        for column, stretch in stretch_columns:
            layout.setColumnStretch(column, stretch)

    def _arrange_adaptive_controls(self, compact):
        for button in self.findChildren(ResponsiveButton):
            button.set_compact(compact)
        if hasattr(self, "param_rows"):
            for row in self.param_rows.values():
                row.set_parent_compact(compact)

        if hasattr(self, "_queue_primary_grid"):
            if compact:
                placements = (
                    (self.btn_browse, 0, 0, 1, 1),
                    (self.btn_remove, 1, 0, 1, 1),
                    (self.btn_clear, 2, 0, 1, 1),
                    (self.file_count_label, 3, 0, 1, 1),
                )
                stretches = ((0, 1),)
            else:
                placements = (
                    (self.btn_browse, 0, 0, 1, 1),
                    (self.btn_remove, 0, 1, 1, 1),
                    (self.btn_clear, 0, 2, 1, 1),
                    (self.file_count_label, 0, 4, 1, 1),
                )
                stretches = ((3, 1),)
            self._place_grid(
                self._queue_primary_grid,
                placements,
                stretches,
            )

        if hasattr(self, "_queue_history_grid"):
            placements = (
                (
                    self.btn_resume_batch,
                    0,
                    0,
                    1,
                    1,
                ),
                (
                    self.btn_retry_failed,
                    1 if compact else 0,
                    0 if compact else 1,
                    1,
                    1,
                ),
            )
            self._place_grid(
                self._queue_history_grid,
                placements,
                ((0 if compact else 2, 1),),
            )

        if hasattr(self, "_queue_preset_grid"):
            placements = (
                (self.queue_preset_label, 0, 0, 1, 1),
                (self.queue_preset_combo, 0, 1, 1, 1),
            )
            self._place_grid(
                self._queue_preset_grid,
                placements,
                ((1, 1),),
            )

        if hasattr(self, "_preview_controls_grid"):
            if compact:
                placements = (
                    (self.btn_render_preview, 0, 0, 1, 1),
                    (self.btn_compare, 0, 1, 1, 1),
                    (self.btn_play_orig, 1, 0, 1, 1),
                    (self.btn_play_proc, 1, 1, 1, 1),
                    (self.preview_offset_label, 2, 0, 1, 1),
                    (self.preview_offset_spin, 2, 1, 1, 1),
                    (self.preview_label, 3, 0, 1, 2),
                )
                stretches = ((0, 1), (1, 1))
            else:
                placements = (
                    (self.btn_render_preview, 0, 0, 1, 1),
                    (self.btn_compare, 0, 1, 1, 1),
                    (self.btn_play_orig, 0, 2, 1, 1),
                    (self.btn_play_proc, 0, 3, 1, 1),
                    (self.preview_offset_label, 0, 4, 1, 1),
                    (self.preview_offset_spin, 0, 5, 1, 1),
                    (self.preview_label, 1, 0, 1, 6),
                )
                stretches = ((5, 1),)
            self._place_grid(
                self._preview_controls_grid,
                placements,
                stretches,
            )

        if hasattr(self, "_preset_controls_grid"):
            if compact:
                placements = (
                    (self.preset_label, 0, 0, 1, 1),
                    (self.preset_combo, 0, 1, 1, 1),
                    (self.btn_save_preset, 1, 0, 1, 1),
                    (self.btn_load_preset, 1, 1, 1, 1),
                )
                stretches = ((1, 1),)
            else:
                placements = (
                    (self.preset_label, 0, 0, 1, 1),
                    (self.preset_combo, 0, 1, 1, 1),
                    (self.btn_save_preset, 0, 2, 1, 1),
                    (self.btn_load_preset, 0, 3, 1, 1),
                )
                stretches = ((4, 1),)
            self._place_grid(
                self._preset_controls_grid,
                placements,
                stretches,
            )

        if hasattr(self, "_toggle_controls_grid"):
            placements = (
                (self.spectral_scan_check, 0, 0, 1, 1),
                (
                    self.meta_check,
                    1 if compact else 0,
                    0 if compact else 1,
                    1,
                    1,
                ),
            )
            self._place_grid(
                self._toggle_controls_grid,
                placements,
                ((0, 1), (1, 1)),
            )

        if hasattr(self, "_format_controls_grid"):
            if compact:
                placements = (
                    (self.format_label, 0, 0, 1, 1),
                    (self.format_combo, 0, 1, 1, 1),
                    (self.worker_count_label, 1, 0, 1, 1),
                    (self.worker_count_spin, 1, 1, 1, 1),
                    (self.btn_open_output, 2, 0, 1, 2),
                )
            else:
                placements = (
                    (self.format_label, 0, 0, 1, 1),
                    (self.format_combo, 0, 1, 1, 1),
                    (self.worker_count_label, 0, 2, 1, 1),
                    (self.worker_count_spin, 0, 3, 1, 1),
                    (self.btn_open_output, 0, 5, 1, 1),
                )
            self._place_grid(
                self._format_controls_grid,
                placements,
                ((1 if compact else 2, 1),),
            )

        if hasattr(self, "_directory_controls_grid"):
            if compact:
                placements = (
                    (self.directory_label, 0, 0, 1, 2),
                    (self.output_dir, 1, 0, 1, 1),
                    (self.btn_browse_output, 1, 1, 1, 1),
                )
            else:
                placements = (
                    (self.directory_label, 0, 0, 1, 1),
                    (self.output_dir, 0, 1, 1, 1),
                    (self.btn_browse_output, 0, 2, 1, 1),
                )
            self._place_grid(
                self._directory_controls_grid,
                placements,
                ((0 if compact else 1, 1),),
            )

        if hasattr(self, "_render_controls_grid"):
            if compact:
                placements = (
                    (self.btn_process, 0, 0, 1, 1),
                    (self.btn_cancel, 1, 0, 1, 1),
                    (self.progress, 2, 0, 1, 1),
                )
                stretches = ((0, 1),)
            else:
                placements = (
                    (self.btn_process, 0, 0, 1, 1),
                    (self.btn_cancel, 0, 1, 1, 1),
                    (self.progress, 0, 2, 1, 1),
                )
                stretches = ((2, 1),)
            self._place_grid(
                self._render_controls_grid,
                placements,
                stretches,
            )

        if hasattr(self, "_log_controls_grid"):
            if compact:
                placements = (
                    (self.btn_open_log, 0, 0, 1, 2),
                    (self.btn_export_support, 1, 0, 1, 2),
                    (self.btn_clear_logs, 2, 0, 1, 2),
                    (self.retention_label, 3, 0, 1, 1),
                    (self.retention_spin, 3, 1, 1, 1),
                    (self.redact_logs_check, 4, 0, 1, 2),
                    (self.btn_diagnostic_privacy, 5, 0, 1, 2),
                )
            else:
                placements = (
                    (self.btn_open_log, 0, 0, 1, 1),
                    (self.btn_export_support, 0, 1, 1, 1),
                    (self.btn_clear_logs, 0, 2, 1, 1),
                    (self.retention_label, 1, 0, 1, 1),
                    (self.retention_spin, 1, 1, 1, 1),
                    (self.redact_logs_check, 2, 0, 1, 2),
                    (self.btn_diagnostic_privacy, 1, 2, 2, 1),
                )
            self._place_grid(
                self._log_controls_grid,
                placements,
                ((0, 1), (1, 1), (2, 1)),
            )

        if hasattr(self, "_compare_controls_grid"):
            if compact:
                placements = [(self.compare_label, 0, 0, 1, 2)]
                for index, button in enumerate(
                    self.compare_buttons.values()
                ):
                    placements.append(
                        (
                            button,
                            1 + index // 2,
                            index % 2,
                            1,
                            1,
                        )
                    )
                placements.append(
                    (self.btn_apply_compare, 3, 0, 1, 2)
                )
                placements.append(
                    (self.compare_history_label, 4, 0, 1, 2)
                )
                stretches = ((0, 1), (1, 1))
            else:
                placements = [(self.compare_label, 0, 0, 1, 1)]
                placements.extend(
                    (button, 0, index + 1, 1, 1)
                    for index, button in enumerate(
                        self.compare_buttons.values()
                    )
                )
                placements.append(
                    (self.btn_apply_compare, 0, 5, 1, 1)
                )
                placements.append(
                    (self.compare_history_label, 1, 0, 1, 6)
                )
                stretches = ((6, 1),)
            self._place_grid(
                self._compare_controls_grid,
                tuple(placements),
                stretches,
            )

    def _standard_icon(self, pixmap):
        return self.style().standardIcon(pixmap)

    def _decorate_button(self, button, pixmap=None, object_name=None):
        if object_name:
            button.setObjectName(object_name)
        if pixmap is not None:
            button.setIcon(self._standard_icon(pixmap))
        label = button.text().strip() or object_name or "Button"
        _set_accessibility(button, label, f"{label} button.")
        return button

    def _configure_accessibility(self):
        _set_accessibility(
            self.scope_label,
            "Usage and evidence scope",
            f"{RIGHTS_ONLY_NOTICE} {EVIDENCE_NOTICE}",
        )
        _set_accessibility(
            self.queue_status_label,
            "Queue status: 0 files",
            "Current number of files in the queue.",
        )
        _set_accessibility(
            self.preset_status_label,
            "Preset status: Extreme",
            "Current processing preset.",
        )
        _set_accessibility(
            self.format_status_label,
            "Format status: WAV",
            "Current output format.",
        )
        _set_accessibility(
            self.render_status_label,
            "Render status: Ready",
            "Current render lifecycle state.",
        )
        _set_accessibility(
            self.file_list,
            "Audio queue",
            (
                "Drop audio files and select queued files. Press Delete or "
                "Backspace to remove selection; press Alt plus Up or Down "
                "Arrow to reorder it."
            ),
        )
        _set_accessibility(
            self.btn_browse,
            "Browse audio files",
            "Add audio files to the queue. Shortcut Control plus O.",
        )
        _set_accessibility(self.btn_remove, "Remove selected files", "Remove selected files from the queue.")
        _set_accessibility(self.btn_clear, "Clear queue", "Remove every file from the queue.")
        _set_accessibility(
            self.btn_resume_batch,
            "Resume batch",
            (
                "Load a batch manifest and continue only pending or "
                "interrupted jobs. Shortcut Control plus R."
            ),
        )
        _set_accessibility(
            self.btn_retry_failed,
            "Retry failed batch jobs",
            (
                "Load a batch manifest and retry only failed or partial jobs. "
                "Shortcut Control plus Shift plus R."
            ),
        )
        _set_accessibility(
            self.queue_preset_combo,
            "Selected queue file preset",
            (
                "Assign Batch settings, a built-in preset, or a snapshot of "
                "current settings to the selected queue files."
            ),
        )
        _set_accessibility(
            self.btn_render_preview,
            "Render preview",
            (
                "Render a short preview; disabled until a file is selected. "
                "Shortcut Control plus P."
            ),
        )
        _set_accessibility(
            self.btn_compare,
            "Compare presets",
            (
                "Render one short sample per preset; disabled until a file "
                "is selected. Shortcut Control plus Shift plus P."
            ),
        )
        _set_accessibility(
            self.preview_offset_spin,
            "Preview start offset",
            (
                "Choose where preview and preset-comparison clips begin, "
                "from 0 to 7199 seconds."
            ),
        )
        _set_accessibility(self.btn_play_orig, "Play original", "Play the selected original file; disabled until audio is available.")
        _set_accessibility(self.btn_play_proc, "Play processed", "Play the selected processed file; disabled until output is available.")
        _set_accessibility(self.btn_open_log, "Open run log", "Open the latest persistent run log; disabled until a run starts.")
        _set_accessibility(
            self.btn_export_support,
            "Export support bundle",
            (
                "Create an atomic ZIP containing bounded diagnostic logs, "
                "environment details, and no audio, manifests, sidecars, or "
                "settings contents."
            ),
        )
        _set_accessibility(
            self.btn_clear_logs,
            "Clear logs",
            (
                "Confirm and delete persistent run logs after closing the "
                "active diagnostic lifecycle."
            ),
        )
        _set_accessibility(
            self.retention_spin,
            "Diagnostic log retention count",
            "Choose how many persistent run logs to retain, from 1 to 365.",
        )
        _set_accessibility(
            self.redact_logs_check,
            "Redact paths in diagnostics",
            (
                "Hide registered input and output locations, the home "
                "directory, temporary paths, and other absolute paths."
            ),
        )
        _set_accessibility(
            self.btn_diagnostic_privacy,
            "Privacy preview and data locations",
            (
                "Preview path redaction and identify local logs, settings, "
                "batch manifests, sidecars, previews, and support bundles."
            ),
        )
        _set_accessibility(self.preset_combo, "Preset", "Choose the processing preset.")
        _set_accessibility(
            self.btn_save_preset,
            "Save preset",
            "Save current settings to JSON. Shortcut Control plus S.",
        )
        _set_accessibility(
            self.btn_load_preset,
            "Load preset",
            (
                "Load settings from JSON. Shortcut Control plus Shift plus O."
            ),
        )
        _set_accessibility(
            self.spectral_scan_check,
            "Narrowband candidate scan",
            "Toggle local narrowband candidate scanning before spectral perturbation.",
        )
        _set_accessibility(
            self.meta_check,
            "Strip ordinary metadata",
            (
                "Remove ordinary tags from saved outputs. C2PA Content "
                "Credentials are inspected separately and require explicit "
                "acknowledgement before any transformed output is created."
            ),
        )
        _set_accessibility(
            self.format_combo,
            "Output format",
            "Choose WAV, FLAC, OGG, or ffmpeg-backed MP3/M4A output.",
        )
        _set_accessibility(
            self.worker_count_spin,
            "Parallel file workers",
            (
                "Choose how many queued files render at the same time, "
                "from 1 to 8. Higher values use more CPU, memory, and disk."
            ),
        )
        _set_accessibility(
            self.spectrogram_check,
            "Export spectrogram comparison",
            (
                "Write a bounded side-by-side before and after PNG for each "
                "usable full render."
            ),
        )
        _set_accessibility(
            self.loudness_report_check,
            "Export loudness and true-peak report",
            (
                "Write an ITU-R BS.1770-5 before and after integrated "
                "loudness and true-peak JSON report for each usable render."
            ),
        )
        _set_accessibility(self.btn_open_output, "Open output folder", "Open the current output directory in the file manager.")
        _set_accessibility(self.output_dir, "Output directory", "Edit the output directory for processed files.")
        _set_accessibility(self.btn_browse_output, "Browse output directory", "Choose the output directory for processed files.")
        _set_accessibility(
            self.btn_process,
            "Process all",
            (
                "Start processing every queued file. Shortcut Control plus "
                "Enter."
            ),
        )
        _set_accessibility(
            self.btn_cancel,
            "Cancel active render",
            (
                "Cancel the active batch, preview, or preset comparison. "
                "Shortcut Escape."
            ),
        )
        _set_accessibility(self.progress, "Render progress", "Shows current render progress.")
        _set_accessibility(self.log_box, "Session log", "Shows processing events, warnings, metrics, and diagnostics.")
        _set_accessibility(
            self.file_count_label,
            "Queue count: 0 files",
            "Current number of queued audio files.",
        )
        _set_accessibility(
            self.preview_label,
            "Preview target: Select a file",
            "Names the file selected for preview and playback.",
        )

        for name, btn in self.compare_buttons.items():
            _set_accessibility(
                btn,
                f"Play {name} compare sample",
                f"Play the rendered {name} preset comparison sample; disabled until compare completes.",
            )
        _set_accessibility(
            self.btn_apply_compare,
            "Save currently playing winner",
            (
                "Save the currently playing comparison preset as this "
                "file's winner and apply it to the main preset selector."
            ),
        )
        _set_accessibility(
            self.compare_history_label,
            "Saved comparison winner",
            "Shows the last comparison preset saved for the selected file.",
        )

    def _configure_shortcuts(self):
        shortcuts = (
            (self.btn_browse, "Ctrl+O"),
            (self.btn_resume_batch, "Ctrl+R"),
            (self.btn_retry_failed, "Ctrl+Shift+R"),
            (self.btn_render_preview, "Ctrl+P"),
            (self.btn_compare, "Ctrl+Shift+P"),
            (self.btn_save_preset, "Ctrl+S"),
            (self.btn_load_preset, "Ctrl+Shift+O"),
            (self.btn_open_output, "Ctrl+Shift+E"),
            (self.btn_process, "Ctrl+Return"),
            (self.btn_cancel, "Esc"),
        )
        for button, sequence in shortcuts:
            key_sequence = QKeySequence(sequence)
            button.setShortcut(key_sequence)
            shortcut_text = key_sequence.toString(
                QKeySequence.SequenceFormat.NativeText
            )
            tooltip = button.toolTip().rstrip()
            if tooltip and not tooltip.endswith((".", "!", "?")):
                tooltip += "."
            button.setToolTip(
                f"{tooltip} Shortcut: {shortcut_text}.".strip()
            )

    def _configure_tab_order(self):
        order = [
            self.btn_browse,
            self.btn_remove,
            self.btn_clear,
            self.btn_resume_batch,
            self.btn_retry_failed,
            self.queue_preset_combo,
            self.file_list,
            self.btn_render_preview,
            self.btn_compare,
            self.btn_play_orig,
            self.btn_play_proc,
            self.preview_offset_spin,
            self.btn_open_log,
            self.btn_export_support,
            self.btn_clear_logs,
            self.retention_spin,
            self.redact_logs_check,
            self.btn_diagnostic_privacy,
            self.preset_combo,
            self.btn_save_preset,
            self.btn_load_preset,
            self.spectral_scan_check,
            self.meta_check,
        ]
        for row in self.param_rows.values():
            order.extend([row.check, row.slider])
        order.extend([
            self.format_combo,
            self.worker_count_spin,
            self.spectrogram_check,
            self.loudness_report_check,
            self.btn_open_output,
            self.output_dir,
            self.btn_browse_output,
            self.btn_process,
            self.btn_cancel,
        ])
        for first, second in zip(order, order[1:]):
            self.setTabOrder(first, second)
        self._tab_order_widgets = order

    def _restore_session_state(self):
        self._session_restored_geometry = False
        settings = self._settings
        geo = settings.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
            self._session_restored_geometry = True
        out_dir = settings.value("session/output_dir")
        if out_dir and isinstance(out_dir, str):
            self.output_dir.setText(out_dir)
        fmt = settings.value("session/output_format")
        if fmt and isinstance(fmt, str):
            idx = self.format_combo.findText(fmt)
            if idx >= 0:
                self.format_combo.setCurrentIndex(idx)
        try:
            workers = int(settings.value(
                "session/parallel_file_workers",
                DEFAULT_PARALLEL_FILE_WORKERS,
            ))
            self.worker_count_spin.setValue(
                _validated_parallel_workers(workers)
            )
        except (TypeError, ValueError):
            self.worker_count_spin.setValue(DEFAULT_PARALLEL_FILE_WORKERS)
        spectrogram = settings.value("session/export_spectrogram", False)
        if isinstance(spectrogram, str):
            spectrogram = spectrogram.strip().lower() in {
                "1", "true", "yes", "on",
            }
        self.spectrogram_check.setChecked(bool(spectrogram))
        loudness_report = settings.value(
            "session/export_loudness_report",
            False,
        )
        if isinstance(loudness_report, str):
            loudness_report = loudness_report.strip().lower() in {
                "1", "true", "yes", "on",
            }
        self.loudness_report_check.setChecked(bool(loudness_report))
        try:
            preview_offset = float(settings.value(
                "session/preview_offset_seconds",
                0.0,
            ))
            if not np.isfinite(preview_offset) or preview_offset < 0:
                raise ValueError("invalid preview offset")
            self.preview_offset_spin.setValue(preview_offset)
        except (TypeError, ValueError):
            self.preview_offset_spin.setValue(0.0)
        preset = settings.value("session/preset")
        if preset and isinstance(preset, str):
            if preset in PRESETS:
                self.preset_combo.setCurrentText(preset)
            elif preset == "Custom":
                serialized = settings.value("session/config_json")
                try:
                    if not isinstance(serialized, str) or not serialized:
                        raise ConfigurationError(
                            "legacy session has no Custom parameter values"
                        )
                    document = _validate_preset_document(
                        json.loads(serialized)
                    )
                    self._apply_config(document["params"])
                    self.preset_combo.blockSignals(True)
                    self.preset_combo.setCurrentText("Custom")
                    self.preset_combo.blockSignals(False)
                    self._sync_header_stats()
                except (
                    ConfigurationError,
                    json.JSONDecodeError,
                    TypeError,
                ) as exc:
                    self.preset_combo.setCurrentText("Extreme")
                    self._log(
                        "Saved Custom settings were invalid or unavailable; "
                        f"restored Extreme instead ({exc})."
                    )
        browse = settings.value("session/last_browse_dir")
        if browse and isinstance(browse, str) and os.path.isdir(browse):
            self._last_browse_dir = browse
        preset_dir = settings.value("session/last_preset_dir")
        if preset_dir and isinstance(preset_dir, str) and os.path.isdir(preset_dir):
            self._last_preset_dir = preset_dir

    def _save_session_state(self):
        settings = self._settings
        params = self._get_params()
        preset_document = _create_preset_document(
            self.preset_combo.currentText(),
            {
                key: value
                for key, value in params.items()
                if key not in {"output_format", "c2pa_policy"}
            },
        )
        settings.setValue("window/geometry", self.saveGeometry())
        settings.setValue("session/output_dir", self.output_dir.text())
        settings.setValue("session/output_format", self.format_combo.currentText())
        settings.setValue(
            "session/parallel_file_workers",
            self.worker_count_spin.value(),
        )
        settings.setValue(
            "session/export_spectrogram",
            self.spectrogram_check.isChecked(),
        )
        settings.setValue(
            "session/export_loudness_report",
            self.loudness_report_check.isChecked(),
        )
        settings.setValue(
            "session/preview_offset_seconds",
            self.preview_offset_spin.value(),
        )
        settings.setValue("session/preset", self.preset_combo.currentText())
        settings.setValue(
            "session/config_json",
            json.dumps(
                preset_document,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        settings.setValue("session/last_browse_dir", self._last_browse_dir)
        settings.setValue("session/last_preset_dir", self._last_preset_dir)
        settings.sync()

    def _build_header(self):
        bar = QFrame()
        bar.setObjectName("topBar")
        self._header_layout = QBoxLayout(
            QBoxLayout.Direction.LeftToRight,
            bar,
        )
        self._header_layout.setContentsMargins(18, 14, 18, 14)
        self._header_layout.setSpacing(16)

        brand_widget = QWidget()
        brand_col = QVBoxLayout(brand_widget)
        brand_col.setContentsMargins(0, 0, 0, 0)
        brand_col.setSpacing(1)
        title = QLabel(APP_NAME)
        title.setObjectName("appTitle")
        _set_relative_font(title, 9.0, bold=True)
        self.scope_label = QLabel(_tr(
            "Rights-owned audio only\n"
            "Local metrics do not predict platform outcomes"
        ))
        self.scope_label.setObjectName("appSubtitle")
        self.scope_label.setWordWrap(True)
        _set_relative_font(self.scope_label, -1.0)
        self.scope_label.setToolTip(_tr(EVIDENCE_NOTICE))
        brand_col.addWidget(title)
        brand_col.addWidget(self.scope_label)
        self._header_layout.addWidget(brand_widget, 1)

        self.queue_status_label = QLabel("0 files")
        self.queue_status_label.setObjectName("statusPill")
        self.preset_status_label = QLabel("Extreme")
        self.preset_status_label.setObjectName("accentPill")
        self.format_status_label = QLabel("WAV")
        self.format_status_label.setObjectName("statusPill")
        self.render_status_label = QLabel(_tr("Ready"))
        self.render_status_label.setObjectName("statusPill")

        self._version_label = QLabel(f"v{VERSION}")
        self._version_label.setObjectName("sectionSubtitle")
        _set_relative_font(self._version_label, -1.0)
        self._header_status_widgets = (
            self.queue_status_label,
            self.preset_status_label,
            self.format_status_label,
            self.render_status_label,
            self._version_label,
        )
        status_widget = QWidget()
        self._header_status_grid = QGridLayout(status_widget)
        self._header_status_grid.setContentsMargins(0, 0, 0, 0)
        self._header_status_grid.setHorizontalSpacing(10)
        self._header_status_grid.setVerticalSpacing(8)
        self._arrange_header_status(False)
        self._header_layout.addWidget(status_widget)
        return bar

    def _arrange_header_status(self, compact):
        grid = self._header_status_grid
        for widget in self._header_status_widgets:
            grid.removeWidget(widget)
        if compact:
            positions = (
                (self.queue_status_label, 0, 0, 1, 1),
                (self.preset_status_label, 0, 1, 1, 1),
                (self.format_status_label, 1, 0, 1, 1),
                (self.render_status_label, 1, 1, 1, 1),
                (self._version_label, 2, 0, 1, 2),
            )
        else:
            positions = tuple(
                (widget, 0, index, 1, 1)
                for index, widget in enumerate(
                    self._header_status_widgets
                )
            )
        for widget, row, column, row_span, column_span in positions:
            grid.addWidget(
                widget,
                row,
                column,
                row_span,
                column_span,
            )
        grid.setColumnStretch(0, 1 if compact else 0)
        grid.setColumnStretch(1, 1 if compact else 0)
        self._version_label.setAlignment(
            Qt.AlignmentFlag.AlignTrailing
            | Qt.AlignmentFlag.AlignVCenter
        )

    def _make_panel(self, title, subtitle=None):
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        outer = QVBoxLayout(panel)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(12)

        head = QVBoxLayout()
        head.setSpacing(2)
        title_label = QLabel(_tr(title))
        title_label.setObjectName("sectionTitle")
        title_label.setWordWrap(True)
        _set_relative_font(title_label, 2.0, bold=True)
        head.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(_tr(subtitle))
            subtitle_label.setObjectName("sectionSubtitle")
            subtitle_label.setWordWrap(True)
            _set_relative_font(subtitle_label, -1.0)
            head.addWidget(subtitle_label)
        outer.addLayout(head)
        return panel, outer

    def _sync_header_stats(self):
        if not hasattr(self, 'queue_status_label'):
            return
        count = self.file_list.count() if hasattr(self, 'file_list') else 0
        count_text = _tr("{n} file", n=count)
        self.queue_status_label.setText(count_text)
        self.queue_status_label.setAccessibleName(
            f"Queue status: {count_text}"
        )
        if hasattr(self, 'preset_combo'):
            preset = self.preset_combo.currentText()
            self.preset_status_label.setText(preset)
            self.preset_status_label.setAccessibleName(
                f"Preset status: {preset}"
            )
        if hasattr(self, 'format_combo'):
            output_format = self.format_combo.currentText()
            self.format_status_label.setText(output_format)
            self.format_status_label.setAccessibleName(
                f"Format status: {output_format}"
            )

    def _set_render_state(self, text):
        if hasattr(self, 'render_status_label'):
            translated = _tr(text)
            self.render_status_label.setText(translated)
            self.render_status_label.setAccessibleName(
                _tr("Render status: {state}", state=translated)
            )

    # --- File section ---
    def _build_files(self):
        panel, lay = self._make_panel(
            "Queue",
            "Add audio files, reorder the batch, and track rendered outputs.",
        )

        self._queue_primary_grid = QGridLayout()
        self._queue_primary_grid.setSpacing(8)
        self.btn_browse = self._decorate_button(
            ResponsiveButton(_tr("Browse")),
            QStyle.StandardPixmap.SP_DialogOpenButton,
        )
        self.btn_browse.clicked.connect(self._on_browse)
        self.btn_remove = self._decorate_button(
            ResponsiveButton(_tr("Remove")),
            QStyle.StandardPixmap.SP_DialogCancelButton,
        )
        self.btn_remove.clicked.connect(self._on_remove_selected)
        self.btn_clear = ResponsiveButton(_tr("Clear"))
        self.btn_clear.clicked.connect(self._on_clear)

        self.file_count_label = QLabel(_tr("{n} file", n=0))
        self.file_count_label.setObjectName("countLabel")

        self._place_grid(
            self._queue_primary_grid,
            (
                (self.btn_browse, 0, 0, 1, 1),
                (self.btn_remove, 0, 1, 1, 1),
                (self.btn_clear, 0, 2, 1, 1),
                (self.file_count_label, 0, 4, 1, 1),
            ),
            ((3, 1),),
        )
        lay.addLayout(self._queue_primary_grid)

        self._queue_history_grid = QGridLayout()
        self._queue_history_grid.setSpacing(8)
        self.btn_resume_batch = self._decorate_button(
            ResponsiveButton(_tr("Resume Batch")),
            QStyle.StandardPixmap.SP_BrowserReload,
        )
        self.btn_resume_batch.setToolTip(
            _tr("Load a batch manifest and continue pending/interrupted jobs")
        )
        self.btn_resume_batch.clicked.connect(
            lambda: self._load_batch_manifest("pending")
        )
        self.btn_retry_failed = self._decorate_button(
            ResponsiveButton(_tr("Retry Failed")),
            QStyle.StandardPixmap.SP_MediaPlay,
        )
        self.btn_retry_failed.setToolTip(
            _tr(
                "Retry failed/partial jobs from the latest batch, or choose a "
                "batch manifest when no recent batch is available"
            )
        )
        self.btn_retry_failed.clicked.connect(self._retry_failed_batch)
        self._place_grid(
            self._queue_history_grid,
            (
                (self.btn_resume_batch, 0, 0, 1, 1),
                (self.btn_retry_failed, 0, 1, 1, 1),
            ),
            ((2, 1),),
        )
        lay.addLayout(self._queue_history_grid)

        self._queue_preset_grid = QGridLayout()
        self._queue_preset_grid.setSpacing(8)
        self.queue_preset_label = QLabel(_tr("Selected preset:"))
        self.queue_preset_combo = QComboBox()
        self.queue_preset_combo.addItems([
            _tr(QUEUE_PRESET_INHERIT),
            *PRESETS.keys(),
            _tr(QUEUE_PRESET_CURRENT),
        ])
        self.queue_preset_combo.setEnabled(False)
        self.queue_preset_label.setBuddy(self.queue_preset_combo)
        self.queue_preset_combo.setToolTip(
            _tr(
                "Assign a preset to selected files; Batch settings follows "
                "the main controls at render time"
            )
        )
        self.queue_preset_combo.currentTextChanged.connect(
            self._on_queue_preset_changed
        )
        self._place_grid(
            self._queue_preset_grid,
            (
                (self.queue_preset_label, 0, 0, 1, 1),
                (self.queue_preset_combo, 0, 1, 1, 1),
            ),
            ((1, 1),),
        )
        lay.addLayout(self._queue_preset_grid)

        self.file_list = DropListWidget()
        self.file_list.setMinimumHeight(140)
        self.file_list.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Ignored,
        )
        self.file_list.filesDropped.connect(self._add_files)
        self.file_list.removeRequested.connect(self._on_remove_selected)
        self.file_list.orderChanged.connect(
            self._on_keyboard_queue_reordered
        )
        self.file_list.itemSelectionChanged.connect(self._update_preview_ui)
        self.file_list.itemSelectionChanged.connect(
            self._sync_queue_preset_control
        )
        lay.addWidget(self.file_list)

        self.queue_activity_label = QLabel(_tr(
            "Queue is empty. Browse for audio or drop files/folders here."
        ))
        self.queue_activity_label.setObjectName("hintLabel")
        self.queue_activity_label.setWordWrap(True)
        self.queue_activity_label.setAccessibleName(
            "Queue status: Queue is empty. Add audio to begin."
        )
        lay.addWidget(self.queue_activity_label)

        hint = QLabel(_tr(
            "Drop files here - drag to reorder - WAV, MP3, FLAC, OGG, "
            "AIFF, Opus"
        ))
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        _set_relative_font(hint, -1.0)
        lay.addWidget(hint)

        return panel

    # --- Settings section ---
    def _build_settings(self):
        panel, lay = self._make_panel(
            "Processing Pipeline",
            "Tune the transform profile before rendering the batch.",
        )

        # Preset row
        self._preset_controls_grid = QGridLayout()
        self._preset_controls_grid.setSpacing(8)
        self.preset_label = QLabel(_tr("Preset:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(PRESETS.keys()) + ['Custom'])
        self.preset_combo.setCurrentText('Extreme')
        self.preset_combo.currentTextChanged.connect(self._on_preset)
        self.preset_label.setBuddy(self.preset_combo)

        self.btn_save_preset = self._decorate_button(
            ResponsiveButton(_tr("Save")),
            QStyle.StandardPixmap.SP_DialogSaveButton,
        )
        self.btn_save_preset.setToolTip(
            _tr("Save current settings to a JSON file")
        )
        self.btn_save_preset.clicked.connect(self._save_preset)

        self.btn_load_preset = self._decorate_button(
            ResponsiveButton(_tr("Load")),
            QStyle.StandardPixmap.SP_DialogOpenButton,
        )
        self.btn_load_preset.setToolTip(
            _tr("Load settings from a JSON file")
        )
        self.btn_load_preset.clicked.connect(self._load_preset)
        self._place_grid(
            self._preset_controls_grid,
            (
                (self.preset_label, 0, 0, 1, 1),
                (self.preset_combo, 0, 1, 1, 1),
                (self.btn_save_preset, 0, 2, 1, 1),
                (self.btn_load_preset, 0, 3, 1, 1),
            ),
            ((4, 1),),
        )
        lay.addLayout(self._preset_controls_grid)

        self._toggle_controls_grid = QGridLayout()
        self._toggle_controls_grid.setHorizontalSpacing(16)
        self._toggle_controls_grid.setVerticalSpacing(8)
        self.spectral_scan_check = QCheckBox(_tr("Narrowband Scan"))
        self.spectral_scan_check.setChecked(True)
        self.spectral_scan_check.stateChanged.connect(lambda _: self._on_param_changed())

        self.meta_check = QCheckBox(_tr("Strip Ordinary Metadata"))
        self.meta_check.setChecked(True)
        self.meta_check.stateChanged.connect(lambda _: self._on_param_changed())
        self._place_grid(
            self._toggle_controls_grid,
            (
                (self.spectral_scan_check, 0, 0, 1, 1),
                (self.meta_check, 0, 1, 1, 1),
            ),
            ((0, 1), (1, 1)),
        )
        lay.addLayout(self._toggle_controls_grid)

        # Param rows
        self.param_rows = {}
        param_scroller = QScrollArea()
        param_scroller.setWidgetResizable(True)
        param_scroller.setMinimumHeight(220)
        param_scroller.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Ignored,
        )
        param_scroller.setFrameShape(QFrame.Shape.NoFrame)
        param_container = QWidget()
        param_lay = QVBoxLayout(param_container)
        param_lay.setContentsMargins(0, 0, 0, 0)
        param_lay.setSpacing(8)
        for key, label, mn, mx, df, suf, dec, ek, *rest in PARAM_DEFS:
            dfact = rest[0] if rest else 1.0
            row = ParamRow(key, label, mn, mx, df, suf, dec, ek, dfact)
            row.changed.connect(self._on_param_changed)
            self.param_rows[key] = row
            param_lay.addWidget(row)
        param_lay.addStretch()
        param_scroller.setWidget(param_container)
        lay.addWidget(param_scroller, 1)

        self._apply_preset('Extreme')
        return panel

    # --- Output section ---
    def _build_output(self):
        panel, lay = self._make_panel(
            "Destination",
            "Choose format and output directory for processed files.",
        )
        self._format_controls_grid = QGridLayout()
        self._format_controls_grid.setSpacing(8)
        self.format_label = QLabel(_tr("Format:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems([fmt.upper() for fmt in _available_output_formats()])
        if not _check_ffmpeg():
            self.format_combo.setToolTip(
                _tr("MP3/M4A export requires ffmpeg in PATH")
            )
        else:
            missing = [f.upper() for f in FFMPEG_FORMAT_ENCODERS if not _ffmpeg_encoder_available(f)]
            if missing:
                self.format_combo.setToolTip(
                    _tr(
                        "ffmpeg lacks encoders for: {encoders}",
                        encoders=', '.join(missing),
                    )
                )
        self.format_combo.currentTextChanged.connect(lambda _: self._sync_header_stats())
        self.format_combo.setMinimumWidth(120)
        self.format_label.setBuddy(self.format_combo)
        self.worker_count_label = QLabel(_tr("Parallel files:"))
        self.worker_count_spin = QSpinBox()
        self.worker_count_spin.setRange(1, MAX_PARALLEL_FILE_WORKERS)
        self.worker_count_spin.setValue(DEFAULT_PARALLEL_FILE_WORKERS)
        self.worker_count_spin.setSuffix(_tr(" workers"))
        self.worker_count_label.setBuddy(self.worker_count_spin)
        self.worker_count_spin.setToolTip(
            _tr(
                "Render this many queued files concurrently; higher values "
                "use more CPU, memory, and disk"
            )
        )
        self.btn_open_output = self._decorate_button(
            ResponsiveButton(_tr("Open")),
            QStyle.StandardPixmap.SP_DirOpenIcon,
        )
        self.btn_open_output.setToolTip(
            _tr("Open output directory in file manager")
        )
        self.btn_open_output.clicked.connect(self._open_output)
        self._place_grid(
            self._format_controls_grid,
            (
                (self.format_label, 0, 0, 1, 1),
                (self.format_combo, 0, 1, 1, 1),
                (self.worker_count_label, 0, 2, 1, 1),
                (self.worker_count_spin, 0, 3, 1, 1),
                (self.btn_open_output, 0, 5, 1, 1),
            ),
            ((2, 1),),
        )
        lay.addLayout(self._format_controls_grid)

        self.spectrogram_check = QCheckBox(
            _tr("Export before/after spectrogram PNG")
        )
        self.spectrogram_check.setChecked(False)
        self.spectrogram_check.setToolTip(
            _tr(
                "Write a bounded full-file comparison image beside each "
                "usable output"
            )
        )
        lay.addWidget(self.spectrogram_check)
        self.loudness_report_check = QCheckBox(
            _tr("Export loudness + true-peak JSON")
        )
        self.loudness_report_check.setChecked(False)
        self.loudness_report_check.setToolTip(
            _tr(
                "Write a before/after integrated loudness and true-peak "
                "JSON report beside each usable output"
            )
        )
        lay.addWidget(self.loudness_report_check)

        self._directory_controls_grid = QGridLayout()
        self._directory_controls_grid.setSpacing(8)
        self.directory_label = QLabel(_tr("Directory:"))
        self.output_dir = QLineEdit(DEFAULT_OUTPUT)
        self.output_dir.setMinimumWidth(160)
        self.directory_label.setBuddy(self.output_dir)
        self.btn_browse_output = self._decorate_button(
            ResponsiveButton(""),
            QStyle.StandardPixmap.SP_DirOpenIcon,
            "iconButton",
        )
        self.btn_browse_output.clicked.connect(self._browse_output)
        self._place_grid(
            self._directory_controls_grid,
            (
                (self.directory_label, 0, 0, 1, 1),
                (self.output_dir, 0, 1, 1, 1),
                (self.btn_browse_output, 0, 2, 1, 1),
            ),
            ((1, 1),),
        )
        lay.addLayout(self._directory_controls_grid)

        return panel

    # --- Controls ---
    def _build_controls(self):
        panel, lay = self._make_panel(
            "Render",
            "Start the full batch or stop an active render.",
        )
        self._render_controls_grid = QGridLayout()
        self._render_controls_grid.setSpacing(10)

        self.btn_process = self._decorate_button(
            ResponsiveButton(_tr("Process All")),
            QStyle.StandardPixmap.SP_MediaPlay,
            "processBtn",
        )
        _set_relative_font(self.btn_process, 2.0, bold=True)
        self.btn_process.clicked.connect(self._on_process)

        self.btn_cancel = self._decorate_button(
            ResponsiveButton(_tr("Cancel")),
            QStyle.StandardPixmap.SP_MediaStop,
            "cancelBtn",
        )
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self._place_grid(
            self._render_controls_grid,
            (
                (self.btn_process, 0, 0, 1, 1),
                (self.btn_cancel, 0, 1, 1, 1),
                (self.progress, 0, 2, 1, 1),
            ),
            ((2, 1),),
        )
        lay.addLayout(self._render_controls_grid)

        return panel

    # --- Preview ---
    def _build_preview(self):
        panel, outer = self._make_panel(
            "Monitor",
            "Render short previews, compare presets, and audition results.",
        )

        # Row 1: render + playback controls
        self._preview_controls_grid = QGridLayout()
        self._preview_controls_grid.setSpacing(8)
        self.btn_render_preview = self._decorate_button(
            ResponsiveButton(_tr("Preview")),
            QStyle.StandardPixmap.SP_BrowserReload,
        )
        self.btn_render_preview.setToolTip(
            _tr(
                "Process {seconds} seconds of the selected file from the "
                "chosen start offset "
                "with current settings so you can hear the result before committing.",
                seconds=int(PREVIEW_DURATION_SEC),
            )
        )
        self.btn_render_preview.clicked.connect(self._on_render_preview)
        self.btn_render_preview.setEnabled(False)

        self.btn_compare = self._decorate_button(
            ResponsiveButton(_tr("Compare")),
            QStyle.StandardPixmap.SP_MediaPlay,
        )
        self.btn_compare.setToolTip(
            _tr(
                "Render a {seconds}s sample from the chosen start offset "
                "with each built-in preset so you "
                "can A/B/C/D audition them, then apply your favorite.",
                seconds=int(COMPARE_DURATION_SEC),
            )
        )
        self.btn_compare.clicked.connect(self._on_compare_presets)
        self.btn_compare.setEnabled(False)

        self.btn_play_orig = self._decorate_button(
            ResponsiveButton(_tr("Original")),
            QStyle.StandardPixmap.SP_MediaPlay,
        )
        self.btn_play_orig.clicked.connect(lambda: self._toggle_play('original'))
        self.btn_play_orig.setEnabled(False)

        self.btn_play_proc = self._decorate_button(
            ResponsiveButton(_tr("Processed")),
            QStyle.StandardPixmap.SP_MediaPlay,
        )
        self.btn_play_proc.clicked.connect(lambda: self._toggle_play('processed'))
        self.btn_play_proc.setEnabled(False)
        self.preview_offset_label = QLabel(_tr("Start:"))
        self.preview_offset_spin = QDoubleSpinBox()
        self.preview_offset_spin.setRange(
            0.0,
            MAX_AUDIO_DURATION_SECONDS - 1.0,
        )
        self.preview_offset_spin.setDecimals(1)
        self.preview_offset_spin.setSingleStep(5.0)
        self.preview_offset_spin.setSuffix(_tr(" s"))
        self.preview_offset_label.setBuddy(self.preview_offset_spin)
        self.preview_offset_spin.setToolTip(
            _tr("Start time for Preview and Compare clips")
        )
        self.preview_label = QLabel(_tr("Select a file"))
        self.preview_label.setObjectName("nowPlaying")
        self.preview_label.setWordWrap(True)
        self._place_grid(
            self._preview_controls_grid,
            (
                (self.btn_render_preview, 0, 0, 1, 1),
                (self.btn_compare, 0, 1, 1, 1),
                (self.btn_play_orig, 0, 2, 1, 1),
                (self.btn_play_proc, 0, 3, 1, 1),
                (self.preview_offset_label, 0, 4, 1, 1),
                (self.preview_offset_spin, 0, 5, 1, 1),
                (self.preview_label, 1, 0, 1, 6),
            ),
            ((5, 1),),
        )
        outer.addLayout(self._preview_controls_grid)

        # Row 2: compare panel (hidden until Compare Presets is rendered)
        self.compare_panel = QWidget()
        self.compare_panel.setObjectName("comparePanel")
        self._compare_controls_grid = QGridLayout(self.compare_panel)
        self._compare_controls_grid.setContentsMargins(0, 2, 0, 0)
        self._compare_controls_grid.setSpacing(8)
        self.compare_label = QLabel(_tr("A/B:"))
        self.compare_buttons = {}
        for name in PRESETS.keys():
            btn = ResponsiveButton(name)
            btn.setObjectName("compareButton")
            btn.setToolTip(_tr("Play the {name} sample", name=name))
            btn.setEnabled(False)
            btn.clicked.connect(lambda _checked=False, n=name: self._play_compare(n))
            self.compare_buttons[name] = btn
        self.btn_apply_compare = ResponsiveButton(
            _tr("Save Playing Winner")
        )
        self.btn_apply_compare.setToolTip(
            _tr(
                "Save the currently playing preset as this file's winner "
                "and make it active for Process All"
            )
        )
        self.btn_apply_compare.setEnabled(False)
        self.btn_apply_compare.clicked.connect(self._apply_playing_compare_preset)
        self.compare_history_label = QLabel(
            _tr("No saved winner for this file")
        )
        self.compare_history_label.setWordWrap(True)
        compare_placements = [(self.compare_label, 0, 0, 1, 1)]
        compare_placements.extend(
            (button, 0, index + 1, 1, 1)
            for index, button in enumerate(self.compare_buttons.values())
        )
        compare_placements.append(
            (self.btn_apply_compare, 0, 5, 1, 1)
        )
        compare_placements.append(
            (self.compare_history_label, 1, 0, 1, 6)
        )
        self._place_grid(
            self._compare_controls_grid,
            tuple(compare_placements),
            ((6, 1),),
        )
        self.compare_panel.setVisible(False)
        outer.addWidget(self.compare_panel)

        if not _MULTIMEDIA_OK:
            for b in (self.btn_play_orig, self.btn_play_proc,
                      self.btn_render_preview, self.btn_compare):
                b.setEnabled(False)
            unavailable = _tr("Requires PyQt6 QtMultimedia module")
            self.btn_render_preview.setToolTip(unavailable)
            self.btn_compare.setToolTip(unavailable)
            self.preview_label.setText(
                _tr("(PyQt6 Multimedia not available)")
            )

        return panel

    # --- Log ---
    def _build_log(self):
        panel, lay = self._make_panel(
            "Session Log",
            "Processing events, privacy controls, and exportable diagnostics.",
        )
        self._log_panel = panel
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(120)
        self.log_box.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        lay.addWidget(self.log_box)
        self._log_controls_grid = QGridLayout()
        self._log_controls_grid.setSpacing(8)
        self.btn_open_log = self._decorate_button(
            ResponsiveButton(_tr("Open Log")),
            QStyle.StandardPixmap.SP_FileIcon,
        )
        self.btn_open_log.setToolTip(
            _tr("Open the latest persistent run log")
        )
        self.btn_open_log.setEnabled(False)
        self.btn_open_log.clicked.connect(self._open_run_log)

        self.btn_clear_logs = self._decorate_button(
            ResponsiveButton(_tr("Clear Logs")),
            QStyle.StandardPixmap.SP_TrashIcon,
        )
        self.btn_clear_logs.setToolTip(
            _tr("Delete all persistent run logs")
        )
        self.btn_clear_logs.clicked.connect(self._clear_all_logs)

        self.btn_export_support = self._decorate_button(
            ResponsiveButton(_tr("Export Support")),
            QStyle.StandardPixmap.SP_DialogSaveButton,
        )
        self.btn_export_support.setToolTip(
            _tr("Export bounded diagnostics as an atomic ZIP support bundle")
        )
        self.btn_export_support.clicked.connect(self._export_support_bundle)

        self.retention_label = QLabel(_tr("Retain:"))
        self.retention_spin = QSpinBox()
        self.retention_spin.setRange(
            MIN_RETAINED_LOGS,
            MAX_CONFIGURABLE_RETAINED_LOGS,
        )
        self.retention_spin.setSuffix(" logs")
        try:
            retained_logs = int(self._settings.value(
                "diagnostics/retained_logs",
                MAX_RETAINED_LOGS,
            ))
            retained_logs = _validated_log_retention(retained_logs)
        except (TypeError, ValueError):
            retained_logs = MAX_RETAINED_LOGS
        self.retention_spin.setValue(retained_logs)
        self.retention_label.setBuddy(self.retention_spin)
        self.retention_spin.editingFinished.connect(
            self._save_diagnostic_preferences
        )

        self.redact_logs_check = QCheckBox(_tr("Redact paths"))
        redact_value = self._settings.value(
            "diagnostics/redact_paths",
            True,
        )
        if isinstance(redact_value, str):
            redact_value = redact_value.strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
        self.redact_logs_check.setChecked(bool(redact_value))
        self.redact_logs_check.toggled.connect(
            self._save_diagnostic_preferences
        )

        self.btn_diagnostic_privacy = self._decorate_button(
            ResponsiveButton(_tr("Privacy & Locations")),
            QStyle.StandardPixmap.SP_MessageBoxInformation,
        )
        self.btn_diagnostic_privacy.setToolTip(
            _tr(
                "Preview path redaction and show every local diagnostics location"
            )
        )
        self.btn_diagnostic_privacy.clicked.connect(
            self._show_diagnostic_privacy
        )
        self._place_grid(
            self._log_controls_grid,
            (
                (self.btn_open_log, 0, 0, 1, 1),
                (self.btn_export_support, 0, 1, 1, 1),
                (self.btn_clear_logs, 0, 2, 1, 1),
                (self.retention_label, 1, 0, 1, 1),
                (self.retention_spin, 1, 1, 1, 1),
                (self.redact_logs_check, 2, 0, 1, 2),
                (self.btn_diagnostic_privacy, 1, 2, 2, 1),
            ),
            ((0, 1), (1, 1), (2, 1)),
        )
        lay.addLayout(self._log_controls_grid)
        return panel

    # --- File list slots ---
    def _on_browse(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Audio Files", self._last_browse_dir,
            "Audio Files (*.wav *.mp3 *.flac *.ogg *.aiff *.aif *.opus);;All Files (*)",
        )
        if files:
            self._last_browse_dir = str(Path(files[0]).parent)
            self._add_files(files)

    def _browse_output(self):
        d = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", self.output_dir.text(),
        )
        if d:
            self.output_dir.setText(d)

    def _open_output(self):
        out_dir = self.output_dir.text().strip() or DEFAULT_OUTPUT
        if not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except OSError as e:
                self._log(f"Cannot create output directory: {e}")
                return
        if not _open_in_file_manager(out_dir):
            self._log(f"Could not open: {out_dir}")

    def _on_clear(self):
        self._stop_playback()
        self.file_list.clear()
        self._update_file_count()
        self._update_preview_ui()
        self._sync_queue_preset_control()

    def _on_remove_selected(self):
        self._stop_playback()
        for item in reversed(self.file_list.selectedItems()):
            self.file_list.takeItem(self.file_list.row(item))
        self._update_file_count()
        self._update_preview_ui()
        self._sync_queue_preset_control()

    def _on_keyboard_queue_reordered(self, message):
        self._update_preview_ui()
        self._log(f"Queue: {message}")
        description = self.file_list.accessibleDescription().split(
            " Last action:",
            1,
        )[0]
        self.file_list.setAccessibleDescription(
            f"{description} Last action: {message}"
        )

    def _add_files(self, paths):
        paths = [str(path) for path in paths if str(path)]
        if not paths:
            return
        if self._active_workers():
            self._log(
                "Cannot start file discovery while another operation is active."
            )
            return
        existing = set()
        for i in range(self.file_list.count()):
            raw = self.file_list.item(i).data(ROLE_INPUT)
            if raw:
                existing.add(os.path.normcase(os.path.abspath(raw)))

        self._discovery_terminal_state = "Scan failed"
        self.discovery_worker = FileDiscoveryWorker(paths, existing)
        active_worker = self.discovery_worker
        self.discovery_worker.progress_signal.connect(
            self._on_discovery_progress
        )
        self.discovery_worker.discovery_done.connect(
            self._on_discovery_done
        )
        self.discovery_worker.finished.connect(
            lambda worker=active_worker:
                self._on_discovery_thread_finished(worker)
        )
        self._set_discovery_ui(True)
        self.discovery_worker.start()

    def _set_queue_notice(self, text):
        translated = _tr(str(text))
        self.queue_activity_label.setText(translated)
        self.queue_activity_label.setAccessibleName(
            _tr("Queue status: {state}", state=translated)
        )

    def _on_discovery_progress(self, scanned, matched, current_path):
        current_name = Path(current_path).name if current_path else ""
        suffix = f" Current folder: {current_name}." if current_name else ""
        self._set_queue_notice(
            f"Scanning: {scanned} entries checked, "
            f"{matched} supported audio files found.{suffix}"
        )

    def _on_discovery_done(self, result):
        if not isinstance(result, FileDiscoveryResult):
            self._discovery_terminal_state = "Scan failed"
            self._set_queue_notice(
                "File discovery failed without a typed result. Try the "
                "folder again or add files individually."
            )
            return
        for path in result.paths:
            self._append_item(path)
        self._update_file_count()
        self._update_preview_ui()

        if result.cancelled:
            self._discovery_terminal_state = "Scan cancelled"
            summary = (
                f"Scan cancelled after {result.scanned_entries} entries; "
                f"kept {len(result.paths)} discovered audio files."
            )
        elif result.error_count:
            self._discovery_terminal_state = "Scan partial"
            summary = (
                f"Added {len(result.paths)} audio files after checking "
                f"{result.scanned_entries} entries; "
                f"{result.error_count} location(s) could not be read."
            )
        else:
            self._discovery_terminal_state = "Ready"
            summary = (
                f"Added {len(result.paths)} audio files after checking "
                f"{result.scanned_entries} entries."
            )
        if result.unsupported_count:
            summary += (
                f" Skipped {result.unsupported_count} unsupported file(s)."
            )
        self._set_queue_notice(summary)
        self._log(f"Discovery: {summary}")
        for message in result.errors:
            self._log(f"Discovery warning: {message}")
        if result.error_count > len(result.errors):
            self._log(
                "Discovery warning: "
                f"{result.error_count - len(result.errors)} additional "
                "scan errors were omitted."
            )

    def _on_discovery_thread_finished(self, worker):
        if self.discovery_worker is not worker:
            return
        self.discovery_worker = None
        self._set_discovery_ui(
            False,
            state=self._discovery_terminal_state,
        )
        self._on_render_thread_finished()

    @staticmethod
    def _file_preset_assignment(name, params):
        return {
            "name": str(name),
            "params": {
                key: copy.deepcopy(params[key])
                for key in _CONFIG_DEFAULTS
                if key in params
            },
        }

    def _assignment_from_manifest_job(self, job):
        config = job.get("config")
        if not isinstance(config, dict):
            return None
        params = {
            key: config[key]
            for key in _CONFIG_DEFAULTS
            if key in config
        }
        for preset_name, preset_params in PRESETS.items():
            if params == preset_params:
                return self._file_preset_assignment(
                    preset_name,
                    preset_params,
                )
        return self._file_preset_assignment(
            job.get("preset_name", "Custom snapshot"),
            params,
        )

    def _decorate_file_preset_item(self, item):
        text = re.sub(r"\s+\[Preset: [^\]]+\]$", "", item.text())
        assignment = item.data(ROLE_FILE_PRESET)
        if isinstance(assignment, dict) and assignment.get("name"):
            text += f"  [Preset: {assignment['name']}]"
        item.setText(text)

    def _sync_queue_preset_control(self):
        if not hasattr(self, "queue_preset_combo"):
            return
        items = self.file_list.selectedItems()
        if not items:
            current = self.file_list.currentItem()
            items = [current] if current is not None else []
        self.queue_preset_combo.setEnabled(bool(items))
        if not items:
            self._syncing_file_preset = True
            try:
                self.queue_preset_combo.setPlaceholderText("")
                self.queue_preset_combo.setCurrentIndex(
                    self.queue_preset_combo.findText(
                        _tr(QUEUE_PRESET_INHERIT)
                    )
                )
            finally:
                self._syncing_file_preset = False
            return
        values = set()
        for item in items:
            assignment = item.data(ROLE_FILE_PRESET)
            if not isinstance(assignment, dict):
                values.add(QUEUE_PRESET_INHERIT)
            elif assignment.get("name") in PRESETS:
                values.add(assignment["name"])
            else:
                values.add(QUEUE_PRESET_CURRENT)
        self._syncing_file_preset = True
        try:
            if len(values) == 1:
                value = next(iter(values))
                self.queue_preset_combo.setPlaceholderText("")
                index = self.queue_preset_combo.findText(_tr(value))
                self.queue_preset_combo.setCurrentIndex(index)
            else:
                self.queue_preset_combo.setCurrentIndex(-1)
                self.queue_preset_combo.setPlaceholderText(
                    _tr("Mixed presets")
                )
        finally:
            self._syncing_file_preset = False

    def _on_queue_preset_changed(self, selected_name):
        if self._syncing_file_preset or not selected_name:
            return
        items = self.file_list.selectedItems()
        if not items:
            current = self.file_list.currentItem()
            items = [current] if current is not None else []
        if not items:
            return
        if selected_name == _tr(QUEUE_PRESET_INHERIT):
            assignment = None
            display_name = QUEUE_PRESET_INHERIT
        elif selected_name == _tr(QUEUE_PRESET_CURRENT):
            assignment = self._file_preset_assignment(
                "Custom snapshot",
                self._get_params(),
            )
            display_name = "Custom snapshot"
        else:
            assignment = self._file_preset_assignment(
                selected_name,
                PRESETS[selected_name],
            )
            display_name = selected_name
        for item in items:
            item.setData(ROLE_FILE_PRESET, copy.deepcopy(assignment))
            self._decorate_file_preset_item(item)
        self._log(
            f"Assigned {display_name} to {len(items)} queue file(s)."
        )

    def _per_file_job_config(self, item, batch_params):
        assignment = item.data(ROLE_FILE_PRESET)
        if not isinstance(assignment, dict):
            return None
        config = dict(assignment["params"])
        config["output_format"] = batch_params["output_format"]
        config["c2pa_policy"] = batch_params["c2pa_policy"]
        return validate_render_config(
            config,
            require_complete=True,
            allow_output_format=True,
        )

    def _append_item(self, path, job_id=None, preset_assignment=None):
        item = QListWidgetItem(f"READY    {Path(path).name}")
        item.setToolTip(path)
        item.setData(ROLE_INPUT, path)
        item.setData(ROLE_OUTPUT, None)
        item.setData(ROLE_JOB_ID, job_id or uuid.uuid4().hex)
        item.setData(ROLE_RESULT, None)
        item.setData(ROLE_COMPARE_WINNER, self._compare_winner_for_path(path))
        item.setData(ROLE_PREVIEW_OFFSET, None)
        item.setData(ROLE_FILE_PRESET, copy.deepcopy(preset_assignment))
        self._decorate_compare_winner_item(item)
        self._decorate_file_preset_item(item)
        self.file_list.addItem(item)

    def _update_file_count(self):
        n = self.file_list.count()
        count_text = _tr("{n} file", n=n)
        self.file_count_label.setText(count_text)
        self.file_count_label.setAccessibleName(
            f"Queue count: {count_text}"
        )
        self._sync_header_stats()
        if hasattr(self, "btn_process"):
            self.btn_process.setEnabled(
                n > 0 and not bool(self._active_workers())
            )
        if (
            n == 0
            and hasattr(self, "queue_activity_label")
            and not bool(self.discovery_worker and self.discovery_worker.isRunning())
        ):
            self._set_queue_notice(
                "Queue is empty. Browse for audio or drop files/folders here."
            )
        self._sync_queue_preset_control()

    # --- Preset slots ---
    def _on_preset(self, name):
        if name in PRESETS:
            self._apply_preset(name)
        self._sync_header_stats()

    def _apply_preset(self, name):
        self._apply_config(PRESETS[name])

    def _apply_config(self, params):
        params = validate_render_config(
            params,
            require_complete=True,
            allow_output_format=False,
        )
        self._applying_preset = True
        try:
            self.meta_check.setChecked(params['strip_metadata'])
            self.spectral_scan_check.setChecked(
                params['spectral_scan_enabled']
            )
            for key, row in self.param_rows.items():
                row.set_value(params[key])
                row.set_enabled_check(params[row.enabled_key])
        finally:
            self._applying_preset = False

    def _on_param_changed(self):
        if self._applying_preset:
            return
        if self.preset_combo.currentText() != 'Custom':
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentText('Custom')
            self.preset_combo.blockSignals(False)
            self._sync_header_stats()

    def _save_preset(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Preset", self._last_preset_dir,
            "SunoJump Preset (*.json);;All Files (*)",
        )
        if not path:
            return
        if not path.lower().endswith('.json'):
            path += '.json'
        try:
            self._last_preset_dir = str(Path(path).parent)
            params = self._get_params()
            preset_data = _create_preset_document(
                "Custom",
                {
                    key: value
                    for key, value in params.items()
                    if key not in {"output_format", "c2pa_policy"}
                },
            )
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(preset_data, f, indent=2)
            self._log(f"Preset saved: {Path(path).name}")
        except Exception as e:
            self._log(f"Save preset failed: {e}")

    def _load_preset(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Preset", self._last_preset_dir,
            "SunoJump Preset (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            self._last_preset_dir = str(Path(path).parent)
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            document = _validate_preset_document(data)
            self._apply_config(document["params"])

            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentText('Custom')
            self.preset_combo.blockSignals(False)
            self._sync_header_stats()
            self._log(f"Preset loaded: {Path(path).name}")
        except Exception as e:
            self._log(f"Load preset failed: {e}")

    def _get_params(self):
        params = {
            'strip_metadata': self.meta_check.isChecked(),
            'spectral_scan_enabled': self.spectral_scan_check.isChecked(),
            'output_format': self.format_combo.currentText().lower(),
            'c2pa_policy': C2PA_POLICY_BLOCK,
        }
        for key, row in self.param_rows.items():
            params[row.enabled_key] = row.is_enabled()
            params[key] = row.value()
        return validate_render_config(
            params,
            require_complete=True,
            allow_output_format=True,
        )

    # --- Processing control ---
    def _confirm_c2pa_output_without_credentials(self, inspections):
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle(_tr("Content Credentials detected"))
        dialog.setText(_tr(
            "One or more source files contain C2PA Content Credentials."
        ))
        dialog.setInformativeText(_tr(
            "SunoJump will transform and re-encode the audio. Original files "
            "remain unchanged, but outputs will not carry or re-sign the "
            "source manifests, and their hard bindings would not apply to "
            "the transformed audio. This is not evidence of an acoustic "
            "detector change."
        ))
        details = []
        for path, inspection in inspections:
            stores = ", ".join(
                f"{store.location} sha256:{store.sha256[:12]}"
                for store in inspection.manifest_stores
            )
            details.append(
                f"{Path(path).name}: {inspection.status}; {stores}"
            )
        dialog.setDetailedText("\n".join(details))
        continue_button = dialog.addButton(
            _tr("Continue without source credentials"),
            QMessageBox.ButtonRole.AcceptRole,
        )
        cancel_button = dialog.addButton(
            QMessageBox.StandardButton.Cancel,
        )
        dialog.setDefaultButton(cancel_button)
        dialog.setEscapeButton(cancel_button)
        dialog.exec()
        return dialog.clickedButton() is continue_button

    def _authorize_source_provenance(self, input_paths, params):
        inspections = [
            (str(path), inspect_c2pa(path))
            for path in input_paths
        ]
        failed = [
            (path, inspection)
            for path, inspection in inspections
            if inspection.failed
        ]
        if failed:
            details = "; ".join(
                f"{Path(path).name}: {inspection.message}"
                for path, inspection in failed
            )
            self._set_render_state("Provenance check failed")
            self._log(
                "C2PA provenance inspection failed; no render started. "
                f"{details}"
            )
            QMessageBox.critical(
                self,
                _tr("Provenance inspection failed"),
                _tr(
                    "SunoJump could not safely inspect source provenance. "
                    "No output was created.\n\n"
                    "{details}",
                    details=details,
                ),
            )
            return None
        present = [
            (path, inspection)
            for path, inspection in inspections
            if inspection.present
        ]
        if not present:
            return params
        if params.get("c2pa_policy") != C2PA_POLICY_ALLOW_REMOVAL:
            if not self._confirm_c2pa_output_without_credentials(present):
                self._set_render_state("Provenance protected")
                self._log(
                    "Render cancelled: source Content Credentials were "
                    "preserved and no output was created."
                )
                return None
        authorized = dict(params)
        authorized["c2pa_policy"] = C2PA_POLICY_ALLOW_REMOVAL
        try:
            return validate_render_config(
                authorized,
                require_complete=False,
                allow_output_format=True,
            )
        except ConfigurationError as exc:
            self._set_render_state("Configuration invalid")
            self._log(f"Cannot apply C2PA policy: {exc}")
            return None

    def _set_processing_ui(self, processing, state=None):
        self.btn_process.setEnabled(
            (not processing) and self.file_list.count() > 0
        )
        self.btn_cancel.setEnabled(processing)
        self.btn_cancel.setText(_tr("Cancel"))
        self._set_general_controls(not processing)
        self._set_render_state(
            state or ("Processing" if processing else "Ready")
        )
        # Preview + compare mutually exclusive with batch processing
        has_selection = self._current_selected_item() is not None
        if _MULTIMEDIA_OK:
            self.btn_render_preview.setEnabled((not processing) and has_selection)
            self.btn_compare.setEnabled((not processing) and has_selection)
        # Lock reordering during processing to preserve index mapping
        self.file_list.setDragEnabled(not processing)
        if processing:
            self.progress.setValue(0)

    def _set_preview_running_ui(self, running, state=None):
        if _MULTIMEDIA_OK:
            self.btn_render_preview.setEnabled(not running)
            self.btn_render_preview.setText(
                _tr("Rendering...") if running else _tr("Preview")
            )
            self.btn_compare.setEnabled(not running)
        self.btn_cancel.setEnabled(running)
        self.btn_cancel.setText(_tr("Cancel"))
        self._set_render_state(
            state or ("Previewing" if running else "Ready")
        )
        self.btn_process.setEnabled(
            (not running) and self.file_list.count() > 0
        )
        self._set_general_controls(not running)
        self.file_list.setDragEnabled(not running)

    def _set_compare_running_ui(self, running, state=None):
        if _MULTIMEDIA_OK:
            self.btn_compare.setEnabled(not running)
            self.btn_compare.setText(
                _tr("Comparing...") if running else _tr("Compare")
            )
            self.btn_render_preview.setEnabled(not running)
            # Individual compare buttons disabled during re-render
            if running:
                for b in self.compare_buttons.values():
                    b.setEnabled(False)
                self.btn_apply_compare.setEnabled(False)
        self.btn_cancel.setEnabled(running)
        self.btn_cancel.setText(_tr("Cancel"))
        self._set_render_state(
            state or ("Comparing" if running else "Ready")
        )
        self.btn_process.setEnabled(
            (not running) and self.file_list.count() > 0
        )
        self._set_general_controls(not running)
        self.file_list.setDragEnabled(not running)

    def _set_discovery_ui(self, scanning, state=None):
        self.btn_process.setEnabled(
            (not scanning) and self.file_list.count() > 0
        )
        self.btn_cancel.setEnabled(scanning)
        self.btn_cancel.setText(
            _tr("Cancel Scan") if scanning else _tr("Cancel")
        )
        self._set_general_controls(not scanning)
        self.file_list.setDragEnabled(not scanning)
        self._set_render_state(
            state or ("Scanning" if scanning else "Ready")
        )
        if scanning:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)

    def _set_general_controls(self, enabled):
        self.btn_browse.setEnabled(enabled)
        self.btn_remove.setEnabled(enabled)
        self.btn_clear.setEnabled(enabled)
        self.btn_resume_batch.setEnabled(enabled)
        self.btn_retry_failed.setEnabled(enabled)
        self.btn_save_preset.setEnabled(enabled)
        self.btn_load_preset.setEnabled(enabled)
        self.spectral_scan_check.setEnabled(enabled)
        self.meta_check.setEnabled(enabled)
        self.btn_export_support.setEnabled(enabled)
        self.btn_clear_logs.setEnabled(enabled)
        self.retention_spin.setEnabled(enabled)
        self.worker_count_spin.setEnabled(enabled)
        self.spectrogram_check.setEnabled(enabled)
        self.loudness_report_check.setEnabled(enabled)
        self.preview_offset_spin.setEnabled(enabled)
        if enabled:
            self._sync_queue_preset_control()
        else:
            self.queue_preset_combo.setEnabled(False)
        self.redact_logs_check.setEnabled(enabled)

    def _on_process(self):
        if self.file_list.count() == 0:
            self._set_render_state("Add files")
            self._set_queue_notice(
                "Queue is empty. Browse for audio or drop files/folders here."
            )
            self._log("No files to process. Add audio to the queue first.")
            return

        self._stop_playback()

        input_paths = [
            self.file_list.item(i).data(ROLE_INPUT)
            for i in range(self.file_list.count())
        ]
        params = self._authorize_source_provenance(
            input_paths,
            self._get_params(),
        )
        if params is None:
            return

        manifest_jobs = []
        for i, input_path in enumerate(input_paths):
            item = self.file_list.item(i)
            manifest_job = {
                "id": self._ensure_item_job_id(item),
                "input_path": input_path,
                "effective_seed": secrets.randbits(64),
            }
            per_file_config = self._per_file_job_config(item, params)
            assignment = item.data(ROLE_FILE_PRESET)
            if per_file_config is not None:
                manifest_job["config"] = per_file_config
                manifest_job["preset_name"] = assignment["name"]
            manifest_jobs.append(manifest_job)
            # Clear any previous processed-path marker
            item.setData(ROLE_OUTPUT, None)
            item.setData(ROLE_PREVIEW_OFFSET, None)

        out_dir = self.output_dir.text().strip() or DEFAULT_OUTPUT
        try:
            os.makedirs(out_dir, exist_ok=True)
            manifest_store = BatchManifestStore.create(
                default_manifest_path(out_dir),
                app_version=VERSION,
                output_dir=out_dir,
                config=params,
                jobs=manifest_jobs,
                audit_options={
                    "spectrogram": self.spectrogram_check.isChecked(),
                    "loudness": self.loudness_report_check.isChecked(),
                },
            )
        except (OSError, BatchManifestError) as exc:
            self._set_render_state("Manifest failed")
            self._log(f"Cannot start batch safely: {exc}")
            return
        self._start_gui_batch(
            manifest_store.select("pending"),
            params,
            out_dir,
            manifest_store,
            self.preset_combo.currentText(),
        )

    def _start_gui_batch(
        self,
        jobs,
        params,
        out_dir,
        manifest_store,
        label,
        recovery_notes=None,
    ):
        files = [job["input_path"] for job in jobs]
        self._set_processing_ui(True)
        self.log_box.clear()
        self._start_run_log(
            "gui-batch", files, out_dir, params, label,
        )
        self._log(f"Starting -- {len(files)} file(s), preset: {label}")
        self._log(f"Output: {out_dir}\n")
        self._log(f"Batch manifest: {manifest_store.path}")
        self._last_batch_manifest_path = str(manifest_store.path)
        for note in recovery_notes or ():
            self._log(f"Recovery: {note}")

        self._batch_terminal_state = "Failed"
        self._batch_result_received = False
        self.worker = ProcessWorker(
            jobs,
            params,
            out_dir,
            manifest_store=manifest_store,
            max_workers=self.worker_count_spin.value(),
            audit_options=manifest_store.audit_options,
        )
        active_worker = self.worker
        self.worker.log_signal.connect(self._log)
        self.worker.progress_signal.connect(self.progress.setValue)
        self.worker.file_progress.connect(self._on_file_progress)
        self.worker.file_started.connect(self._on_file_started)
        self.worker.file_done.connect(self._on_file_done)
        self.worker.all_done.connect(self._on_all_done)
        self.worker.finished.connect(
            lambda worker=active_worker: self._on_batch_thread_finished(worker)
        )
        self.worker.start()

    def _retry_failed_batch(self):
        recent = self._last_batch_manifest_path
        if recent and os.path.isfile(recent):
            self._load_batch_manifest("failed", path=recent)
            return
        self._load_batch_manifest("failed")

    def _load_batch_manifest(self, policy, path=None):
        if self._active_workers():
            self._log("Cannot load batch history while a render is active.")
            return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Load Batch Manifest",
                self.output_dir.text().strip() or DEFAULT_OUTPUT,
                (
                    f"SunoJump Batch (*{BATCH_MANIFEST_SUFFIX});;"
                    "JSON Files (*.json)"
                ),
            )
        if not path:
            return
        try:
            store = BatchManifestStore.load(path)
            notes = store.reconcile()
            params = validate_render_config(
                store.config,
                require_complete=True,
                allow_output_format=True,
            )
            audit_options = _validated_audit_options(store.audit_options)
            jobs = _validated_manifest_jobs(
                store.select(policy),
                params,
            )
            if not jobs:
                counts = ", ".join(
                    f"{state}={count}"
                    for state, count in store.counts.items()
                    if count
                )
                self._set_render_state("Nothing to resume")
                self._log(
                    f"No {policy} jobs in {Path(path).name} ({counts})."
                )
                return
            output_format = params["output_format"].upper()
            if self.format_combo.findText(output_format) < 0:
                raise BatchManifestError(
                    f"saved output format {output_format} is unavailable"
                )
        except (
            BatchManifestError,
            ConfigurationError,
            OSError,
        ) as exc:
            self._set_render_state("Manifest invalid")
            self._log(f"Cannot resume batch: {exc}")
            return

        self._stop_playback()
        self.file_list.clear()
        for job in jobs:
            self._append_item(
                job["input_path"],
                job_id=job["id"],
                preset_assignment=self._assignment_from_manifest_job(job),
            )
        self._update_file_count()
        config_without_format = {
            key: value
            for key, value in params.items()
            if key != "output_format"
        }
        self._apply_config(config_without_format)
        self.format_combo.setCurrentText(output_format)
        self.spectrogram_check.setChecked(audit_options["spectrogram"])
        self.loudness_report_check.setChecked(audit_options["loudness"])
        self.output_dir.setText(store.output_dir)
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentText("Custom")
        self.preset_combo.blockSignals(False)
        self._sync_header_stats()
        self._update_preview_ui()
        self._start_gui_batch(
            jobs,
            params,
            store.output_dir,
            store,
            f"Recovered ({policy})",
            recovery_notes=notes,
        )

    def _on_cancel(self):
        active = []
        for label, worker in (
            ("file scan", self.discovery_worker),
            ("batch", self.worker),
            ("preview", self.preview_worker),
            ("comparison", self.compare_worker),
        ):
            if worker is not None and worker.isRunning():
                active.append((label, worker))
        if not active:
            self._log("No active operation to cancel.")
            return
        self._set_render_state("Cancelling")
        self.btn_cancel.setEnabled(False)
        for _, worker in active:
            worker.cancel()
        labels = ", ".join(label for label, _ in active)
        self._log(f"\nCancelling {labels}...")

    def _on_file_started(self, job_id):
        item = self._find_item_by_job_id(job_id)
        if item is not None:
            name = Path(item.data(ROLE_INPUT)).name
            item.setText(f"RUNNING  {name}")
            self._decorate_file_preset_item(item)

    def _on_file_progress(self, job_id, value):
        item = self._find_item_by_job_id(job_id)
        if item is not None:
            name = Path(item.data(ROLE_INPUT)).name
            item.setText(f"RUNNING {int(value):3d}%  {name}")
            self._decorate_file_preset_item(item)

    def _on_file_done(self, job_id, result):
        if not isinstance(result, RenderResult):
            self._log("Ignored untyped worker result.")
            return
        item = self._job_item_matches_result(job_id, result)
        if item is None:
            self._log(
                f"Ignored stale result for queue job {job_id}: "
                f"{Path(result.input_path).name}"
            )
        else:
            name = Path(item.data(ROLE_INPUT)).name
            if result.state is RenderState.SUCCEEDED:
                item.setText(
                    f"DONE      {name} -> {Path(result.output_path).name}"
                )
                item.setData(ROLE_OUTPUT, result.output_path)
            elif result.state is RenderState.PARTIAL:
                code = result.error_code.value
                reason = result.message or code
                item.setText(
                    f"PARTIAL   {name} -> {Path(result.output_path).name} "
                    f"— {code}: {reason}"
                )
                item.setData(ROLE_OUTPUT, result.output_path)
            elif result.state is RenderState.CANCELLED:
                reason = result.message or "cancelled"
                item.setText(f"CANCELLED {name} — {reason}")
                item.setData(ROLE_OUTPUT, None)
            else:
                code = (
                    result.error_code.value
                    if result.error_code is not None
                    else "unknown_error"
                )
                reason = result.message or code
                item.setText(f"FAILED    {name} — {code}: {reason}")
                item.setData(ROLE_OUTPUT, None)
            item.setData(ROLE_RESULT, result)
            item.setData(ROLE_PREVIEW_OFFSET, None)
            self._decorate_file_preset_item(item)
            detail = format_render_result(result)
            item.setToolTip(detail)
            item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, detail)
        self._update_preview_ui()

    def _on_all_done(self, result):
        self._batch_result_received = True
        if not isinstance(result, BatchResult):
            self.progress.setValue(min(self.progress.value(), 99))
            self._batch_terminal_state = "Failed"
            self._set_render_state("Failed")
            self._log("\nBatch failed: worker returned an untyped outcome.")
            return
        if result.state is RenderState.SUCCEEDED:
            self.progress.setValue(100)
            self._batch_terminal_state = "Complete"
            self._set_render_state("Complete")
        elif result.state is RenderState.PARTIAL:
            self.progress.setValue(min(self.progress.value(), 99))
            self._batch_terminal_state = "Partial"
            self._set_render_state("Partial")
        elif result.state is RenderState.CANCELLED:
            self.progress.setValue(min(self.progress.value(), 99))
            self._batch_terminal_state = "Cancelled"
            self._set_render_state("Cancelled")
        else:
            self.progress.setValue(min(self.progress.value(), 99))
            self._batch_terminal_state = "Failed"
            self._set_render_state("Failed")
        counts = result.counts
        retryable = counts[RenderState.FAILED.value] + counts[
            RenderState.PARTIAL.value
        ]
        if retryable:
            self._set_queue_notice(
                f"{retryable} failed/partial job(s). Each row shows its "
                "reason; choose Retry Failed to run them again."
            )
        elif counts[RenderState.CANCELLED.value]:
            self._set_queue_notice(
                f"{counts[RenderState.CANCELLED.value]} job(s) cancelled. "
                "Use Resume Batch to continue the saved batch."
            )
        else:
            self._set_queue_notice(
                f"All {counts[RenderState.SUCCEEDED.value]} queued job(s) "
                "finished successfully."
            )
        self._log(f"\n{format_batch_result(result)}")

    def _on_batch_thread_finished(self, worker):
        if self.worker is not worker:
            return
        if not self._batch_result_received:
            self._batch_terminal_state = "Failed"
            self.progress.setValue(min(self.progress.value(), 99))
            self._log("Batch worker exited without a terminal result.")
        self.worker = None
        self._set_processing_ui(
            False,
            state=self._batch_terminal_state,
        )
        self._on_render_thread_finished()

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.append(f"[{ts}] {msg}")
        if self._current_run_log is not None:
            self._current_run_log.write(msg)
        sb = self.log_box.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _start_run_log(self, mode, files, out_dir, params, preset_name):
        try:
            if self._current_run_log is not None:
                self._current_run_log.close()
            self._current_run_log = RunDiagnostics(
                mode,
                redact=self.redact_logs_check.isChecked(),
                max_logs=self.retention_spin.value(),
            )
            self._current_run_log.write_header(mode, files, out_dir, params, preset_name)
            self.btn_open_log.setEnabled(True)
            self._log(f"Run log: {self._current_run_log.path}")
        except Exception as e:
            self._current_run_log = None
            self.btn_open_log.setEnabled(False)
            self._log(f"Run log unavailable: {e}")

    def _open_run_log(self):
        if self._current_run_log is None:
            self._log("No run log available yet.")
            return
        if not _open_file(str(self._current_run_log.path)):
            self._log(f"Could not open run log: {self._current_run_log.path}")

    def _save_diagnostic_preferences(self, *_args):
        retained_logs = self.retention_spin.value()
        redact = self.redact_logs_check.isChecked()
        self._settings.setValue(
            "diagnostics/retained_logs",
            retained_logs,
        )
        self._settings.setValue("diagnostics/redact_paths", redact)
        self._settings.sync()
        protected = ()
        if self._current_run_log is not None:
            protected = (self._current_run_log.path,)
            try:
                self._current_run_log.set_redaction(redact)
            except RuntimeError:
                self._current_run_log = None
                self.btn_open_log.setEnabled(False)
                protected = ()
        report = _enforce_log_retention(
            retained_logs,
            protected_paths=protected,
        )
        if report["failures"]:
            details = "; ".join(
                f"{item['name']}: {item['error']}"
                for item in report["failures"]
            )
            self._log(f"Log retention incomplete: {details}")
        elif report["deleted"]:
            self._log(
                "Log retention removed "
                f"{len(report['deleted'])} old file(s); "
                f"{len(report['remaining'])} remain."
            )

    def _show_diagnostic_privacy(self):
        output_dir = self.output_dir.text().strip() or DEFAULT_OUTPUT
        locations = _diagnostic_data_locations(
            output_dir,
            self._settings.fileName(),
            self._preview_tempdir,
        )
        sample = "\n".join((
            f"Input: {Path.home() / 'Music' / 'example.wav'}",
            f"Output: {Path(output_dir).expanduser() / 'example_sj.wav'}",
            f"Temporary preview: {Path(tempfile.gettempdir()) / 'preview.wav'}",
        ))
        redact = self.redact_logs_check.isChecked()
        preview = (
            _redact_diagnostic_text(sample)
            if redact
            else sample
        )
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setWindowTitle("Diagnostic privacy and locations")
        dialog.setText(
            "Path redaction is "
            f"{'enabled' if redact else 'disabled'} for new log entries "
            "and support-bundle export."
        )
        dialog.setInformativeText(
            "Open Show Details to preview the exported text and identify "
            "every local diagnostics location. Support bundles exclude audio, "
            "batch manifests, sidecars, and GUI settings contents."
        )
        dialog.setDetailedText(
            "REDACTION PREVIEW\n"
            f"{preview}\n\n"
            "LOCAL DATA LOCATIONS\n"
            + "\n".join(locations)
        )
        dialog.exec()

    def _export_support_bundle(self):
        if self._active_render_workers():
            self._log(
                "Support bundle export is unavailable while a render is active."
            )
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        default_path = str(
            Path.home() / f"SunoJump-support-{stamp}.zip"
        )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Support Bundle",
            default_path,
            "ZIP archive (*.zip)",
        )
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        self._log("Support bundle export requested.")
        try:
            result = export_support_bundle(
                path,
                redact=self.redact_logs_check.isChecked(),
                output_dir=self.output_dir.text().strip() or DEFAULT_OUTPUT,
                settings_location=self._settings.fileName(),
                preview_dir=self._preview_tempdir,
            )
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            self._log(f"Support bundle export failed: {exc}")
            QMessageBox.critical(
                self,
                "Support bundle not created",
                str(exc),
            )
            return
        detail = (
            f"Created {result['path']}\n"
            f"SHA-256: {result['sha256']}\n"
            f"Included logs: {result['included_logs']}\n"
            f"Path redaction: {'on' if result['redacted'] else 'off'}"
        )
        if result["read_failures"]:
            detail += (
                "\nUnreadable logs: "
                + ", ".join(
                    item["name"] for item in result["read_failures"]
                )
            )
        self._log(
            "Support bundle created: "
            f"{result['included_logs']} log(s), "
            f"sha256:{result['sha256'][:12]}"
        )
        QMessageBox.information(
            self,
            "Support bundle created",
            detail,
        )

    def _clear_all_logs(self):
        if self._active_render_workers():
            self._log(
                "Logs cannot be cleared while a render is active."
            )
            QMessageBox.warning(
                self,
                "Logs are active",
                "Wait for the current render to finish before clearing logs.",
            )
            return
        log_dir = _diagnostics_dir()
        if not log_dir.is_dir():
            self._log("No log directory found.")
            return
        logs = list(log_dir.glob('*.log'))
        if not logs:
            self._log(f"No persistent logs found in {log_dir}.")
            return
        answer = QMessageBox.question(
            self,
            "Delete persistent logs?",
            (
                f"Delete {len(logs)} log file(s) from:\n{log_dir}\n\n"
                "This cannot be undone. Audio, batch manifests, replay "
                "sidecars, support bundles, and GUI settings are not deleted."
            ),
            (
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.Cancel
            ),
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._log("Log deletion cancelled.")
            return
        if self._current_run_log is not None:
            self._current_run_log.close()
            self._current_run_log = None
        self.btn_open_log.setEnabled(False)
        report = _delete_diagnostic_logs(log_dir)
        summary = (
            f"Deleted {len(report['deleted'])} of {report['found']} "
            f"log file(s); {len(report['remaining'])} remain."
        )
        self._log(summary)
        if report["failures"] or report["remaining"]:
            details = "\n".join(
                f"{item['name']}: {item['error']}"
                for item in report["failures"]
            )
            if report["remaining"]:
                details += (
                    ("\n" if details else "")
                    + "Remaining: "
                    + ", ".join(report["remaining"])
                )
            QMessageBox.warning(
                self,
                "Log deletion incomplete",
                summary + "\n\n" + details,
            )
        else:
            QMessageBox.information(
                self,
                "Logs deleted",
                summary,
            )

    # --- Preview / playback ---
    def _current_selected_item(self):
        items = self.file_list.selectedItems()
        if items:
            return items[0]
        if self.file_list.count() > 0:
            return self.file_list.item(0)
        return None

    def _ensure_item_job_id(self, item):
        job_id = item.data(ROLE_JOB_ID)
        if not isinstance(job_id, str) or not job_id:
            job_id = uuid.uuid4().hex
            item.setData(ROLE_JOB_ID, job_id)
        return job_id

    def _find_item_by_job_id(self, job_id):
        """Resolve a stable queue identity without relying on wrapper addresses."""
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item.data(ROLE_JOB_ID) == job_id:
                return item
        return None

    def _job_item_matches_result(self, job_id, result):
        item = self._find_item_by_job_id(job_id)
        if item is None:
            return None
        input_path = item.data(ROLE_INPUT)
        if (
            not input_path
            or _norm_output_path(input_path)
            != _norm_output_path(result.input_path)
        ):
            return None
        return item

    def _is_preview_output(self, path):
        """True if path is a file inside our preview temp directory."""
        if not path or not self._preview_tempdir:
            return False
        try:
            return os.path.commonpath(
                [os.path.abspath(path), os.path.abspath(self._preview_tempdir)],
            ) == os.path.abspath(self._preview_tempdir)
        except ValueError:
            return False

    def _queue_preview_cleanup(self, output_path):
        if self._is_preview_output(output_path):
            self._deferred_preview_cleanup.add(str(output_path))

    def _flush_deferred_preview_cleanup(self):
        if any(
            worker is not None and worker.isRunning()
            for worker in (self.preview_worker, self.compare_worker)
        ):
            return
        pending = tuple(self._deferred_preview_cleanup)
        self._deferred_preview_cleanup.clear()
        for output_path in pending:
            _remove_file_silent(output_path)
            _remove_file_silent(_sidecar_path_for_output(output_path))

    # --- Render preview ---
    def _on_render_preview(self):
        if not _MULTIMEDIA_OK:
            return
        if self.worker and self.worker.isRunning():
            self._log("Cannot render preview while batch is processing.")
            return
        if self.preview_worker and self.preview_worker.isRunning():
            return  # Already rendering
        if self.compare_worker and self.compare_worker.isRunning():
            self._log("Cannot render preview while comparing presets.")
            return

        item = self._current_selected_item()
        if item is None:
            self._log("Select a file to preview first.")
            return

        input_path = item.data(ROLE_INPUT)
        if not input_path or not os.path.isfile(input_path):
            self._log("Selected file not found.")
            return

        # Stop any current playback before overwriting processed-path
        self._stop_playback()

        if self._preview_tempdir is None or not os.path.isdir(self._preview_tempdir):
            try:
                self._preview_tempdir = tempfile.mkdtemp(prefix='sunojump_preview_')
            except OSError as e:
                self._log(f"Cannot create preview temp dir: {e}")
                return

        # Clean previous evidence only while no preview worker can still use it.
        prev = item.data(ROLE_OUTPUT)
        if self._is_preview_output(prev):
            self._queue_preview_cleanup(prev)
            self._flush_deferred_preview_cleanup()
            item.setData(ROLE_OUTPUT, None)
            item.setData(ROLE_PREVIEW_OFFSET, None)

        params = self._authorize_source_provenance(
            [input_path],
            self._get_params(),
        )
        if params is None:
            return
        self._preview_offset_seconds = self.preview_offset_spin.value()
        preview_log_params = {
            **params,
            "preview_offset_seconds": self._preview_offset_seconds,
        }
        self._start_run_log(
            "gui-preview", [input_path], self._preview_tempdir, preview_log_params,
            self.preset_combo.currentText(),
        )
        self._preview_job_id = self._ensure_item_job_id(item)
        self._preview_run_id = uuid.uuid4().hex
        self._preview_terminal_state = "Failed"
        self._set_preview_running_ui(True)
        self._log(
            f"Rendering {int(PREVIEW_DURATION_SEC)}s preview at "
            f"{self._preview_offset_seconds:.1f}s of "
            f"{Path(input_path).name} with current settings..."
        )

        self.preview_worker = PreviewWorker(
            input_path,
            params,
            self._preview_tempdir,
            self._preview_job_id,
            self._preview_run_id,
            offset_sec=self._preview_offset_seconds,
        )
        active_worker = self.preview_worker
        self.preview_worker.log_signal.connect(self._log)
        self.preview_worker.progress_signal.connect(self.progress.setValue)
        self.preview_worker.done.connect(self._on_preview_done)
        self.preview_worker.finished.connect(
            lambda worker=active_worker: self._on_preview_thread_finished(worker)
        )
        self.preview_worker.start()

    def _on_preview_done(self, job_id, run_id, result):
        if not isinstance(result, RenderResult):
            self._preview_terminal_state = "Failed"
            self._log("Preview worker returned an untyped result.")
            return

        if (
            job_id != self._preview_job_id
            or run_id != self._preview_run_id
        ):
            if result.usable_output:
                self._queue_preview_cleanup(result.output_path)
            self._log("Ignored stale preview result from an older render.")
            return

        if result.state is RenderState.CANCELLED:
            self._preview_terminal_state = "Cancelled"
            self._log("Preview cancelled.")
            self._update_preview_ui()
            return
        if not result.usable_output:
            self._preview_terminal_state = "Failed"
            self._log(f"Preview failed: {format_render_result(result)}")
            self._update_preview_ui()
            return

        item = self._job_item_matches_result(job_id, result)
        if item is None:
            self._preview_terminal_state = "Ready"
            self._queue_preview_cleanup(result.output_path)
            self._log(
                "Preview ready but its queue job no longer exists; "
                "the stale output was not attached."
            )
            self._update_preview_ui()
            return

        self._preview_terminal_state = "Ready"
        item.setData(ROLE_OUTPUT, result.output_path)
        item.setData(ROLE_PREVIEW_OFFSET, self._preview_offset_seconds)
        self._update_preview_ui()
        self._log(f"Preview ready: {Path(result.output_path).name}")

        # Auto-play so the user immediately hears the result
        if self.file_list.currentItem() is not item:
            self.file_list.setCurrentItem(item)
        self._toggle_play('processed')

    def _on_preview_thread_finished(self, worker):
        if self.preview_worker is not worker:
            return
        self.preview_worker = None
        self._set_preview_running_ui(
            False,
            state=self._preview_terminal_state,
        )
        self._flush_deferred_preview_cleanup()
        self._update_preview_ui()
        self._on_render_thread_finished()

    # --- Compare presets ---
    def _on_compare_presets(self):
        if not _MULTIMEDIA_OK:
            return
        if self.worker and self.worker.isRunning():
            return
        if self.preview_worker and self.preview_worker.isRunning():
            return
        if self.compare_worker and self.compare_worker.isRunning():
            return

        item = self._current_selected_item()
        if item is None:
            self._log("Select a file to compare presets.")
            return
        input_path = item.data(ROLE_INPUT)
        if not input_path or not os.path.isfile(input_path):
            self._log("Selected file not found.")
            return

        policy_params = self._authorize_source_provenance(
            [input_path],
            {"c2pa_policy": C2PA_POLICY_BLOCK},
        )
        if policy_params is None:
            return

        self._stop_playback()

        if self._preview_tempdir is None or not os.path.isdir(self._preview_tempdir):
            try:
                self._preview_tempdir = tempfile.mkdtemp(prefix='sunojump_preview_')
            except OSError as e:
                self._log(f"Cannot create preview temp dir: {e}")
                return

        # Reset compare state
        self._compare_results = {}
        self._playing_compare_preset = None
        self._compare_job_id = self._ensure_item_job_id(item)
        self._compare_run_id = uuid.uuid4().hex
        self._compare_terminal_state = "Failed"
        self._compare_offset_seconds = self.preview_offset_spin.value()
        self.compare_panel.setVisible(True)
        self._update_compare_history_label(item)
        for name, btn in self.compare_buttons.items():
            btn.setEnabled(False)
            btn.setText(_tr("{name} ...", name=name))
        self.btn_apply_compare.setEnabled(False)
        self.btn_apply_compare.setText(_tr("Save Playing Winner"))

        self._set_compare_running_ui(True)
        self._start_run_log(
            "gui-compare", [input_path], self._preview_tempdir,
            {
                'presets': list(PRESETS.keys()),
                'duration_sec': COMPARE_DURATION_SEC,
                'preview_offset_seconds': self._compare_offset_seconds,
                'c2pa_policy': policy_params["c2pa_policy"],
            },
            "Compare Presets",
        )
        self._log(
            f"Rendering {int(COMPARE_DURATION_SEC)}s sample at "
            f"{self._compare_offset_seconds:.1f}s per preset "
            f"({len(PRESETS)} presets)..."
        )

        self.compare_worker = PresetCompareWorker(
            input_path,
            self._preview_tempdir,
            self._compare_job_id,
            self._compare_run_id,
            c2pa_policy=policy_params["c2pa_policy"],
            offset_sec=self._compare_offset_seconds,
        )
        active_worker = self.compare_worker
        self.compare_worker.log_signal.connect(self._log)
        self.compare_worker.progress_signal.connect(self.progress.setValue)
        self.compare_worker.preset_done.connect(self._on_compare_preset_done)
        self.compare_worker.all_done.connect(self._on_compare_all_done)
        self.compare_worker.finished.connect(
            lambda worker=active_worker: self._on_compare_thread_finished(worker)
        )
        self.compare_worker.start()

    def _on_compare_preset_done(self, job_id, run_id, name, result):
        if not isinstance(result, RenderResult):
            self._log(f"Compare {name} returned an untyped result.")
            return
        if (
            job_id != self._compare_job_id
            or run_id != self._compare_run_id
            or self._job_item_matches_result(job_id, result) is None
        ):
            if result.usable_output:
                self._queue_preview_cleanup(result.output_path)
            self._log(f"Ignored stale {name} comparison result.")
            return
        btn = self.compare_buttons.get(name)
        if btn is None:
            if result.usable_output:
                self._queue_preview_cleanup(result.output_path)
            return
        if result.usable_output:
            self._compare_results[name] = result.output_path
            btn.setText(_tr("Play {name}", name=name))
            btn.setEnabled(True)
        elif result.state is RenderState.CANCELLED:
            btn.setText(_tr("{name} (cancelled)", name=name))
            btn.setEnabled(False)
        else:
            btn.setText(_tr("{name} (failed)", name=name))
            btn.setEnabled(False)

    def _on_compare_all_done(self, job_id, run_id, result):
        if (
            job_id != self._compare_job_id
            or run_id != self._compare_run_id
        ):
            self._log("Ignored terminal result from an older comparison.")
            return
        if not isinstance(result, BatchResult):
            self._compare_terminal_state = "Failed"
            self._log("Comparison worker returned an untyped terminal result.")
            return
        if result.state is RenderState.CANCELLED:
            self._compare_terminal_state = "Cancelled"
        elif result.state is RenderState.SUCCEEDED:
            self._compare_terminal_state = "Ready"
        elif result.state is RenderState.PARTIAL:
            self._compare_terminal_state = "Partial"
        else:
            self._compare_terminal_state = "Failed"
        n_ok = len(self._compare_results)
        self._log(
            f"Compare {result.state.value}: "
            f"{n_ok}/{len(PRESETS)} presets rendered."
        )
        self._update_preview_ui()

    def _on_compare_thread_finished(self, worker):
        if self.compare_worker is not worker:
            return
        self.compare_worker = None
        self._set_compare_running_ui(
            False,
            state=self._compare_terminal_state,
        )
        self._flush_deferred_preview_cleanup()
        self._update_preview_ui()
        self._on_render_thread_finished()

    def _play_compare(self, preset_name):
        """Play the compare sample for the given preset (toggle on/off)."""
        if not _MULTIMEDIA_OK or self.player is None:
            return
        path = self._compare_results.get(preset_name)
        if not path or not os.path.isfile(path):
            return

        # Toggle off if this one is currently playing
        if self._playing_compare_preset == preset_name:
            self.player.stop()
            return

        # Bracket transition -- prevents Stopped signal from _stop_playback()
        # (or the implicit stop in setSource) from wiping the new state.
        self._media_transitioning = True
        try:
            if self._playing_source is not None or self._playing_compare_preset is not None:
                self.player.stop()
            self.player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
            self.player.play()
            self._playing_compare_preset = preset_name
            self._playing_source = None  # compare mode, not original/processed
        finally:
            self._media_transitioning = False
        self._update_compare_buttons()
        self.btn_apply_compare.setEnabled(True)
        self.btn_apply_compare.setText(
            _tr("Save {preset} Winner", preset=preset_name)
        )

    def _update_compare_buttons(self):
        for name, btn in self.compare_buttons.items():
            if name not in self._compare_results:
                continue
            if self._playing_compare_preset == name:
                btn.setText(_tr("Stop {name}", name=name))
            else:
                btn.setText(_tr("Play {name}", name=name))

    def _apply_playing_compare_preset(self):
        if not self._playing_compare_preset:
            return
        name = self._playing_compare_preset
        item = self._find_item_by_job_id(self._compare_job_id)
        if item is None:
            self._log("Comparison winner was not saved: queue item is gone.")
            return
        if self.player is not None:
            self.player.stop()
        self.preset_combo.setCurrentText(name)  # triggers _on_preset -> _apply_preset
        if self._save_compare_winner(item, name):
            self.btn_apply_compare.setText(
                _tr("Saved {preset} Winner", preset=name)
            )
            self._log(
                f"Saved A/B winner for {Path(item.data(ROLE_INPUT)).name}: "
                f"{name}; applied it to Process All."
            )

    def _toggle_play(self, source):
        if not _MULTIMEDIA_OK or self.player is None:
            return
        if self._playing_source == source:
            self.player.stop()
            return

        item = self._current_selected_item()
        if item is None:
            return

        path = item.data(ROLE_INPUT if source == 'original' else ROLE_OUTPUT)
        if not path or not os.path.isfile(path):
            self._log(f"Cannot preview: file not available")
            return

        # Bracket the source transition so the Stopped signal from
        # player.stop() doesn't race ahead of our state update.
        self._media_transitioning = True
        try:
            self.player.stop()
            self.player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
            self.player.play()
            if source == 'original':
                if (
                    self.compare_panel.isVisible()
                    and item.data(ROLE_JOB_ID) == self._compare_job_id
                    and self._compare_results
                ):
                    preview_offset = self._compare_offset_seconds
                else:
                    preview_offset = item.data(ROLE_PREVIEW_OFFSET)
                if preview_offset is None:
                    preview_offset = self.preview_offset_spin.value()
                self.player.setPosition(max(0, int(float(preview_offset) * 1000)))
            self._playing_source = source
            self._playing_compare_preset = None
        finally:
            self._media_transitioning = False
        self._update_preview_ui()

    def _stop_playback(self):
        if self.player is not None:
            if self._playing_source is not None or self._playing_compare_preset is not None:
                self.player.stop()
        self._playing_source = None
        self._playing_compare_preset = None

    def _on_playback_state_changed(self, state):
        if self.player is None:
            return
        if self._media_transitioning:
            # Stopped signal from our own stop() during a source swap;
            # ignore -- the new play() call will drive state back to Playing.
            return
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self._playing_source = None
            self._playing_compare_preset = None
            self._update_preview_ui()
            self._update_compare_buttons()
            if self._compare_results:
                self.btn_apply_compare.setText(
                    _tr("Save Playing Winner")
                )
                self.btn_apply_compare.setEnabled(False)

    def _on_player_error(self, error, error_str=""):
        if error == QMediaPlayer.Error.NoError:
            return
        self._log(f"Playback error: {error_str or error}")
        self._playing_source = None
        self._update_preview_ui()

    def _update_preview_ui(self):
        if not _MULTIMEDIA_OK:
            return
        item = self._current_selected_item()

        # Hide stale compare panel if the file it was rendered for is gone
        # or selection moved to a different file.
        if self.compare_panel.isVisible():
            current_job_id = (
                item.data(ROLE_JOB_ID) if item is not None else None
            )
            if item is None or current_job_id != self._compare_job_id:
                if self._playing_compare_preset is not None and self.player is not None:
                    self.player.stop()
                self._playing_compare_preset = None
                for output_path in self._compare_results.values():
                    self._queue_preview_cleanup(output_path)
                self._compare_results = {}
                self._compare_job_id = None
                self._compare_run_id = None
                self._compare_terminal_state = "Ready"
                self.compare_panel.setVisible(False)
                self._flush_deferred_preview_cleanup()

        # Render Preview button: enabled when a file is selected and no job is running
        preview_worker_running = bool(self.preview_worker and self.preview_worker.isRunning())
        batch_running = bool(self.worker and self.worker.isRunning())
        compare_running = bool(self.compare_worker and self.compare_worker.isRunning())
        can_run = (
            item is not None
            and not preview_worker_running
            and not batch_running
            and not compare_running
        )
        self.btn_render_preview.setEnabled(can_run)
        self.btn_compare.setEnabled(can_run)

        if item is None:
            self.btn_play_orig.setText(_tr("Original"))
            self.btn_play_proc.setText(_tr("Processed"))
            self.btn_play_orig.setEnabled(False)
            self.btn_play_proc.setEnabled(False)
            self.preview_label.setText(_tr("Select a file"))
            self.preview_label.setAccessibleName(
                _tr("Preview target: Select a file")
            )
            self.compare_label.setText(_tr("A/B: select a file"))
            self._update_compare_history_label(None)
            return

        orig_path = item.data(ROLE_INPUT)
        proc_path = item.data(ROLE_OUTPUT)
        orig_ok = bool(orig_path) and os.path.isfile(orig_path)
        proc_ok = bool(proc_path) and os.path.isfile(proc_path)
        is_preview = self._is_preview_output(proc_path)

        display_name = Path(orig_path).name if orig_path else ""
        if is_preview and proc_ok:
            preview_offset = float(item.data(ROLE_PREVIEW_OFFSET) or 0.0)
            display_name = (
                f"{display_name}  (preview from {preview_offset:.1f}s, "
                f"up to {int(PREVIEW_DURATION_SEC)}s)"
            )
        self.preview_label.setText(display_name)
        self.preview_label.setAccessibleName(
            _tr("Preview target: {name}", name=display_name)
        )
        compare_target = Path(orig_path).name if orig_path else "unknown file"
        self.compare_label.setText(
            _tr("A/B for {name}:", name=compare_target)
        )
        self.compare_label.setAccessibleName(
            _tr("Preset comparison target: {name}", name=compare_target)
        )
        self._update_compare_history_label(item)

        processed_label = _tr("Preview") if is_preview else _tr("Processed")

        if self._playing_source == 'original':
            self.btn_play_orig.setText(_tr("Stop"))
            self.btn_play_orig.setEnabled(True)
            self.btn_play_proc.setText(processed_label)
            self.btn_play_proc.setEnabled(False)
        elif self._playing_source == 'processed':
            self.btn_play_orig.setText(_tr("Original"))
            self.btn_play_orig.setEnabled(False)
            self.btn_play_proc.setText(_tr("Stop"))
            self.btn_play_proc.setEnabled(True)
        else:
            self.btn_play_orig.setText(_tr("Original"))
            self.btn_play_proc.setText(processed_label)
            self.btn_play_orig.setEnabled(orig_ok)
            self.btn_play_proc.setEnabled(proc_ok)

    def _active_render_workers(self):
        return [
            (label, worker)
            for label, worker in (
                ("batch", self.worker),
                ("preview", self.preview_worker),
                ("comparison", self.compare_worker),
            )
            if worker is not None and worker.isRunning()
        ]

    def _active_workers(self):
        workers = list(self._active_render_workers())
        if (
            self.discovery_worker is not None
            and self.discovery_worker.isRunning()
        ):
            workers.insert(0, ("file scan", self.discovery_worker))
        return workers

    def _on_render_thread_finished(self):
        if self._close_pending and not self._active_workers():
            self._close_pending = False
            self._set_render_state("Safe to close")
            self._log(
                "Cancellation finished. Close the window again to exit safely."
            )

    def _cleanup_preview_tempdir(self):
        if self._active_render_workers():
            return False
        self._flush_deferred_preview_cleanup()
        if self._preview_tempdir and os.path.isdir(self._preview_tempdir):
            shutil.rmtree(self._preview_tempdir, ignore_errors=True)
            if os.path.isdir(self._preview_tempdir):
                self._log(
                    f"Preview temp cleanup incomplete: {self._preview_tempdir}"
                )
                return False
        self._preview_tempdir = None
        return True

    def closeEvent(self, event):
        active = self._active_workers()
        if active:
            for _, worker in active:
                worker.cancel()
            self._set_render_state("Waiting to close")
            self.btn_cancel.setEnabled(False)
            for _, worker in active:
                worker.wait(3000)
            still_running = self._active_workers()
            if still_running:
                self._close_pending = True
                labels = ", ".join(label for label, _ in still_running)
                self._log(
                    "Close paused: cancellation is still in progress for "
                    f"{labels}. Wait for 'Safe to close', then close again."
                )
                event.ignore()
                return

        self._close_pending = False
        # Disconnect playback state handlers only after every worker has
        # reached a terminal state; queued media signals then cannot touch
        # deallocated widgets.
        if self.player is not None:
            try:
                self.player.playbackStateChanged.disconnect(
                    self._on_playback_state_changed
                )
                self.player.errorOccurred.disconnect(self._on_player_error)
            except (TypeError, RuntimeError):
                pass
            self.player.stop()
        if not self._cleanup_preview_tempdir():
            self._set_render_state("Cleanup failed")
            self._log(
                "Close paused: preview files could not be removed. "
                "Close again after resolving the reported path."
            )
            event.ignore()
            return
        self._save_session_state()
        if self._current_run_log is not None:
            self._current_run_log.close()
            self._current_run_log = None
        event.accept()


# ============================================================
#  CLI Mode
# ============================================================
_CLI_PASS_KEYS = {
    "metadata": "strip_metadata",
    "spectral-scan": "spectral_scan_enabled",
    "spectral": "spectral_enabled",
    "spectral-sub-bass": "spectral_sub_bass_enabled",
    "spectral-low-mids": "spectral_low_mids_enabled",
    "spectral-presence": "spectral_presence_enabled",
    "spectral-air": "spectral_air_enabled",
    "dynamic-eq": "dynamic_eq_enabled",
    "pitch": "pitch_enabled",
    "tempo": "tempo_enabled",
    "phase": "phase_enabled",
    "stereo": "stereo_enabled",
    "noise": "noise_enabled",
    "dynamics": "dynamics_enabled",
    "humanize": "humanize_enabled",
    "reencode": "reencode_enabled",
}
_CLI_VALUE_OVERRIDES = {
    "spectral": ("spectral_strength", "spectral", "--spectral"),
    "spectral_sub_bass": (
        "spectral_sub_bass_strength",
        "spectral-sub-bass",
        "--spectral-sub-bass",
    ),
    "spectral_low_mids": (
        "spectral_low_mids_strength",
        "spectral-low-mids",
        "--spectral-low-mids",
    ),
    "spectral_presence": (
        "spectral_presence_strength",
        "spectral-presence",
        "--spectral-presence",
    ),
    "spectral_air": (
        "spectral_air_strength",
        "spectral-air",
        "--spectral-air",
    ),
    "dynamic_eq": ("dynamic_eq_amount", "dynamic-eq", "--dynamic-eq"),
    "pitch": ("pitch_range", "pitch", "--pitch"),
    "tempo": ("tempo_range", "tempo", "--tempo"),
    "phase": ("phase_amount", "phase", "--phase"),
    "stereo": ("stereo_shift", "stereo", "--stereo"),
    "noise": ("noise_level", "noise", "--noise"),
    "dynamics": ("dynamics_amount", "dynamics", "--dynamics"),
    "humanize": ("humanize_amount", "humanize", "--humanize"),
    "reencode": ("reencode_bitrate", "reencode", "--reencode"),
}


def _parse_cli_log_retention(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            _tr("diagnostic retention must be an integer")
        ) from exc
    try:
        return _validated_log_retention(parsed)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _build_cli_parser():
    parser = argparse.ArgumentParser(
        description=_tr(
            f'{APP_NAME} v{VERSION} -- rights-owned audio variation '
            'and local evidence'
        ),
        epilog=_tr(EVIDENCE_NOTICE),
    )
    parser.add_argument('-i', '--input', required=False,
                        help=_tr('Input audio file or directory'))
    parser.add_argument(
        '--watch',
        metavar='DIRECTORY',
        help=_tr(
            'Continuously process stable supported files dropped directly '
            'into this directory'
        ),
    )
    parser.add_argument('-o', '--output', default=None,
                        help=_tr('Output directory'))
    config_source = parser.add_mutually_exclusive_group()
    config_source.add_argument(
        '-p', '--preset', default='moderate',
        choices=['gentle', 'moderate', 'aggressive', 'extreme'],
        help=_tr('Processing preset'),
    )
    parser.add_argument('-f', '--format', default='wav',
                        choices=list(OUTPUT_EXTENSIONS.keys()),
                        dest='out_format', help=_tr('Output audio format'))
    config_source.add_argument(
        '--preset-file',
        default=None,
        help=_tr('Validated JSON preset; replaces -p/--preset'),
    )
    config_source.add_argument(
        '--profile',
        default=None,
        metavar='JSON',
        help=_tr(
            'Versioned JSON profile composing a built-in preset with '
            'sparse overrides'
        ),
    )
    parser.add_argument(
        '--locale',
        default=None,
        metavar='LOCALE',
        help=_tr(
            'Locale name for translated GUI/CLI strings '
            '(for example en or qps-ploc)'
        ),
    )
    parser.add_argument(
        '--compute',
        choices=COMPUTE_BACKEND_CHOICES,
        default='cpu',
        help=_tr(
            'FFT compute backend: cpu (release-safe default), auto '
            '(CUDA with CPU fallback), or cuda (fail if unavailable)'
        ),
    )
    parser.add_argument(
        '--workers',
        type=lambda value: _validated_parallel_workers(int(value)),
        default=DEFAULT_PARALLEL_FILE_WORKERS,
        metavar='COUNT',
        help=_tr(
            'parallel file workers, from 1 to 8 (default: 2)'
        ),
    )
    parser.add_argument(
        '--result-format',
        choices=('human', 'json', 'jsonl'),
        default='human',
        help=_tr(
            'result stream: human logs on stderr (default), one JSON batch '
            'document, or per-file JSON Lines plus a batch record'
        ),
    )
    parser.add_argument(
        '--spectrogram',
        action='store_true',
        help=_tr(
            'Export a bounded before/after side-by-side spectrogram PNG '
            'for each usable render'
        ),
    )
    parser.add_argument(
        '--loudness-report',
        action='store_true',
        help=_tr(
            'Export an ITU-R BS.1770-5 before/after integrated loudness '
            'and true-peak JSON report for each usable render'
        ),
    )
    parser.add_argument(
        '--manifest',
        default=None,
        help=_tr('Path for the new atomic batch manifest'),
    )
    parser.add_argument(
        '--resume',
        default=None,
        metavar='MANIFEST',
        help=_tr('Resume jobs from an existing SunoJump batch manifest'),
    )
    parser.add_argument(
        '--retry',
        choices=sorted(RETRY_POLICIES),
        default=None,
        help=_tr(
            'With --resume, select pending (default), unfinished, failed, '
            'or cancelled jobs only'
        ),
    )
    parser.add_argument(
        '--c2pa-policy',
        choices=sorted(C2PA_POLICIES),
        default=C2PA_POLICY_BLOCK,
        help=_tr(
            "C2PA source policy: block (default) refuses transformed "
            "outputs; allow-removal explicitly acknowledges that originals "
            "stay unchanged while outputs omit and do not re-sign source "
            "Content Credentials"
        ),
    )
    parser.add_argument(
        '--diagnostic-retention',
        type=_parse_cli_log_retention,
        default=MAX_RETAINED_LOGS,
        metavar='COUNT',
        help=_tr(
            "retain 1-365 persistent run logs "
            f"(default: {MAX_RETAINED_LOGS})"
        ),
    )
    parser.add_argument(
        '--no-redact-diagnostics',
        action='store_true',
        help=_tr(
            "explicitly keep absolute paths in new run logs; path redaction "
            "is enabled by default"
        ),
    )
    parser.add_argument(
        '--no-spectral-scan',
        '--no-watermark-scan',
        action='store_true',
        dest='no_spectral_scan',
        help=_tr(
            'Disable the local narrowband candidate scan pre-pass; '
            '--no-watermark-scan is a compatibility alias'
        ),
    )
    parser.add_argument(
        '--enable-pass',
        action='append',
        default=[],
        choices=sorted(_CLI_PASS_KEYS),
        metavar='PASS',
        help=_tr(
            'Enable a named pass using its preset/current amount; repeatable'
        ),
    )
    parser.add_argument(
        '--disable-pass',
        action='append',
        default=[],
        choices=sorted(_CLI_PASS_KEYS),
        metavar='PASS',
        help=_tr(
            'Disable a named pass; repeatable and conflicts with its value flag'
        ),
    )
    parser.add_argument(
        '--spectral',
        type=float,
        help=_tr('Set spectral perturbation (0.0-1.0) and enable its pass'),
    )
    parser.add_argument(
        '--spectral-sub-bass',
        type=float,
        help=_tr(
            'Set sub-bass spectral amount (0.0-1.0) and enable its pass'
        ),
    )
    parser.add_argument(
        '--spectral-low-mids',
        type=float,
        help=_tr(
            'Set low-mids spectral amount (0.0-1.0) and enable its pass'
        ),
    )
    parser.add_argument(
        '--spectral-presence',
        type=float,
        help=_tr(
            'Set presence spectral amount (0.0-1.0) and enable its pass'
        ),
    )
    parser.add_argument(
        '--spectral-air',
        type=float,
        help=_tr('Set air spectral amount (0.0-1.0) and enable its pass'),
    )
    parser.add_argument(
        '--dynamic-eq',
        type=float,
        help=_tr('Set Dynamic EQ amount (0.0-1.0) and enable its pass'),
    )
    parser.add_argument(
        '--pitch',
        type=float,
        help=_tr('Set pitch range (0.0-5.0 semitones) and enable its pass'),
    )
    parser.add_argument(
        '--tempo',
        type=float,
        help=_tr('Set tempo variation (0.0-0.15) and enable its pass'),
    )
    parser.add_argument(
        '--phase',
        type=float,
        help=_tr('Set phase scrambling (0.0-1.0) and enable its pass'),
    )
    parser.add_argument(
        '--stereo',
        type=float,
        help=_tr('Set stereo manipulation (0.0-0.5) and enable its pass'),
    )
    parser.add_argument(
        '--noise',
        type=float,
        help=_tr('Set noise level (-70 to -30 dB) and enable its pass'),
    )
    parser.add_argument(
        '--dynamics',
        type=float,
        help=_tr('Set dynamics amount (0.0-1.0) and enable its pass'),
    )
    parser.add_argument(
        '--humanize',
        type=float,
        help=_tr('Set humanization amount (0.0-1.0) and enable its pass'),
    )
    parser.add_argument(
        '--reencode',
        type=int,
        help=_tr('Set lossy re-encode bitrate (96-320) and enable its pass'),
    )
    parser.add_argument('--seed', type=int, default=None,
                        help=_tr(
                            'Random seed for reproducible output '
                            '(same seed = same bytes)'
                        ))
    parser.add_argument(
        '--version',
        action='version',
        version=f'{APP_NAME} v{VERSION}',
        help=_tr('Print the application version and exit'),
    )
    parser.add_argument(
        '--native-runtime',
        action='store_true',
        help=_tr(
            'Print machine-readable native dependency versions and exit'
        ),
    )
    return parser


def _apply_cli_overrides(params, args):
    result = validate_render_config(
        params,
        require_complete=True,
        allow_output_format=False,
    )
    enabled_passes = set(args.enable_pass)
    disabled_passes = set(args.disable_pass)
    if args.no_spectral_scan:
        disabled_passes.add("spectral-scan")
    conflicts = enabled_passes & disabled_passes
    if conflicts:
        raise ConfigurationError(
            "pass cannot be both enabled and disabled: "
            + ", ".join(sorted(conflicts))
        )

    for attribute, (config_key, pass_name, flag) in (
        _CLI_VALUE_OVERRIDES.items()
    ):
        value = getattr(args, attribute)
        if value is None:
            continue
        if pass_name in disabled_passes:
            raise ConfigurationError(
                f"{flag} conflicts with --disable-pass {pass_name}"
            )
        result[config_key] = value
        enabled_key = NUMBER_FIELDS_BY_KEY[config_key].enabled_key
        result[enabled_key] = True

    for pass_name in enabled_passes:
        result[_CLI_PASS_KEYS[pass_name]] = True
    for pass_name in disabled_passes:
        result[_CLI_PASS_KEYS[pass_name]] = False
    return validate_render_config(
        result,
        require_complete=True,
        allow_output_format=False,
    )


def _argv_uses_any_option(tokens, options):
    return any(
        token in options
        or any(token.startswith(f"{option}=") for option in options)
        for token in tokens
    )


def _cli_result_record(job, result):
    payload = result.to_dict()
    payload.pop("schema_version", None)
    return {
        "schema_id": CLI_RESULTS_SCHEMA_ID,
        "schema_version": RESULT_SCHEMA_VERSION,
        "record_type": "render_result",
        "job_id": str(job["id"]),
        **payload,
    }


def _cli_batch_record(
    batch_result,
    jobs,
    *,
    output_dir,
    manifest_path=None,
    include_results=True,
):
    if len(jobs) != len(batch_result.results):
        raise ValueError("CLI result jobs must align with batch results")
    payload = batch_result.to_dict()
    payload.pop("schema_version", None)
    payload.pop("results", None)
    record = {
        "schema_id": CLI_RESULTS_SCHEMA_ID,
        "schema_version": RESULT_SCHEMA_VERSION,
        "record_type": "batch_result",
        "app_version": VERSION,
        "output_dir": str(Path(output_dir).resolve()),
        **payload,
    }
    if manifest_path is not None:
        record["manifest_path"] = str(Path(manifest_path).resolve())
    if include_results:
        record["results"] = [
            _cli_result_record(job, result)
            for job, result in zip(jobs, batch_result.results)
        ]
    return record


def _emit_cli_results(
    batch_result,
    jobs,
    *,
    result_format,
    output_dir,
    manifest_path=None,
    stream=None,
):
    if result_format == "human":
        return
    destination = stream or sys.stdout
    if result_format == "json":
        print(
            json.dumps(
                _cli_batch_record(
                    batch_result,
                    jobs,
                    output_dir=output_dir,
                    manifest_path=manifest_path,
                ),
                sort_keys=True,
            ),
            file=destination,
        )
        return
    if result_format == "jsonl":
        for job, result in zip(jobs, batch_result.results):
            print(
                json.dumps(
                    _cli_result_record(job, result),
                    sort_keys=True,
                ),
                file=destination,
            )
        print(
            json.dumps(
                _cli_batch_record(
                    batch_result,
                    jobs,
                    output_dir=output_dir,
                    manifest_path=manifest_path,
                    include_results=False,
                ),
                sort_keys=True,
            ),
            file=destination,
        )
        return
    raise ValueError(f"unsupported CLI result format: {result_format}")


def _cli_exit_code(batch_result):
    if batch_result.state is RenderState.SUCCEEDED:
        return 0
    if any(result.usable_output for result in batch_result.results):
        return 1
    return 2


class WatchFolderTracker:
    """Detect supported top-level files after their size/mtime stabilizes."""

    def __init__(self, directory, stability_seconds=WATCH_STABILITY_SECONDS):
        self.directory = Path(directory).resolve()
        self.stability_seconds = max(0.0, float(stability_seconds))
        self._observed = {}
        self._submitted = {}

    def scan(self, now=None):
        observed_at = time.monotonic() if now is None else float(now)
        ready = []
        current_paths = set()
        for path in sorted(
            self.directory.iterdir(),
            key=lambda candidate: candidate.name.casefold(),
        ):
            if path.suffix.lower() not in SUPPORTED_FORMATS:
                continue
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
            except OSError:
                continue
            normalized = str(path.resolve())
            current_paths.add(normalized)
            signature = (int(stat.st_size), int(stat.st_mtime_ns))
            previous = self._observed.get(normalized)
            if previous is None or previous[0] != signature:
                self._observed[normalized] = (signature, observed_at)
                stable_since = observed_at
            else:
                stable_since = previous[1]
            if self._submitted.get(normalized) == signature:
                continue
            if observed_at - stable_since >= self.stability_seconds:
                ready.append((normalized, signature))

        for normalized in set(self._observed) - current_paths:
            self._observed.pop(normalized, None)
            self._submitted.pop(normalized, None)
        return ready

    def mark_submitted(self, path, signature):
        self._submitted[str(Path(path).resolve())] = tuple(signature)


def _run_watch_folder_cli(
    *,
    watch_dir,
    output_dir,
    params,
    preset_name,
    workers,
    compute_backend,
    seed=None,
    diagnostic_retention=MAX_RETAINED_LOGS,
    redact_diagnostics=True,
    stop_event=None,
    poll_seconds=WATCH_POLL_SECONDS,
    stability_seconds=WATCH_STABILITY_SECONDS,
    max_cycles=None,
    diagnostic_path=None,
    result_format="human",
    audit_options=None,
):
    watch_root = Path(watch_dir).resolve()
    output_root = Path(output_dir).resolve()
    if not watch_root.is_dir():
        raise ValueError(f"watch directory not found: {watch_root}")
    if output_root == watch_root:
        raise ValueError(
            "watch output directory must differ from the watched directory"
        )
    output_root.mkdir(parents=True, exist_ok=True)
    worker_count = _validated_parallel_workers(workers)
    audit_options = _validated_audit_options(audit_options)
    tracker = WatchFolderTracker(watch_root, stability_seconds)
    requested_stop = stop_event or threading.Event()
    cancel_event = threading.Event()
    output_lock = threading.Lock()
    log_lock = threading.Lock()
    used_outputs = set()
    results = []
    result_jobs = []
    started_at = time.monotonic()
    run_log = RunDiagnostics(
        "cli-watch",
        path=diagnostic_path,
        redact=redact_diagnostics,
        max_logs=diagnostic_retention,
    )

    def watch_log(message):
        with log_lock:
            print(message, file=sys.stderr)
            run_log.write(message)

    run_log.write_header(
        "cli-watch",
        [],
        output_root,
        params,
        preset_name,
        "generated per stable file",
    )
    watch_log(f"{APP_NAME} v{VERSION}")
    watch_log(RIGHTS_ONLY_NOTICE)
    watch_log(EVIDENCE_NOTICE)
    watch_log(f"Watching: {watch_root}")
    watch_log(f"Output: {output_root}")
    watch_log(
        f"Stable-file delay: {float(stability_seconds):.1f}s | "
        f"Parallel workers: {worker_count}"
    )
    watch_log("Press Ctrl+C to stop.")

    executor = ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="sunojump-watch-file",
    )
    inflight = {}
    progress_buckets = {}
    cycles = 0

    def collect_finished():
        for future in list(inflight):
            if not future.done():
                continue
            job, _signature = inflight.pop(future)
            path = job["input_path"]
            try:
                result = future.result()
            except Exception as exc:
                watch_log(traceback.format_exc().rstrip())
                result = RenderResult(
                    state=RenderState.FAILED,
                    input_path=path,
                    error_code=RenderErrorCode.UNEXPECTED,
                    message=str(exc),
                )
            results.append(result)
            result_jobs.append(job)
            watch_log(format_render_result(result))

    try:
        while not requested_stop.is_set():
            collect_finished()
            available = max(0, worker_count - len(inflight))
            if available:
                for path, signature in tracker.scan()[:available]:
                    tracker.mark_submitted(path, signature)
                    effective_seed = (
                        seed if seed is not None else secrets.randbits(64)
                    )
                    job = {
                        "id": uuid.uuid4().hex,
                        "input_path": path,
                        "effective_seed": effective_seed,
                    }
                    try:
                        manifest_store = BatchManifestStore.create(
                            default_manifest_path(output_root),
                            app_version=VERSION,
                            output_dir=output_root,
                            config=params,
                            jobs=[job],
                            audit_options=audit_options,
                        )
                    except (OSError, BatchManifestError) as exc:
                        result = RenderResult(
                            state=RenderState.FAILED,
                            input_path=path,
                            error_code=RenderErrorCode.MANIFEST_WRITE_FAILED,
                            message=str(exc),
                            effective_seed=effective_seed,
                        )
                        results.append(result)
                        result_jobs.append(job)
                        watch_log(format_render_result(result))
                        continue
                    label = f"[watch {Path(path).name}]"

                    def progress(value, jid=job["id"], prefix=label):
                        bucket = min(99, max(0, int(value))) // 10 * 10
                        with log_lock:
                            if bucket <= progress_buckets.get(jid, -10):
                                return
                            progress_buckets[jid] = bucket
                            text = f"{prefix} Progress: {bucket}%"
                            print(text, file=sys.stderr)
                            run_log.write(text)

                    future = executor.submit(
                        _run_batch_render_job,
                        job_id=job["id"],
                        filepath=path,
                        effective_seed=effective_seed,
                        params=params,
                        output_dir=str(output_root),
                        used_outputs=used_outputs,
                        output_lock=output_lock,
                        manifest_store=manifest_store,
                        cancel_event=cancel_event,
                        log_fn=lambda message, prefix=label: watch_log(
                            f"{prefix} {message}"
                        ),
                        progress_fn=progress,
                        started_fn=lambda prefix=label: watch_log(
                            f"{prefix} Stable file accepted"
                        ),
                        compute_backend=compute_backend,
                        audit_options=audit_options,
                    )
                    inflight[future] = (job, signature)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                requested_stop.set()
                break
            requested_stop.wait(max(0.01, float(poll_seconds)))
    except KeyboardInterrupt:
        watch_log("Stopping watch folder...")
        requested_stop.set()
    finally:
        cancel_event.set()
        executor.shutdown(wait=True, cancel_futures=False)
        collect_finished()
        run_log.close()

    if results:
        batch = BatchResult.from_results(
            results,
            time.monotonic() - started_at,
        )
        print(format_batch_result(batch), file=sys.stderr)
        _emit_cli_results(
            batch,
            result_jobs,
            result_format=result_format,
            output_dir=output_root,
        )
        return batch
    print(
        "Watch stopped; no stable audio files were processed.",
        file=sys.stderr,
    )
    return None


def cli_main():
    configure_locale(requested_locale_from_argv(sys.argv[1:]))
    parser = _build_cli_parser()
    args = parser.parse_args()
    try:
        resolve_compute_backend(args.compute)
    except ComputeBackendError as exc:
        parser.error(str(exc))
    if args.seed is not None and args.seed < 0:
        parser.error("--seed must be a non-negative integer")
    if args.retry and not args.resume:
        parser.error("--retry requires --resume")
    if args.watch and (args.input or args.resume or args.manifest or args.retry):
        parser.error(
            "--watch cannot be combined with --input, --resume, "
            "--manifest, or --retry"
        )
    if args.resume and args.manifest:
        parser.error("--manifest cannot be combined with --resume")
    if not args.resume and not args.input and not args.watch:
        parser.error(
            "--input is required unless --resume or --watch is used"
        )

    recovery_notes = []
    manifest_store = None
    audit_options = _validated_audit_options({
        "spectrogram": bool(args.spectrogram),
        "loudness": bool(args.loudness_report),
    })
    if args.resume:
        forbidden = {
            "-i", "--input", "-o", "--output", "-p", "--preset",
            "-f", "--format", "--preset-file", "--profile", "--seed",
            "--no-spectral-scan", "--no-watermark-scan",
            "--c2pa-policy",
            "--enable-pass", "--disable-pass", "--spectral",
            "--spectral-sub-bass", "--spectral-low-mids",
            "--spectral-presence", "--spectral-air", "--dynamic-eq",
            "--pitch", "--tempo", "--phase", "--stereo", "--noise",
            "--dynamics", "--humanize", "--reencode", "--spectrogram",
            "--loudness-report",
        }
        if _argv_uses_any_option(sys.argv[1:], forbidden):
            parser.error(
                "--resume uses the manifest's inputs, output directory, "
                "configuration, and seeds; remove conflicting options"
            )
        try:
            manifest_store = BatchManifestStore.load(args.resume)
            recovery_notes = manifest_store.reconcile()
            params = validate_render_config(
                manifest_store.config,
                require_complete=True,
                allow_output_format=True,
            )
            audit_options = _validated_audit_options(
                manifest_store.audit_options
            )
            policy = args.retry or "pending"
            jobs = _validated_manifest_jobs(
                manifest_store.select(policy),
                params,
            )
        except (
            BatchManifestError,
            ConfigurationError,
            OSError,
        ) as exc:
            print(f"Error: cannot resume batch: {exc}", file=sys.stderr)
            sys.exit(2)
        if not jobs:
            counts = ", ".join(
                f"{state}={count}"
                for state, count in manifest_store.counts.items()
                if count
            )
            print(
                f"No {policy} jobs in {manifest_store.path} ({counts}).",
                file=sys.stderr,
            )
            return
        out_dir = manifest_store.output_dir
        preset_name = f"Recovered ({policy})"
    else:
        preset_name = args.preset.capitalize()
        params = dict(PRESETS[preset_name])
        if args.profile:
            try:
                with open(args.profile, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                document = _validate_profile_document(data)
                params = document["params"]
                preset_name = document["name"]
                print(
                    f"Loaded profile from {args.profile}",
                    file=sys.stderr,
                )
            except (
                ConfigurationError,
                json.JSONDecodeError,
                OSError,
                TypeError,
            ) as exc:
                print(
                    f"Error: invalid configuration in profile: {exc}",
                    file=sys.stderr,
                )
                sys.exit(2)
        elif args.preset_file:
            try:
                with open(args.preset_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                document = _validate_preset_document(data)
                params = document["params"]
                print(
                    f"Loaded preset from {args.preset_file}",
                    file=sys.stderr,
                )
                preset_name = document["name"]
            except (
                ConfigurationError,
                json.JSONDecodeError,
                OSError,
                TypeError,
            ) as exc:
                print(
                    f"Error: invalid configuration in preset file: {exc}",
                    file=sys.stderr,
                )
                sys.exit(2)
        try:
            params = _apply_cli_overrides(params, args)
            params["output_format"] = args.out_format
            params["c2pa_policy"] = args.c2pa_policy
            params = validate_render_config(
                params,
                require_complete=True,
                allow_output_format=True,
            )
        except ConfigurationError as exc:
            print(f"Error: invalid configuration: {exc}", file=sys.stderr)
            sys.exit(2)

        if args.watch:
            watch_path = Path(args.watch)
            if not watch_path.is_dir():
                print(
                    f"Error: watch directory not found: {args.watch}",
                    file=sys.stderr,
                )
                sys.exit(2)
            watch_format = params["output_format"]
            if _format_requires_ffmpeg(watch_format):
                if not _check_ffmpeg():
                    print(
                        f"Error: {watch_format.upper()} export requires "
                        "ffmpeg in PATH. Use WAV/FLAC/OGG or install ffmpeg.",
                        file=sys.stderr,
                    )
                    sys.exit(2)
                if not _ffmpeg_encoder_available(watch_format):
                    encoder = FFMPEG_FORMAT_ENCODERS.get(
                        watch_format,
                        watch_format,
                    )
                    print(
                        f"Error: ffmpeg lacks {encoder} encoder for "
                        f"{watch_format.upper()} export.",
                        file=sys.stderr,
                    )
                    sys.exit(2)
            watch_output = args.output or str(watch_path / "output")
            try:
                watch_result = _run_watch_folder_cli(
                    watch_dir=watch_path,
                    output_dir=watch_output,
                    params=params,
                    preset_name=preset_name,
                    workers=args.workers,
                    compute_backend=args.compute,
                    seed=args.seed,
                    diagnostic_retention=args.diagnostic_retention,
                    redact_diagnostics=not args.no_redact_diagnostics,
                    result_format=args.result_format,
                    audit_options=audit_options,
                )
                if watch_result is None:
                    return
                sys.exit(_cli_exit_code(watch_result))
            except (OSError, ValueError) as exc:
                print(
                    f"Error: cannot start watch folder: {exc}",
                    file=sys.stderr,
                )
                sys.exit(2)

        input_path = Path(args.input)
        files = []
        if input_path.is_dir():
            for file_path in sorted(input_path.rglob('*')):
                if file_path.suffix.lower() in SUPPORTED_FORMATS:
                    files.append(str(file_path))
        elif input_path.is_file():
            files.append(str(input_path))
        else:
            print(f"Error: {args.input} not found", file=sys.stderr)
            sys.exit(2)
        if not files:
            print(_tr("No supported audio files found."), file=sys.stderr)
            sys.exit(2)
        inspections = [
            (filepath, inspect_c2pa(filepath))
            for filepath in files
        ]
        failed_inspections = [
            (filepath, inspection)
            for filepath, inspection in inspections
            if inspection.failed
        ]
        if failed_inspections:
            for filepath, inspection in failed_inspections:
                print(
                    "Error: C2PA provenance inspection failed for "
                    f"{Path(filepath).name}: {inspection.message}",
                    file=sys.stderr,
                )
            print(
                "No output was created because source provenance could not "
                "be inspected safely.",
                file=sys.stderr,
            )
            sys.exit(2)
        present_inspections = [
            (filepath, inspection)
            for filepath, inspection in inspections
            if inspection.present
        ]
        if (
            present_inspections
            and args.c2pa_policy != C2PA_POLICY_ALLOW_REMOVAL
        ):
            for filepath, inspection in present_inspections:
                stores = ", ".join(
                    f"{store.location} sha256:{store.sha256[:12]}"
                    for store in inspection.manifest_stores
                )
                print(
                    f"Error: {Path(filepath).name} contains C2PA Content "
                    f"Credentials ({inspection.status}; {stores}).",
                    file=sys.stderr,
                )
            print(
                "Processing is blocked. Re-run with "
                "--c2pa-policy allow-removal only if you accept that source "
                "files stay unchanged while transformed outputs omit and do "
                "not re-sign those credentials. This is not evidence of an "
                "acoustic-detector change.",
                file=sys.stderr,
            )
            sys.exit(2)
        if present_inspections:
            print(
                "C2PA policy acknowledged: originals remain unchanged; "
                "transformed outputs will omit and not re-sign source "
                "Content Credentials.",
                file=sys.stderr,
            )
        jobs = [
            {
                "id": uuid.uuid4().hex,
                "input_path": filepath,
                "effective_seed": (
                    args.seed
                    if args.seed is not None
                    else secrets.randbits(64)
                ),
            }
            for filepath in files
        ]
        out_dir = args.output or DEFAULT_OUTPUT

    out_format = params["output_format"]
    if _format_requires_ffmpeg(out_format):
        if not _check_ffmpeg():
            print(
                f"Error: {out_format.upper()} export requires ffmpeg in PATH. "
                "Use WAV/FLAC/OGG or install ffmpeg.",
                file=sys.stderr,
            )
            sys.exit(2)
        if not _ffmpeg_encoder_available(out_format):
            encoder = FFMPEG_FORMAT_ENCODERS.get(out_format, out_format)
            print(
                f"Error: ffmpeg lacks {encoder} encoder for "
                f"{out_format.upper()} export. Install ffmpeg with {encoder} "
                "support or use WAV/FLAC/OGG.",
                file=sys.stderr,
            )
            sys.exit(2)

    try:
        os.makedirs(out_dir, exist_ok=True)
        if manifest_store is None:
            manifest_store = BatchManifestStore.create(
                args.manifest or default_manifest_path(out_dir),
                app_version=VERSION,
                output_dir=out_dir,
                config=params,
                jobs=jobs,
                audit_options=audit_options,
            )
            jobs = manifest_store.select("pending")
    except (OSError, BatchManifestError) as exc:
        print(f"Error: cannot create batch state: {exc}", file=sys.stderr)
        sys.exit(2)

    files = [job["input_path"] for job in jobs]
    run_log = RunDiagnostics(
        'cli',
        redact=not args.no_redact_diagnostics,
        max_logs=args.diagnostic_retention,
    )

    def cli_log(msg):
        print(msg, file=sys.stderr)
        run_log.write(msg)

    print(f"{APP_NAME} v{VERSION}", file=sys.stderr)
    print(RIGHTS_ONLY_NOTICE, file=sys.stderr)
    print(EVIDENCE_NOTICE, file=sys.stderr)
    print(
        f"Preset: {preset_name} | Format: {out_format.upper()} | "
        f"Files: {len(files)}",
        file=sys.stderr,
    )
    print(f"Run log: {run_log.path}", file=sys.stderr)
    print(
        f"Batch manifest: {manifest_store.path}\n",
        file=sys.stderr,
    )
    run_log.write_header(
        'cli',
        files,
        out_dir,
        params,
        preset_name,
        "recorded per file in batch manifest",
    )
    for note in recovery_notes:
        cli_log(f"Recovery: {note}")

    started_at = time.monotonic()
    results = [None] * len(jobs)
    used_outputs = set()
    output_lock = threading.Lock()
    console_lock = threading.Lock()
    cancel_event = threading.Event()
    progress_state = {}

    def synchronized_cli_log(message):
        with console_lock:
            cli_log(message)

    worker_count = min(args.workers, len(jobs))
    synchronized_cli_log(f"Parallel file workers: {worker_count}")
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="sunojump-cli-file",
    ) as executor:
        future_jobs = {}
        for index, job in enumerate(jobs):
            job_id = job["id"]
            filepath = job["input_path"]
            label = f"[{index + 1}/{len(jobs)} {Path(filepath).name}]"
            job_params = job.get("config", params)

            def job_log(message, prefix=label):
                synchronized_cli_log(f"{prefix} {message}")

            def job_progress(value, jid=job_id, prefix=label):
                bucket = min(99, max(0, int(value))) // 10 * 10
                with console_lock:
                    previous = progress_state.get(jid, -10)
                    if bucket <= previous:
                        return
                    progress_state[jid] = bucket
                    cli_log(f"{prefix} Progress: {bucket}%")

            future = executor.submit(
                _run_batch_render_job,
                job_id=job_id,
                filepath=filepath,
                effective_seed=job["effective_seed"],
                params=job_params,
                output_dir=out_dir,
                used_outputs=used_outputs,
                output_lock=output_lock,
                manifest_store=manifest_store,
                cancel_event=cancel_event,
                log_fn=job_log,
                progress_fn=job_progress,
                started_fn=lambda prefix=label: synchronized_cli_log(
                    f"{prefix} Started"
                ),
                compute_backend=args.compute,
                audit_options=audit_options,
            )
            future_jobs[future] = (index, job_id, filepath)

        for future in as_completed(future_jobs):
            index, _job_id, filepath = future_jobs[future]
            try:
                result = future.result()
            except Exception as exc:
                synchronized_cli_log(traceback.format_exc().rstrip())
                result = RenderResult(
                    state=RenderState.FAILED,
                    input_path=str(filepath),
                    error_code=RenderErrorCode.UNEXPECTED,
                    message=str(exc),
                    effective_seed=jobs[index]["effective_seed"],
                )
            results[index] = result
            synchronized_cli_log(format_render_result(result))
            synchronized_cli_log("---")

    batch_result = BatchResult.from_results(
        results,
        time.monotonic() - started_at,
    )
    cli_log(f"\n{format_batch_result(batch_result)}")
    cli_log(f"Output directory: {out_dir}")
    cli_log(f"Batch manifest: {manifest_store.path}")
    run_log.close()
    _emit_cli_results(
        batch_result,
        jobs,
        result_format=args.result_format,
        output_dir=out_dir,
        manifest_path=manifest_store.path,
    )
    sys.exit(_cli_exit_code(batch_result))


# ============================================================
#  Entry Point
# ============================================================
if __name__ == '__main__':
    _requested_locale = requested_locale_from_argv(sys.argv[1:])
    configure_locale(_requested_locale)
    _cli_flags = {
        '-i', '--input', '-h', '--help', '--version', '--native-runtime',
        '--manifest', '--resume', '--retry', '--compute', '--workers',
        '--watch', '--result-format', '--spectrogram', '--loudness-report',
    }
    if len(sys.argv) > 1 and _argv_uses_any_option(
        sys.argv[1:],
        _cli_flags,
    ):
        if '--version' in sys.argv:
            print(f"{APP_NAME} v{VERSION}")
            sys.exit(0)
        if '--native-runtime' in sys.argv:
            print(json.dumps(_native_runtime_report(), sort_keys=True))
            sys.exit(0)
        cli_main()
    else:
        app = QApplication(without_locale_args(sys.argv))
        configure_locale(_requested_locale, app)
        app.setStyle('Fusion')
        app.setStyleSheet(STYLE)
        win = MainWindow(locale_name=_requested_locale)
        win.show()
        sys.exit(app.exec())
