#!/usr/bin/env python3
"""SunoJump v1.2.0 - Audio fingerprint masking tool for Suno AI"""

VERSION = "1.2.0"
APP_NAME = "SunoJump"

# --- Bootstrap ---
import subprocess, sys

def _bootstrap():
    deps = {
        'PyQt6': 'PyQt6',
        'numpy': 'numpy',
        'scipy': 'scipy',
        'soundfile': 'soundfile',
        'mutagen': 'mutagen',
    }
    missing = []
    for module, package in deps.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if not missing:
        return
    installed = False
    for attempt in [
        [sys.executable, '-m', 'pip', 'install'] + missing,
        [sys.executable, '-m', 'pip', 'install', '--user'] + missing,
        [sys.executable, '-m', 'pip', 'install', '--break-system-packages'] + missing,
    ]:
        try:
            subprocess.check_call(attempt, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            installed = True
            break
        except subprocess.CalledProcessError:
            continue
    if not installed:
        print(f"ERROR: Failed to install required packages: {', '.join(missing)}", file=sys.stderr)
        print("Install manually:  pip install " + " ".join(missing), file=sys.stderr)
        sys.exit(1)

_bootstrap()

# --- Imports ---
import os, json, argparse, tempfile, shutil, threading
from pathlib import Path
from datetime import datetime

import numpy as np
import soundfile as sf
from scipy import signal
import mutagen
from mutagen import File as MutagenFile

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QPushButton, QLabel, QListWidget, QListWidgetItem,
    QComboBox, QLineEdit, QCheckBox, QSlider, QProgressBar,
    QTextEdit, QFileDialog, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QDesktopServices

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
    'base': '#1e1e2e', 'mantle': '#181825', 'crust': '#11111b',
    'surface0': '#313244', 'surface1': '#45475a', 'surface2': '#585b70',
    'text': '#cdd6f4', 'subtext': '#a6adc8', 'overlay': '#6c7086',
    'blue': '#89b4fa', 'green': '#a6e3a1', 'red': '#f38ba8',
    'yellow': '#f9e2af', 'mauve': '#cba6f7', 'peach': '#fab387',
    'teal': '#94e2d5', 'lavender': '#b4befe',
}

SUPPORTED_FORMATS = {'.wav', '.mp3', '.flac', '.ogg', '.aiff', '.aif', '.opus'}

# UserRole keys on QListWidgetItem
ROLE_INPUT = Qt.ItemDataRole.UserRole
ROLE_OUTPUT = Qt.ItemDataRole.UserRole + 1

