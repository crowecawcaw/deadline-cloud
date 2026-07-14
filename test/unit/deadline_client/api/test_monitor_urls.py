# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Tests for deadline.client.api._monitor_urls (get_monitor_url and helpers).
"""

from unittest.mock import MagicMock, patch

import pytest

from deadline.client import api, config
from deadline.client.api import get_monitor_url
from deadline.client.api._monitor_urls import (
    _get_job_monitor_url,
    _get_monitor_subdomain,
)
from deadline.client.api._session import AwsCredentialsSource

SUBDOMAIN = "iadproductionsandbox"
REGION = "us-east-1"
FARM_ID = "farm-48235bbdf9a8424bbad7e26c6170b074"
QUEUE_ID = "queue-4847e932013148628fbe9ecb7e29a99c"
JOB_ID = "job-4d8a9716356340bba8f825745f52d05c"
STEP_ID = "step-953199a0081448a690c1b039453e11b7"
TASK_ID = "task-953199a0081448a690c1b039453e11b7-2"

HOST = f"https://{SUBDOMAIN}.{REGION}.deadlinecloud.amazonaws.com"


def test_get_monitor_url_all_farms():
    """With no farm, the URL points at the (non-region-scoped) all-farms list."""
    assert get_monitor_url(SUBDOMAIN, REGION) == f"{HOST}/farms"


def test_get_monitor_url_farm():
    assert get_monitor_url(SUBDOMAIN, REGION, farm_id=FARM_ID) == f"{HOST}/{REGION}/farms/{FARM_ID}"


def test_get_monitor_url_queue():
    assert (
        get_monitor_url(SUBDOMAIN, REGION, farm_id=FARM_ID, queue_id=QUEUE_ID)
        == f"{HOST}/{REGION}/farms/{FARM_ID}/queues/{QUEUE_ID}"
    )


def test_get_monitor_url_job():
    assert (
        get_monitor_url(SUBDOMAIN, REGION, farm_id=FARM_ID, queue_id=QUEUE_ID, job_id=JOB_ID)
        == f"{HOST}/{REGION}/farms/{FARM_ID}/queues/{QUEUE_ID}?jobId={JOB_ID}"
    )


def test_get_monitor_url_step():
    assert (
        get_monitor_url(
            SUBDOMAIN, REGION, farm_id=FARM_ID, queue_id=QUEUE_ID, job_id=JOB_ID, step_id=STEP_ID
        )
        == f"{HOST}/{REGION}/farms/{FARM_ID}/queues/{QUEUE_ID}?jobId={JOB_ID}&stepId={STEP_ID}"
    )


def test_get_monitor_url_task():
    """The full task URL matches the example from the feature request."""
    assert get_monitor_url(
        SUBDOMAIN,
        REGION,
        farm_id=FARM_ID,
        queue_id=QUEUE_ID,
        job_id=JOB_ID,
        step_id=STEP_ID,
        task_id=TASK_ID,
    ) == (
        f"{HOST}/{REGION}/farms/{FARM_ID}/queues/{QUEUE_ID}"
        f"?jobId={JOB_ID}&stepId={STEP_ID}&taskId={TASK_ID}"
    )


def test_get_monitor_url_requires_subdomain():
    with pytest.raises(ValueError, match="subdomain"):
        get_monitor_url("", REGION, farm_id=FARM_ID)


@pytest.mark.parametrize(
    "kwargs, missing",
    [
        (dict(queue_id=QUEUE_ID), "queue_id requires farm_id"),
        (dict(farm_id=FARM_ID, job_id=JOB_ID), "job_id requires queue_id"),
        (dict(farm_id=FARM_ID, queue_id=QUEUE_ID, step_id=STEP_ID), "step_id requires job_id"),
        (
            dict(farm_id=FARM_ID, queue_id=QUEUE_ID, job_id=JOB_ID, task_id=TASK_ID),
            "task_id requires step_id",
        ),
    ],
)
def test_get_monitor_url_hierarchy_violations(kwargs, missing):
    """A lower-level id without its parent is a ValueError (e.g. queue with no farm)."""
    with pytest.raises(ValueError, match=missing):
        get_monitor_url(SUBDOMAIN, REGION, **kwargs)


def test_get_monitor_url_region_defaults_from_config(fresh_deadline_config):
    """When region is omitted it resolves from defaults.farm_region, like elsewhere."""
    config.set_setting("defaults.farm_region", "eu-west-1")
    url = get_monitor_url(SUBDOMAIN, farm_id=FARM_ID)
    assert (
        url
        == f"https://{SUBDOMAIN}.eu-west-1.deadlinecloud.amazonaws.com/eu-west-1/farms/{FARM_ID}"
    )


def test_get_monitor_url_requires_region(fresh_deadline_config):
    """With no explicit region and nothing configured, we raise rather than emit a bad URL."""
    with pytest.raises(ValueError, match="region is required"):
        get_monitor_url(SUBDOMAIN, farm_id=FARM_ID)


def test_get_monitor_url_url_encodes_query_values():
    """Query parameter values are URL-encoded."""
    url = get_monitor_url(
        SUBDOMAIN,
        REGION,
        farm_id=FARM_ID,
        queue_id=QUEUE_ID,
        job_id="job with spaces&x=1",
    )
    assert "jobId=job+with+spaces%26x%3D1" in url


def test_get_monitor_subdomain_returns_none_for_host_creds(fresh_deadline_config):
    """Host-provided credentials have no monitor, so there is no subdomain."""
    with patch.object(
        api._monitor_urls,
        "get_credentials_source",
        return_value=AwsCredentialsSource.HOST_PROVIDED,
    ):
        assert _get_monitor_subdomain() is None


def test_get_monitor_subdomain_calls_get_monitor(fresh_deadline_config):
    """For DCM credentials, the subdomain is read from deadline:GetMonitor."""
    mock_client = MagicMock()
    mock_client.get_monitor.return_value = {"subdomain": SUBDOMAIN, "monitorId": "monitor-abc"}
    with (
        patch.object(
            api._monitor_urls,
            "get_credentials_source",
            return_value=AwsCredentialsSource.DEADLINE_CLOUD_MONITOR_LOGIN,
        ),
        patch.object(api._monitor_urls, "get_monitor_id", return_value="monitor-abc"),
    ):
        assert _get_monitor_subdomain(deadline_client=mock_client) == SUBDOMAIN
    mock_client.get_monitor.assert_called_once_with(monitorId="monitor-abc")


def test_get_job_monitor_url_happy_path(fresh_deadline_config):
    config.set_setting("defaults.farm_region", REGION)
    mock_client = MagicMock()
    mock_client.get_monitor.return_value = {"subdomain": SUBDOMAIN}
    with (
        patch.object(
            api._monitor_urls,
            "get_credentials_source",
            return_value=AwsCredentialsSource.DEADLINE_CLOUD_MONITOR_LOGIN,
        ),
        patch.object(api._monitor_urls, "get_monitor_id", return_value="monitor-abc"),
    ):
        url = _get_job_monitor_url(
            farm_id=FARM_ID, queue_id=QUEUE_ID, job_id=JOB_ID, deadline_client=mock_client
        )
    assert url == f"{HOST}/{REGION}/farms/{FARM_ID}/queues/{QUEUE_ID}?jobId={JOB_ID}"


def test_get_job_monitor_url_none_without_monitor(fresh_deadline_config):
    """Non-monitor credentials yield None (no URL surfaced)."""
    with patch.object(
        api._monitor_urls,
        "get_credentials_source",
        return_value=AwsCredentialsSource.HOST_PROVIDED,
    ):
        assert _get_job_monitor_url(farm_id=FARM_ID, queue_id=QUEUE_ID, job_id=JOB_ID) is None


def test_get_job_monitor_url_swallows_errors(fresh_deadline_config):
    """Any failure while building the URL results in None, never an exception."""
    mock_client = MagicMock()
    mock_client.get_monitor.side_effect = RuntimeError("boom")
    with (
        patch.object(
            api._monitor_urls,
            "get_credentials_source",
            return_value=AwsCredentialsSource.DEADLINE_CLOUD_MONITOR_LOGIN,
        ),
        patch.object(api._monitor_urls, "get_monitor_id", return_value="monitor-abc"),
    ):
        assert (
            _get_job_monitor_url(
                farm_id=FARM_ID, queue_id=QUEUE_ID, job_id=JOB_ID, deadline_client=mock_client
            )
            is None
        )
