# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Tests for the CLI farm commands.
"""

from unittest.mock import MagicMock, patch
import os

import boto3  # type: ignore[import]
from botocore.exceptions import ClientError  # type: ignore[import]
from click.testing import CliRunner

from deadline.client import api, config
from deadline.client.cli import main

MOCK_FARMS_LIST = [
    {
        "farmId": "farm-0123456789abcdef0123456789abcdef",
        "description": "A Description.",
        "displayName": "Testing Farm",
    },
    {
        "farmId": "farm-0123456789abcdef0123456789abcdeg",
        "description": "",
        "displayName": "Another Farm",
    },
]

os.environ["AWS_ENDPOINT_URL_DEADLINE"] = "https://fake-endpoint"


def test_cli_farm_list(fresh_deadline_config, mock_telemetry, monkeypatch):
    """
    Confirm that the CLI interface prints out the expected list of
    farms, given mock data.
    """
    # Scope the multi-region fan-out to a single region for a deterministic listing.
    monkeypatch.setenv("DEADLINE_CLOUD_REGIONS", "us-west-2")
    with patch.object(api._session, "get_boto3_session") as session_mock:
        # Copy the module-level farms: list_farms tags each farm dict with its region
        # in place, which would otherwise pollute the shared MOCK_FARMS_LIST.
        session_mock().client("deadline").list_farms.return_value = {
            "farms": [dict(f) for f in MOCK_FARMS_LIST]
        }

        runner = CliRunner()
        result = runner.invoke(main, ["farm", "list"])

        # The multi-region fan-out tags each farm with its region, and the structured
        # output leads with the region column per the (region, farm_id) convention.
        assert (
            result.output
            == """- region: us-west-2
  farmId: farm-0123456789abcdef0123456789abcdef
  displayName: Testing Farm
- region: us-west-2
  farmId: farm-0123456789abcdef0123456789abcdeg
  displayName: Another Farm

