# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
tests the deadline.client.api functions relating to queues
"""

from unittest.mock import patch

import pytest

from deadline.client import api

QUEUES_LIST = [
    {
        "queueId": "queue-0123456789abcdef0123456789abcdef",
        "description": "",
        "displayName": "Testing queue",
    },
    {
        "queueId": "queue-0123456789abcdef0123456789abcdeg",
        "description": "With a description!",
        "displayName": "Another queue",
    },
    {
        "queueId": "queue-0123756789abcdef0123456789abcdeg",
        "description": "Described",
        "displayName": "Third queue",
    },
    {
        "queueId": "queue-0123456789abcdef012a456789abcdeg",
        "description": "multiple\nline\ndescription",
        "displayName": "queue six",
    },
    {
        "queueId": "queue-0123456789abcdef0123450789abcaeg",
        "description": "Queue",
        "displayName": "Queue",
    },
]


def test_list_queues_paginated(fresh_deadline_config):
    """Confirm api.list_queues concatenates multiple pages"""
    with patch.object(api._session, "get_boto3_session") as session_mock:
        session_mock().client("deadline").list_queues.side_effect = [
            {"queues": QUEUES_LIST[:2], "nextToken": "abc"},
            {"queues": QUEUES_LIST[2:3], "nextToken": "def"},
            {"queues": QUEUES_LIST[3:]},
        ]

        # Call the API
        queues = api.list_queues()

        assert queues["queues"] == QUEUES_LIST


@pytest.mark.parametrize("pass_principal_id_filter", [True, False])
@pytest.mark.parametrize("user_identities", [True, False])
def test_list_queues_principal_id(fresh_deadline_config, pass_principal_id_filter, user_identities):
    """Confirm api.list_queues sets the principalId parameter appropriately"""

    with patch.object(api._session, "get_boto3_session") as session_mock:
        session_mock().client("deadline").list_queues.side_effect = [
            {"queues": QUEUES_LIST},
        ]
        if user_identities:
            session_mock()._session.get_scoped_config.return_value = {
                "studio_id": "studioid",
                "user_id": "userid",
                "identity_store_id": "idstoreid",
            }

        # Call the API
        if pass_principal_id_filter:
            queues = api.list_queues(principalId="otheruserid")
        else:
            queues = api.list_queues()

        assert queues["queues"] == QUEUES_LIST

        if pass_principal_id_filter:
            session_mock().client("deadline").list_queues.assert_called_once_with(
                principalId="otheruserid"
            )
        elif user_identities:
            session_mock().client("deadline").list_queues.assert_called_once_with(
                principalId="userid"
            )
        else:
            session_mock().client("deadline").list_queues.assert_called_once_with()
