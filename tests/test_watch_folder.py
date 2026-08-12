#!/usr/bin/env python3
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

import sunojump
from render_results import RenderState


class WatchFolderTrackerTests(unittest.TestCase):
    def test_tracker_waits_for_stability_and_reprocesses_modifications(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "drop.wav"
            unsupported = root / "notes.txt"
            audio.write_bytes(b"a")
            unsupported.write_text("ignore", encoding="utf-8")
            tracker = sunojump.WatchFolderTracker(
                root,
                stability_seconds=1.0,
            )

            self.assertEqual(tracker.scan(now=10.0), [])
            self.assertEqual(tracker.scan(now=10.5), [])
            ready = tracker.scan(now=11.0)
            self.assertEqual([Path(path).name for path, _ in ready], ["drop.wav"])
            path, signature = ready[0]
            tracker.mark_submitted(path, signature)
            self.assertEqual(tracker.scan(now=12.0), [])

            audio.write_bytes(b"modified")
            self.assertEqual(tracker.scan(now=13.0), [])
            modified = tracker.scan(now=14.0)
            self.assertEqual(len(modified), 1)
            self.assertNotEqual(modified[0][1], signature)

    def test_tracker_only_scans_top_level_supported_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "nested"
            nested.mkdir()
            (nested / "hidden.wav").write_bytes(b"audio")
            (root / "visible.flac").write_bytes(b"audio")
            tracker = sunojump.WatchFolderTracker(
                root,
                stability_seconds=0.0,
            )

            ready = tracker.scan(now=1.0)

        self.assertEqual(
            [Path(path).name for path, _ in ready],
            ["visible.flac"],
        )


class WatchFolderIntegrationTests(unittest.TestCase):
    def test_stable_drop_produces_validated_output_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            watch_dir = root / "watch"
            output_dir = watch_dir / "output"
            watch_dir.mkdir()
            input_path = watch_dir / "drop.wav"
            samplerate = 8000
            t = np.arange(samplerate, dtype=np.float64) / samplerate
            sf.write(
                input_path,
                0.2 * np.sin(2.0 * np.pi * 440.0 * t),
                samplerate,
                subtype="PCM_16",
            )
            params = dict(sunojump.PRESETS["Moderate"])
            for key in tuple(params):
                if key.endswith("_enabled"):
                    params[key] = False
            params.update({
                "strip_metadata": True,
                "output_format": "wav",
                "c2pa_policy": sunojump.C2PA_POLICY_BLOCK,
            })
            stop = threading.Event()

            def stop_after_evidence():
                deadline = time.monotonic() + 15.0
                while time.monotonic() < deadline:
                    outputs = list(output_dir.glob("*_sj.wav"))
                    if outputs and outputs[0].with_suffix(
                        ".sidecar.json"
                    ).is_file():
                        stop.set()
                        return
                    time.sleep(0.02)
                stop.set()

            stopper = threading.Thread(
                target=stop_after_evidence,
                daemon=True,
            )
            stopper.start()
            batch = sunojump._run_watch_folder_cli(
                watch_dir=watch_dir,
                output_dir=output_dir,
                params=params,
                preset_name="Metadata only",
                workers=1,
                compute_backend="cpu",
                seed=123,
                stop_event=stop,
                poll_seconds=0.02,
                stability_seconds=0.0,
                diagnostic_path=root / "watch.log",
            )
            stopper.join(timeout=2.0)

            outputs = list(output_dir.glob("*_sj.wav"))
            manifests = list(output_dir.glob("*.sunojump-batch.json"))

        self.assertIsNotNone(batch)
        self.assertEqual(batch.state, RenderState.SUCCEEDED)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(len(manifests), 1)

    def test_output_directory_cannot_equal_watch_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "must differ"):
                sunojump._run_watch_folder_cli(
                    watch_dir=temp_dir,
                    output_dir=temp_dir,
                    params={"output_format": "wav"},
                    preset_name="test",
                    workers=1,
                    compute_backend="cpu",
                    stop_event=threading.Event(),
                    max_cycles=1,
                    diagnostic_path=Path(temp_dir) / "watch.log",
                )


if __name__ == "__main__":
    unittest.main()