"""
        )
        assert result.exit_code == 0


def test_cli_farm_list_override_profile(fresh_deadline_config, monkeypatch):
    """
    Confirms that the --profile option overrides the option to boto3.Session.
    """
    # Scope the multi-region fan-out to a single region so list_farms makes one call.
    monkeypatch.setenv("DEADLINE_CLOUD_REGIONS", "us-west-2")
    # set the "user identities" property to True so it doesn't probe the boto3.Session
    # for configuration.
    config.set_setting("defaults.aws_profile_name", "NonDefaultProfileName")
    config.set_setting("defaults.aws_profile_name", "DifferentProfileName")

    with patch.object(boto3, "Session") as session_mock:
        session_mock().client("deadline").list_farms.return_value = {
            "farms": [dict(f) for f in MOCK_FARMS_LIST]
        }
        session_mock()._session.get_scoped_config().get.return_value = "some-monitor-id"
        session_mock.reset_mock()

        runner = CliRunner()
        result = runner.invoke(main, ["farm", "list", "--profile", "NonDefaultProfileName"])

        assert result.exit_code == 0
        session_mock.assert_called_with(profile_name="NonDefaultProfileName", region_name=None)
        session_mock().client().list_farms.assert_called_once_with()


def test_cli_farm_list_fans_out_across_regions(fresh_deadline_config, mock_telemetry, monkeypatch):
    """
    With no --region, `farm list` fans out across every Deadline Cloud region and
    annotates each farm with the region it came from.
    """
    monkeypatch.setenv("DEADLINE_CLOUD_REGIONS", "us-west-2,us-east-1")

    def _list_farms(*, config=None, region=None):
        # With region=None the real API fans out across regions; emulate that here by
        # returning one farm per region, each annotated with its region.
        regions = [region] if region else ["us-west-2", "us-east-1"]
        return {
            "farms": [
                {
                    "region": r,
                    "farmId": f"farm-{r.replace('-', '')}",
                    "displayName": f"Farm in {r}",
                }
                for r in regions
            ]
        }

    with patch.object(api, "list_farms", side_effect=_list_farms) as list_farms_mock:
        runner = CliRunner()
        result = runner.invoke(main, ["farm", "list"])

        assert result.exit_code == 0
        # No --region given => list_farms called with region=None to trigger the fan-out.
        assert list_farms_mock.call_args.kwargs["region"] is None
        # Region column appears first and both regions' farms are present.
        assert "region: us-west-2" in result.output
        assert "region: us-east-1" in result.output
        assert "Farm in us-west-2" in result.output
        assert "Farm in us-east-1" in result.output


def test_cli_farm_list_single_region(fresh_deadline_config, mock_telemetry, monkeypatch):
    """
    With --region, `farm list` scopes to exactly that region (no fan-out)."""
    with patch.object(api, "list_farms") as list_farms_mock:
        list_farms_mock.return_value = {
            "farms": [
                {
                    "region": "us-east-1",
                    "farmId": "farm-0123456789abcdef0123456789abcdef",
                    "displayName": "Testing Farm",
                }
            ]
        }

        runner = CliRunner()
        result = runner.invoke(main, ["farm", "list", "--region", "us-east-1"])

        assert result.exit_code == 0
        # The explicit region is passed through to scope the single-region call.
        assert list_farms_mock.call_args.kwargs["region"] == "us-east-1"
        assert "region: us-east-1" in result.output
        assert "Testing Farm" in result.output


def test_cli_farm_list_total_failure(fresh_deadline_config, monkeypatch):
    """
    When every region fails, the fan-out raises a DeadlineOperationError whose
    per-region summary is surfaced by the CLI with a non-zero exit code.
    """
    monkeypatch.setenv("DEADLINE_CLOUD_REGIONS", "us-west-2,us-east-1")
    with patch.object(api._session, "get_boto3_session") as session_mock:
        session_mock().client("deadline").list_farms.side_effect = ClientError(
            {"Error": {"Message": "A botocore client error"}}, "ListFarms"
        )

        runner = CliRunner()
        result = runner.invoke(main, ["farm", "list"])

        assert result.exit_code != 0
        assert "Failed to list farms" in result.output
        assert "us-west-2" in result.output
        assert "us-east-1" in result.output


def test_cli_farm_list_client_error(fresh_deadline_config, monkeypatch):
    # Scope to a single region; with the only region failing, the fan-out treats this
    # as a total failure and surfaces the client error to the CLI.
    monkeypatch.setenv("DEADLINE_CLOUD_REGIONS", "us-west-2")
    with patch.object(api._session, "get_boto3_session") as session_mock:
        session_mock().client("deadline").list_farms.side_effect = ClientError(
            {"Error": {"Message": "A botocore client error"}}, "client error"
        )

        runner = CliRunner()
        result = runner.invoke(main, ["farm", "list"])

        # With every (here, the only) region failing, the multi-region fan-out raises a
        # DeadlineOperationError summarizing each region's cause, which the CLI surfaces.
        assert "Failed to list farms" in result.output
        assert "us-west-2" in result.output
        assert "A botocore client error" in result.output
        assert result.exit_code != 0


def test_cli_farm_list_region_no_farms_is_empty(fresh_deadline_config, mock_telemetry):
    """
    `farm list --region <region-with-no-farms>` exits 0 and prints an empty
    list (an empty result is not an error for a single explicit region).
    """
    with patch.object(api._session, "get_boto3_session") as session_mock:
        session_mock().client("deadline").list_farms.return_value = {"farms": []}

        runner = CliRunner()
        result = runner.invoke(main, ["farm", "list", "--region", "us-east-1"])

        assert result.exit_code == 0, result.output
        # Empty list renders as YAML's empty-list marker, not an error.
        assert result.output.strip() == "[]"


def test_cli_farm_list_partial_failure_exits_zero_with_survivors(
    fresh_deadline_config, mock_telemetry, monkeypatch, caplog
):
    """
    when one region fails during the fan-out, the CLI warns but still exits 0
    with the surviving regions' farms.
    """
    monkeypatch.setenv("DEADLINE_CLOUD_REGIONS", "us-west-2,us-east-1")

    good_client = MagicMock()
    good_client.list_farms.return_value = {
        "farms": [{"farmId": "farm-good", "displayName": "Good Farm"}]
    }
    bad_client = MagicMock()
    bad_client.list_farms.side_effect = ClientError(
        {"Error": {"Message": "region opted out"}}, "ListFarms"
    )
    clients = {"us-west-2": good_client, "us-east-1": bad_client}

    def fake_get_client(service_name, config=None, region=None):
        return clients[region]

    with caplog.at_level("WARNING"), patch.object(
        api._list_apis, "get_boto3_client", side_effect=fake_get_client
    ), patch.object(api._list_apis, "_apply_principal_id_filter"):
        runner = CliRunner()
        result = runner.invoke(main, ["farm", "list"])

    # Surviving region's farm is shown, command still succeeds.
    assert result.exit_code == 0, result.output
    assert "farm-good" in result.output
    assert "us-west-2" in result.output
    # The failure was surfaced as a warning (not fatal).
    assert any(
        "us-east-1" in rec.message and "region opted out" in rec.message for rec in caplog.records
    )


def test_cli_farm_get(fresh_deadline_config, mock_telemetry):
    """
    Confirm that the CLI interface prints out the expected farm, given mock data.
    """
    config.set_setting("defaults.farm_id", "farm-0123456789abcdef0123456789abcdef")

    with patch.object(api._session, "get_boto3_session") as session_mock:
        session_mock().client("deadline").get_farm.return_value = MOCK_FARMS_LIST[0]

        runner = CliRunner()
        result = runner.invoke(main, ["farm", "get"])

        assert (
            result.output
            == """farmId: farm-0123456789abcdef0123456789abcdef
