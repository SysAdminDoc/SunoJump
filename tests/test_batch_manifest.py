#!/usr/bin/env python3
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import soundfile as sf
from PyQt6.QtWidgets import QApplication

import batch_manifest
from batch_manifest import (
    BATCH_MANIFEST_SCHEMA_VERSION,
    BatchManifestError,
    BatchManifestStore,
)
from render_results import (
    OutputValidation,
    RenderErrorCode,
    RenderResult,
    RenderState,
)
import sunojump


ROOT = Path(__file__).resolve().parents[1]


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _config():
    config = dict(sunojump.PRESETS["Gentle"])
    config["output_format"] = "wav"
    return config


def _jobs(temp_path, count=1):
    jobs = []
    for index in range(count):
        input_path = temp_path / f"input-{index}.wav"
        input_path.write_bytes(f"input-{index}".encode("ascii"))
        jobs.append({
            "id": f"job-{index}",
            "input_path": str(input_path),
            "effective_seed": index + 10,
        })
    return jobs


class BatchManifestStoreTests(unittest.TestCase):
    def _create(self, temp_path, count=1):
        return BatchManifestStore.create(
            temp_path / "batch.sunojump-batch.json",
            app_version=sunojump.VERSION,
            output_dir=temp_path / "output",
            config=_config(),
            jobs=_jobs(temp_path, count),
        )

    def test_create_persists_atomic_pending_jobs_and_stable_seeds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = self._create(temp_path, count=2)
            loaded = BatchManifestStore.load(store.path)

            self.assertEqual(
                loaded.payload["schema_version"],
                BATCH_MANIFEST_SCHEMA_VERSION,
            )
            self.assertEqual(
                [(job["id"], job["state"], job["effective_seed"])
                 for job in loaded.jobs],
                [
                    ("job-0", "pending", 10),
                    ("job-1", "pending", 11),
                ],
            )
            self.assertEqual(list(temp_path.glob("*.tmp.json")), [])

    def test_per_file_config_and_preset_name_survive_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            jobs = _jobs(temp_path)
            jobs[0]["config"] = {
                **_config(),
                "pitch_range": 1.25,
                "c2pa_policy": sunojump.C2PA_POLICY_BLOCK,
            }
            jobs[0]["preset_name"] = "Custom snapshot"

            store = BatchManifestStore.create(
                temp_path / "batch.sunojump-batch.json",
                app_version=sunojump.VERSION,
                output_dir=temp_path / "output",
                config={
                    **_config(),
                    "c2pa_policy": sunojump.C2PA_POLICY_BLOCK,
                },
                jobs=jobs,
            )
            loaded = BatchManifestStore.load(store.path)

            self.assertEqual(
                loaded.jobs[0]["config"]["pitch_range"],
                1.25,
            )
            self.assertEqual(
                loaded.jobs[0]["preset_name"],
                "Custom snapshot",
            )

    def test_schema_one_manifest_migrates_in_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = self._create(temp_path)
            payload = json.loads(store.path.read_text(encoding="utf-8"))
            payload["schema_version"] = 1
            store.path.write_text(
                json.dumps(payload),
                encoding="utf-8",
            )

            loaded = BatchManifestStore.load(store.path)

            self.assertEqual(
                loaded.payload["schema_version"],
                BATCH_MANIFEST_SCHEMA_VERSION,
            )
            self.assertEqual(loaded.jobs[0]["id"], "job-0")

    def test_invalid_per_file_config_or_preset_name_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = self._create(temp_path)
            variants = []
            invalid_config = deepcopy(store.payload)
            invalid_config["jobs"][0]["config"] = []
            variants.append(invalid_config)
            invalid_name = deepcopy(store.payload)
            invalid_name["jobs"][0]["preset_name"] = ""
            variants.append(invalid_name)

            for payload in variants:
                with self.subTest(payload=payload):
                    with self.assertRaises(BatchManifestError):
                        BatchManifestStore(store.path, payload)

    def test_interrupted_running_job_recovers_to_pending_with_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = self._create(temp_path)
            planned = temp_path / "output" / "input-0_sj.wav"
            store.begin_attempt("job-0", planned)

            recovered = BatchManifestStore.load(store.path)
            notes = recovered.reconcile()
            job = recovered.jobs[0]

            self.assertEqual(job["state"], "pending")
            self.assertEqual(job["attempts"], 1)
            self.assertEqual(job["history"][-1]["state"], "running")
            self.assertNotIn("planned_output_path", job)
            self.assertNotIn("started_at", job)
            self.assertEqual(recovered.select("pending")[0]["id"], "job-0")
            self.assertIn("running -> pending", notes[0])

    def test_reconcile_revalidates_success_hashes_and_retry_keeps_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = self._create(temp_path)
            output = temp_path / "output" / "input-0_sj.wav"
            sidecar = Path(f"{output}.sunojump.json")
            output.parent.mkdir()
            output.write_bytes(b"validated audio")
            sidecar.write_text('{"evidence": true}\n', encoding="utf-8")
            store.begin_attempt("job-0", output)
            store.finish_job(
                "job-0",
                state="succeeded",
                output_path=str(output),
                output_sha256=_digest(output),
                sidecar_path=str(sidecar),
                sidecar_sha256=_digest(sidecar),
                input_sha256="a" * 64,
            )

            self.assertEqual(
                BatchManifestStore.load(store.path).reconcile(),
                [],
            )
            original_bytes = b"changed after success"
            output.write_bytes(original_bytes)
            recovered = BatchManifestStore.load(store.path)
            notes = recovered.reconcile()

            self.assertEqual(recovered.jobs[0]["state"], "failed")
            self.assertEqual(
                recovered.jobs[0]["error_code"],
                "recovery_validation_failed",
            )
            self.assertIn("output SHA-256 mismatch", notes[0])
            self.assertEqual(recovered.select("failed")[0]["id"], "job-0")

            retry_output = temp_path / "output" / "input-0_sj_2.wav"
            recovered.begin_attempt("job-0", retry_output)
            self.assertEqual(output.read_bytes(), original_bytes)
            self.assertEqual(
                recovered.jobs[0]["history"][-1]["state"],
                "failed",
            )
            self.assertEqual(
                recovered.jobs[0]["planned_output_path"],
                str(retry_output.resolve()),
            )

    def test_retry_policies_select_only_requested_terminal_states(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = self._create(temp_path, count=4)
            states = ("pending", "partial", "failed", "cancelled")
            for job, state in zip(store.jobs, states):
                job["state"] = state
            store.save()

            self.assertEqual(
                [job["state"] for job in store.select("pending")],
                ["pending"],
            )
            self.assertEqual(
                [job["state"] for job in store.select("failed")],
                ["partial", "failed"],
            )
            self.assertEqual(
                [job["state"] for job in store.select("cancelled")],
                ["cancelled"],
            )
            self.assertEqual(
                [job["state"] for job in store.select("unfinished")],
                list(states),
            )

    def test_invalid_future_or_duplicate_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = self._create(temp_path, count=2)
            variants = []
            future = deepcopy(store.payload)
            future["schema_version"] += 1
            variants.append(future)
            duplicate = deepcopy(store.payload)
            duplicate["jobs"][1]["id"] = duplicate["jobs"][0]["id"]
            variants.append(duplicate)
            invalid_hash = deepcopy(store.payload)
            invalid_hash["jobs"][0]["output_sha256"] = "not-a-digest"
            variants.append(invalid_hash)

            for payload in variants:
                with self.subTest(payload=payload):
                    with self.assertRaises(BatchManifestError):
                        BatchManifestStore(store.path, payload)

    def test_failed_atomic_replace_preserves_previous_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = self._create(temp_path)
            before = json.loads(store.path.read_text(encoding="utf-8"))
            store.jobs[0]["state"] = "cancelled"

            with mock.patch.object(
                batch_manifest.os,
                "replace",
                side_effect=OSError("simulated replace failure"),
            ):
                with self.assertRaises(BatchManifestError):
                    store.save()

            self.assertEqual(
                json.loads(store.path.read_text(encoding="utf-8")),
                before,
            )
            self.assertEqual(
                list(temp_path.glob(".*.tmp.json")),
                [],
            )

    def test_distinct_jobs_can_update_one_manifest_concurrently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = self._create(temp_path, count=4)

            def update(job_index):
                job_id = f"job-{job_index}"
                output_path = temp_path / f"output-{job_index}.wav"
                store.begin_attempt(job_id, output_path)
                store.finish_job(
                    job_id,
                    state="failed",
                    error_code="decode_failed",
                    message=f"failure {job_index}",
                )

            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(update, range(4)))

            persisted = BatchManifestStore.load(store.path)

        self.assertEqual(
            [job["state"] for job in persisted.jobs],
            ["failed"] * 4,
        )
        self.assertEqual(
            [job["attempts"] for job in persisted.jobs],
            [1] * 4,
        )


class BatchManifestWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cancel_before_start_persists_every_job_as_cancelled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = BatchManifestStore.create(
                temp_path / "batch.sunojump-batch.json",
                app_version=sunojump.VERSION,
                output_dir=temp_path / "output",
                config=_config(),
                jobs=_jobs(temp_path, count=2),
            )
            worker = sunojump.ProcessWorker(
                store.select("pending"),
                _config(),
                store.output_dir,
                manifest_store=store,
            )
            worker.cancel()
            worker.run()

            persisted = BatchManifestStore.load(store.path)
            self.assertEqual(
                [job["state"] for job in persisted.jobs],
                ["cancelled", "cancelled"],
            )
            self.assertEqual(
                [job["attempts"] for job in persisted.jobs],
                [0, 0],
            )

    def test_mixed_worker_outcomes_persist_success_and_failure(self):
        original_processor = sunojump.AudioProcessor

        class FakeProcessor:
            def __init__(self, _params, seed=None, **_kwargs):
                self.seed = seed

            def process(self, input_path, output_path):
                if Path(input_path).stem.endswith("0"):
                    output = Path(output_path)
                    output.write_bytes(b"rendered audio")
                    sidecar = Path(f"{output}.sunojump.json")
                    sidecar.write_text("{}\n", encoding="utf-8")
                    return RenderResult(
                        state=RenderState.SUCCEEDED,
                        input_path=str(input_path),
                        output_path=str(output),
                        validation=OutputValidation(
                            input_sha256=_digest(input_path),
                            output_sha256=_digest(output),
                            output_bytes=output.stat().st_size,
                            sample_rate_hz=8000,
                            channels=1,
                            frames=8000,
                            duration_seconds=1.0,
                            peak=0.25,
                            hashes_distinct=True,
                            decoder="test",
                        ),
                        effective_seed=self.seed,
                        sidecar_path=str(sidecar),
                        sidecar_sha256=_digest(sidecar),
                    )
                return RenderResult(
                    state=RenderState.FAILED,
                    input_path=str(input_path),
                    error_code=RenderErrorCode.DECODE_FAILED,
                    effective_seed=self.seed,
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = BatchManifestStore.create(
                temp_path / "batch.sunojump-batch.json",
                app_version=sunojump.VERSION,
                output_dir=temp_path / "output",
                config=_config(),
                jobs=_jobs(temp_path, count=2),
            )
            worker = sunojump.ProcessWorker(
                store.select("pending"),
                _config(),
                store.output_dir,
                manifest_store=store,
            )
            sunojump.AudioProcessor = FakeProcessor
            try:
                worker.run()
            finally:
                sunojump.AudioProcessor = original_processor

            persisted = BatchManifestStore.load(store.path)
            self.assertEqual(
                [job["state"] for job in persisted.jobs],
                ["succeeded", "failed"],
            )
            self.assertEqual(
                [job["effective_seed"] for job in persisted.jobs],
                [10, 11],
            )
            self.assertTrue(persisted.jobs[0]["output_sha256"])
            self.assertEqual(
                persisted.jobs[1]["error_code"],
                "decode_failed",
            )

    def test_worker_dispatches_each_jobs_own_config(self):
        seen = {}

        class FakeProcessor:
            def __init__(self, params, **_kwargs):
                self.marker = params["marker"]

            def process(self, input_path, _output_path):
                seen[Path(input_path).name] = self.marker
                return RenderResult(
                    state=RenderState.FAILED,
                    input_path=str(input_path),
                    error_code=RenderErrorCode.DECODE_FAILED,
                )

        jobs = [
            {
                "id": "first",
                "input_path": "first.wav",
                "effective_seed": 1,
                "config": {"marker": "gentle"},
            },
            {
                "id": "second",
                "input_path": "second.wav",
                "effective_seed": 2,
                "config": {"marker": "aggressive"},
            },
        ]
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            mock.patch.object(sunojump, "AudioProcessor", FakeProcessor),
        ):
            worker = sunojump.ProcessWorker(
                jobs,
                {"marker": "batch"},
                temp_dir,
                max_workers=2,
            )
            worker.run()

        self.assertEqual(
            seen,
            {"first.wav": "gentle", "second.wav": "aggressive"},
        )

    def test_manifest_write_failure_is_a_typed_worker_failure(self):
        class FailingStore:
            def begin_attempt(self, _job_id, _output_path):
                raise BatchManifestError("disk unavailable")

            def finish_job(self, *_args, **_kwargs):
                raise BatchManifestError("disk unavailable")

        with tempfile.TemporaryDirectory() as temp_dir:
            worker = sunojump.ProcessWorker(
                [("job-0", "input.wav", 42)],
                {"output_format": "wav"},
                temp_dir,
                manifest_store=FailingStore(),
            )
            results = []
            worker.file_done.connect(
                lambda _job_id, result: results.append(result)
            )
            worker.run()

            self.assertEqual(len(results), 1)
            self.assertEqual(
                results[0].error_code,
                RenderErrorCode.MANIFEST_WRITE_FAILED,
            )


