# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Helpers for constructing AWS Deadline Cloud monitor (web console) URLs.

The primary entry point, :func:`get_monitor_url`, is a pure URL *formatter*: it
takes the pieces of a resource path and returns the corresponding monitor URL.
It never makes network calls -- the caller must supply the monitor ``subdomain``
(available from the ``deadline:GetMonitor`` API's ``subdomain``/``url`` fields,
or the Deadline Cloud console under Monitor details).

The full host of a monitor is ``subdomain.Region.deadlinecloud.amazonaws.com``.
Resource paths mirror the routes the monitor web app itself uses:

- All farms:  ``/farms``
- Farm:       ``/<region>/farms/<farm_id>``
- Queue:      ``/<region>/farms/<farm_id>/queues/<queue_id>``
- A job/step/task is selected on the queue page via ``jobId``/``stepId``/``taskId``
  query parameters.
"""

from __future__ import annotations

from configparser import ConfigParser
from typing import Optional
from urllib.parse import quote, urlencode

from botocore.client import BaseClient  # type: ignore[import]

from ._session import (
    AwsCredentialsSource,
    _resolve_region,
    get_boto3_client,
    get_credentials_source,
    get_monitor_id,
)

__all__ = ["get_monitor_url"]

# The parent domain shared by every Deadline Cloud monitor. The full monitor
# host is "<subdomain>.<region>.<_MONITOR_DOMAIN>".
_MONITOR_DOMAIN = "deadlinecloud.amazonaws.com"


def get_monitor_url(
    subdomain: str,
    region: Optional[str] = None,
    farm_id: Optional[str] = None,
    queue_id: Optional[str] = None,
    job_id: Optional[str] = None,
    step_id: Optional[str] = None,
    task_id: Optional[str] = None,
    *,
    config: Optional[ConfigParser] = None,
) -> str:
    """
    Builds the AWS Deadline Cloud monitor URL for a resource.

    This is a pure formatter and makes no network calls. The ``subdomain`` is
    required; it is the monitor-specific label in the host
    ``<subdomain>.<region>.deadlinecloud.amazonaws.com`` and can be read from the
    ``deadline:GetMonitor`` API (its ``subdomain`` field) or the Deadline Cloud
    console.

    The resource identifiers form a hierarchy -- ``queue_id`` requires ``farm_id``,
    ``job_id`` requires ``queue_id``, ``step_id`` requires ``job_id``, and
    ``task_id`` requires ``step_id``. Supplying a lower-level id without its
    parent (for example a ``queue_id`` with no ``farm_id``) raises ``ValueError``.
    With ``farm_id`` omitted the URL points at the "all farms" list.

    Example:
        ```python
        from deadline.client.api import get_monitor_url

        # Queue URL
        get_monitor_url("mymonitor", "us-east-1", farm_id="farm-1234", queue_id="queue-5678")
        # 'https://mymonitor.us-east-1.deadlinecloud.amazonaws.com/us-east-1/farms/farm-1234/queues/queue-5678'

        # Task URL (job + step + task selected on the queue page)
        get_monitor_url(
            "mymonitor", "us-east-1",
            farm_id="farm-1234", queue_id="queue-5678",
            job_id="job-9abc", step_id="step-def0", task_id="task-def0-2",
        )
        ```

    Args:
        subdomain (str): The monitor subdomain (required).
        region (str, optional): The AWS region of the resource. When omitted, it is
            resolved the same way as elsewhere in the client: an explicit value wins,
            otherwise the ``defaults.farm_region`` setting is used.
        farm_id (str, optional): The farm to link to.
        queue_id (str, optional): The queue to link to. Requires ``farm_id``.
        job_id (str, optional): The job to select. Requires ``queue_id``.
        step_id (str, optional): The step to select. Requires ``job_id``.
        task_id (str, optional): The task to select. Requires ``step_id``.
        config (ConfigParser, optional): The AWS Deadline Cloud configuration object
            to use when resolving the region.

    Returns:
        str: The fully-qualified monitor URL.

    Raises:
        ValueError: If ``subdomain`` is empty, the resource hierarchy is violated,
            or no region can be resolved.
    """
    if not subdomain:
        raise ValueError("A monitor subdomain is required to build a monitor URL.")

    # Validate the resource hierarchy: each id requires its parent.
    if queue_id and not farm_id:
        raise ValueError("queue_id requires farm_id to build a monitor URL.")
    if job_id and not queue_id:
        raise ValueError("job_id requires queue_id to build a monitor URL.")
    if step_id and not job_id:
        raise ValueError("step_id requires job_id to build a monitor URL.")
    if task_id and not step_id:
        raise ValueError("task_id requires step_id to build a monitor URL.")

    resolved_region = _resolve_region(config=config, region=region)
    if not resolved_region:
        raise ValueError(
            "A region is required to build a monitor URL. Pass region= explicitly or "
            "configure defaults.farm_region."
        )

    host = f"https://{subdomain}.{resolved_region}.{_MONITOR_DOMAIN}"

    # No farm -> the "all farms" list, which is not region-scoped in the path.
    if not farm_id:
        return f"{host}/farms"

    path = f"/{resolved_region}/farms/{quote(farm_id)}"
    if queue_id:
        path += f"/queues/{quote(queue_id)}"
        # A job/step/task is selected on the queue page via query parameters.
        query = [
            (key, value)
            for key, value in (("jobId", job_id), ("stepId", step_id), ("taskId", task_id))
            if value
        ]
        if query:
            path += "?" + urlencode(query)

    return host + path


def _get_monitor_subdomain(
    config: Optional[ConfigParser] = None,
    deadline_client: Optional[BaseClient] = None,
) -> Optional[str]:
    """
    Best-effort lookup of the monitor subdomain for the current profile.

    Returns the subdomain only when the credentials were written by Deadline Cloud
    monitor (so a monitor exists to link to) and the ``deadline:GetMonitor`` call
    succeeds. Returns ``None`` otherwise. Never raises.
    """
    if get_credentials_source(config=config) != AwsCredentialsSource.DEADLINE_CLOUD_MONITOR_LOGIN:
        return None
    monitor_id = get_monitor_id(config=config)
    if not monitor_id:
        return None
    client = deadline_client or get_boto3_client("deadline", config=config)
    monitor = client.get_monitor(monitorId=monitor_id)
    return monitor.get("subdomain")


def _get_job_monitor_url(
    config: Optional[ConfigParser] = None,
    farm_id: Optional[str] = None,
    queue_id: Optional[str] = None,
    job_id: Optional[str] = None,
    deadline_client: Optional[BaseClient] = None,
) -> Optional[str]:
    """
    Best-effort monitor URL for a just-submitted job.

    Returns ``None`` (rather than raising) whenever the URL can't be built -- for
    example when the credentials didn't come from Deadline Cloud monitor, the
    ``deadline:GetMonitor`` call fails, or a region can't be resolved. This keeps
    URL generation from ever interfering with job submission itself.
    """
    try:
        subdomain = _get_monitor_subdomain(config=config, deadline_client=deadline_client)
        if not subdomain:
            return None
        return get_monitor_url(
            subdomain,
            farm_id=farm_id,
            queue_id=queue_id,
            job_id=job_id,
            config=config,
        )
    except Exception:
        return None