description: A Description.
displayName: Testing Farm

"""
        )
        assert result.exit_code == 0
        session_mock().client("deadline").get_farm.assert_called_once_with(
            farmId="farm-0123456789abcdef0123456789abcdef"
        )


def test_cli_farm_get_override_profile(fresh_deadline_config):
    """
    Confirms that the --profile option overrides the option to boto3.Session.
    """
    # set the farm id for the overridden profile
    config.set_setting("defaults.aws_profile_name", "NonDefaultProfileName")
    config.set_setting("defaults.farm_id", "farm-overriddenid")
    config.set_setting("defaults.aws_profile_name", "DifferentProfileName")

    with patch.object(boto3, "Session") as session_mock:
        session_mock().client("deadline").get_farm.return_value = MOCK_FARMS_LIST[0]
        session_mock.reset_mock()

        runner = CliRunner()
        result = runner.invoke(main, ["farm", "get", "--profile", "NonDefaultProfileName"])

        assert result.exit_code == 0
        session_mock.assert_called_once_with(profile_name="NonDefaultProfileName", region_name=None)
        session_mock().client().get_farm.assert_called_once_with(farmId="farm-overriddenid")


def test_cli_farm_get_no_default_set(fresh_deadline_config):
    """
    Confirm that the CLI interface prints out the expected farm, given mock data.
    """

    with patch.object(api._session, "get_boto3_session") as session_mock:
        session_mock().client("deadline").get_farm.return_value = MOCK_FARMS_LIST[0]

        runner = CliRunner()
        result = runner.invoke(main, ["farm", "get"])

        assert "Missing '--farm-id' or default Farm ID configuration" in result.output
        assert result.exit_code != 0


def test_cli_farm_get_explicit_farm_id(fresh_deadline_config, mock_telemetry):
    """
    Confirm that the CLI interface prints out the expected farm, given mock data.
    """

    with patch.object(api._session, "get_boto3_session") as session_mock:
        session_mock().client("deadline").get_farm.return_value = MOCK_FARMS_LIST[0]

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["farm", "get", "--farm-id", "farm-0123456789abcdef0123456789abcdef"],
        )

        assert (
            result.output
            == """farmId: farm-0123456789abcdef0123456789abcdef
description: A Description.
displayName: Testing Farm

"""
        )
        assert result.exit_code == 0
        session_mock().client("deadline").get_farm.assert_called_once_with(
            farmId="farm-0123456789abcdef0123456789abcdef"
        )
