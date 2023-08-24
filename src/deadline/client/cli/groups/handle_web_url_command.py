# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
The `deadline handle-web-url` command.
"""

import sys
import urllib

import click

from ...config import config_file
from ...exceptions import DeadlineOperationError
from .._common import (
    PROMPT_WHEN_COMPLETE,
    apply_cli_options_to_config,
    handle_error,
    prompt_at_completion,
)
from ..deadline_web_url import (
    DEADLINE_URL_SCHEME_NAME,
    install_deadline_web_url_handler,
    parse_query_string,
    uninstall_deadline_web_url_handler,
    validate_resource_ids,
)
from .job_group import download_job_output


@click.command(name="handle-web-url")
@click.argument("url", required=False)
@click.option(
    "--prompt-when-complete",
    default=False,
    is_flag=True,
    help="Prompt for keyboard input when completed.",
)
@click.option(
    "--install",
    default=False,
    is_flag=True,
    help=f"Install this CLI command as the {DEADLINE_URL_SCHEME_NAME}:// URL handler",
)
@click.option(
    "--uninstall",
    default=False,
    is_flag=True,
    help=f"Uninstall this CLI command as the {DEADLINE_URL_SCHEME_NAME}:// URL handler",
)
@click.option(
    "--all-users",
    default=False,
    is_flag=True,
    help="With --install or --uninstall, the installation is for all users.",
)
@handle_error
@click.pass_context
def cli_handle_web_url(
    ctx: click.Context,
    url: str,
    prompt_when_complete: bool,
    install: bool,
    uninstall: bool,
    all_users: bool,
):
    """
    Runs Amazon Deadline Cloud commands sent from a web browser.

    Commands use the deadline:// URL scheme, with expected
    format deadline://<handle-web-url-command>?args=value&args=value.

    This function automatically picks the best AWS profile to use
    based on the provided farm-id and queue-id.

    Current supported commands:
        deadline://download-output
            ?farm-id=<farm-id>
            &queue-id=<queue-id>
            &job-id=<job-id>
            &step-id=<step-id>                      # optional
            &task-id=<task-id>                      # optional
    """
    ctx.obj[PROMPT_WHEN_COMPLETE] = prompt_when_complete

    # Determine whether we're handling a URL or installing/uninstalling the Amazon Deadline Cloud URL handler
    if url:
        if install or uninstall or all_users:
            raise DeadlineOperationError(
                "The --install, --uninstall and --all-users options cannot be used with a provided URL."
            )

        split_url = urllib.parse.urlsplit(url)

        if split_url.scheme != DEADLINE_URL_SCHEME_NAME:
            raise DeadlineOperationError(
                f"URL scheme {split_url.scheme} is not supported. Only {DEADLINE_URL_SCHEME_NAME} is supported."
            )

        # Validate that the command is supp
        if split_url.netloc == "download-output":
            url_queries = parse_query_string(
                split_url.query,
                parameter_names=["farm-id", "queue-id", "job-id", "step-id", "task-id", "profile"],
                required_parameter_names=["farm-id", "queue-id", "job-id"],
            )

            # Validate the IDs
            # We copy the dict without the 'profile' key as that isn't a resource ID
            validate_resource_ids({k: url_queries[k] for k in url_queries.keys() - {"profile"}})

            job_id = url_queries.pop("job_id")
            step_id = url_queries.pop("step_id", None)
            task_id = url_queries.pop("task_id", None)

            # Add the standard option "profile", using the one provided by the url(set by Cloud Companion)
            # or choosing a best guess based on farm and queue IDs
            url_queries["profile"] = url_queries.pop(
                "profile",
                config_file.get_best_profile_for_farm(
                    url_queries["farm_id"], url_queries["queue_id"]
                ),
            )

            # Get a temporary config object with the remaining standard options handled
            config = apply_cli_options_to_config(
                required_options={"farm_id", "queue_id"}, config=None, **url_queries
            )

            farm_id = str(config_file.get_setting("defaults.farm_id", config=config))
            queue_id = str(config_file.get_setting("defaults.queue_id", config=config))

            download_job_output(config, farm_id, queue_id, job_id, step_id, task_id)
        else:
            raise DeadlineOperationError(
                f"Command {split_url.netloc} is not supported through handle-web-url.",
            )
    elif install and uninstall:
        raise DeadlineOperationError(
            "Only one of the --install and --uninstall options may be provided."
        )
    elif install:
        install_deadline_web_url_handler(all_users=all_users)
    elif uninstall:
        uninstall_deadline_web_url_handler(all_users=all_users)
    else:
        raise DeadlineOperationError(
            "At least one of a URL, --install, or --uninstall must be provided."
        )

    prompt_at_completion(ctx)
    sys.exit(0)