class BatchManifestCliIntegrationTests(unittest.TestCase):
    @staticmethod
    def _tone(sr=8000):
        time_axis = np.arange(sr, dtype=np.float64) / sr
        return 0.25 * np.sin(2.0 * np.pi * 440.0 * time_axis)

    def test_failed_hash_recovery_retries_without_overwriting_original(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "input.wav"
            output_dir = temp_path / "output"
            manifest_path = temp_path / "batch.sunojump-batch.json"
            sf.write(input_path, self._tone(), 8000)
            first = subprocess.run(
                [
                    sys.executable,
                    "sunojump.py",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_dir),
                    "--preset",
                    "gentle",
                    "--seed",
                    "7",
                    "--manifest",
                    str(manifest_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            first_manifest = BatchManifestStore.load(manifest_path)
            original_output = Path(first_manifest.jobs[0]["output_path"])
            original_output.write_bytes(b"changed after recorded success")

            resumed = subprocess.run(
                [
                    sys.executable,
                    "sunojump.py",
                    f"--resume={manifest_path}",
                    "--retry",
                    "failed",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertIn("succeeded -> failed", resumed.stderr)
            self.assertEqual(
                original_output.read_bytes(),
                b"changed after recorded success",
            )
            final_manifest = BatchManifestStore.load(manifest_path)
            final_job = final_manifest.jobs[0]
            self.assertEqual(final_job["state"], "succeeded")
            self.assertNotEqual(Path(final_job["output_path"]), original_output)
            self.assertTrue(Path(final_job["output_path"]).is_file())
            self.assertEqual(
                final_job["history"][-1]["error_code"],
                "recovery_validation_failed",
            )


if __name__ == "__main__":
    unittest.main()
