# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""End-to-end tests for `deadline job download-output` and `queue sync-output`."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from deadline.job_attachments.asset_manifests.hash_algorithms import HashAlgorithm, hash_data

# `deadline queue sync-output` requires Python >= 3.9 at the CLI level.
requires_py39 = pytest.mark.skipif(
    sys.version_info < (3, 9),
    reason="`deadline queue sync-output` itself requires Python >= 3.9",
)


def test_cli_job_download_output(seeded_farm_queue, run_cli, s3_client, tmp_path):
    from _constants import BUCKET, ROOT_PREFIX

    backend, farm_id, queue_id, env = seeded_farm_queue
    job_id = "job-fedcba9876543210fedcba9876543210"
    step_id = "step-fedcba9876543210fedcba9876543210"
    task_id = "task-fedcba9876543210fedcba9876543210-0"
    asset_root = str(tmp_path / "outputs")
    Path(asset_root).mkdir()

    content = b"rendered-output"
    file_hash = hash_data(content, HashAlgorithm.XXH128)
    s3_client.put_object(Bucket=BUCKET, Key=f"{ROOT_PREFIX}/Data/{file_hash}.xxh128", Body=content)

    manifest_body = json.dumps(
        {
            "hashAlg": "xxh128",
            "manifestVersion": "2023-03-03",
            "paths": [
                {"hash": file_hash, "mtime": 1234000000, "path": "result.txt", "size": len(content)}
            ],
            "totalSize": len(content),
        }
    ).encode()
    manifest_key = (
        f"{ROOT_PREFIX}/Manifests/{farm_id}/{queue_id}/{job_id}/{step_id}/{task_id}/"
        f"sessionaction-0/outputmanifestv2023-03-03_output"
    )
    s3_client.put_object(
        Bucket=BUCKET,
        Key=manifest_key,
        Body=manifest_body,
        Metadata={"asset-root": asset_root},
    )

    backend.jobs[(farm_id, queue_id, job_id)] = {
        "jobId": job_id,
        "name": "mock-job",
        "lifecycleStatus": "CREATE_COMPLETE",
        "lifecycleStatusMessage": "",
        "priority": 50,
        "createdAt": backend._now(),
        "createdBy": "tester",
        "taskRunStatus": "READY",
        "attachments": {
            "manifests": [
                {
                    "rootPath": asset_root,
                    "rootPathFormat": "windows" if os.name == "nt" else "posix",
                }
            ],
            "fileSystem": "COPIED",
        },
    }

    r = run_cli(
        env,
        "job",
        "download-output",
        "--job-id",
        job_id,
        "--conflict-resolution",
        "OVERWRITE",
        "--yes",
    )
    assert r.returncode == 0, r.stderr or r.stdout
    assert (Path(asset_root) / "result.txt").read_text() == "rendered-output"


@requires_py39
def test_cli_queue_sync_output_requires_storage_profile(seeded_farm_queue, run_cli):
    _, _, _, env = seeded_farm_queue
    r = run_cli(env, "queue", "sync-output")
    # With no --storage-profile-id and no --ignore-storage-profiles, the command errors out.
    assert r.returncode != 0
    combined = r.stdout + r.stderr
    assert "storage profile" in combined.lower()


@requires_py39
def test_cli_queue_sync_output_ignore_storage_profiles(seeded_farm_queue, run_cli, tmp_path):
    _, _, _, env = seeded_farm_queue
    checkpoint_dir = tmp_path / "checkpoint"
    r = run_cli(
        env,
        "queue",
        "sync-output",
        "--ignore-storage-profiles",
        "--checkpoint-dir",
        str(checkpoint_dir),
        "--dry-run",
    )
    # No jobs to process; the command exits cleanly.
    assert r.returncode == 0, r.stderr or r.stdout
