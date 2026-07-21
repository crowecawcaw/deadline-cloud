# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
tests the deadline.client.api functions relating to Farms
"""

from unittest.mock import patch

import pytest

from deadline.client import api

FARMS_LIST = [
    {
        "farmId": "farm-0123456789abcdef0123456789abcdef",
        "displayName": "Testing Farm",
        "description": "",
    },
    {
        "farmId": "farm-0123456789abcdef0123456789abcdeg",
        "displayName": "Another Farm",
        "description": "With a description!",
    },
    {
        "farmId": "farm-0123756789abcdef0123456789abcdeg",
        "displayName": "Third Farm",
        "description": "Described",
    },
    {
        "farmId": "farm-0123456789abcdef012a456789abcdeg",
        "displayName": "Farm six",
        "description": "multiple\nline\ndescription",
    },
    {
        "farmId": "farm-0123456789abcdef0123450789abcaeg",
        "displayName": "Farm",
        "description": "Farm",
    },
]


def test_list_farms_paginated(fresh_deadline_config, monkeypatch):
    """Confirm api.list_farms concatenates multiple pages"""
    # Scope to a single region so the multi-region fan-out queries exactly one region,
    # keeping this a focused single-region pagination test.
    monkeypatch.setenv("DEADLINE_CLOUD_REGIONS", "us-west-2")
    with patch.object(api._session, "get_boto3_session") as session_mock:
        session_mock().client("deadline").list_farms.side_effect = [
            {"farms": [dict(f) for f in FARMS_LIST[:2]], "nextToken": "abc"},
            {"farms": [dict(f) for f in FARMS_LIST[2:3]], "nextToken": "def"},
            {"farms": [dict(f) for f in FARMS_LIST[3:]]},
        ]

        # Call the API
        farms = api.list_farms()

        # Farms are tagged with their region (additive), so compare ignoring region.
        returned = [{k: v for k, v in f.items() if k != "region"} for f in farms["farms"]]
        assert returned == FARMS_LIST
        assert all(f["region"] == "us-west-2" for f in farms["farms"])


def test_list_farms_empty_region_uses_fanout(fresh_deadline_config, monkeypatch):
    """An empty-string region must not be forwarded to boto3 as a single-region query.

    ``region=""`` should be treated as "no region given" and fall through to the
    multi-region fan-out (which tags farms with the resolved region), rather than
    taking the single-region path and mislabeling every farm with ``region=""``.
    """
    monkeypatch.setenv("DEADLINE_CLOUD_REGIONS", "us-west-2")
    with patch.object(api._session, "get_boto3_session") as session_mock:
        session_mock().client("deadline").list_farms.side_effect = [
            {"farms": [dict(f) for f in FARMS_LIST]},
        ]

        farms = api.list_farms(region="")

        # Fan-out tags each farm with the resolved region, never the empty string.
        assert all(f["region"] == "us-west-2" for f in farms["farms"])


@pytest.mark.parametrize("pass_principal_id_filter", [True, False])
@pytest.mark.parametrize("user_identities", [True, False])
def test_list_farms_principal_id(
    fresh_deadline_config, pass_principal_id_filter, user_identities, monkeypatch
):
    """Confirm api.list_farms sets the principalId parameter appropriately"""

    # Scope to a single region so the fan-out makes exactly one ListFarms call.
    monkeypatch.setenv("DEADLINE_CLOUD_REGIONS", "us-west-2")
    with patch.object(api._session, "get_boto3_session") as session_mock:
        session_mock().client("deadline").list_farms.side_effect = [
            {"farms": [dict(f) for f in FARMS_LIST]},
        ]

        session_mock()._session.get_scoped_config().get.return_value = "some-monitor-id"
        if user_identities:
            session_mock()._session.get_scoped_config.return_value = {
                "monitor_id": "some-monitor-id",
                "user_id": "userid",
                "identity_store_id": "idstoreid",
            }

        # Call the API
        if pass_principal_id_filter:
            farms = api.list_farms(principalId="otheruserid")
        else:
            farms = api.list_farms()

        returned = [{k: v for k, v in f.items() if k != "region"} for f in farms["farms"]]
        assert returned == FARMS_LIST

        if pass_principal_id_filter:
            session_mock().client("deadline").list_farms.assert_called_once_with(
                principalId="otheruserid"
            )
        elif user_identities:
            session_mock().client("deadline").list_farms.assert_called_once_with(
                principalId="userid"
            )
        else:
            session_mock().client("deadline").list_farms.assert_called_once_with()
