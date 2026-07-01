#!/usr/bin/env python3
"""SunoJump v1.6.1 - Audio fingerprint masking tool for Suno AI"""

import multiprocessing
multiprocessing.freeze_support()

import sys
if sys.version_info < (3, 11):
    sys.stderr.write(
        f"SunoJump requires Python 3.11 or later (found {sys.version}).\n"
    )
    sys.exit(1)

# --- Imports ---
import os, json, argparse, tempfile, shutil, threading, hashlib
import platform, traceback
import subprocess
from pathlib import Path
from datetime import datetime

VERSION = "1.6.1"
APP_NAME = "SunoJump"
PRESET_SCHEMA_VERSION = 1

try:
    import numpy as np
    import soundfile as sf
    import scipy
    from scipy import signal
    import mutagen
    from mutagen import File as MutagenFile
except ImportError as e:
    missing = getattr(e, 'name', None) or str(e)
    print(f"ERROR: Missing required Python dependency: {missing}", file=sys.stderr)
    print("Install dependencies with:  python -m pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QLabel, QListWidget, QListWidgetItem,
        QComboBox, QLineEdit, QCheckBox, QSlider, QProgressBar,
        QTextEdit, QFileDialog, QAbstractItemView, QFrame, QSizePolicy,
        QStyle, QScrollArea,
    )
    from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QSettings, PYQT_VERSION_STR, QT_VERSION_STR
    from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QDesktopServices
