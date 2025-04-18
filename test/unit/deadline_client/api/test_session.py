# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

import os
from unittest import TestCase, mock

from botocore.session import Session as BotocoreSession
from botocore.config import Config

from deadline.client.api._session import get_boto3_client


class TestSession(TestCase):
    """Tests for the _session.py module."""

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch("deadline.client.api._session.get_boto3_session")
    def test_get_boto3_client_sets_deadline_endpoint_from_profile(self, mock_get_session):
        """Test that get_boto3_client sets AWS_ENDPOINT_URL_DEADLINE from profile config."""
        # Setup mock session with a profile that has deadline_endpoint
        mock_session = mock.MagicMock()
        mock_get_session.return_value = mock_session

        mock_botocore_session = mock.MagicMock(spec=BotocoreSession)
        mock_session._session = mock_botocore_session

        # Mock the profile config with a deadline_endpoint
        mock_botocore_session.get_scoped_config.return_value = {
            "deadline_endpoint": "https://deadline.custom-region.amazonaws.com"
        }

        # Mock get_default_client_config
        mock_config = mock.MagicMock(spec=Config)
        with mock.patch(
            "deadline.client.api._session.get_default_client_config", return_value=mock_config
        ):
            # Call the function with deadline service
            get_boto3_client("deadline")

            # Verify that AWS_ENDPOINT_URL_DEADLINE was set correctly
            self.assertEqual(
                os.environ.get("AWS_ENDPOINT_URL_DEADLINE"),
                "https://deadline.custom-region.amazonaws.com",
            )

            # Verify that the client was created with the correct service name
            mock_session.client.assert_called_once_with("deadline", config=mock_config)

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch("deadline.client.api._session.get_boto3_session")
    def test_get_boto3_client_without_deadline_endpoint(self, mock_get_session):
        """Test that get_boto3_client doesn't set AWS_ENDPOINT_URL_DEADLINE when not in profile."""
        # Setup mock session with a profile that doesn't have deadline_endpoint
        mock_session = mock.MagicMock()
        mock_get_session.return_value = mock_session

        mock_botocore_session = mock.MagicMock(spec=BotocoreSession)
        mock_session._session = mock_botocore_session

        # Mock the profile config without a deadline_endpoint
        mock_botocore_session.get_scoped_config.return_value = {"region": "us-west-2"}

        # Mock get_default_client_config
        mock_config = mock.MagicMock(spec=Config)
        with mock.patch(
            "deadline.client.api._session.get_default_client_config", return_value=mock_config
        ):
            # Call the function with deadline service
            get_boto3_client("deadline")

            # Verify that AWS_ENDPOINT_URL_DEADLINE was not set
            self.assertNotIn("AWS_ENDPOINT_URL_DEADLINE", os.environ)

            # Verify that the client was created with the correct service name
            mock_session.client.assert_called_once_with("deadline", config=mock_config)

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch("deadline.client.api._session.get_boto3_session")
    def test_get_boto3_client_non_deadline_service(self, mock_get_session):
        """Test that get_boto3_client doesn't check for deadline_endpoint for non-deadline services."""
        # Setup mock session
        mock_session = mock.MagicMock()
        mock_get_session.return_value = mock_session

        mock_botocore_session = mock.MagicMock(spec=BotocoreSession)
        mock_session._session = mock_botocore_session

        # Mock the profile config with a deadline_endpoint
        mock_botocore_session.get_scoped_config.return_value = {
            "deadline_endpoint": "https://deadline.custom-region.amazonaws.com"
        }

        # Mock get_default_client_config
        mock_config = mock.MagicMock(spec=Config)
        with mock.patch(
            "deadline.client.api._session.get_default_client_config", return_value=mock_config
        ):
            # Call the function with a non-deadline service
            get_boto3_client("s3")

            # Verify that AWS_ENDPOINT_URL_DEADLINE was not set
            self.assertNotIn("AWS_ENDPOINT_URL_DEADLINE", os.environ)

            # Verify that the client was created with the correct service name
            mock_session.client.assert_called_once_with("s3", config=mock_config)

    @mock.patch.dict(
        os.environ,
        {"AWS_ENDPOINT_URL_DEADLINE": "https://existing-endpoint.amazonaws.com"},
        clear=True,
    )
    @mock.patch("deadline.client.api._session.get_boto3_session")
    def test_get_boto3_client_does_not_override_existing_env_var(self, mock_get_session):
        """Test that get_boto3_client does not override an existing AWS_ENDPOINT_URL_DEADLINE env var."""
        # Setup mock session with a profile that has deadline_endpoint
        mock_session = mock.MagicMock()
        mock_get_session.return_value = mock_session

        mock_botocore_session = mock.MagicMock(spec=BotocoreSession)
        mock_session._session = mock_botocore_session

        # Mock the profile config with a deadline_endpoint
        mock_botocore_session.get_scoped_config.return_value = {
            "deadline_endpoint": "https://deadline.new-endpoint.amazonaws.com"
        }

        # Mock get_default_client_config
        mock_config = mock.MagicMock(spec=Config)
        with mock.patch(
            "deadline.client.api._session.get_default_client_config", return_value=mock_config
        ):
            # Call the function with deadline service
            get_boto3_client("deadline")

            # Verify that AWS_ENDPOINT_URL_DEADLINE was not overridden
            self.assertEqual(
                os.environ.get("AWS_ENDPOINT_URL_DEADLINE"),
                "https://existing-endpoint.amazonaws.com",
            )
