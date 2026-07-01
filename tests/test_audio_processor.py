#!/usr/bin/env python3
import threading
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

import sunojump
from sunojump import AudioProcessor, _format_requires_ffmpeg, _output_extension, _planned_output_path


class CoupledPitchTempoTests(unittest.TestCase):
    def _params(self):
        return {
            'pitch_enabled': True,
            'pitch_range': 0.8,
            'tempo_enabled': True,
            'tempo_range': 0.05,
        }

    def _sample_audio(self, sr=8000, seconds=3.0):
        t = np.arange(int(sr * seconds), dtype=np.float64) / sr
        left = 0.25 * np.sin(2.0 * np.pi * 220.0 * t)
        right = 0.25 * np.sin(2.0 * np.pi * 330.0 * t)
        return np.column_stack([left, right])

    def test_coupled_pitch_tempo_is_deterministic_and_length_stable(self):
        sr = 8000
        audio = self._sample_audio(sr=sr)

        proc_a = AudioProcessor(self._params(), seed=123)
        proc_b = AudioProcessor(self._params(), seed=123)

        out_a = proc_a._pitch_tempo_coupled_microvar(audio, sr)
        out_b = proc_b._pitch_tempo_coupled_microvar(audio, sr)

        self.assertEqual(out_a.shape, audio.shape)
        self.assertTrue(np.all(np.isfinite(out_a)))
        self.assertGreater(np.max(np.abs(out_a - audio)), 1e-6)
        np.testing.assert_allclose(out_a, out_b, rtol=0, atol=1e-12)

    def test_tempo_warp_keeps_chunk_boundaries_aligned(self):
        sr = 8000
        audio = self._sample_audio(sr=sr, seconds=1.0)
        proc = AudioProcessor(self._params(), seed=123)

        out = proc._tempo_warp_aligned_chunk(audio, 0.08)

        self.assertEqual(out.shape, audio.shape)
        np.testing.assert_allclose(out[0], audio[0], atol=1e-12)
        np.testing.assert_allclose(out[-1], audio[-1], atol=1e-9)


class SpectralBandTests(unittest.TestCase):
    def test_band_strength_falls_back_clamps_and_honors_enabled_flag(self):
        proc = AudioProcessor({
            'spectral_air_strength': 2.0,
            'spectral_presence_enabled': False,
        }, seed=123)

        self.assertEqual(proc._spectral_band_strength('spectral_air', 0.3), 1.0)
        self.assertEqual(proc._spectral_band_strength('spectral_presence', 0.7), 0.0)
        self.assertEqual(proc._spectral_band_strength('spectral_sub_bass', 0.4), 0.4)

    def test_air_band_perturbation_changes_output_with_other_bands_disabled(self):
        sr = 48000
        t = np.arange(sr, dtype=np.float64) / sr
        audio = (
            0.20 * np.sin(2.0 * np.pi * 60.0 * t)
            + 0.20 * np.sin(2.0 * np.pi * 300.0 * t)
            + 0.20 * np.sin(2.0 * np.pi * 4000.0 * t)
            + 0.20 * np.sin(2.0 * np.pi * 12000.0 * t)
        )
        params = {
            'spectral_sub_bass_enabled': False,
            'spectral_low_mids_enabled': False,
            'spectral_presence_enabled': False,
            'spectral_air_enabled': True,
            'spectral_air_strength': 1.0,
        }
        proc = AudioProcessor(params, seed=123)

        out = proc._spectral_perturb_ch(audio, sr, 0.0)

        self.assertEqual(out.shape, audio.shape)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertGreater(np.max(np.abs(out - audio)), 1e-6)

    def test_spectral_window_selector_uses_sweep_sizes(self):
        proc = AudioProcessor({}, seed=123)

        seen = {proc._choose_spectral_window(5000) for _ in range(40)}

        self.assertTrue(seen.issubset({1024, 2048, 4096}))
        self.assertGreaterEqual(len(seen), 2)

    def test_spectral_perturb_accepts_4096_window(self):
        sr = 16000
        t = np.arange(sr, dtype=np.float64) / sr
        audio = 0.25 * np.sin(2.0 * np.pi * 440.0 * t)
        proc = AudioProcessor({}, seed=123)

        out = proc._spectral_perturb_ch(audio, sr, 0.4, nperseg=4096)

        self.assertEqual(out.shape, audio.shape)
        self.assertTrue(np.all(np.isfinite(out)))