except ImportError as e:
    missing = getattr(e, 'name', None) or str(e)
    print(f"ERROR: Missing required GUI dependency: {missing}", file=sys.stderr)
    print("Install dependencies with:  python -m pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

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

# UserRole keys on QListWidgetItem
ROLE_INPUT = Qt.ItemDataRole.UserRole
ROLE_OUTPUT = Qt.ItemDataRole.UserRole + 1

PRESETS = {
    'Gentle': {
        'strip_metadata': True,
        'watermark_scan_enabled': True,
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
        'watermark_scan_enabled': True,
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
        'watermark_scan_enabled': True,
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
        'watermark_scan_enabled': True,
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

_PRESET_MIGRATIONS = {}


def _migrate_preset(data):
    schema = data.get('schema_version', 0)
    if schema > PRESET_SCHEMA_VERSION:
        raise ValueError(
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


PARAM_DEFS = [
    # (key, label, min, max, default, suffix, decimals, enabled_key, display_factor)
    ('spectral_strength', 'Spectral Perturbation', 0.0, 1.0, 0.30, '', 2, 'spectral_enabled', 1.0),
    ('spectral_sub_bass_strength', 'Sub-Bass Spectral', 0.0, 1.0, 0.30, '', 2, 'spectral_sub_bass_enabled', 1.0),
    ('spectral_low_mids_strength', 'Low-Mids Spectral', 0.0, 1.0, 0.30, '', 2, 'spectral_low_mids_enabled', 1.0),
    ('spectral_presence_strength', 'Presence Spectral', 0.0, 1.0, 0.30, '', 2, 'spectral_presence_enabled', 1.0),
    ('spectral_air_strength', 'Air Spectral', 0.0, 1.0, 0.30, '', 2, 'spectral_air_enabled', 1.0),
    ('dynamic_eq_amount', 'Dynamic EQ', 0.0, 1.0, 0.20, '', 2, 'dynamic_eq_enabled', 1.0),
    ('pitch_range', 'Pitch Micro-Shift', 0.0, 5.0, 0.80, ' st', 1, 'pitch_enabled', 1.0),
    ('tempo_range', 'Tempo Micro-Variation', 0.0, 0.15, 0.05, '%', 1, 'tempo_enabled', 100.0),
    ('phase_amount', 'Phase Scrambling', 0.0, 1.0, 0.30, '', 2, 'phase_enabled', 1.0),
    ('stereo_shift', 'Stereo Manipulation', 0.0, 0.5, 0.10, '', 2, 'stereo_enabled', 1.0),
    ('noise_level', 'Noise Injection', -70.0, -30.0, -50.0, ' dB', 0, 'noise_enabled', 1.0),
    ('dynamics_amount', 'Dynamics Modification', 0.0, 1.0, 0.20, '', 2, 'dynamics_enabled', 1.0),
    ('humanize_amount', 'Humanization', 0.0, 1.0, 0.30, '', 2, 'humanize_enabled', 1.0),
    ('reencode_bitrate', 'Lossy Re-encode', 96, 320, 192, ' kbps', 0, 'reencode_enabled', 1.0),
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
WATERMARK_SCAN_MAX_CANDIDATES = 5
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
    font-size: 13px;
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
    font-size: 28px;
    font-weight: bold;
}}
QLabel#appSubtitle {{
    color: {C['subtext']};
    font-size: 12px;
}}
QLabel#sectionTitle {{
    color: {C['text']};
    font-size: 15px;
    font-weight: bold;
}}
QLabel#sectionSubtitle {{
    color: {C['overlay']};
    font-size: 11px;
}}
QLabel#statusPill, QLabel#accentPill {{
    background-color: {C['surface0']};
    border: 1px solid {C['surface1']};
    border-radius: 8px;
    padding: 5px 10px;
    color: {C['subtext']};
    font-size: 12px;
    font-weight: bold;
}}
QLabel#accentPill {{
    background-color: {C['accent']};
    border-color: {C['accent']};
    color: {C['crust']};
}}
QLabel#countLabel {{
    color: {C['accent_soft']};
    font-size: 12px;
    font-weight: bold;
}}
QLabel#hintLabel {{
    color: {C['overlay']};
    font-size: 11px;
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
QPushButton:disabled {{
    background-color: {C['mantle']};
    color: {C['overlay']};
    border-color: {C['surface0']};
}}
QPushButton#processBtn {{
    background-color: {C['accent']};
    border-color: {C['accent']};
    color: {C['crust']};
    font-size: 15px;
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
    font-size: 12px;
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
QComboBox {{
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
QLineEdit {{
    background-color: {C['crust']};
    border: 1px solid {C['surface1']};
    border-radius: 7px;
    padding: 7px 10px;
    color: {C['text']};
}}
QLineEdit:focus {{
    border-color: {C['accent']};
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
    font-size: 12px;
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
    widget.setAccessibleName(name)
    widget.setAccessibleDescription(description)
    if hasattr(widget, 'setToolTip') and not widget.toolTip():
        widget.setToolTip(description)


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


MAX_RETAINED_LOGS = 30


def _new_diagnostics_path(prefix='run'):
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    return _diagnostics_dir() / f"{prefix}-{stamp}.log"


def _enforce_log_retention(max_logs=MAX_RETAINED_LOGS):
    log_dir = _diagnostics_dir()
    if not log_dir.is_dir():
        return
    logs = sorted(log_dir.glob('*.log'), key=lambda p: p.stat().st_mtime)
    while len(logs) > max_logs:
        oldest = logs.pop(0)
        try:
            oldest.unlink()
        except OSError:
            pass


def _redact_home_paths(text):
    home = str(Path.home())
    if sys.platform.startswith('win'):
        lower = text.lower()
        home_lower = home.lower()
        result = []
        i = 0
        while i < len(text):
            if lower[i:i + len(home_lower)] == home_lower:
                result.append('~')
                i += len(home)
            else:
                result.append(text[i])
                i += 1
        return ''.join(result)
    return text.replace(home, '~')


def _diagnostic_environment_lines():
    return [
        f"App: {APP_NAME} v{VERSION}",
        f"Python: {sys.version.replace(os.linesep, ' ')}",
        f"Executable: {sys.executable}",
        f"Frozen: {bool(getattr(sys, 'frozen', False))}",
        f"Platform: {platform.platform()}",
        f"numpy: {np.__version__}",
        f"scipy: {scipy.__version__}",
        f"soundfile: {getattr(sf, '__version__', 'unknown')}",
        f"mutagen: {getattr(mutagen, 'version_string', 'unknown')}",
        f"PyQt6: {PYQT_VERSION_STR}",
        f"Qt: {QT_VERSION_STR}",
        f"PyQt6 Multimedia: {'available' if _MULTIMEDIA_OK else 'missing'}",
        f"ffmpeg: {'available' if _check_ffmpeg() else 'missing'}"
        + (f" (encoders: {', '.join(k for k, v in _probe_ffmpeg_encoders().items() if v) or 'none'})" if _check_ffmpeg() else ""),
    ]


class RunDiagnostics:
    def __init__(self, prefix='run', path=None, redact=True):
        self.path = Path(path) if path is not None else _new_diagnostics_path(prefix)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._redact = redact
        self.path.write_text('', encoding='utf-8')
        _enforce_log_retention()

    def write(self, msg):
        text = '' if msg is None else str(msg)
        if self._redact:
            text = _redact_home_paths(text)
        lines = text.splitlines() or ['']
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self._lock:
            with self.path.open('a', encoding='utf-8') as f:
                for line in lines:
                    f.write(f"[{ts}] {line}\n")

    def write_header(self, mode, inputs, output_dir, params, preset_name=None, seed=None):
        self.write(f"{APP_NAME} v{VERSION} run started")
        self.write(f"Mode: {mode}")
        self.write(f"Preset: {preset_name or 'Custom'}")
        self.write(f"Seed: {seed if seed is not None else 'random'}")
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


def _planned_output_path(input_path, output_dir, ext, used_paths=None):
    """Return a collision-free output path for an input file."""
    used_paths = used_paths if used_paths is not None else set()
    stem = Path(input_path).stem
    base = os.path.join(output_dir, f"{stem}_sj{ext}")
    candidate = base
    counter = 2
    while _norm_output_path(candidate) in used_paths or os.path.exists(candidate):
        candidate = os.path.join(output_dir, f"{stem}_sj_{counter}{ext}")
        counter += 1
    used_paths.add(_norm_output_path(candidate))
    return candidate, candidate != base


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


def _humanize_bytes(n_bytes):
    value = float(n_bytes)
    for suffix in ('B', 'KB', 'MB', 'GB'):
        if value < 1024.0 or suffix == 'GB':
            return f"{value:.1f} {suffix}" if suffix != 'B' else f"{int(value)} B"
        value /= 1024.0


def _preflight_audio_input(input_path, preview_seconds=None):
    path = Path(input_path)
    if path.suffix.lower() not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported audio format: {path.suffix or '(none)'}")

    try:
        stat = path.stat()
    except OSError as e:
        raise ValueError(f"cannot access input file: {e}") from e

    if not path.is_file():
        raise ValueError("input path is not a file")
    if stat.st_size <= 0:
        raise ValueError("empty audio file")
    if stat.st_size > MAX_INPUT_FILE_BYTES:
        raise ValueError(
            "input file is too large "
            f"({_humanize_bytes(stat.st_size)} > {_humanize_bytes(MAX_INPUT_FILE_BYTES)})"
        )

    try:
        info = sf.info(str(path))
    except Exception as e:
        raise ValueError(f"unsupported or malformed audio file: {e}") from e

    frames = int(getattr(info, 'frames', 0) or 0)
    sr = int(getattr(info, 'samplerate', 0) or 0)
    channels = int(getattr(info, 'channels', 0) or 0)
    if frames <= 0:
        raise ValueError("empty audio file")
    if sr <= 0:
        raise ValueError("invalid sample rate")
    if sr > MAX_AUDIO_SAMPLE_RATE:
        raise ValueError(
            f"sample rate too high ({sr} Hz > {MAX_AUDIO_SAMPLE_RATE} Hz)"
        )
    if channels <= 0:
        raise ValueError("invalid channel count")
    if channels > MAX_AUDIO_CHANNELS:
        raise ValueError(
            f"too many channels ({channels} > {MAX_AUDIO_CHANNELS})"
        )

    duration = frames / float(sr)
    preview_requested = preview_seconds is not None and preview_seconds > 0
    if not preview_requested and duration > MAX_AUDIO_DURATION_SECONDS:
        raise ValueError(
            f"audio duration too long ({duration / 60.0:.1f} min > "
            f"{MAX_AUDIO_DURATION_SECONDS / 60.0:.1f} min)"
        )

    read_frames = frames
    if preview_requested:
        read_frames = min(frames, max(1, int(preview_seconds * sr)))

    decoded_bytes = read_frames * channels * np.dtype('float64').itemsize
    if decoded_bytes > MAX_DECODED_AUDIO_BYTES:
        raise ValueError(
            "decoded audio would exceed memory guardrail "
            f"({_humanize_bytes(decoded_bytes)} > "
            f"{_humanize_bytes(MAX_DECODED_AUDIO_BYTES)})"
        )

    return {
        'frames': frames,
        'samplerate': sr,
        'channels': channels,
        'duration': duration,
        'read_frames': read_frames,
        'decoded_bytes': decoded_bytes,
    }


# ============================================================
#  Audio Processor
# ============================================================
class AudioProcessor:
    # Humanize pass processes long audio in chunks to bound peak memory.
    # Chunks are rendered with a shared modulation curve (continuous across
    # boundaries) so the output is indistinguishable from whole-file rendering.
    _HUMANIZE_CHUNK_SEC = 60.0

    def __init__(self, params, log_fn=None, progress_fn=None, cancel_event=None, seed=None):
        self.params = params
        self.log = log_fn or print
        self.progress = progress_fn or (lambda v: None)
        self.rng = np.random.default_rng(seed) if seed is not None else np.random.default_rng()
        self._cancel_event = cancel_event or threading.Event()
        self._watermark_candidates = []
        self._seed = seed
        self._trace = {"passes": {}}

    def cancel(self):
        self._cancel_event.set()

    def _is_cancelled(self):
        return self._cancel_event.is_set()

    # --- Main pipeline ---
    def process(self, input_path, output_path, preview_seconds=None):
        """Process audio file.

        If preview_seconds is set and > 0, only the first N seconds of the
        input are loaded and processed. This keeps render time short enough
        for interactive preset A/B auditioning.
        """
        self.log(f"Loading {Path(input_path).name}...")
        self._trace = {"passes": {}}

        try:
            preflight = _preflight_audio_input(input_path, preview_seconds)
        except ValueError as e:
            self.log(f"  Error: {e}")
            return False

        read_kwargs = {'dtype': 'float64'}
        if preview_seconds and preview_seconds > 0:
            read_kwargs['frames'] = preflight['read_frames']

        try:
            audio, sr = sf.read(input_path, **read_kwargs)
        except Exception as e:
            self.log(f"  Error reading file: {e}")
            self.log(traceback.format_exc().rstrip())
            return False

        if audio.size == 0:
            self.log("  Error: empty audio file")
            return False

        # Trim to preview length if requested
        if preview_seconds and preview_seconds > 0:
            max_samples = int(preview_seconds * sr)
            if audio.ndim == 1:
                audio = audio[:max_samples] if len(audio) > max_samples else audio
            else:
                audio = audio[:max_samples] if audio.shape[0] > max_samples else audio
            self.log(f"  Preview mode: first {preview_seconds:.0f}s ({audio.shape[0]/sr:.1f}s actual)")

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
            if self.params.get('watermark_scan_enabled', True):
                pass_names.append('Watermark Band Scan')
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
            return False

        for i, name in enumerate(pass_names):
            if self._is_cancelled():
                self.log("Cancelled.")
                return False

            self.log(f"  Pass {i+1}/{total}: {name}...")
            self.progress(int((i / total) * 90))

            try:
                if name == 'Metadata Strip':
                    pass  # applied on save
                elif name == 'Watermark Band Scan':
                    self._watermark_candidates = self._scan_watermark_bands(audio, sr)
                    if self._watermark_candidates:
                        bands = self._format_watermark_candidates(self._watermark_candidates)
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
                return False

        audio = np.clip(audio, -1.0, 1.0)

        # Save
        self.log(f"Saving {Path(output_path).name}...")
        self.progress(92)

        save_audio = audio[:, 0] if mono else audio
        fmt = self.params.get('output_format', 'wav').lower()
        tmp_output = None
        try:
            if self._is_cancelled():
                self.log("Cancelled.")
                return False
            tmp_output = _make_atomic_output_temp(output_path)
            if _format_requires_ffmpeg(fmt):
                if not _check_ffmpeg():
                    self.log(
                        f"  Save error: {fmt.upper()} export requires ffmpeg in PATH"
                    )
                    return False
                if not _ffmpeg_encoder_available(fmt):
                    encoder = FFMPEG_FORMAT_ENCODERS.get(fmt, fmt)
                    self.log(
                        f"  Save error: ffmpeg lacks {encoder} encoder for {fmt.upper()} export"
                    )
                    return False
                self._export_with_ffmpeg(save_audio, sr, tmp_output, fmt)
            elif fmt == 'flac':
                sf.write(tmp_output, save_audio, sr, format='FLAC')
            elif fmt == 'ogg':
                sf.write(tmp_output, save_audio, sr, format='OGG', subtype='VORBIS')
            else:
                sf.write(tmp_output, save_audio, sr, subtype='PCM_24')

            if self.params.get('strip_metadata', True):
                self._strip_metadata(tmp_output)

            if self._is_cancelled():
                self.log("Cancelled.")
                return False

            os.replace(tmp_output, output_path)
            tmp_output = None
        except Exception as e:
            self.log(f"  Save error: {e}")
            self.log(traceback.format_exc().rstrip())
            return False
        finally:
            _remove_file_silent(tmp_output)

        # Modification strength
        self.progress(96)
        orig_ch = original[:, 0]
        proc_ch = audio[:, 0]
        n = min(len(orig_ch), len(proc_ch))
        strength = self._compute_strength(orig_ch[:n], proc_ch[:n])

        self.log(f"Modification strength: {strength:.0f}%")
        if strength < 25:
            self.log("  Light -- may not be sufficient")
        elif strength < 50:
            self.log("  Moderate -- likely effective")
        elif strength < 75:
            self.log("  Strong -- highly likely effective")
        else:
            self.log("  Extreme -- verify audio quality")

        # Detection-risk signature (heuristic): lower after processing = more
        # natural-looking, less likely to trip AI-detection classifiers. This
        # is a directional indicator, not a guarantee against any specific
        # detector. Skipped for very short inputs where features are unstable.
        if n >= int(sr * 5):
            pre_risk = self._compute_detection_risk(orig_ch[:n], sr)
            post_risk = self._compute_detection_risk(proc_ch[:n], sr)
            delta = pre_risk - post_risk
            arrow = "down" if delta > 0 else "up" if delta < 0 else "flat"
            self.log(
                f"Detection signature: {pre_risk:.0f}% -> {post_risk:.0f}% "
                f"({arrow} {abs(delta):.0f}%)"
            )
            match = self._compute_constellation_match(orig_ch[:n], proc_ch[:n], sr)
            self.log(f"Constellation match: 100% -> {match:.0f}% landmarks")

        self._write_sidecar(input_path, output_path, sr, pass_names, strength)

        self.progress(100)
        return True

    def _write_sidecar(self, input_path, output_path, sr, pass_names, strength):
        try:
            hasher = hashlib.sha256()
            with open(input_path, 'rb') as f:
                for block in iter(lambda: f.read(65536), b''):
                    hasher.update(block)
            input_hash = hasher.hexdigest()
        except OSError:
            input_hash = None

        sidecar = {
            "sunojump_version": VERSION,
            "schema_version": PRESET_SCHEMA_VERSION,
            "seed": self._seed,
            "input_file": Path(input_path).name,
            "input_sha256": input_hash,
            "output_file": Path(output_path).name,
            "sample_rate": sr,
            "enabled_passes": pass_names,
            "modification_strength": round(strength, 1),
            "params": {
                k: v for k, v in self.params.items()
                if not callable(v)
            },
            "environment": {
                "numpy": np.__version__,
                "scipy": scipy.__version__,
                "soundfile": getattr(sf, '__version__', 'unknown'),
                "mutagen": getattr(mutagen, 'version_string', 'unknown'),
            },
            "passes": self._trace.get("passes", {}),
        }
        sidecar_path = Path(output_path).with_suffix('.sidecar.json')
        try:
            sidecar_path.write_text(
                json.dumps(sidecar, indent=2, default=str) + "\n",
                encoding='utf-8',
            )
            self.log(f"Sidecar written: {sidecar_path.name}")
        except OSError as e:
            self.log(f"  Warning: sidecar write failed: {e}")

    def _trace_segments(self, pass_name, segments):
        self._trace["passes"][pass_name] = {
            "segment_count": len(segments),
            "segments": segments,
        }

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

    # --- Watermark-band scan pre-pass ---
    def _scan_watermark_bands(self, audio, sr):
        if audio.shape[0] < 1024:
            return []

        mono = audio[:, 0] if audio.shape[1] == 1 else np.mean(audio, axis=1)
        n = len(mono)
        nperseg = min(4096, 1 << (n.bit_length() - 1))
        if nperseg < 512:
            return []
        noverlap = int(nperseg * 0.75)

        try:
            f, _, Zxx = signal.stft(mono, sr, nperseg=nperseg, noverlap=noverlap)
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
            if len(candidates) >= WATERMARK_SCAN_MAX_CANDIDATES:
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

    def _format_watermark_candidates(self, candidates):
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
        """Process in 3-second segments so the perturbation varies across the
        track. Each segment gets an independent random perturbation, which
        breaks detectors that look for consistent spectral signatures across
        the whole file (the hallmark of many AI music outputs)."""
        strength = self.params.get('spectral_strength', 0.3)
        n = audio.shape[0]
        base_seg_samples = int(3.0 * sr)
        overlap = int(0.1 * sr)

        # Short audio: single pass (no segmentation benefit)
        if n <= base_seg_samples:
            nperseg = self._choose_spectral_window(n)
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

        f, t, Zxx = signal.stft(channel, sr, nperseg=nperseg, noverlap=noverlap)
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
            for cand in self._watermark_candidates:
                band_mask = (f >= cand['low_hz']) & (f <= cand['high_hz'])
                if np.any(band_mask):
                    depth = min(0.55, 0.30 + cand['score'] * 0.01)
                    mag[band_mask] *= self.rng.uniform(
                        1.0 - scan_strength * depth,
                        1.0 + scan_strength * depth,
                        mag[band_mask].shape,
                    )

        Zxx_new = mag * np.exp(1j * phase)
        _, result = signal.istft(Zxx_new, sr, nperseg=nperseg, noverlap=noverlap)

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

        f, _, Zxx = signal.stft(channel, sr, nperseg=nperseg, noverlap=noverlap)
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
        _, result = signal.istft(Zxx_new, sr, nperseg=nperseg, noverlap=noverlap)
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
        fingerprint still varies across the track.
        """
        max_st = self.params.get('pitch_range', 0.8)
        max_var = self.params.get('tempo_range', 0.05)
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

        _, _, Z = signal.stft(
            signal_1d, nperseg=nperseg, noverlap=nperseg - hop,
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

        _, result = signal.istft(
            Z_out, nperseg=nperseg, noverlap=nperseg - hop,
        )
        return result

    # --- Non-uniform tempo micro-variation ---
    def _tempo_microvar(self, audio, sr):
        max_var = self.params.get('tempo_range', 0.05)
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
        result = np.zeros_like(audio)
        for ch in range(audio.shape[1]):
            result[:, ch] = self._phase_scramble_ch(audio[:, ch], sr, amount)
        return result

    def _phase_scramble_ch(self, channel, sr, amount):
        nperseg = _nperseg_for(len(channel))
        if nperseg == 0:
            return channel.copy()
        noverlap = nperseg // 2

        f, t, Zxx = signal.stft(channel, sr, nperseg=nperseg, noverlap=noverlap)
        mag = np.abs(Zxx)
        phase = np.angle(Zxx)

        phase_noise = self.rng.uniform(-np.pi, np.pi, phase.shape) * amount
        phase += phase_noise

        Zxx_new = mag * np.exp(1j * phase)
        _, result = signal.istft(Zxx_new, sr, nperseg=nperseg, noverlap=noverlap)

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
            f, t, audio_z = signal.stft(channel, sr, nperseg=nperseg, noverlap=noverlap)
            _, _, noise_z = signal.stft(noise, sr, nperseg=nperseg, noverlap=noverlap)
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

        _, shaped = signal.istft(shaped_z, sr, nperseg=nperseg, noverlap=noverlap)
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
        w1 = 2.0 * np.pi * mod_freq / sr
        w2 = 2.0 * np.pi * mod_freq * 2.7 / sr
        wb = 2.0 * np.pi * breath_freq / sr
        phase1 = phase0 * 1.3

        chunk = max(sr, int(self._HUMANIZE_CHUNK_SEC * sr))
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

    # --- Strength metric ---
    def _compute_strength(self, original, processed):
        sig_power = np.mean(original ** 2)
        if sig_power < 1e-12:
            return 0.0  # silence in, silence out
        diff = original - processed
        diff_power = np.mean(diff ** 2) + 1e-12
        snr = 10.0 * np.log10(sig_power / diff_power)
        return max(0.0, min(100.0, (40.0 - snr) * 2.5))

    # --- Detection risk heuristic ---
    def _compute_detection_risk(self, mono, sr):
        """Heuristic 0-100 score of how AI-generated the audio looks based on
        feature patterns common AI-music detectors exploit. This is a
        directional indicator, not a classifier trained on any specific
        detector's ground truth.

        Features combined:
          1. Spectral regularity -- AI output tends to have abnormally low
             frame-to-frame variance in its magnitude spectrum.
          2. High-frequency rolloff -- Many AI generators cut hard at ~16 kHz.
          3. Phase evolution -- Synthetic audio often has more predictable
             phase trajectories than natural recordings.
          4. Short-term dynamic variance -- AI output is often too "even".
        """
        if len(mono) < sr:
            return 50.0  # insufficient data -- neutral score

        nperseg = _nperseg_for(len(mono))
        if nperseg == 0:
            return 50.0

        _, _, Zxx = signal.stft(
            mono, sr, nperseg=nperseg, noverlap=nperseg // 2,
        )
        mag = np.abs(Zxx) + 1e-10
        log_mag = np.log(mag)

        # 1. Spectral regularity: low variance -> looks synthetic
        per_bin_var = np.var(log_mag, axis=1)
        mean_variance = float(np.mean(per_bin_var))
        # Natural music: variance roughly 0.5-2.0. AI: often 0.1-0.4.
        # Map: variance 0.1 -> 90 (risky), 1.5+ -> 10 (natural)
        regularity_score = np.clip(100.0 - mean_variance * 55.0, 0.0, 100.0)

        # 2. High-frequency rolloff ratio
        freqs_per_bin = (sr / 2.0) / max(1, mag.shape[0] - 1)
        bin_16k = int(16000.0 / freqs_per_bin)
        bin_20k = int(20000.0 / freqs_per_bin)
        energy_total = float(np.mean(mag))
        if energy_total > 1e-10 and bin_20k > bin_16k and bin_20k < mag.shape[0]:
            energy_16_20 = float(np.mean(mag[bin_16k:bin_20k]))
            ratio = energy_16_20 / (energy_total + 1e-10)
            # Natural music: 0.02-0.10. AI (hard-cut at 16k): near zero.
            rolloff_score = np.clip(90.0 - ratio * 1200.0, 0.0, 100.0)
        else:
            rolloff_score = 40.0  # low sample rate, can't measure

        # 3. Phase evolution variance
        # Natural audio: phase differences have high entropy. AI: more coherent.
        phase = np.angle(Zxx)
        phase_diff = np.diff(phase, axis=1)
        # Wrap phase differences to [-pi, pi]
        phase_diff = np.mod(phase_diff + np.pi, 2 * np.pi) - np.pi
        phase_entropy = float(np.std(phase_diff))
        # Natural: phase_entropy ~1.5-1.8. AI: ~1.0-1.3.
        phase_score = np.clip(150.0 - phase_entropy * 90.0, 0.0, 100.0)

        # 4. Short-term dynamic variance
        frame_size = 1024
        n_frames = len(mono) // frame_size
        if n_frames >= 8:
            frames = mono[:n_frames * frame_size].reshape(n_frames, frame_size)
            rms_vals = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
            # Coefficient of variation (more robust than raw variance)
            cov = float(np.std(rms_vals) / (np.mean(rms_vals) + 1e-12))
            # Natural: cov 0.3-0.8. AI: often 0.1-0.3.
            dynamic_score = np.clip(110.0 - cov * 180.0, 0.0, 100.0)
        else:
            dynamic_score = 40.0

        # Weighted blend. Weights derived from which features most strongly
        # correlate with AI-detector outputs in informal testing.
        total = (
            0.30 * regularity_score
            + 0.25 * rolloff_score
            + 0.20 * phase_score
            + 0.25 * dynamic_score
        )
        return float(np.clip(total, 0.0, 100.0))

    def _compute_constellation_match(self, original, processed, sr):
        """Estimate surviving constellation fingerprints using hash overlap."""
        orig_hashes = self._constellation_hashes(original, sr)
        proc_hashes = self._constellation_hashes(processed, sr)
        if not orig_hashes or not proc_hashes:
            return 0.0
        overlap = len(orig_hashes & proc_hashes)
        return float(np.clip((overlap / len(orig_hashes)) * 100.0, 0.0, 100.0))

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
        f, _, Zxx = signal.stft(work, sr, nperseg=nperseg, noverlap=nperseg - hop)
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
class ProcessWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    # file_started(row_index) - fired just before processing each file
    file_started = pyqtSignal(int)
    # file_done(row_index, success, output_path_or_empty)
    file_done = pyqtSignal(int, bool, str)
    # all_done(total_seconds)
    all_done = pyqtSignal(float)

    def __init__(self, files, params, output_dir):
        super().__init__()
        self.files = files
        self.params = params
        self.output_dir = output_dir
        self._cancel_event = threading.Event()

    def run(self):
        import time as _time
        try:
            os.makedirs(self.output_dir, exist_ok=True)
        except OSError as e:
            # Report and emit all_done so the UI doesn't stay stuck on "processing"
            self.log_signal.emit(f"Cannot create output directory: {e}")
            self.all_done.emit(0.0)
            return
        n_files = len(self.files)
        t_start = _time.time()
        used_outputs = set()

        for idx, filepath in enumerate(self.files):
            if self._cancel_event.is_set():
                break

            self.file_started.emit(idx)

            # Map per-file progress (0-100) to batch progress
            def batch_progress(v, _idx=idx, _n=n_files):
                self.progress_signal.emit((_idx * 100 + v) // _n)

            processor = AudioProcessor(
                self.params,
                log_fn=lambda msg: self.log_signal.emit(msg),
                progress_fn=batch_progress,
                cancel_event=self._cancel_event,
            )

            fmt = self.params.get('output_format', 'wav').lower()
            ext = _output_extension(fmt)
            out_path, renamed = _planned_output_path(
                filepath, self.output_dir, ext, used_outputs,
            )

            self.log_signal.emit(f"\n[{idx+1}/{n_files}] {Path(filepath).name}")
            if renamed:
                self.log_signal.emit(
                    f"Output name collision avoided: {Path(out_path).name}",
                )
            self.log_signal.emit(f"Output path: {out_path}")
            try:
                ok = processor.process(filepath, out_path)
            except Exception as e:
                self.log_signal.emit(f"Unexpected render failure: {e}")
                self.log_signal.emit(traceback.format_exc().rstrip())
                ok = False
            self.log_signal.emit("Result: success" if ok else "Result: failed")

            self.file_done.emit(idx, ok, out_path if ok else "")

        self.all_done.emit(_time.time() - t_start)

    def cancel(self):
        self._cancel_event.set()


# ============================================================
#  Preview Worker Thread
# ============================================================
class PreviewWorker(QThread):
    """Renders a short clip of a single file for audition purposes."""

    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    # done(success, output_path_or_empty, item_id)
    done = pyqtSignal(bool, str, int)

    def __init__(self, input_path, params, temp_dir, item_id, duration_sec=PREVIEW_DURATION_SEC):
        super().__init__()
        self.input_path = input_path
        self.params = params
        self.temp_dir = temp_dir
        self.item_id = item_id
        self.duration_sec = duration_sec
        self._cancel_event = threading.Event()

    def run(self):
        try:
            os.makedirs(self.temp_dir, exist_ok=True)
        except OSError as e:
            self.log_signal.emit(f"Preview: cannot create temp dir: {e}")
            self.done.emit(False, "", self.item_id)
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
        )
        self.log_signal.emit(f"Preview output path: {out_path}")
        try:
            ok = processor.process(self.input_path, out_path, preview_seconds=self.duration_sec)
        except Exception as e:
            self.log_signal.emit(f"Preview failed unexpectedly: {e}")
            self.log_signal.emit(traceback.format_exc().rstrip())
            ok = False
        self.done.emit(ok, out_path if ok else "", self.item_id)

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
    # preset_done(preset_name, ok, path)
    preset_done = pyqtSignal(str, bool, str)
    # all_done(results_dict)  mapping preset_name -> out_path (only successes)
    all_done = pyqtSignal(dict)

    def __init__(self, input_path, temp_dir, duration_sec=COMPARE_DURATION_SEC):
        super().__init__()
        self.input_path = input_path
        self.temp_dir = temp_dir
        self.duration_sec = duration_sec
        self._cancel_event = threading.Event()

    def run(self):
        results = {}
        try:
            os.makedirs(self.temp_dir, exist_ok=True)
        except OSError as e:
            self.log_signal.emit(f"Compare: cannot create temp dir: {e}")
            self.all_done.emit(results)
            return

        stem = Path(self.input_path).stem
        ts = datetime.now().strftime("%H%M%S%f")
        preset_names = list(PRESETS.keys())
        n_presets = len(preset_names)

        for i, name in enumerate(preset_names):
            if self._cancel_event.is_set():
                break
            self.log_signal.emit(f"Compare {i+1}/{n_presets}: {name}")

            # Map single-render progress (0-100) to overall (0-100)
            def sub_progress(v, _i=i, _n=n_presets):
                self.progress_signal.emit((_i * 100 + v) // _n)

            params = dict(PRESETS[name])
            params['output_format'] = 'wav'
            params['reencode_enabled'] = False

            out_path = os.path.join(
                self.temp_dir, f"{stem}_compare_{name}_{ts}.wav",
            )
            self.log_signal.emit(f"  Compare output path: {out_path}")

            proc = AudioProcessor(
                params,
                log_fn=lambda m: self.log_signal.emit(f"  {m}"),
                progress_fn=sub_progress,
                cancel_event=self._cancel_event,
            )
            try:
                ok = proc.process(
                    self.input_path, out_path, preview_seconds=self.duration_sec,
                )
            except Exception as e:
                self.log_signal.emit(f"  Compare {name} failed unexpectedly: {e}")
                self.log_signal.emit(traceback.format_exc().rstrip())
                ok = False
            if ok:
                results[name] = out_path
                self.preset_done.emit(name, True, out_path)
            else:
                self.preset_done.emit(name, False, "")

        self.progress_signal.emit(100)
        self.all_done.emit(results)

    def cancel(self):
        self._cancel_event.set()


# ============================================================
#  Custom Widgets
# ============================================================
class DropListWidget(QListWidget):
    """File list that accepts external file drops and allows internal reordering."""

    filesDropped = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

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


class ParamRow(QWidget):
    changed = pyqtSignal()

    def __init__(self, key, label, min_val, max_val, default, suffix='',
                 decimals=2, enabled_key='', display_factor=1.0):
        super().__init__()
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

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)

        self.check = QCheckBox()
        self.check.setChecked(True)
        self.check.setToolTip(f"Enable {label}")
        _set_accessibility(
            self.check,
            f"Enable {label}",
            f"Toggle the {label} processing pass.",
        )
        lay.addWidget(self.check)

        self._label = QLabel(label)
        self._label.setObjectName("paramName")
        self._label.setFixedWidth(180)
        lay.addWidget(self._label)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 200)
        _set_accessibility(
            self.slider,
            f"{label} amount",
            f"Adjust {label} from {min_val} to {max_val}{suffix}.",
        )
        lay.addWidget(self.slider, 1)

        self.val_label = QLabel()
        self.val_label.setObjectName("paramValue")
        self.val_label.setFixedWidth(72)
        self.val_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        lay.addWidget(self.val_label)

        self.slider.valueChanged.connect(self._update_label)
        self.check.stateChanged.connect(self._on_check_changed)
        self.set_value(default)

    def _on_check_changed(self, state):
        enabled = self.check.isChecked()
        self.slider.setEnabled(enabled)
        self.val_label.setEnabled(enabled)
        self.changed.emit()

    def _update_label(self):
        display_val = self.value() * self.display_factor
        self.val_label.setText(f"{display_val:.{self.decimals}f}{self.suffix}")
        self.changed.emit()

    def value(self):
        return self.min_val + (self.max_val - self.min_val) * self.slider.value() / 200.0

    def set_value(self, v):
        v = max(self.min_val, min(self.max_val, v))
        val_range = self.max_val - self.min_val
        if val_range < 1e-12:
            pos = 0
        else:
            pos = int((v - self.min_val) / val_range * 200)
        self.slider.setValue(pos)

    def is_enabled(self):
        return self.check.isChecked()

    def set_enabled_check(self, b):
        self.check.setChecked(b)


# ============================================================
#  Main Window
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        self.setMinimumSize(1060, 780)
        self.resize(1180, 880)
        self.worker = None
        self.preview_worker = None
        self.compare_worker = None
        self._preview_tempdir = None  # created lazily on first preview
        self._preview_item_id = None  # id() of the QListWidgetItem being previewed
        self._compare_results = {}  # preset_name -> path
        self._compare_for_item_id = None  # id() of item compare was rendered for
        self._playing_compare_preset = None  # name of preset currently playing from compare
        # Suppresses stale StoppedState signals during source transitions
        # (player.stop() + setSource() + play() fires Stopped then Playing;
        # the Stopped handler would otherwise wipe _playing_source state
        # that _toggle_play() just set).
        self._media_transitioning = False
        self._applying_preset = False
        self._last_browse_dir = str(Path.home())
        self._last_preset_dir = str(Path.home())
        self._current_run_log = None

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
        central.setObjectName("appRoot")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 18)
        root.setSpacing(14)

        root.addWidget(self._build_header())

        workspace = QHBoxLayout()
        workspace.setSpacing(14)

        left_col = QVBoxLayout()
        left_col.setSpacing(14)
        left_col.addWidget(self._build_files(), 5)
        left_col.addWidget(self._build_preview(), 0)
        left_col.addWidget(self._build_log(), 3)

        right_col = QVBoxLayout()
        right_col.setSpacing(14)
        right_col.addWidget(self._build_settings(), 7)
        right_col.addWidget(self._build_output(), 0)
        right_col.addWidget(self._build_controls(), 0)

        workspace.addLayout(left_col, 5)
        workspace.addLayout(right_col, 6)
        root.addLayout(workspace, 1)
        self._sync_header_stats()
        self._configure_accessibility()
        self._configure_tab_order()
        self._restore_session_state()

        if not self._session_restored_geometry:
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                x = (geo.width() - self.width()) // 2 + geo.x()
                y = (geo.height() - self.height()) // 2 + geo.y()
                self.move(x, y)

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
            self.file_list,
            "Audio queue",
            "Drop audio files, select queued files, and reorder the batch.",
        )
        _set_accessibility(self.btn_browse, "Browse audio files", "Add audio files to the queue.")
        _set_accessibility(self.btn_remove, "Remove selected files", "Remove selected files from the queue.")
        _set_accessibility(self.btn_clear, "Clear queue", "Remove every file from the queue.")
        _set_accessibility(self.btn_render_preview, "Render preview", "Render a short preview; disabled until a file is selected.")
        _set_accessibility(self.btn_compare, "Compare presets", "Render one short sample per preset; disabled until a file is selected.")
        _set_accessibility(self.btn_play_orig, "Play original", "Play the selected original file; disabled until audio is available.")
        _set_accessibility(self.btn_play_proc, "Play processed", "Play the selected processed file; disabled until output is available.")
        _set_accessibility(self.btn_open_log, "Open run log", "Open the latest persistent run log; disabled until a run starts.")
        _set_accessibility(self.btn_clear_logs, "Clear logs", "Delete all persistent run logs from the log directory.")
        _set_accessibility(self.preset_combo, "Preset", "Choose the processing preset.")
        _set_accessibility(self.btn_save_preset, "Save preset", "Save current settings to a JSON preset file.")
        _set_accessibility(self.btn_load_preset, "Load preset", "Load settings from a JSON preset file.")
        _set_accessibility(self.watermark_scan_check, "Watermark scan", "Toggle automatic watermark-band scanning before spectral perturbation.")
        _set_accessibility(self.meta_check, "Metadata strip", "Toggle metadata stripping on saved output files.")
        _set_accessibility(
            self.format_combo,
            "Output format",
            "Choose WAV, FLAC, OGG, or ffmpeg-backed MP3/M4A output.",
        )
        _set_accessibility(self.btn_open_output, "Open output folder", "Open the current output directory in the file manager.")
        _set_accessibility(self.output_dir, "Output directory", "Edit the output directory for processed files.")
        _set_accessibility(self.btn_browse_output, "Browse output directory", "Choose the output directory for processed files.")
        _set_accessibility(self.btn_process, "Process all", "Start processing every queued file.")
        _set_accessibility(self.btn_cancel, "Cancel processing", "Cancel the active render; disabled when no render is running.")
        _set_accessibility(self.progress, "Render progress", "Shows current render progress.")
        _set_accessibility(self.log_box, "Session log", "Shows processing events, warnings, metrics, and diagnostics.")

        for name, btn in self.compare_buttons.items():
            _set_accessibility(
                btn,
                f"Play {name} compare sample",
                f"Play the rendered {name} preset comparison sample; disabled until compare completes.",
            )
        _set_accessibility(
            self.btn_apply_compare,
            "Apply currently playing preset",
            "Apply the currently playing comparison preset to the main preset selector.",
        )

    def _configure_tab_order(self):
        order = [
            self.btn_browse,
            self.btn_remove,
            self.btn_clear,
            self.file_list,
            self.btn_render_preview,
            self.btn_compare,
            self.btn_play_orig,
            self.btn_play_proc,
            self.btn_open_log,
            self.preset_combo,
            self.btn_save_preset,
            self.btn_load_preset,
            self.watermark_scan_check,
            self.meta_check,
        ]
        for row in self.param_rows.values():
            order.extend([row.check, row.slider])
        order.extend([
            self.format_combo,
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
        settings = QSettings(APP_NAME, APP_NAME)
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
        preset = settings.value("session/preset")
        if preset and isinstance(preset, str):
            idx = self.preset_combo.findText(preset)
            if idx >= 0:
                self.preset_combo.setCurrentText(preset)
        browse = settings.value("session/last_browse_dir")
        if browse and isinstance(browse, str) and os.path.isdir(browse):
            self._last_browse_dir = browse
        preset_dir = settings.value("session/last_preset_dir")
        if preset_dir and isinstance(preset_dir, str) and os.path.isdir(preset_dir):
            self._last_preset_dir = preset_dir

    def _save_session_state(self):
        settings = QSettings(APP_NAME, APP_NAME)
        settings.setValue("window/geometry", self.saveGeometry())
        settings.setValue("session/output_dir", self.output_dir.text())
        settings.setValue("session/output_format", self.format_combo.currentText())
        settings.setValue("session/preset", self.preset_combo.currentText())
        settings.setValue("session/last_browse_dir", self._last_browse_dir)
        settings.setValue("session/last_preset_dir", self._last_preset_dir)

    def _build_header(self):
        bar = QFrame()
        bar.setObjectName("topBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(16)

        brand_col = QVBoxLayout()
        brand_col.setSpacing(1)
        title = QLabel(APP_NAME)
        title.setObjectName("appTitle")
        subtitle = QLabel("Audio fingerprint masking studio")
        subtitle.setObjectName("appSubtitle")
        brand_col.addWidget(title)
        brand_col.addWidget(subtitle)
        lay.addLayout(brand_col, 1)

        self.queue_status_label = QLabel("0 files")
        self.queue_status_label.setObjectName("statusPill")
        self.preset_status_label = QLabel("Extreme")
        self.preset_status_label.setObjectName("accentPill")
        self.format_status_label = QLabel("WAV")
        self.format_status_label.setObjectName("statusPill")
        self.render_status_label = QLabel("Ready")
        self.render_status_label.setObjectName("statusPill")

        for widget in (
            self.queue_status_label,
            self.preset_status_label,
            self.format_status_label,
            self.render_status_label,
        ):
            lay.addWidget(widget)

        version = QLabel(f"v{VERSION}")
        version.setObjectName("sectionSubtitle")
        lay.addWidget(version)
        return bar

    def _make_panel(self, title, subtitle=None):
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        outer = QVBoxLayout(panel)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(12)

        head = QVBoxLayout()
        head.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        head.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("sectionSubtitle")
            head.addWidget(subtitle_label)
        outer.addLayout(head)
        return panel, outer

    def _sync_header_stats(self):
        if not hasattr(self, 'queue_status_label'):
            return
        count = self.file_list.count() if hasattr(self, 'file_list') else 0
        self.queue_status_label.setText(f"{count} file{'s' if count != 1 else ''}")
        if hasattr(self, 'preset_combo'):
            self.preset_status_label.setText(self.preset_combo.currentText())
        if hasattr(self, 'format_combo'):
            self.format_status_label.setText(self.format_combo.currentText())

    def _set_render_state(self, text):
        if hasattr(self, 'render_status_label'):
            self.render_status_label.setText(text)

    # --- File section ---
    def _build_files(self):
        panel, lay = self._make_panel(
            "Queue",
            "Add audio files, reorder the batch, and track rendered outputs.",
        )

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_browse = self._decorate_button(
            QPushButton("Browse"),
            QStyle.StandardPixmap.SP_DialogOpenButton,
        )
        self.btn_browse.clicked.connect(self._on_browse)
        self.btn_remove = self._decorate_button(
            QPushButton("Remove"),
            QStyle.StandardPixmap.SP_DialogCancelButton,
        )
        self.btn_remove.clicked.connect(self._on_remove_selected)
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self._on_clear)

        self.file_count_label = QLabel("0 files")
        self.file_count_label.setObjectName("countLabel")

        btn_row.addWidget(self.btn_browse)
        btn_row.addWidget(self.btn_remove)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch()
        btn_row.addWidget(self.file_count_label)
        lay.addLayout(btn_row)

        self.file_list = DropListWidget()
        self.file_list.setMinimumHeight(220)
        self.file_list.filesDropped.connect(self._add_files)
        self.file_list.itemSelectionChanged.connect(self._update_preview_ui)
        lay.addWidget(self.file_list)

        hint = QLabel("Drop files here - drag to reorder - WAV, MP3, FLAC, OGG, AIFF, Opus")
        hint.setObjectName("hintLabel")
        lay.addWidget(hint)

        return panel

    # --- Settings section ---
    def _build_settings(self):
        panel, lay = self._make_panel(
            "Processing Pipeline",
            "Tune the transform profile before rendering the batch.",
        )

        # Preset row
        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        preset_row.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(PRESETS.keys()) + ['Custom'])
        self.preset_combo.setCurrentText('Extreme')
        self.preset_combo.currentTextChanged.connect(self._on_preset)
        preset_row.addWidget(self.preset_combo)

        self.btn_save_preset = self._decorate_button(
            QPushButton("Save"),
            QStyle.StandardPixmap.SP_DialogSaveButton,
        )
        self.btn_save_preset.setToolTip("Save current settings to a JSON file")
        self.btn_save_preset.clicked.connect(self._save_preset)
        preset_row.addWidget(self.btn_save_preset)

        self.btn_load_preset = self._decorate_button(
            QPushButton("Load"),
            QStyle.StandardPixmap.SP_DialogOpenButton,
        )
        self.btn_load_preset.setToolTip("Load settings from a JSON file")
        self.btn_load_preset.clicked.connect(self._load_preset)
        preset_row.addWidget(self.btn_load_preset)

        preset_row.addStretch()
        lay.addLayout(preset_row)

        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(16)
        self.watermark_scan_check = QCheckBox("Watermark Scan")
        self.watermark_scan_check.setChecked(True)
        self.watermark_scan_check.stateChanged.connect(lambda _: self._on_param_changed())
        toggle_row.addWidget(self.watermark_scan_check)

        self.meta_check = QCheckBox("Metadata Strip")
        self.meta_check.setChecked(True)
        self.meta_check.stateChanged.connect(lambda _: self._on_param_changed())
        toggle_row.addWidget(self.meta_check)
        toggle_row.addStretch()
        lay.addLayout(toggle_row)

        # Param rows
        self.param_rows = {}
        param_scroller = QScrollArea()
        param_scroller.setWidgetResizable(True)
        param_scroller.setMinimumHeight(320)
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
        format_row = QHBoxLayout()
        format_row.setSpacing(8)
        format_row.addWidget(QLabel("Format:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems([fmt.upper() for fmt in _available_output_formats()])
        if not _check_ffmpeg():
            self.format_combo.setToolTip("MP3/M4A export requires ffmpeg in PATH")
        else:
            missing = [f.upper() for f in FFMPEG_FORMAT_ENCODERS if not _ffmpeg_encoder_available(f)]
            if missing:
                self.format_combo.setToolTip(f"ffmpeg lacks encoders for: {', '.join(missing)}")
        self.format_combo.currentTextChanged.connect(lambda _: self._sync_header_stats())
        self.format_combo.setFixedWidth(140)
        format_row.addWidget(self.format_combo)
        format_row.addStretch()
        self.btn_open_output = self._decorate_button(
            QPushButton("Open"),
            QStyle.StandardPixmap.SP_DirOpenIcon,
        )
        self.btn_open_output.setToolTip("Open output directory in file manager")
        self.btn_open_output.clicked.connect(self._open_output)
        format_row.addWidget(self.btn_open_output)
        lay.addLayout(format_row)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        dir_row.addWidget(QLabel("Directory:"))
        self.output_dir = QLineEdit(DEFAULT_OUTPUT)
        self.output_dir.setMinimumWidth(320)
        dir_row.addWidget(self.output_dir, 1)
        self.btn_browse_output = self._decorate_button(
            QPushButton(""),
            QStyle.StandardPixmap.SP_DirOpenIcon,
            "iconButton",
        )
        self.btn_browse_output.setFixedWidth(36)
        self.btn_browse_output.clicked.connect(self._browse_output)
        dir_row.addWidget(self.btn_browse_output)
        lay.addLayout(dir_row)

        return panel

    # --- Controls ---
    def _build_controls(self):
        panel, lay = self._make_panel(
            "Render",
            "Start the full batch or stop an active render.",
        )
        row = QHBoxLayout()
        row.setSpacing(10)

        self.btn_process = self._decorate_button(
            QPushButton("Process All"),
            QStyle.StandardPixmap.SP_MediaPlay,
            "processBtn",
        )
        self.btn_process.clicked.connect(self._on_process)
        row.addWidget(self.btn_process)

        self.btn_cancel = self._decorate_button(
            QPushButton("Cancel"),
            QStyle.StandardPixmap.SP_MediaStop,
            "cancelBtn",
        )
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        row.addWidget(self.btn_cancel)

        row.addSpacing(8)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        row.addWidget(self.progress, 1)
        lay.addLayout(row)

        return panel

    # --- Preview ---
    def _build_preview(self):
        panel, outer = self._make_panel(
            "Monitor",
            "Render short previews, compare presets, and audition results.",
        )

        # Row 1: render + playback controls
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.btn_render_preview = self._decorate_button(
            QPushButton("Preview"),
            QStyle.StandardPixmap.SP_BrowserReload,
        )
        self.btn_render_preview.setToolTip(
            f"Process the first {int(PREVIEW_DURATION_SEC)} seconds of the selected file "
            "with current settings so you can hear the result before committing."
        )
        self.btn_render_preview.clicked.connect(self._on_render_preview)
        self.btn_render_preview.setEnabled(False)
        row1.addWidget(self.btn_render_preview)

        self.btn_compare = self._decorate_button(
            QPushButton("Compare"),
            QStyle.StandardPixmap.SP_MediaPlay,
        )
        self.btn_compare.setToolTip(
            f"Render a {int(COMPARE_DURATION_SEC)}s sample with each built-in preset "
            "so you can A/B/C/D audition them, then apply your favorite."
        )
        self.btn_compare.clicked.connect(self._on_compare_presets)
        self.btn_compare.setEnabled(False)
        row1.addWidget(self.btn_compare)

        self.btn_play_orig = self._decorate_button(
            QPushButton("Original"),
            QStyle.StandardPixmap.SP_MediaPlay,
        )
        self.btn_play_orig.clicked.connect(lambda: self._toggle_play('original'))
        self.btn_play_orig.setEnabled(False)
        row1.addWidget(self.btn_play_orig)

        self.btn_play_proc = self._decorate_button(
            QPushButton("Processed"),
            QStyle.StandardPixmap.SP_MediaPlay,
        )
        self.btn_play_proc.clicked.connect(lambda: self._toggle_play('processed'))
        self.btn_play_proc.setEnabled(False)
        row1.addWidget(self.btn_play_proc)

        row1.addSpacing(12)
        self.preview_label = QLabel("Select a file")
        self.preview_label.setObjectName("nowPlaying")
        row1.addWidget(self.preview_label, 1)
        outer.addLayout(row1)

        # Row 2: compare panel (hidden until Compare Presets is rendered)
        self.compare_panel = QWidget()
        self.compare_panel.setObjectName("comparePanel")
        compare_lay = QHBoxLayout(self.compare_panel)
        compare_lay.setContentsMargins(0, 2, 0, 0)
        compare_lay.setSpacing(8)
        compare_lay.addWidget(QLabel("A/B:"))
        self.compare_buttons = {}
        for name in PRESETS.keys():
            btn = QPushButton(name)
            btn.setObjectName("compareButton")
            btn.setToolTip(f"Play the {name} sample")
            btn.setEnabled(False)
            btn.clicked.connect(lambda _checked=False, n=name: self._play_compare(n))
            self.compare_buttons[name] = btn
            compare_lay.addWidget(btn)
        compare_lay.addSpacing(12)
        self.btn_apply_compare = QPushButton("Apply Currently Playing")
        self.btn_apply_compare.setToolTip(
            "Set the currently playing preset as the active preset for Process All"
        )
        self.btn_apply_compare.setEnabled(False)
        self.btn_apply_compare.clicked.connect(self._apply_playing_compare_preset)
        compare_lay.addWidget(self.btn_apply_compare)
        compare_lay.addStretch()
        self.compare_panel.setVisible(False)
        outer.addWidget(self.compare_panel)

        if not _MULTIMEDIA_OK:
            for b in (self.btn_play_orig, self.btn_play_proc,
                      self.btn_render_preview, self.btn_compare):
                b.setEnabled(False)
            self.btn_render_preview.setToolTip("Requires PyQt6 QtMultimedia module")
            self.btn_compare.setToolTip("Requires PyQt6 QtMultimedia module")
            self.preview_label.setText("(PyQt6 Multimedia not available)")

        return panel

    # --- Log ---
    def _build_log(self):
        panel, lay = self._make_panel(
            "Session Log",
            "Processing events, warnings, and strength metrics.",
        )
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(170)
        lay.addWidget(self.log_box)
        actions = QHBoxLayout()
        actions.addStretch()
        self.btn_open_log = self._decorate_button(
            QPushButton("Open Log"),
            QStyle.StandardPixmap.SP_FileIcon,
        )
        self.btn_open_log.setToolTip("Open the latest persistent run log")
        self.btn_open_log.setEnabled(False)
        self.btn_open_log.clicked.connect(self._open_run_log)
        actions.addWidget(self.btn_open_log)

        self.btn_clear_logs = self._decorate_button(
            QPushButton("Clear Logs"),
            QStyle.StandardPixmap.SP_TrashIcon,
        )
        self.btn_clear_logs.setToolTip("Delete all persistent run logs")
        self.btn_clear_logs.clicked.connect(self._clear_all_logs)
        actions.addWidget(self.btn_clear_logs)

        lay.addLayout(actions)
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

    def _on_remove_selected(self):
        self._stop_playback()
        for item in reversed(self.file_list.selectedItems()):
            self.file_list.takeItem(self.file_list.row(item))
        self._update_file_count()
        self._update_preview_ui()

    def _add_files(self, paths):
        existing = set()
        for i in range(self.file_list.count()):
            raw = self.file_list.item(i).data(ROLE_INPUT)
            if raw:
                existing.add(os.path.normcase(os.path.abspath(raw)))

        added = 0
        for p in paths:
            p_path = Path(p)
            if p_path.is_dir():
                for f in sorted(p_path.rglob('*')):
                    norm = os.path.normcase(os.path.abspath(str(f)))
                    if f.suffix.lower() in SUPPORTED_FORMATS and norm not in existing:
                        self._append_item(str(f))
                        existing.add(norm)
                        added += 1
            elif p_path.is_file():
                if p_path.suffix.lower() not in SUPPORTED_FORMATS:
                    self._log(f"Unsupported format: {p_path.name}")
                    continue
                norm = os.path.normcase(os.path.abspath(str(p_path)))
                if norm in existing:
                    continue
                self._append_item(str(p_path))
                existing.add(norm)
                added += 1
        self._update_file_count()
        self._update_preview_ui()

    def _append_item(self, path):
        item = QListWidgetItem(f"READY    {Path(path).name}")
        item.setToolTip(path)
        item.setData(ROLE_INPUT, path)
        item.setData(ROLE_OUTPUT, None)
        self.file_list.addItem(item)

    def _update_file_count(self):
        n = self.file_list.count()
        self.file_count_label.setText(f"{n} file{'s' if n != 1 else ''}")
        self._sync_header_stats()

    # --- Preset slots ---
    def _on_preset(self, name):
        if name in PRESETS:
            self._apply_preset(name)
        self._sync_header_stats()

    def _apply_preset(self, name):
        self._applying_preset = True
        try:
            p = PRESETS[name]
            self.meta_check.setChecked(p.get('strip_metadata', True))
            self.watermark_scan_check.setChecked(p.get('watermark_scan_enabled', True))
            for key, row in self.param_rows.items():
                if key in p:
                    row.set_value(p[key])
                if row.enabled_key in p:
                    row.set_enabled_check(p[row.enabled_key])
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
            # Omit output_format - that's a per-session setting, not a preset
            preset_data = {
                'name': 'Custom',
                'version': VERSION,
                'schema_version': PRESET_SCHEMA_VERSION,
                'params': {k: v for k, v in params.items() if k != 'output_format'},
            }
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
            if not isinstance(data, dict):
                raise ValueError("Invalid preset file format")
            data = _migrate_preset(data)
            params = data.get('params', data)
            if not isinstance(params, dict):
                raise ValueError("Missing params block")

            self._applying_preset = True
            try:
                if 'strip_metadata' in params:
                    self.meta_check.setChecked(bool(params['strip_metadata']))
                if 'watermark_scan_enabled' in params:
                    self.watermark_scan_check.setChecked(bool(params['watermark_scan_enabled']))
                for key, row in self.param_rows.items():
                    if key in params:
                        try:
                            row.set_value(float(params[key]))
                        except (TypeError, ValueError):
                            pass
                    if row.enabled_key in params:
                        row.set_enabled_check(bool(params[row.enabled_key]))
            finally:
                self._applying_preset = False

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
            'watermark_scan_enabled': self.watermark_scan_check.isChecked(),
            'output_format': self.format_combo.currentText().lower(),
        }
        for key, row in self.param_rows.items():
            params[row.enabled_key] = row.is_enabled()
            params[key] = row.value()
        return params

    # --- Processing control ---
    def _set_processing_ui(self, processing):
        self.btn_process.setEnabled(not processing)
        self.btn_cancel.setEnabled(processing)
        self._set_general_controls(not processing)
        self._set_render_state("Processing" if processing else "Ready")
        # Preview + compare mutually exclusive with batch processing
        has_selection = self._current_selected_item() is not None
        if _MULTIMEDIA_OK:
            self.btn_render_preview.setEnabled((not processing) and has_selection)
            self.btn_compare.setEnabled((not processing) and has_selection)
        # Lock reordering during processing to preserve index mapping
        self.file_list.setDragEnabled(not processing)
        if processing:
            self.progress.setValue(0)

    def _set_preview_running_ui(self, running):
        if _MULTIMEDIA_OK:
            self.btn_render_preview.setEnabled(not running)
            self.btn_render_preview.setText("Rendering..." if running else "Preview")
            self.btn_compare.setEnabled(not running)
        self._set_render_state("Previewing" if running else "Ready")
        self.btn_process.setEnabled(not running)
        self._set_general_controls(not running)
        self.file_list.setDragEnabled(not running)

    def _set_compare_running_ui(self, running):
        if _MULTIMEDIA_OK:
            self.btn_compare.setEnabled(not running)
            self.btn_compare.setText("Comparing..." if running else "Compare")
            self.btn_render_preview.setEnabled(not running)
            # Individual compare buttons disabled during re-render
            if running:
                for b in self.compare_buttons.values():
                    b.setEnabled(False)
                self.btn_apply_compare.setEnabled(False)
        self._set_render_state("Comparing" if running else "Ready")
        self.btn_process.setEnabled(not running)
        self._set_general_controls(not running)
        self.file_list.setDragEnabled(not running)

    def _set_general_controls(self, enabled):
        self.btn_browse.setEnabled(enabled)
        self.btn_remove.setEnabled(enabled)
        self.btn_clear.setEnabled(enabled)
        self.btn_save_preset.setEnabled(enabled)
        self.btn_load_preset.setEnabled(enabled)
        self.watermark_scan_check.setEnabled(enabled)
        self.meta_check.setEnabled(enabled)

    def _on_process(self):
        if self.file_list.count() == 0:
            self._set_render_state("Add files")
            self._log("No files to process.")
            return

        self._stop_playback()

        files = []
        for i in range(self.file_list.count()):
            files.append(self.file_list.item(i).data(ROLE_INPUT))
            # Clear any previous processed-path marker
            self.file_list.item(i).setData(ROLE_OUTPUT, None)

        out_dir = self.output_dir.text().strip() or DEFAULT_OUTPUT
        params = self._get_params()

        self._set_processing_ui(True)
        self.log_box.clear()
        self._start_run_log(
            "gui-batch", files, out_dir, params, self.preset_combo.currentText(),
        )
        self._log(f"Starting -- {len(files)} file(s), preset: {self.preset_combo.currentText()}")
        self._log(f"Output: {out_dir}\n")

        self.worker = ProcessWorker(files, params, out_dir)
        self.worker.log_signal.connect(self._log)
        self.worker.progress_signal.connect(self.progress.setValue)
        self.worker.file_started.connect(self._on_file_started)
        self.worker.file_done.connect(self._on_file_done)
        self.worker.all_done.connect(self._on_all_done)
        self.worker.start()

    def _on_cancel(self):
        if self.worker:
            self._set_render_state("Cancelling")
            self.worker.cancel()
            self._log("\nCancelling...")

    def _on_file_started(self, idx):
        if 0 <= idx < self.file_list.count():
            item = self.file_list.item(idx)
            name = Path(item.data(ROLE_INPUT)).name
            item.setText(f"RUNNING  {name}")

    def _on_file_done(self, idx, ok, out_path):
        if 0 <= idx < self.file_list.count():
            item = self.file_list.item(idx)
            name = Path(item.data(ROLE_INPUT)).name
            if ok:
                item.setText(f"DONE     {name} -> {Path(out_path).name}")
                item.setData(ROLE_OUTPUT, out_path)
            else:
                item.setText(f"FAILED   {name}")
                item.setData(ROLE_OUTPUT, None)
        self._update_preview_ui()

    def _on_all_done(self, total_seconds=0.0):
        self._set_processing_ui(False)
        self.progress.setValue(100)
        self._set_render_state("Complete")
        if total_seconds > 0.01:
            mins, secs = divmod(total_seconds, 60)
            timing = f" ({int(mins)}m {secs:.1f}s)" if mins else f" ({total_seconds:.1f}s)"
        else:
            timing = ""
        self._log(f"\nAll done.{timing}")

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.append(f"[{ts}] {msg}")
        if self._current_run_log is not None:
            self._current_run_log.write(msg)
        sb = self.log_box.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _start_run_log(self, mode, files, out_dir, params, preset_name):
        try:
            self._current_run_log = RunDiagnostics(mode)
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

    def _clear_all_logs(self):
        log_dir = _diagnostics_dir()
        if not log_dir.is_dir():
            self._log("No log directory found.")
            return
        logs = list(log_dir.glob('*.log'))
        count = 0
        for log_file in logs:
            try:
                log_file.unlink()
                count += 1
            except OSError:
                pass
        self._log(f"Cleared {count} log file(s).")

    # --- Preview / playback ---
    def _current_selected_item(self):
        items = self.file_list.selectedItems()
        if items:
            return items[0]
        if self.file_list.count() > 0:
            return self.file_list.item(0)
        return None

    def _find_item_by_id(self, item_id):
        """Find a QListWidgetItem by id() - handles list mutation during preview."""
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if id(item) == item_id:
                return item
        return None

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

        # Clean up previous preview file for this item so temp dir doesn't grow
        prev = item.data(ROLE_OUTPUT)
        if self._is_preview_output(prev) and os.path.isfile(prev):
            try:
                os.unlink(prev)
            except OSError:
                pass
            item.setData(ROLE_OUTPUT, None)

        params = self._get_params()
        self._start_run_log(
            "gui-preview", [input_path], self._preview_tempdir, params,
            self.preset_combo.currentText(),
        )
        self._preview_item_id = id(item)
        self._set_preview_running_ui(True)
        self._log(
            f"Rendering {int(PREVIEW_DURATION_SEC)}s preview of "
            f"{Path(input_path).name} with current settings..."
        )

        self.preview_worker = PreviewWorker(
            input_path, params, self._preview_tempdir, self._preview_item_id,
        )
        self.preview_worker.log_signal.connect(self._log)
        self.preview_worker.progress_signal.connect(self.progress.setValue)
        self.preview_worker.done.connect(self._on_preview_done)
        self.preview_worker.start()

    def _on_preview_done(self, ok, out_path, item_id):
        self._set_preview_running_ui(False)

        if not ok:
            self._log("Preview render failed.")
            self._update_preview_ui()
            return

        # Re-locate the item by id() in case the list was modified mid-render
        item = self._find_item_by_id(item_id)
        if item is None:
            self._log("Preview ready but original list item was removed.")
            # Orphan the temp file; it will be cleaned on close
            self._update_preview_ui()
            return

        item.setData(ROLE_OUTPUT, out_path)
        self._update_preview_ui()
        self._log(f"Preview ready: {Path(out_path).name}")

        # Auto-play so the user immediately hears the result
        if self.file_list.currentItem() is not item:
            self.file_list.setCurrentItem(item)
        self._toggle_play('processed')

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
        self._compare_for_item_id = id(item)
        self.compare_panel.setVisible(True)
        for name, btn in self.compare_buttons.items():
            btn.setEnabled(False)
            btn.setText(f"{name} ...")
        self.btn_apply_compare.setEnabled(False)
        self.btn_apply_compare.setText("Apply Currently Playing")

        self._set_compare_running_ui(True)
        self._start_run_log(
            "gui-compare", [input_path], self._preview_tempdir,
            {'presets': list(PRESETS.keys()), 'duration_sec': COMPARE_DURATION_SEC},
            "Compare Presets",
        )
        self._log(
            f"Rendering {int(COMPARE_DURATION_SEC)}s sample per preset "
            f"({len(PRESETS)} presets)..."
        )

        self.compare_worker = PresetCompareWorker(input_path, self._preview_tempdir)
        self.compare_worker.log_signal.connect(self._log)
        self.compare_worker.progress_signal.connect(self.progress.setValue)
        self.compare_worker.preset_done.connect(self._on_compare_preset_done)
        self.compare_worker.all_done.connect(self._on_compare_all_done)
        self.compare_worker.start()

    def _on_compare_preset_done(self, name, ok, out_path):
        btn = self.compare_buttons.get(name)
        if btn is None:
            return
        if ok:
            self._compare_results[name] = out_path
            btn.setText(f"Play {name}")
            btn.setEnabled(True)
        else:
            btn.setText(f"{name} (failed)")
            btn.setEnabled(False)

    def _on_compare_all_done(self, results):
        self._set_compare_running_ui(False)
        n_ok = sum(1 for b in self.compare_buttons.values() if b.isEnabled())
        self._log(f"Compare complete: {n_ok}/{len(PRESETS)} presets rendered.")
        self._update_preview_ui()

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
        self.btn_apply_compare.setText(f"Apply {preset_name}")

    def _update_compare_buttons(self):
        for name, btn in self.compare_buttons.items():
            if name not in self._compare_results:
                continue
            if self._playing_compare_preset == name:
                btn.setText(f"Stop {name}")
            else:
                btn.setText(f"Play {name}")

    def _apply_playing_compare_preset(self):
        if not self._playing_compare_preset:
            return
        name = self._playing_compare_preset
        self.player.stop()
        self.preset_combo.setCurrentText(name)  # triggers _on_preset -> _apply_preset
        self._log(f"Applied preset: {name}")

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
                self.btn_apply_compare.setText("Apply Currently Playing")
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
            if item is None or id(item) != self._compare_for_item_id:
                if self._playing_compare_preset is not None and self.player is not None:
                    self.player.stop()
                self._playing_compare_preset = None
                self._compare_results = {}
                self._compare_for_item_id = None
                self.compare_panel.setVisible(False)

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
            self.btn_play_orig.setText("Original")
            self.btn_play_proc.setText("Processed")
            self.btn_play_orig.setEnabled(False)
            self.btn_play_proc.setEnabled(False)
            self.preview_label.setText("Select a file")
            return

        orig_path = item.data(ROLE_INPUT)
        proc_path = item.data(ROLE_OUTPUT)
        orig_ok = bool(orig_path) and os.path.isfile(orig_path)
        proc_ok = bool(proc_path) and os.path.isfile(proc_path)
        is_preview = self._is_preview_output(proc_path)

        display_name = Path(orig_path).name if orig_path else ""
        if is_preview and proc_ok:
            display_name = f"{display_name}  (preview: {int(PREVIEW_DURATION_SEC)}s)"
        self.preview_label.setText(display_name)

        processed_label = "Preview" if is_preview else "Processed"

        if self._playing_source == 'original':
            self.btn_play_orig.setText("Stop")
            self.btn_play_orig.setEnabled(True)
            self.btn_play_proc.setText(processed_label)
            self.btn_play_proc.setEnabled(False)
        elif self._playing_source == 'processed':
            self.btn_play_orig.setText("Original")
            self.btn_play_orig.setEnabled(False)
            self.btn_play_proc.setText("Stop")
            self.btn_play_proc.setEnabled(True)
        else:
            self.btn_play_orig.setText("Original")
            self.btn_play_proc.setText(processed_label)
            self.btn_play_orig.setEnabled(orig_ok)
            self.btn_play_proc.setEnabled(proc_ok)

    def closeEvent(self, event):
        # Disconnect playback state handler before stopping; otherwise the
        # Stopped signal queued by stop() can fire after the window is being
        # destroyed, hitting deallocated widgets.
        if self.player is not None:
            try:
                self.player.playbackStateChanged.disconnect(self._on_playback_state_changed)
                self.player.errorOccurred.disconnect(self._on_player_error)
            except (TypeError, RuntimeError):
                pass
            self.player.stop()
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        if self.preview_worker and self.preview_worker.isRunning():
            self.preview_worker.cancel()
            self.preview_worker.wait(3000)
        if self.compare_worker and self.compare_worker.isRunning():
            self.compare_worker.cancel()
            self.compare_worker.wait(3000)
        # Clean up preview temp directory
        if self._preview_tempdir and os.path.isdir(self._preview_tempdir):
            shutil.rmtree(self._preview_tempdir, ignore_errors=True)
        self._save_session_state()
        event.accept()


# ============================================================
#  CLI Mode
# ============================================================
def _clamp(value, lo, hi, name):
    """Clamp CLI argument to valid range, warn if out of bounds."""
    if value < lo or value > hi:
        print(f"Warning: --{name} {value} out of range [{lo}, {hi}], clamping.")
        return max(lo, min(hi, value))
    return value


def cli_main():
    parser = argparse.ArgumentParser(
        description=f'{APP_NAME} v{VERSION} -- Audio fingerprint masking tool',
    )
    parser.add_argument('-i', '--input', required=True,
                        help='Input audio file or directory')
    parser.add_argument('-o', '--output', default=None, help='Output directory')
    parser.add_argument('-p', '--preset', default='moderate',
                        choices=['gentle', 'moderate', 'aggressive', 'extreme'])
    parser.add_argument('-f', '--format', default='wav',
                        choices=list(OUTPUT_EXTENSIONS.keys()),
                        dest='out_format')
    parser.add_argument('--preset-file', default=None,
                        help='Path to JSON preset file (overrides -p/--preset)')
    parser.add_argument('--no-watermark-scan', action='store_true',
                        help='Disable automatic watermark-band scan pre-pass')
    parser.add_argument('--spectral', type=float, help='Spectral perturbation (0.0-1.0)')
    parser.add_argument('--spectral-sub-bass', type=float,
                        help='Sub-bass spectral perturbation (0.0-1.0)')
    parser.add_argument('--spectral-low-mids', type=float,
                        help='Low-mids spectral perturbation (0.0-1.0)')
    parser.add_argument('--spectral-presence', type=float,
                        help='Presence-band spectral perturbation (0.0-1.0)')
    parser.add_argument('--spectral-air', type=float,
                        help='Air-band spectral perturbation (0.0-1.0)')
    parser.add_argument('--dynamic-eq', type=float, help='Dynamic EQ amount (0.0-1.0)')
    parser.add_argument('--pitch', type=float, help='Pitch micro-shift in semitones (0.0-5.0)')
    parser.add_argument('--tempo', type=float, help='Tempo variation (0.0-0.15)')
    parser.add_argument('--phase', type=float, help='Phase scrambling (0.0-1.0)')
    parser.add_argument('--stereo', type=float, help='Stereo manipulation (0.0-0.5)')
    parser.add_argument('--noise', type=float, help='Noise level in dB (-70 to -30)')
    parser.add_argument('--dynamics', type=float, help='Dynamics amount (0.0-1.0)')
    parser.add_argument('--humanize', type=float, help='Humanization amount (0.0-1.0)')
    parser.add_argument('--reencode', type=int, help='Lossy re-encode bitrate (96-320)')
    parser.add_argument('--seed', type=int, default=None,
                        help='Random seed for reproducible output (same seed = same bytes)')
    args = parser.parse_args()

    # Start with built-in preset
    preset_name = args.preset.capitalize()
    params = dict(PRESETS.get(preset_name, PRESETS['Moderate']))
    params['output_format'] = args.out_format

    if _format_requires_ffmpeg(args.out_format):
        if not _check_ffmpeg():
            print(
                f"Error: {args.out_format.upper()} export requires ffmpeg in PATH. "
                "Use WAV/FLAC/OGG or install ffmpeg.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not _ffmpeg_encoder_available(args.out_format):
            encoder = FFMPEG_FORMAT_ENCODERS.get(args.out_format, args.out_format)
            print(
                f"Error: ffmpeg lacks {encoder} encoder for {args.out_format.upper()} export. "
                f"Install ffmpeg with {encoder} support or use WAV/FLAC/OGG.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Optional JSON preset file override
    if args.preset_file:
        try:
            with open(args.preset_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = _migrate_preset(data)
            loaded = data.get('params', data) if isinstance(data, dict) else {}
            if not isinstance(loaded, dict):
                raise ValueError("preset file missing params block")
            # Only accept known keys to avoid poisoning
            known = (
                {'strip_metadata', 'watermark_scan_enabled'}
                | {d[0] for d in PARAM_DEFS}
                | {d[7] for d in PARAM_DEFS}
            )
            for k, v in loaded.items():
                if k in known:
                    params[k] = v
            print(f"Loaded preset from {args.preset_file}")
            preset_name = data.get('name', 'Custom') if isinstance(data, dict) else 'Custom'
        except Exception as e:
            print(f"Warning: could not load preset file: {e}")

    # Override with CLI args (validated)
    if args.no_watermark_scan:
        params['watermark_scan_enabled'] = False
    if args.spectral is not None:
        params['spectral_strength'] = _clamp(args.spectral, 0.0, 1.0, 'spectral')
    if args.spectral_sub_bass is not None:
        params['spectral_sub_bass_strength'] = _clamp(
            args.spectral_sub_bass, 0.0, 1.0, 'spectral-sub-bass',
        )
        params['spectral_sub_bass_enabled'] = True
    if args.spectral_low_mids is not None:
        params['spectral_low_mids_strength'] = _clamp(
            args.spectral_low_mids, 0.0, 1.0, 'spectral-low-mids',
        )
        params['spectral_low_mids_enabled'] = True
    if args.spectral_presence is not None:
        params['spectral_presence_strength'] = _clamp(
            args.spectral_presence, 0.0, 1.0, 'spectral-presence',
        )
        params['spectral_presence_enabled'] = True
    if args.spectral_air is not None:
        params['spectral_air_strength'] = _clamp(args.spectral_air, 0.0, 1.0, 'spectral-air')
        params['spectral_air_enabled'] = True
    if args.dynamic_eq is not None:
        params['dynamic_eq_amount'] = _clamp(args.dynamic_eq, 0.0, 1.0, 'dynamic-eq')
        params['dynamic_eq_enabled'] = True
    if args.pitch is not None:
        params['pitch_range'] = _clamp(args.pitch, 0.0, 5.0, 'pitch')
    if args.tempo is not None:
        params['tempo_range'] = _clamp(args.tempo, 0.0, 0.15, 'tempo')
    if args.phase is not None:
        params['phase_amount'] = _clamp(args.phase, 0.0, 1.0, 'phase')
    if args.stereo is not None:
        params['stereo_shift'] = _clamp(args.stereo, 0.0, 0.5, 'stereo')
    if args.noise is not None:
        params['noise_level'] = _clamp(args.noise, -70.0, -30.0, 'noise')
    if args.dynamics is not None:
        params['dynamics_amount'] = _clamp(args.dynamics, 0.0, 1.0, 'dynamics')
    if args.humanize is not None:
        params['humanize_amount'] = _clamp(args.humanize, 0.0, 1.0, 'humanize')
    if args.reencode is not None:
        params['reencode_bitrate'] = _clamp(args.reencode, 96, 320, 'reencode')
        params['reencode_enabled'] = True

    # Collect input files
    input_path = Path(args.input)
    files = []
    if input_path.is_dir():
        for f in sorted(input_path.rglob('*')):
            if f.suffix.lower() in SUPPORTED_FORMATS:
                files.append(str(f))
    elif input_path.is_file():
        files.append(str(input_path))
    else:
        print(f"Error: {args.input} not found")
        sys.exit(1)

    if not files:
        print("No supported audio files found.")
        sys.exit(1)

    out_dir = args.output or DEFAULT_OUTPUT
    os.makedirs(out_dir, exist_ok=True)

    ext = _output_extension(args.out_format)

    run_log = RunDiagnostics('cli')

    def cli_log(msg):
        print(msg)
        run_log.write(msg)

    print(f"{APP_NAME} v{VERSION}")
    print(f"Preset: {preset_name} | Format: {args.out_format.upper()} | Files: {len(files)}")
    print(f"Run log: {run_log.path}\n")
    run_log.write_header('cli', files, out_dir, params, preset_name, args.seed)

    fail_count = 0
    used_outputs = set()
    for filepath in files:
        out_path, renamed = _planned_output_path(filepath, out_dir, ext, used_outputs)
        if renamed:
            cli_log(f"Output name collision avoided: {Path(out_path).name}")
        cli_log(f"Output path: {out_path}")

        proc = AudioProcessor(params, log_fn=cli_log, progress_fn=lambda v: None,
                              seed=args.seed)
        try:
            ok = proc.process(filepath, out_path)
        except Exception as e:
            cli_log(f"Unexpected render failure: {e}")
            cli_log(traceback.format_exc().rstrip())
            ok = False
        if not ok:
            fail_count += 1
        cli_log("Result: success" if ok else "Result: failed")
        cli_log("---")

    cli_log(f"\nDone. Output: {out_dir}")
    if fail_count:
        cli_log(f"{fail_count} file(s) failed.")
        sys.exit(2)


# ============================================================
#  Entry Point
# ============================================================
if __name__ == '__main__':
    _cli_flags = {'-i', '--input', '-h', '--help', '--version'}
    if len(sys.argv) > 1 and any(a in _cli_flags for a in sys.argv[1:]):
        if '--version' in sys.argv:
            print(f"{APP_NAME} v{VERSION}")
            sys.exit(0)
        cli_main()
    else:
        app = QApplication(sys.argv)
        app.setStyle('Fusion')
        app.setStyleSheet(STYLE)
        win = MainWindow()
        win.show()
        sys.exit(app.exec())