PRESETS = {
    'Gentle': {
        'strip_metadata': True,
        'spectral_enabled': True, 'spectral_strength': 0.10,
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
        'spectral_enabled': True, 'spectral_strength': 0.30,
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
        'spectral_enabled': True, 'spectral_strength': 0.50,
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
        'spectral_enabled': True, 'spectral_strength': 0.70,
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

PARAM_DEFS = [
    # (key, label, min, max, default, suffix, decimals, enabled_key, display_factor)
    ('spectral_strength', 'Spectral Perturbation', 0.0, 1.0, 0.30, '', 2, 'spectral_enabled', 1.0),
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

# --- Stylesheet ---
STYLE = f"""
QMainWindow, QWidget {{
    background-color: {C['base']};
    color: {C['text']};
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
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
    border-radius: 6px;
    padding: 6px 16px;
    color: {C['text']};
    font-weight: bold;
}}
QPushButton:hover {{
    background-color: {C['surface1']};
    border-color: {C['blue']};
}}
QPushButton:pressed {{
    background-color: {C['surface2']};
}}
QPushButton:disabled {{
    background-color: {C['mantle']};
    color: {C['overlay']};
    border-color: {C['surface0']};
}}
QPushButton#processBtn {{
    background-color: {C['blue']};
    color: {C['crust']};
    font-size: 14px;
    padding: 8px 24px;
}}
QPushButton#processBtn:hover {{
    background-color: {C['lavender']};
}}
QPushButton#processBtn:disabled {{
    background-color: {C['surface1']};
    color: {C['overlay']};
}}
QPushButton#cancelBtn {{
    background-color: {C['red']};
    color: {C['crust']};
}}
QListWidget {{
    background-color: {C['mantle']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 4px;
    color: {C['text']};
}}
QListWidget::item {{
    padding: 4px 8px;
    border-radius: 4px;
}}
QListWidget::item:selected {{
    background-color: {C['surface0']};
}}
QComboBox {{
    background-color: {C['surface0']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 4px 8px;
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
    background-color: {C['mantle']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 4px 8px;
    color: {C['text']};
}}
QLineEdit:focus {{
    border-color: {C['blue']};
}}
QCheckBox {{
    color: {C['text']};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {C['surface2']};
    background-color: {C['surface0']};
}}
QCheckBox::indicator:checked {{
    background-color: {C['blue']};
    border-color: {C['blue']};
}}
QSlider::groove:horizontal {{
    background: {C['surface0']};
    height: 6px;
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {C['blue']};
    width: 14px;
    height: 14px;
    margin: -4px 0;
    border-radius: 7px;
}}
QSlider::sub-page:horizontal {{
    background: {C['blue']};
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
    background-color: {C['surface0']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    text-align: center;
    color: {C['text']};
    height: 22px;
}}
QProgressBar::chunk {{
    background-color: {C['green']};
    border-radius: 5px;
}}
QTextEdit {{
    background-color: {C['mantle']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 6px;
    color: {C['subtext']};
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 12px;
}}
QScrollBar:vertical {{
    background: {C['mantle']};
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


def _open_in_file_manager(path):
    """Open a directory in the OS file manager. Cross-platform."""
    if not os.path.isdir(path):
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))


# ============================================================
#  Audio Processor
# ============================================================
class AudioProcessor:
    # Humanize pass processes long audio in chunks to bound peak memory.
    # Chunks are rendered with a shared modulation curve (continuous across
    # boundaries) so the output is indistinguishable from whole-file rendering.
    _HUMANIZE_CHUNK_SEC = 60.0

    def __init__(self, params, log_fn=None, progress_fn=None, cancel_event=None):
        self.params = params
        self.log = log_fn or print
        self.progress = progress_fn or (lambda v: None)
        self.rng = np.random.default_rng()
        self._cancel_event = cancel_event or threading.Event()

    def cancel(self):
        self._cancel_event.set()

    def _is_cancelled(self):
        return self._cancel_event.is_set()

    # --- Main pipeline ---
    def process(self, input_path, output_path):
        self.log(f"Loading {Path(input_path).name}...")

        try:
            audio, sr = sf.read(input_path, dtype='float64')
        except Exception as e:
            self.log(f"  Error reading file: {e}")
            return False

        if audio.size == 0:
            self.log("  Error: empty audio file")
            return False

        mono = audio.ndim == 1
        if mono:
            audio = audio[:, np.newaxis]

        original = audio.copy()

        # Build pass list
        pass_names = []
        if self.params.get('strip_metadata', True):
            pass_names.append('Metadata Strip')
        if self.params.get('spectral_enabled'):
            pass_names.append('Spectral Perturbation')
        if self.params.get('pitch_enabled'):
            pass_names.append('Pitch Micro-Shift')
        if self.params.get('tempo_enabled'):
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
                elif name == 'Spectral Perturbation':
                    audio = self._spectral_perturb(audio, sr)
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
                self.log(f"    Warning: {name} failed ({e}), skipping")

        audio = np.clip(audio, -1.0, 1.0)

        # Save
        self.log(f"Saving {Path(output_path).name}...")
        self.progress(92)

        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        save_audio = audio[:, 0] if mono else audio
        fmt = self.params.get('output_format', 'wav').lower()
        try:
            if fmt == 'flac':
                sf.write(output_path, save_audio, sr, format='FLAC')
            elif fmt == 'ogg':
                sf.write(output_path, save_audio, sr, format='OGG', subtype='VORBIS')
            else:
                sf.write(output_path, save_audio, sr, subtype='PCM_24')
        except Exception as e:
            self.log(f"  Save error: {e}")
            return False

        if self.params.get('strip_metadata', True):
            self._strip_metadata(output_path)

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

        self.progress(100)
        return True

    # --- Metadata ---
    def _strip_metadata(self, filepath):
        try:
            f = MutagenFile(filepath)
            if f is not None:
                f.delete()
                f.save()
        except Exception:
            pass

    # --- Spectral perturbation ---
    def _spectral_perturb(self, audio, sr):
        strength = self.params.get('spectral_strength', 0.3)
        result = np.zeros_like(audio)
        for ch in range(audio.shape[1]):
            result[:, ch] = self._spectral_perturb_ch(audio[:, ch], sr, strength)
        return result

    def _spectral_perturb_ch(self, channel, sr, strength):
        nperseg = _nperseg_for(len(channel))
        if nperseg == 0:
            return channel.copy()
        noverlap = nperseg // 2

        f, t, Zxx = signal.stft(channel, sr, nperseg=nperseg, noverlap=noverlap)
        mag = np.abs(Zxx)
        phase = np.angle(Zxx)

        noise = self.rng.normal(1.0, strength * 0.05, mag.shape)
        mag *= np.maximum(noise, 0.01)

        high_mask = f > 16000
        if np.any(high_mask):
            mag[high_mask] *= self.rng.uniform(
                1.0 - strength * 0.4, 1.0 + strength * 0.4, mag[high_mask].shape,
            )

        low_mask = f < 60
        if np.any(low_mask):
            mag[low_mask] *= self.rng.uniform(
                1.0 - strength * 0.3, 1.0 + strength * 0.3, mag[low_mask].shape,
            )

        Zxx_new = mag * np.exp(1j * phase)
        _, result = signal.istft(Zxx_new, sr, nperseg=nperseg, noverlap=noverlap)

        orig_len = len(channel)
        if len(result) > orig_len:
            result = result[:orig_len]
        elif len(result) < orig_len:
            result = np.pad(result, (0, orig_len - len(result)))
        return result

    # --- Non-uniform pitch micro-shift ---
    def _pitch_microshift(self, audio, sr):
        max_st = self.params.get('pitch_range', 0.8)
        if max_st < 0.001:
            return audio

        n = audio.shape[0]
        seg_samples = int(2.0 * sr)
        n_segments = max(1, n // seg_samples)

        shifts = self.rng.uniform(-max_st, max_st, n_segments)
        factors = 2.0 ** (shifts / 12.0)

        seg_size = n / n_segments
        src = [0.0]
        dst = [0.0]
        for i, f in enumerate(factors):
            src.append((i + 1) * seg_size)
            dst.append(dst[-1] + seg_size / f)

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

    # --- Non-uniform tempo micro-variation ---
    def _tempo_microvar(self, audio, sr):
        max_var = self.params.get('tempo_range', 0.05)
        if max_var < 0.001:
            return audio

        n = audio.shape[0]
        seg_samples = int(2.5 * sr)
        n_segments = max(1, n // seg_samples)

        factors = self.rng.uniform(1.0 - max_var, 1.0 + max_var, n_segments)

        seg_size = n / n_segments
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
            result[:, ch] += pink * level_lin
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
            self.log("    ffmpeg not found -- skipping re-encode")
            return audio

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
            self.log(f"    Re-encode failed: {e}")
            return audio
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


# ============================================================
#  Process Worker Thread
# ============================================================
class ProcessWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    # file_done(row_index, success, output_path_or_empty)
    file_done = pyqtSignal(int, bool, str)
    all_done = pyqtSignal()

    def __init__(self, files, params, output_dir):
        super().__init__()
        self.files = files
        self.params = params
        self.output_dir = output_dir
        self._cancel_event = threading.Event()

    def run(self):
        os.makedirs(self.output_dir, exist_ok=True)
        n_files = len(self.files)

        for idx, filepath in enumerate(self.files):
            if self._cancel_event.is_set():
                break

            # Map per-file progress (0-100) to batch progress
            def batch_progress(v, _idx=idx, _n=n_files):
                self.progress_signal.emit((_idx * 100 + v) // _n)

            processor = AudioProcessor(
                self.params,
                log_fn=lambda msg: self.log_signal.emit(msg),
                progress_fn=batch_progress,
                cancel_event=self._cancel_event,
            )

            stem = Path(filepath).stem
            ext_map = {'wav': '.wav', 'flac': '.flac', 'ogg': '.ogg'}
            fmt = self.params.get('output_format', 'wav').lower()
            ext = ext_map.get(fmt, '.wav')
            out_path = os.path.join(self.output_dir, f"{stem}_sj{ext}")

            self.log_signal.emit(f"\n[{idx+1}/{n_files}] {Path(filepath).name}")
            ok = processor.process(filepath, out_path)

            self.file_done.emit(idx, ok, out_path if ok else "")

        self.all_done.emit()

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
        self.key = key
        self.enabled_key = enabled_key
        self.min_val = min_val
        self.max_val = max_val
        self.decimals = decimals
        self.suffix = suffix
        self.display_factor = display_factor

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)

        self.check = QCheckBox()
        self.check.setChecked(True)
        lay.addWidget(self.check)

        self._label = QLabel(label)
        self._label.setFixedWidth(170)
        lay.addWidget(self._label)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 200)
        lay.addWidget(self.slider, 1)

        self.val_label = QLabel()
        self.val_label.setFixedWidth(65)
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
        self.setMinimumSize(680, 860)
        self.resize(720, 940)
        self.worker = None
        self._applying_preset = False
        self._last_browse_dir = str(Path.home())
        self._last_preset_dir = str(Path.home())

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
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 8, 12, 12)
        root.setSpacing(6)

        # Title
        title_row = QHBoxLayout()
        title = QLabel(APP_NAME)
        title.setStyleSheet(f"font-size: 20px; font-weight: bold; color: {C['blue']};")
        ver = QLabel(f"v{VERSION}")
        ver.setStyleSheet(f"font-size: 12px; color: {C['overlay']};")
        title_row.addWidget(title)
        title_row.addWidget(ver)
        title_row.addStretch()
        root.addLayout(title_row)

        root.addWidget(self._build_files())
        root.addWidget(self._build_settings())
        root.addWidget(self._build_output())
        root.addLayout(self._build_controls())
        root.addWidget(self._build_preview())
        root.addWidget(self._build_log())

        # Center on screen
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2 + geo.x()
            y = (geo.height() - self.height()) // 2 + geo.y()
            self.move(x, y)

    # --- File section ---
    def _build_files(self):
        grp = QGroupBox("Files")
        lay = QVBoxLayout(grp)

        btn_row = QHBoxLayout()
        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self._on_browse)
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.clicked.connect(self._on_remove_selected)
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self._on_clear)

        self.file_count_label = QLabel("0 files")
        self.file_count_label.setStyleSheet(f"color: {C['overlay']}; font-style: italic;")

        btn_row.addWidget(self.btn_browse)
        btn_row.addWidget(self.btn_remove)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch()
        btn_row.addWidget(self.file_count_label)
        lay.addLayout(btn_row)

        self.file_list = DropListWidget()
        self.file_list.setMinimumHeight(90)
        self.file_list.setMaximumHeight(140)
        self.file_list.filesDropped.connect(self._add_files)
        self.file_list.itemSelectionChanged.connect(self._update_preview_ui)
        lay.addWidget(self.file_list)

        hint = QLabel("Drop files here  \u2022  drag to reorder")
        hint.setStyleSheet(f"color: {C['overlay']}; font-style: italic; font-size: 11px;")
        lay.addWidget(hint)

        return grp

    # --- Settings section ---
    def _build_settings(self):
        grp = QGroupBox("Processing Pipeline")
        lay = QVBoxLayout(grp)

        # Preset row
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(PRESETS.keys()) + ['Custom'])
        self.preset_combo.setCurrentText('Moderate')
        self.preset_combo.currentTextChanged.connect(self._on_preset)
        preset_row.addWidget(self.preset_combo)

        self.btn_save_preset = QPushButton("Save...")
        self.btn_save_preset.setToolTip("Save current settings to a JSON file")
        self.btn_save_preset.clicked.connect(self._save_preset)
        preset_row.addWidget(self.btn_save_preset)

        self.btn_load_preset = QPushButton("Load...")
        self.btn_load_preset.setToolTip("Load settings from a JSON file")
        self.btn_load_preset.clicked.connect(self._load_preset)
        preset_row.addWidget(self.btn_load_preset)

        preset_row.addStretch()

        self.meta_check = QCheckBox("Metadata Strip")
        self.meta_check.setChecked(True)
        self.meta_check.stateChanged.connect(lambda _: self._on_param_changed())
        preset_row.addWidget(self.meta_check)
        lay.addLayout(preset_row)

        # Param rows
        self.param_rows = {}
        for key, label, mn, mx, df, suf, dec, ek, *rest in PARAM_DEFS:
            dfact = rest[0] if rest else 1.0
            row = ParamRow(key, label, mn, mx, df, suf, dec, ek, dfact)
            row.changed.connect(self._on_param_changed)
            self.param_rows[key] = row
            lay.addWidget(row)

        self._apply_preset('Moderate')
        return grp

    # --- Output section ---
    def _build_output(self):
        grp = QGroupBox("Output")
        lay = QHBoxLayout(grp)

        lay.addWidget(QLabel("Format:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(['WAV', 'FLAC', 'OGG'])
        lay.addWidget(self.format_combo)

        lay.addSpacing(16)
        lay.addWidget(QLabel("Directory:"))
        self.output_dir = QLineEdit(DEFAULT_OUTPUT)
        lay.addWidget(self.output_dir, 1)
        btn_dir = QPushButton("...")
        btn_dir.setFixedWidth(36)
        btn_dir.clicked.connect(self._browse_output)
        lay.addWidget(btn_dir)

        self.btn_open_output = QPushButton("Open")
        self.btn_open_output.setToolTip("Open output directory in file manager")
        self.btn_open_output.clicked.connect(self._open_output)
        lay.addWidget(self.btn_open_output)

        return grp

    # --- Controls ---
    def _build_controls(self):
        lay = QHBoxLayout()

        self.btn_process = QPushButton("Process All")
        self.btn_process.setObjectName("processBtn")
        self.btn_process.clicked.connect(self._on_process)
        lay.addWidget(self.btn_process)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("cancelBtn")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        lay.addWidget(self.btn_cancel)

        lay.addSpacing(16)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        lay.addWidget(self.progress, 1)

        return lay

    # --- Preview ---
    def _build_preview(self):
        grp = QGroupBox("Preview")
        lay = QHBoxLayout(grp)

        self.btn_play_orig = QPushButton("Play Original")
        self.btn_play_orig.clicked.connect(lambda: self._toggle_play('original'))
        self.btn_play_orig.setEnabled(False)
        lay.addWidget(self.btn_play_orig)

        self.btn_play_proc = QPushButton("Play Processed")
        self.btn_play_proc.clicked.connect(lambda: self._toggle_play('processed'))
        self.btn_play_proc.setEnabled(False)
        lay.addWidget(self.btn_play_proc)

        lay.addSpacing(12)
        self.preview_label = QLabel("Select a file")
        self.preview_label.setStyleSheet(f"color: {C['overlay']}; font-style: italic;")
        lay.addWidget(self.preview_label, 1)

        if not _MULTIMEDIA_OK:
            self.btn_play_orig.setEnabled(False)
            self.btn_play_proc.setEnabled(False)
            self.preview_label.setText("(PyQt6 Multimedia not available)")

        return grp

    # --- Log ---
    def _build_log(self):
        grp = QGroupBox("Log")
        lay = QVBoxLayout(grp)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(110)
        self.log_box.setMaximumHeight(180)
        lay.addWidget(self.log_box)
        return grp

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
        item = QListWidgetItem(f"  {Path(path).name}")
        item.setData(ROLE_INPUT, path)
        item.setData(ROLE_OUTPUT, None)
        self.file_list.addItem(item)

    def _update_file_count(self):
        n = self.file_list.count()
        self.file_count_label.setText(f"{n} file{'s' if n != 1 else ''}")

    # --- Preset slots ---
    def _on_preset(self, name):
        if name in PRESETS:
            self._apply_preset(name)

    def _apply_preset(self, name):
        self._applying_preset = True
        try:
            p = PRESETS[name]
            self.meta_check.setChecked(p.get('strip_metadata', True))
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
            params = data.get('params', data)  # accept raw dict for flexibility
            if not isinstance(params, dict):
                raise ValueError("Missing params block")

            self._applying_preset = True
            try:
                if 'strip_metadata' in params:
                    self.meta_check.setChecked(bool(params['strip_metadata']))
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
            self._log(f"Preset loaded: {Path(path).name}")
        except Exception as e:
            self._log(f"Load preset failed: {e}")

    def _get_params(self):
        params = {
            'strip_metadata': self.meta_check.isChecked(),
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
        self.btn_browse.setEnabled(not processing)
        self.btn_remove.setEnabled(not processing)
        self.btn_clear.setEnabled(not processing)
        self.btn_save_preset.setEnabled(not processing)
        self.btn_load_preset.setEnabled(not processing)
        # Lock reordering during processing to preserve index mapping
        self.file_list.setDragEnabled(not processing)
        if processing:
            self.progress.setValue(0)

    def _on_process(self):
        if self.file_list.count() == 0:
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
        self._log(f"Starting -- {len(files)} file(s), preset: {self.preset_combo.currentText()}")
        self._log(f"Output: {out_dir}\n")

        self.worker = ProcessWorker(files, params, out_dir)
        self.worker.log_signal.connect(self._log)
        self.worker.progress_signal.connect(self.progress.setValue)
        self.worker.file_done.connect(self._on_file_done)
        self.worker.all_done.connect(self._on_all_done)
        self.worker.start()

    def _on_cancel(self):
        if self.worker:
            self.worker.cancel()
            self._log("\nCancelling...")

    def _on_file_done(self, idx, ok, out_path):
        if 0 <= idx < self.file_list.count():
            item = self.file_list.item(idx)
            name = Path(item.data(ROLE_INPUT)).name
            if ok:
                item.setText(f"  {name}  [-> {Path(out_path).name}]")
                item.setData(ROLE_OUTPUT, out_path)
            else:
                item.setText(f"  {name}  [FAILED]")
                item.setData(ROLE_OUTPUT, None)
        self._update_preview_ui()

    def _on_all_done(self):
        self._set_processing_ui(False)
        self.progress.setValue(100)
        self._log("\nAll done.")

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.append(f"[{ts}] {msg}")
        sb = self.log_box.verticalScrollBar()
        sb.setValue(sb.maximum())

    # --- Preview / playback ---
    def _current_selected_item(self):
        items = self.file_list.selectedItems()
        if items:
            return items[0]
        if self.file_list.count() > 0:
            return self.file_list.item(0)
        return None

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

        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        self.player.play()
        self._playing_source = source
        self._update_preview_ui()

    def _stop_playback(self):
        if self.player is not None and self._playing_source is not None:
            self.player.stop()
        self._playing_source = None

    def _on_playback_state_changed(self, state):
        if self.player is None:
            return
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self._playing_source = None
            self._update_preview_ui()

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

        if item is None:
            self.btn_play_orig.setText("Play Original")
            self.btn_play_proc.setText("Play Processed")
            self.btn_play_orig.setEnabled(False)
            self.btn_play_proc.setEnabled(False)
            self.preview_label.setText("Select a file")
            return

        orig_path = item.data(ROLE_INPUT)
        proc_path = item.data(ROLE_OUTPUT)
        orig_ok = bool(orig_path) and os.path.isfile(orig_path)
        proc_ok = bool(proc_path) and os.path.isfile(proc_path)

        self.preview_label.setText(Path(orig_path).name if orig_path else "")

        if self._playing_source == 'original':
            self.btn_play_orig.setText("Stop")
            self.btn_play_orig.setEnabled(True)
            self.btn_play_proc.setText("Play Processed")
            self.btn_play_proc.setEnabled(False)
        elif self._playing_source == 'processed':
            self.btn_play_orig.setText("Play Original")
            self.btn_play_orig.setEnabled(False)
            self.btn_play_proc.setText("Stop")
            self.btn_play_proc.setEnabled(True)
        else:
            self.btn_play_orig.setText("Play Original")
            self.btn_play_proc.setText("Play Processed")
            self.btn_play_orig.setEnabled(orig_ok)
            self.btn_play_proc.setEnabled(proc_ok)

    def closeEvent(self, event):
        if self.player is not None:
            self.player.stop()
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
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
    parser.add_argument('-i', '--input', required=True, help='Input audio file or directory')
    parser.add_argument('-o', '--output', default=None, help='Output file or directory')
    parser.add_argument('-p', '--preset', default='moderate',
                        choices=['gentle', 'moderate', 'aggressive', 'extreme'])
    parser.add_argument('-f', '--format', default='wav', choices=['wav', 'flac', 'ogg'],
                        dest='out_format')
    parser.add_argument('--preset-file', default=None,
                        help='Path to JSON preset file (overrides -p/--preset)')
    parser.add_argument('--spectral', type=float, help='Spectral perturbation (0.0-1.0)')
    parser.add_argument('--pitch', type=float, help='Pitch micro-shift in semitones (0.0-5.0)')
    parser.add_argument('--tempo', type=float, help='Tempo variation (0.0-0.15)')
    parser.add_argument('--phase', type=float, help='Phase scrambling (0.0-1.0)')
    parser.add_argument('--stereo', type=float, help='Stereo manipulation (0.0-0.5)')
    parser.add_argument('--noise', type=float, help='Noise level in dB (-70 to -30)')
    parser.add_argument('--dynamics', type=float, help='Dynamics amount (0.0-1.0)')
    parser.add_argument('--humanize', type=float, help='Humanization amount (0.0-1.0)')
    parser.add_argument('--reencode', type=int, help='Lossy re-encode bitrate (96-320)')
    args = parser.parse_args()

    # Start with built-in preset
    preset_name = args.preset.capitalize()
    params = dict(PRESETS.get(preset_name, PRESETS['Moderate']))
    params['output_format'] = args.out_format

    # Optional JSON preset file override
    if args.preset_file:
        try:
            with open(args.preset_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            loaded = data.get('params', data) if isinstance(data, dict) else {}
            if not isinstance(loaded, dict):
                raise ValueError("preset file missing params block")
            # Only accept known keys to avoid poisoning
            known = {'strip_metadata'} | {d[0] for d in PARAM_DEFS} | {d[7] for d in PARAM_DEFS}
            for k, v in loaded.items():
                if k in known:
                    params[k] = v
            print(f"Loaded preset from {args.preset_file}")
            preset_name = data.get('name', 'Custom') if isinstance(data, dict) else 'Custom'
        except Exception as e:
            print(f"Warning: could not load preset file: {e}")

    # Override with CLI args (validated)
    if args.spectral is not None:
        params['spectral_strength'] = _clamp(args.spectral, 0.0, 1.0, 'spectral')
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

    ext_map = {'wav': '.wav', 'flac': '.flac', 'ogg': '.ogg'}
    ext = ext_map.get(args.out_format, '.wav')

    print(f"{APP_NAME} v{VERSION}")
    print(f"Preset: {preset_name} | Format: {args.out_format.upper()} | Files: {len(files)}\n")

    fail_count = 0
    for filepath in files:
        stem = Path(filepath).stem
        out_path = os.path.join(out_dir, f"{stem}_sj{ext}")

        proc = AudioProcessor(params, log_fn=print, progress_fn=lambda v: None)
        ok = proc.process(filepath, out_path)
        if not ok:
            fail_count += 1
        print("---")

    print(f"\nDone. Output: {out_dir}")
    if fail_count:
        print(f"{fail_count} file(s) failed.")
        sys.exit(2)


# ============================================================
#  Entry Point
# ============================================================
if __name__ == '__main__':
    if len(sys.argv) > 1 and ('-i' in sys.argv or '--input' in sys.argv):
        cli_main()
    else:
        app = QApplication(sys.argv)
        app.setStyle('Fusion')
        app.setStyleSheet(STYLE)
        win = MainWindow()
        win.show()
        sys.exit(app.exec())
