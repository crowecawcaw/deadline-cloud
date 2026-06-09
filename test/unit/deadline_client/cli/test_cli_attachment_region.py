# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Tests that the `deadline attachment` download/upload commands scope their deadline
client to the configured farm region (multi-region support).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from deadline.client.cli import main
from deadline.client.config import config_file
from deadline.client.cli._groups import attachment_group
from deadline.job_attachments.models import JobAttachmentS3Settings
from deadline.job_attachments.progress_tracker import DownloadSummaryStatistics
from ..shared_constants import MOCK_FARM_ID, MOCK_QUEUE_ID

MOCK_REGION = "eu-central-1"

MOCK_S3_SETTINGS = JobAttachmentS3Settings(s3BucketName="mock-bucket", rootPrefix="MockRootPrefix")


def _write_manifest(tmp_path):
    manifest_path = tmp_path / "abc123_manifest"
    manifest_path.write_text(
        json.dumps({"hashAlg": "xxh128", "manifestVersion": "2023-03-03", "paths": []})
    )
    return str(manifest_path)


@pytest.fixture
def configured_farm_region(fresh_deadline_config):
    config_file.set_setting("defaults.farm_id", MOCK_FARM_ID)
    config_file.set_setting("defaults.queue_id", MOCK_QUEUE_ID)
    config_file.set_setting("defaults.farm_region", MOCK_REGION)
    yield fresh_deadline_config


def test_attachment_download_scopes_deadline_client_to_region(configured_farm_region, tmp_path):
    manifest_path = _write_manifest(tmp_path)

    mock_queue = MagicMock()
    mock_queue.jobAttachmentSettings = MOCK_S3_SETTINGS

    with patch.object(
        attachment_group.api, "get_boto3_session", return_value=MagicMock()
    ), patch.object(attachment_group, "get_queue", return_value=mock_queue), patch.object(
        attachment_group, "get_session_client", return_value=MagicMock()
    ) as mock_get_session_client, patch.object(
        attachment_group.api, "get_queue_user_boto3_session", return_value=MagicMock()
    ), patch.object(
        attachment_group,
        "_attachment_download",
        return_value=DownloadSummaryStatistics(),
    ):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "attachment",
                "download",
                "--manifests",
                manifest_path,
                "--conflict-resolution",
                "SKIP",
            ],
        )

    assert result.exit_code == 0, result.output
    mock_get_session_client.assert_called_once()
    assert mock_get_session_client.call_args.kwargs["region"] == MOCK_REGION


def test_attachment_download_get_queue_uses_region_scoped_session(configured_farm_region, tmp_path):
    """
    The GetQueue that fetches job-attachment S3 settings must run in the farm's region,
    not the session/profile region. We scope it by passing a region-scoped session
    (boto3 resolves the regional endpoint itself), not by hand-building an endpoint URL.
    """
    manifest_path = _write_manifest(tmp_path)

    mock_queue = MagicMock()
    mock_queue.jobAttachmentSettings = MOCK_S3_SETTINGS

    base_session = MagicMock(name="base_session")
    scoped_session = MagicMock(name="scoped_session")

    def _get_session(*args, **kwargs):
        return scoped_session if kwargs.get("region") else base_session

    with patch.object(
        attachment_group.api, "get_boto3_session", side_effect=_get_session
    ) as mock_get_session, patch.object(
        attachment_group, "get_queue", return_value=mock_queue
    ) as mock_get_queue, patch.object(
        attachment_group, "get_session_client", return_value=MagicMock()
    ), patch.object(
        attachment_group.api, "get_queue_user_boto3_session", return_value=MagicMock()
    ), patch.object(
        attachment_group,
        "_attachment_download",
        return_value=DownloadSummaryStatistics(),
    ):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "attachment",
                "download",
                "--manifests",
                manifest_path,
                "--conflict-resolution",
                "SKIP",
            ],
        )

    assert result.exit_code == 0, result.output
    # A region-scoped session was requested for the farm's region...
    assert any(c.kwargs.get("region") == MOCK_REGION for c in mock_get_session.call_args_list)
    # ...and GetQueue ran against that region-scoped session (no manual endpoint URL).
    mock_get_queue.assert_called_once()
    assert mock_get_queue.call_args.kwargs["session"] is scoped_session
    assert "deadline_endpoint_url" not in mock_get_queue.call_args.kwargs


def test_attachment_upload_scopes_deadline_client_to_region(configured_farm_region, tmp_path):
    manifest_path = _write_manifest(tmp_path)

    mock_queue = MagicMock()
    mock_queue.jobAttachmentSettings = MOCK_S3_SETTINGS

    with patch.object(
        attachment_group.api, "get_boto3_session", return_value=MagicMock()
    ), patch.object(attachment_group, "get_queue", return_value=mock_queue), patch.object(
        attachment_group, "get_session_client", return_value=MagicMock()
    ) as mock_get_session_client, patch.object(
        attachment_group.api, "get_queue_user_boto3_session", return_value=MagicMock()
    ), patch.object(attachment_group, "_attachment_upload", return_value=MagicMock()):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["attachment", "upload", "--manifests", manifest_path],
        )

    assert result.exit_code == 0, result.output
    mock_get_session_client.assert_called_once()
    assert mock_get_session_client.call_args.kwargs["region"] == MOCK_REGION


def test_attachment_upload_get_queue_uses_region_scoped_session(configured_farm_region, tmp_path):
    """The upload-path GetQueue must run in the farm's region via a region-scoped session."""
    manifest_path = _write_manifest(tmp_path)

    mock_queue = MagicMock()
    mock_queue.jobAttachmentSettings = MOCK_S3_SETTINGS

    base_session = MagicMock(name="base_session")
    scoped_session = MagicMock(name="scoped_session")

    def _get_session(*args, **kwargs):
        return scoped_session if kwargs.get("region") else base_session

    with patch.object(
        attachment_group.api, "get_boto3_session", side_effect=_get_session
    ) as mock_get_session, patch.object(
        attachment_group, "get_queue", return_value=mock_queue
    ) as mock_get_queue, patch.object(
        attachment_group, "get_session_client", return_value=MagicMock()
    ), patch.object(
        attachment_group.api, "get_queue_user_boto3_session", return_value=MagicMock()
    ), patch.object(attachment_group, "_attachment_upload", return_value=MagicMock()):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["attachment", "upload", "--manifests", manifest_path],
        )

    assert result.exit_code == 0, result.output
    assert any(c.kwargs.get("region") == MOCK_REGION for c in mock_get_session.call_args_list)
    mock_get_queue.assert_called_once()
    assert mock_get_queue.call_args.kwargs["session"] is scoped_session
    assert "deadline_endpoint_url" not in mock_get_queue.call_args.kwargs