class WatermarkScanTests(unittest.TestCase):
    def test_scan_detects_stable_high_frequency_candidate(self):
        sr = 48000
        rng = np.random.default_rng(123)
        t = np.arange(sr * 2, dtype=np.float64) / sr
        tone = 0.45 * np.sin(2.0 * np.pi * 12000.0 * t)
        noise = rng.normal(0.0, 0.01, len(t))
        audio = np.column_stack([tone + noise, tone + noise])
        proc = AudioProcessor({'watermark_scan_enabled': True}, seed=123)

        candidates = proc._scan_watermark_bands(audio, sr)

        self.assertTrue(
            any(abs(c['center_hz'] - 12000.0) < 80.0 for c in candidates),
            candidates,
        )

    def test_detected_candidate_band_changes_spectral_output(self):
        sr = 48000
        t = np.arange(sr, dtype=np.float64) / sr
        audio = 0.35 * np.sin(2.0 * np.pi * 12000.0 * t)
        proc = AudioProcessor({
            'spectral_sub_bass_enabled': False,
            'spectral_low_mids_enabled': False,
            'spectral_presence_enabled': False,
            'spectral_air_enabled': False,
        }, seed=123)
        proc._watermark_candidates = [{
            'center_hz': 12000.0,
            'low_hz': 11800.0,
            'high_hz': 12200.0,
            'score': 12.0,
        }]

        out = proc._spectral_perturb_ch(audio, sr, 0.5)

        self.assertEqual(out.shape, audio.shape)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertGreater(np.max(np.abs(out - audio)), 1e-6)


class DynamicEqTests(unittest.TestCase):
    def test_dynamic_eq_preserves_loudness(self):
        sr = 16000
        t = np.arange(sr * 2, dtype=np.float64) / sr
        envelope = 0.6 + 0.3 * np.sin(2.0 * np.pi * 1.5 * t)
        left = envelope * (
            0.20 * np.sin(2.0 * np.pi * 140.0 * t)
            + 0.16 * np.sin(2.0 * np.pi * 1200.0 * t)
            + 0.12 * np.sin(2.0 * np.pi * 5200.0 * t)
        )
        right = envelope * (
            0.18 * np.sin(2.0 * np.pi * 220.0 * t)
            + 0.14 * np.sin(2.0 * np.pi * 2400.0 * t)
            + 0.10 * np.sin(2.0 * np.pi * 7000.0 * t)
        )
        audio = np.column_stack([left, right])
        proc = AudioProcessor({'dynamic_eq_amount': 0.8}, seed=123)

        before = proc._integrated_lufs(audio, sr)
        out = proc._dynamic_eq(audio, sr)
        after = proc._integrated_lufs(out, sr)

        self.assertEqual(out.shape, audio.shape)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertGreater(np.max(np.abs(out - audio)), 1e-6)
        self.assertLess(abs(before - after), 0.25)


class NoiseInjectionTests(unittest.TestCase):
    def test_masking_aware_noise_tracks_louder_regions(self):
        sr = 16000
        t = np.arange(sr * 2, dtype=np.float64) / sr
        quiet = 0.02 * np.sin(2.0 * np.pi * 440.0 * t[:sr])
        loud = 0.45 * np.sin(2.0 * np.pi * 440.0 * t[:sr])
        mono = np.concatenate([quiet, loud])
        audio = mono[:, np.newaxis]
        proc = AudioProcessor({'noise_level': -35.0}, seed=123)

        out = proc._inject_noise(audio, sr)[:, 0]
        added = out - mono

        quiet_rms = proc._rms(added[:sr])
        loud_rms = proc._rms(added[sr:])

        self.assertGreater(loud_rms, quiet_rms * 1.5)
        self.assertLessEqual(proc._rms(added), 10.0 ** (-35.0 / 20.0) * 1.05)


