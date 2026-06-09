# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Tests that get_queue_parameter_definitions threads its region argument through to
get_boto3_client so the deadline client is scoped to the farm's region.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from deadline.client.api import _queue_parameters
from ..shared_constants import MOCK_FARM_ID, MOCK_QUEUE_ID


@pytest.mark.parametrize("region", ["ap-southeast-2", None])
def test_get_queue_parameter_definitions_passes_region(region):
    """The region argument is forwarded to get_boto3_client; region=None preserves
    the documented resolution behavior."""
    deadline_client = MagicMock()
    deadline_client.list_queue_environments.return_value = {"environments": []}

    with patch.object(
        _queue_parameters, "get_boto3_client", return_value=deadline_client
    ) as mock_get_boto3_client:
        result = _queue_parameters.get_queue_parameter_definitions(
            region=region,
            farmId=MOCK_FARM_ID,
            queueId=MOCK_QUEUE_ID,
        )

    assert result == []
    mock_get_boto3_client.assert_called_once_with("deadline", config=None, region=region)
