# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
All the `deadline fleet` commands.
"""

import click
from botocore.exceptions import ClientError  # type: ignore[import]

from ... import api
from ...config import config_file
from ...exceptions import DeadlineOperationError
from .._common import (
    _apply_cli_options_to_config,
    _cli_object_repr,
    _echo_result,
    _handle_error,
    _output_option,
    _resolve_output_format,
    _suggest_resources_on_client_error,
)
from .._main import deadline as main


@main.group(name="fleet")
@_handle_error
def cli_fleet():
    """
    List available Deadline Cloud fleets or get details of a specific fleet.

    \b
    Learn more about [fleets and workers](https://docs.aws.amazon.com/deadline-cloud/latest/userguide/manage-fleets.html)
    """


@cli_fleet.command(name="list")
@click.option("--profile", help="The AWS profile to use.")
@click.option("--farm-id", help="The farm to use.")
@click.option("--region", help="The AWS region of the farm.")
@_output_option
@_handle_error
def fleet_list(output, **args):
    """
    Lists the available Deadline Cloud fleets in the farm. If the AWS profile
    is created from a Deadline Cloud monitor login, it will list only the
    fleets you have permission to access.
    """
    # Get a temporary config object with the standard options handled
    config = _apply_cli_options_to_config(required_options={"farm_id"}, **args)

    farm_id = config_file.get_setting("defaults.farm_id", config=config)

    try:
        response = api.list_fleets(farmId=farm_id, config=config)
    except ClientError as exc:
        suggestion = _suggest_resources_on_client_error(exc, farm_id=farm_id, config=config)
        raise DeadlineOperationError(
            f"Failed to get Fleets from Deadline:\n{exc}{suggestion}"
        ) from exc

    # Select which fields to print and in which order
    structured_fleet_list = [
        {field: fleet[field] for field in ["fleetId", "displayName"]}
        for fleet in response["fleets"]
    ]

    _echo_result(structured_fleet_list, output)


@cli_fleet.command(name="get")
@click.option("--profile", help="The AWS profile to use.")
@click.option("--farm-id", help="The farm to use.")
@click.option("--fleet-id", help="The fleet to use.")
@click.option(
    "--queue-id", help="If no fleet is provided, gets the fleets associated with this queue."
)
@click.option("--region", help="The AWS region of the farm.")
@_output_option
@_handle_error
def fleet_get(fleet_id, queue_id, output, **args):
    """
    Get the details of a Deadline Cloud fleet in the farm. If no fleet ID is
    provided, it gets the details of all fleets associated with the queue.
    """
    if fleet_id and queue_id:
        raise DeadlineOperationError(
            "Only one of the --fleet-id and --queue-id options may be provided."
        )

    # Get a temporary config object with the standard options handled
    config = _apply_cli_options_to_config(required_options={"farm_id"}, **args)

    farm_id = config_file.get_setting("defaults.farm_id", config=config)
    if not fleet_id:
        queue_id = config_file.get_setting("defaults.queue_id", config=config)
        if not queue_id:
            raise click.UsageError(
                "Missing '--fleet-id', '--queue-id', or default Queue ID configuration"
            )

    deadline = api.get_boto3_client("deadline", config=config)

    if fleet_id:
        try:
            response = deadline.get_fleet(farmId=farm_id, fleetId=fleet_id)
        except ClientError as exc:
            suggestion = _suggest_resources_on_client_error(
                exc, farm_id=farm_id, fleet_id=fleet_id, config=config
            )
            raise DeadlineOperationError(
                f"Failed to get Fleet from Deadline:\n{exc}{suggestion}"
            ) from exc
        response.pop("ResponseMetadata", None)

        _echo_result(response, output)
    else:
        try:
            response = deadline.get_queue(farmId=farm_id, queueId=queue_id)
        except ClientError as exc:
            suggestion = _suggest_resources_on_client_error(
                exc, farm_id=farm_id, queue_id=queue_id, config=config
            )
            raise DeadlineOperationError(
                f"Failed to get Queue from Deadline:\n{exc}{suggestion}"
            ) from exc
        queue_name = response["displayName"]

        response = api._list_apis._call_paginated_deadline_list_api(
            deadline.list_queue_fleet_associations,
            "queueFleetAssociations",
            farmId=farm_id,
            queueId=queue_id,
        )
        response.pop("ResponseMetadata", None)
        qfa_list = response["queueFleetAssociations"]

        fleets = []
        for qfa in qfa_list:
            fleet = deadline.get_fleet(farmId=farm_id, fleetId=qfa["fleetId"])
            fleet.pop("ResponseMetadata", None)
            fleet["queueFleetAssociationStatus"] = qfa["status"]
            fleets.append(fleet)

        if _resolve_output_format(output) == "json":
            # Emit a single structured object rather than a text header plus
            # multiple YAML blocks, so the output parses as one JSON document.
            _echo_result({"queueId": queue_id, "queueName": queue_name, "fleets": fleets}, output)
        else:
            click.echo(
                f"Showing all fleets ({len(qfa_list)} total) associated with queue: {queue_name}"
            )
            for fleet in fleets:
                click.echo("")
                click.echo(_cli_object_repr(fleet))
