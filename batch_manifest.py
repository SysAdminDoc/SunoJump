"""Atomic, schema-versioned batch state for safe resume and retry."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid


BATCH_MANIFEST_SCHEMA_ID = "com.sunojump.batch-manifest"
BATCH_MANIFEST_SCHEMA_VERSION = 1
BATCH_MANIFEST_SUFFIX = ".sunojump-batch.json"

JOB_STATES = {"pending", "running", "succeeded", "partial", "failed", "cancelled"}
TERMINAL_STATES = {"succeeded", "partial", "failed", "cancelled"}
RETRY_POLICIES = {
    "pending": {"pending"},
    "unfinished": {"pending", "partial", "failed", "cancelled"},
    "failed": {"partial", "failed"},
    "cancelled": {"cancelled"},
}


class BatchManifestError(ValueError):
    """Raised when persisted batch state cannot be trusted or updated."""


def _utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _sha256_file(path: str | os.PathLike[str]) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
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


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}.",
        suffix=".tmp.json",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            descriptor = None
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
        _fsync_directory(path.parent)
    except (OSError, TypeError, ValueError) as exc:
        raise BatchManifestError(
            f"cannot write batch manifest {path}: {exc}"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def default_manifest_path(output_dir: str | os.PathLike[str]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(output_dir) / (
        f"SunoJump_Batch_{stamp}_{uuid.uuid4().hex[:8]}"
        f"{BATCH_MANIFEST_SUFFIX}"
    )


class BatchManifestStore:
    def __init__(self, path: str | os.PathLike[str], payload: dict):
        self.path = Path(path).resolve()
        self.payload = payload
        self._validate()

    @classmethod
    def create(
        cls,
        path: str | os.PathLike[str],
        *,
        app_version: str,
        output_dir: str | os.PathLike[str],
        config: dict,
        jobs: list[dict],
    ) -> "BatchManifestStore":
        destination = Path(path).resolve()
        if destination.exists():
            raise BatchManifestError(
                f"batch manifest already exists: {destination}"
            )
        created_at = _utc_timestamp()
        normalized_jobs = []
        for job in jobs:
            normalized_jobs.append({
                "id": str(job["id"]),
                "input_path": str(Path(job["input_path"]).resolve()),
                "effective_seed": job["effective_seed"],
                "state": "pending",
                "attempts": 0,
                "history": [],
                "created_at": created_at,
            })
        payload = {
            "schema_id": BATCH_MANIFEST_SCHEMA_ID,
            "schema_version": BATCH_MANIFEST_SCHEMA_VERSION,
            "batch_id": uuid.uuid4().hex,
            "app_version": str(app_version),
            "created_at": created_at,
            "updated_at": created_at,
            "output_dir": str(Path(output_dir).resolve()),
            "config": deepcopy(config),
            "jobs": normalized_jobs,
        }
        store = cls(destination, payload)
        store.save()
        return store

    @classmethod
    def load(
        cls,
        path: str | os.PathLike[str],
    ) -> "BatchManifestStore":
        manifest_path = Path(path).resolve()
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BatchManifestError(
                f"cannot read batch manifest {manifest_path}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise BatchManifestError("batch manifest root must be an object")
        return cls(manifest_path, payload)

    def _validate(self) -> None:
        payload = self.payload
        if payload.get("schema_id") != BATCH_MANIFEST_SCHEMA_ID:
            raise BatchManifestError("unrecognized batch manifest schema")
        schema_version = payload.get("schema_version")
        if schema_version != BATCH_MANIFEST_SCHEMA_VERSION:
            raise BatchManifestError(
                f"unsupported batch manifest schema version {schema_version}; "
                f"expected {BATCH_MANIFEST_SCHEMA_VERSION}"
            )
        for key in (
            "batch_id",
            "app_version",
            "created_at",
            "updated_at",
        ):
            if not isinstance(payload.get(key), str) or not payload[key]:
                raise BatchManifestError(
                    f"batch manifest requires a non-empty {key}"
                )
        if not isinstance(payload.get("config"), dict):
            raise BatchManifestError("batch manifest config must be an object")
        if (
            not isinstance(payload.get("output_dir"), str)
            or not payload["output_dir"]
        ):
            raise BatchManifestError("batch manifest requires an output directory")
        jobs = payload.get("jobs")
        if not isinstance(jobs, list) or not jobs:
            raise BatchManifestError("batch manifest requires at least one job")
        seen_ids = set()
        for job in jobs:
            if not isinstance(job, dict):
                raise BatchManifestError("batch manifest job must be an object")
            job_id = job.get("id")
            if not isinstance(job_id, str) or not job_id or job_id in seen_ids:
                raise BatchManifestError("batch manifest job IDs must be unique strings")
            seen_ids.add(job_id)
            if (
                not isinstance(job.get("input_path"), str)
                or not job["input_path"]
            ):
                raise BatchManifestError(f"job {job_id} requires an input path")
            seed = job.get("effective_seed")
            if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
                raise BatchManifestError(
                    f"job {job_id} requires a non-negative effective seed"
                )
            if job.get("state") not in JOB_STATES:
                raise BatchManifestError(
                    f"job {job_id} has invalid state {job.get('state')!r}"
                )
            attempts = job.get("attempts")
            if isinstance(attempts, bool) or not isinstance(attempts, int):
                raise BatchManifestError(
                    f"job {job_id} requires an integer attempt count"
                )
            history = job.get("history", [])
            if (
                attempts < 0
                or not isinstance(history, list)
                or not all(isinstance(entry, dict) for entry in history)
            ):
                raise BatchManifestError(f"job {job_id} has invalid history")
            for key in ("output_sha256", "sidecar_sha256", "input_sha256"):
                digest = job.get(key)
                if digest is not None and (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(ch not in "0123456789abcdef" for ch in digest)
                ):
                    raise BatchManifestError(
                        f"job {job_id} has an invalid {key}"
                    )
            for key in (
                "output_path",
                "sidecar_path",
                "planned_output_path",
            ):
                value = job.get(key)
                if value is not None and (
                    not isinstance(value, str) or not value
                ):
                    raise BatchManifestError(
                        f"job {job_id} has an invalid {key}"
                    )
            if job["state"] == "succeeded":
                required_evidence = (
                    "output_path",
                    "output_sha256",
                    "sidecar_path",
                    "sidecar_sha256",
                )
                if any(not job.get(key) for key in required_evidence):
                    raise BatchManifestError(
                        f"successful job {job_id} has incomplete evidence"
                    )

    @property
    def jobs(self) -> list[dict]:
        return self.payload["jobs"]

    @property
    def output_dir(self) -> str:
        return self.payload["output_dir"]

    @property
    def config(self) -> dict:
        return deepcopy(self.payload["config"])

    def save(self) -> None:
        self._validate()
        self.payload["updated_at"] = _utc_timestamp()
        _write_json_atomic(self.path, self.payload)

    def _job(self, job_id: str) -> dict:
        for job in self.jobs:
            if job["id"] == job_id:
                return job
        raise BatchManifestError(f"unknown batch job ID: {job_id}")

    def select(self, policy: str = "pending") -> list[dict]:
        states = RETRY_POLICIES.get(policy)
        if states is None:
            raise BatchManifestError(f"unknown retry policy: {policy}")
        return [
            deepcopy(job)
            for job in self.jobs
            if job["state"] in states
        ]

    def begin_attempt(self, job_id: str, planned_output_path: str) -> None:
        job = self._job(job_id)
        if job["state"] == "succeeded":
            raise BatchManifestError(
                f"successful job {job_id} cannot be retried without reconciliation"
            )
        if job["state"] in TERMINAL_STATES:
            snapshot = {
                key: deepcopy(job[key])
                for key in (
                    "state",
                    "attempts",
                    "started_at",
                    "completed_at",
                    "output_path",
                    "output_sha256",
                    "sidecar_path",
                    "sidecar_sha256",
                    "error_code",
                    "message",
                )
                if key in job
            }
            job.setdefault("history", []).append(snapshot)
        for key in (
            "completed_at",
            "output_path",
            "output_sha256",
            "sidecar_path",
            "sidecar_sha256",
            "error_code",
            "message",
            "recovered_at",
        ):
            job.pop(key, None)
        job["state"] = "running"
        job["attempts"] += 1
        job["started_at"] = _utc_timestamp()
        job["planned_output_path"] = str(Path(planned_output_path).resolve())
        self.save()

    def finish_job(
        self,
        job_id: str,
        *,
        state: str,
        output_path: str | None = None,
        output_sha256: str | None = None,
        sidecar_path: str | None = None,
        sidecar_sha256: str | None = None,
        input_sha256: str | None = None,
        error_code: str | None = None,
        message: str = "",
    ) -> None:
        if state not in TERMINAL_STATES:
            raise BatchManifestError(f"cannot finish a job as {state!r}")
        job = self._job(job_id)
        job["state"] = state
        job["completed_at"] = _utc_timestamp()
        job.pop("planned_output_path", None)
        for key, value in (
            ("output_path", output_path),
            ("output_sha256", output_sha256),
            ("sidecar_path", sidecar_path),
            ("sidecar_sha256", sidecar_sha256),
            ("input_sha256", input_sha256),
            ("error_code", error_code),
        ):
            if value is None:
                job.pop(key, None)
            else:
                job[key] = value
        if message:
            job["message"] = message
        else:
            job.pop("message", None)
        self.save()

    def reconcile(self) -> list[str]:
        notes = []
        changed = False
        for job in self.jobs:
            if job["state"] == "running":
                job.setdefault("history", []).append({
                    key: deepcopy(job[key])
                    for key in (
                        "state",
                        "attempts",
                        "started_at",
                        "planned_output_path",
                    )
                    if key in job
                })
                job["state"] = "pending"
                job["recovered_at"] = _utc_timestamp()
                job["message"] = (
                    "Recovered an interrupted running job; no artifact was overwritten."
                )
                job.pop("started_at", None)
                job.pop("planned_output_path", None)
                notes.append(f"{job['id']}: running -> pending")
                changed = True
                continue
            if job["state"] != "succeeded":
                continue
            failures = []
            for label in ("output", "sidecar"):
                path = job.get(f"{label}_path")
                expected = job.get(f"{label}_sha256")
                if not path or not expected:
                    failures.append(f"{label} evidence is incomplete")
                    continue
                try:
                    actual = _sha256_file(path)
                except OSError as exc:
                    failures.append(f"{label} is unavailable: {exc}")
                    continue
                if actual != expected:
                    failures.append(
                        f"{label} SHA-256 mismatch "
                        f"(expected {expected}, found {actual})"
                    )
            if failures:
                job["state"] = "failed"
                job["error_code"] = "recovery_validation_failed"
                job["message"] = "; ".join(failures)
                job["recovered_at"] = _utc_timestamp()
                notes.append(f"{job['id']}: succeeded -> failed ({job['message']})")
                changed = True
        if changed:
            self.save()
        return notes

    @property
    def counts(self) -> dict[str, int]:
        counts = Counter(job["state"] for job in self.jobs)
        return {state: counts[state] for state in sorted(JOB_STATES)}