class AudioPreflightTests(unittest.TestCase):
    class _Info:
        def __init__(self, frames=1000, samplerate=48000, channels=2):
            self.frames = frames
            self.samplerate = samplerate
            self.channels = channels

    def _processor(self, logs):
        return AudioProcessor({
            'strip_metadata': False,
            'spectral_enabled': True,
            'watermark_scan_enabled': False,
            'spectral_strength': 0.05,
        }, log_fn=logs.append, seed=123)

    def _assert_preflight_rejects_before_read(self, input_path, expected_log):
        logs = []
        old_read = sunojump.sf.read
        read_called = []

        def fail_read(*_args, **_kwargs):
            read_called.append(True)
            raise AssertionError("sf.read should not be called")

        sunojump.sf.read = fail_read
        try:
            ok = self._processor(logs).process(str(input_path), str(Path(input_path).with_name('out.wav')))
        finally:
            sunojump.sf.read = old_read

        self.assertFalse(ok)
        self.assertFalse(read_called)
        self.assertTrue(any(expected_log in line for line in logs), logs)

    def test_valid_file_passes_preflight_and_processes(self):
        sr = 8000
        t = np.arange(sr, dtype=np.float64) / sr
        audio = 0.20 * np.sin(2.0 * np.pi * 440.0 * t)
        logs = []

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / 'input.wav'
            output_path = Path(tmp) / 'output.wav'
            sf.write(input_path, audio, sr)

            ok = self._processor(logs).process(str(input_path), str(output_path))

            self.assertTrue(ok, logs)
            self.assertTrue(output_path.exists())

    def test_empty_file_rejected_before_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / 'empty.wav'
            input_path.write_bytes(b'')

            self._assert_preflight_rejects_before_read(input_path, "empty audio file")

    def test_malformed_file_rejected_before_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / 'broken.wav'
            input_path.write_bytes(b'not a real wave file')

            self._assert_preflight_rejects_before_read(input_path, "unsupported or malformed")

    def test_unsupported_extension_rejected_before_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / 'input.txt'
            input_path.write_text('not audio', encoding='utf-8')

            self._assert_preflight_rejects_before_read(input_path, "unsupported audio format")

    def test_excessive_channel_count_rejected_before_decode(self):
        old_info = sunojump.sf.info
        old_read = sunojump.sf.read
        read_called = []
        logs = []
        sunojump.sf.info = lambda _path: self._Info(channels=sunojump.MAX_AUDIO_CHANNELS + 1)
        sunojump.sf.read = lambda *_args, **_kwargs: read_called.append(True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                input_path = Path(tmp) / 'too_many_channels.wav'
                input_path.write_bytes(b'RIFF data')
                ok = self._processor(logs).process(str(input_path), str(Path(tmp) / 'out.wav'))
        finally:
            sunojump.sf.info = old_info
            sunojump.sf.read = old_read

        self.assertFalse(ok)
        self.assertFalse(read_called)
        self.assertTrue(any("too many channels" in line for line in logs), logs)

    def test_excessive_sample_rate_rejected_before_decode(self):
        old_info = sunojump.sf.info
        old_read = sunojump.sf.read
        read_called = []
        logs = []
        sunojump.sf.info = lambda _path: self._Info(samplerate=sunojump.MAX_AUDIO_SAMPLE_RATE + 1)
        sunojump.sf.read = lambda *_args, **_kwargs: read_called.append(True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                input_path = Path(tmp) / 'too_fast.wav'
                input_path.write_bytes(b'RIFF data')
                ok = self._processor(logs).process(str(input_path), str(Path(tmp) / 'out.wav'))
        finally:
            sunojump.sf.info = old_info
            sunojump.sf.read = old_read

        self.assertFalse(ok)
        self.assertFalse(read_called)
        self.assertTrue(any("sample rate too high" in line for line in logs), logs)

    def test_decoded_memory_guard_rejects_before_decode(self):
        old_info = sunojump.sf.info
        old_read = sunojump.sf.read
        read_called = []
        logs = []
        frames = sunojump.MAX_DECODED_AUDIO_BYTES // (2 * 8) + 1
        sunojump.sf.info = lambda _path: self._Info(frames=frames, channels=2)
        sunojump.sf.read = lambda *_args, **_kwargs: read_called.append(True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                input_path = Path(tmp) / 'huge.wav'
                input_path.write_bytes(b'RIFF data')
                ok = self._processor(logs).process(str(input_path), str(Path(tmp) / 'out.wav'))
        finally:
            sunojump.sf.info = old_info
            sunojump.sf.read = old_read

        self.assertFalse(ok)
        self.assertFalse(read_called)
        self.assertTrue(any("decoded audio would exceed memory guardrail" in line for line in logs), logs)

    def test_preview_reads_only_requested_frames(self):
        old_info = sunojump.sf.info
        old_read = sunojump.sf.read
        read_kwargs = []
        logs = []
        sunojump.sf.info = lambda _path: self._Info(frames=48000 * 60, samplerate=48000, channels=1)

        def fake_read(_path, **kwargs):
            read_kwargs.append(kwargs)
            return np.zeros(48000, dtype=np.float64), 48000

        sunojump.sf.read = fake_read
        try:
            with tempfile.TemporaryDirectory() as tmp:
                input_path = Path(tmp) / 'long.wav'
                output_path = Path(tmp) / 'out.wav'
                input_path.write_bytes(b'RIFF data')
                ok = AudioProcessor({}, log_fn=logs.append, seed=123).process(
                    str(input_path), str(output_path), preview_seconds=2.0,
                )
        finally:
            sunojump.sf.info = old_info
            sunojump.sf.read = old_read

        self.assertTrue(ok, logs)
        self.assertEqual(read_kwargs[0].get('frames'), 96000)


class FailClosedProcessingTests(unittest.TestCase):
    def test_enabled_pass_failure_aborts_without_output(self):
        sr = 8000
        t = np.arange(sr, dtype=np.float64) / sr
        audio = 0.25 * np.sin(2.0 * np.pi * 440.0 * t)
        logs = []

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / 'input.wav'
            output_path = Path(tmp) / 'output.wav'
            sf.write(input_path, audio, sr)

            proc = AudioProcessor({
                'strip_metadata': False,
                'spectral_enabled': True,
                'watermark_scan_enabled': False,
            }, log_fn=logs.append, seed=123)

            def fail_spectral(_audio, _sr):
                raise RuntimeError("synthetic failure")

            proc._spectral_perturb = fail_spectral

            ok = proc.process(str(input_path), str(output_path))

            self.assertFalse(ok)
            self.assertFalse(output_path.exists())
            self.assertTrue(
                any("Spectral Perturbation failed" in line for line in logs),
                logs,
            )


class OutputPathTests(unittest.TestCase):
    def test_output_paths_are_unique_for_duplicate_stems(self):
        used = set()

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / 'out'
            first = Path(tmp) / 'a' / 'song.wav'
            second = Path(tmp) / 'b' / 'song.wav'

            path_a, renamed_a = _planned_output_path(first, out_dir, '.wav', used)
            path_b, renamed_b = _planned_output_path(second, out_dir, '.wav', used)

            self.assertEqual(Path(path_a).name, 'song_sj.wav')
            self.assertEqual(Path(path_b).name, 'song_sj_2.wav')
            self.assertFalse(renamed_a)
            self.assertTrue(renamed_b)
            self.assertNotEqual(path_a, path_b)

    def test_output_path_avoids_existing_file(self):
        used = set()

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            existing = out_dir / 'song_sj.wav'
            existing.write_text('existing', encoding='utf-8')

            path, renamed = _planned_output_path(
                out_dir / 'song.wav', out_dir, '.wav', used,
            )

            self.assertEqual(Path(path).name, 'song_sj_2.wav')
            self.assertTrue(renamed)


class OutputFormatTests(unittest.TestCase):
    def test_output_extension_maps_ffmpeg_formats(self):
        self.assertEqual(_output_extension('wav'), '.wav')
        self.assertEqual(_output_extension('mp3'), '.mp3')
        self.assertEqual(_output_extension('m4a'), '.m4a')
        self.assertTrue(_format_requires_ffmpeg('mp3'))
        self.assertTrue(_format_requires_ffmpeg('m4a'))
        self.assertFalse(_format_requires_ffmpeg('flac'))

    def test_ffmpeg_output_format_fails_closed_when_ffmpeg_missing(self):
        sr = 8000
        t = np.arange(sr, dtype=np.float64) / sr
        audio = 0.25 * np.sin(2.0 * np.pi * 440.0 * t)
        logs = []
        old_check = sunojump._check_ffmpeg
        sunojump._check_ffmpeg = lambda: False
        try:
            with tempfile.TemporaryDirectory() as tmp:
                input_path = Path(tmp) / 'input.wav'
                output_path = Path(tmp) / 'output.mp3'
                sf.write(input_path, audio, sr)
                proc = AudioProcessor({
                    'strip_metadata': True,
                    'output_format': 'mp3',
                }, log_fn=logs.append, seed=123)

                ok = proc.process(str(input_path), str(output_path))

                self.assertFalse(ok)
                self.assertFalse(output_path.exists())
                self.assertTrue(any("MP3 export requires ffmpeg" in line for line in logs))
        finally:
            sunojump._check_ffmpeg = old_check


class FfmpegEncoderProbeTests(unittest.TestCase):
    def test_missing_encoder_rejects_format_before_render(self):
        sr = 8000
        t = np.arange(sr, dtype=np.float64) / sr
        audio = 0.25 * np.sin(2.0 * np.pi * 440.0 * t)
        logs = []
        old_check = sunojump._check_ffmpeg
        old_encoders = sunojump._ffmpeg_encoders
        sunojump._check_ffmpeg = lambda: True
        sunojump._ffmpeg_encoders = {'mp3': False, 'm4a': True}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                input_path = Path(tmp) / 'input.wav'
                output_path = Path(tmp) / 'output.mp3'
                sf.write(input_path, audio, sr)
                proc = AudioProcessor({
                    'strip_metadata': True,
                    'output_format': 'mp3',
                }, log_fn=logs.append, seed=123)

                ok = proc.process(str(input_path), str(output_path))

                self.assertFalse(ok)
                self.assertFalse(output_path.exists())
                self.assertTrue(any("libmp3lame" in line for line in logs))
        finally:
            sunojump._check_ffmpeg = old_check
            sunojump._ffmpeg_encoders = old_encoders

    def test_available_formats_excludes_unsupported_encoders(self):
        old_check = sunojump._check_ffmpeg
        old_encoders = sunojump._ffmpeg_encoders
        sunojump._check_ffmpeg = lambda: True
        sunojump._ffmpeg_encoders = {'mp3': True, 'm4a': False}
        try:
            formats = sunojump._available_output_formats()
            self.assertIn('mp3', formats)
            self.assertNotIn('m4a', formats)
        finally:
            sunojump._check_ffmpeg = old_check
            sunojump._ffmpeg_encoders = old_encoders

    def test_encoder_available_false_when_ffmpeg_missing(self):
        old_check = sunojump._check_ffmpeg
        old_encoders = sunojump._ffmpeg_encoders
        sunojump._check_ffmpeg = lambda: False
        sunojump._ffmpeg_encoders = None
        try:
            self.assertFalse(sunojump._ffmpeg_encoder_available('mp3'))
            self.assertFalse(sunojump._ffmpeg_encoder_available('m4a'))
        finally:
            sunojump._check_ffmpeg = old_check
            sunojump._ffmpeg_encoders = old_encoders


class AtomicOutputTests(unittest.TestCase):
    def _audio_fixture(self, sr=8000):
        t = np.arange(sr, dtype=np.float64) / sr
        return 0.25 * np.sin(2.0 * np.pi * 440.0 * t), sr

    def _leftovers(self, tmp, input_path):
        return sorted(
            p.name for p in Path(tmp).iterdir()
            if p.name != Path(input_path).name
        )

    def test_soundfile_write_failure_removes_temp_and_final_output(self):
        audio, sr = self._audio_fixture()
        logs = []
        old_write = sunojump.sf.write
        write_targets = []

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / 'input.wav'
            output_path = Path(tmp) / 'output.wav'
            old_write(input_path, audio, sr)

            def fail_output_write(path, *args, **kwargs):
                write_targets.append(str(path))
                Path(path).write_bytes(b'partial output')
                raise RuntimeError("synthetic save failure")

            sunojump.sf.write = fail_output_write
            try:
                ok = AudioProcessor({'strip_metadata': True}, log_fn=logs.append, seed=123).process(
                    str(input_path), str(output_path),
                )
            finally:
                sunojump.sf.write = old_write

            self.assertFalse(ok)
            self.assertFalse(output_path.exists())
            self.assertEqual([], self._leftovers(tmp, input_path))
            self.assertTrue(
                any(Path(path).name.startswith('.output.') for path in write_targets),
                write_targets,
            )

    def test_ffmpeg_failure_removes_temp_and_final_output(self):
        audio, sr = self._audio_fixture()
        logs = []
        old_check = sunojump._check_ffmpeg
        old_encoders = sunojump._ffmpeg_encoders
        old_run = sunojump.subprocess.run

        class FakeResult:
            returncode = 1
            stderr = "synthetic encoder failure"
            stdout = ""

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / 'input.wav'
            output_path = Path(tmp) / 'output.mp3'
            sf.write(input_path, audio, sr)

            def fake_run(cmd, *args, **kwargs):
                Path(cmd[-1]).write_bytes(b'partial mp3')
                return FakeResult()

            sunojump._check_ffmpeg = lambda: True
            sunojump._ffmpeg_encoders = {'mp3': True, 'm4a': True}
            sunojump.subprocess.run = fake_run
            try:
                ok = AudioProcessor({
                    'strip_metadata': True,
                    'output_format': 'mp3',
                }, log_fn=logs.append, seed=123).process(
                    str(input_path), str(output_path),
                )
            finally:
                sunojump._check_ffmpeg = old_check
                sunojump._ffmpeg_encoders = old_encoders
                sunojump.subprocess.run = old_run

            self.assertFalse(ok)
            self.assertFalse(output_path.exists())
            self.assertEqual([], self._leftovers(tmp, input_path))
            self.assertTrue(any("synthetic encoder failure" in line for line in logs), logs)

    def test_cancel_after_save_removes_temp_and_final_output(self):
        audio, sr = self._audio_fixture()
        logs = []
        cancel_event = threading.Event()
        old_write = sunojump.sf.write

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / 'input.wav'
            output_path = Path(tmp) / 'output.wav'
            old_write(input_path, audio, sr)

            def cancel_after_output_write(path, *args, **kwargs):
                result = old_write(path, *args, **kwargs)
                if Path(path).name != input_path.name:
                    cancel_event.set()
                return result

            sunojump.sf.write = cancel_after_output_write
            try:
                ok = AudioProcessor(
                    {'strip_metadata': True},
                    log_fn=logs.append,
                    cancel_event=cancel_event,
                    seed=123,
                ).process(str(input_path), str(output_path))
            finally:
                sunojump.sf.write = old_write

            self.assertFalse(ok)
            self.assertFalse(output_path.exists())
            self.assertEqual([], self._leftovers(tmp, input_path))
            self.assertTrue(any("Cancelled." in line for line in logs), logs)


class ConstellationSelfTestTests(unittest.TestCase):
    def test_constellation_match_is_high_for_identical_audio(self):
        sr = 16000
        t = np.arange(sr * 3, dtype=np.float64) / sr
        audio = (
            0.30 * np.sin(2.0 * np.pi * 440.0 * t)
            + 0.20 * np.sin(2.0 * np.pi * 880.0 * t)
            + 0.10 * np.sin(2.0 * np.pi * 1760.0 * t)
        )
        proc = AudioProcessor({}, seed=123)

        match = proc._compute_constellation_match(audio, audio, sr)

        self.assertGreater(match, 95.0)

    def test_constellation_match_drops_for_different_audio(self):
        sr = 16000
        t = np.arange(sr * 3, dtype=np.float64) / sr
        original = (
            0.30 * np.sin(2.0 * np.pi * 440.0 * t)
            + 0.20 * np.sin(2.0 * np.pi * 880.0 * t)
            + 0.10 * np.sin(2.0 * np.pi * 1760.0 * t)
        )
        processed = (
            0.30 * np.sin(2.0 * np.pi * 523.25 * t)
            + 0.20 * np.sin(2.0 * np.pi * 1046.5 * t)
            + 0.10 * np.sin(2.0 * np.pi * 2093.0 * t)
        )
        proc = AudioProcessor({}, seed=123)

        match = proc._compute_constellation_match(original, processed, sr)

        self.assertLess(match, 50.0)


if __name__ == '__main__':
    unittest.main()
