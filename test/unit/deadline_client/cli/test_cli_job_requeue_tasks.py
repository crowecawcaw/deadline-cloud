# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Tests for the CLI job commands.
"""

import pytest
from unittest.mock import MagicMock, patch, call

import click
from click.testing import CliRunner

from deadline.client.cli import main
from deadline.client.cli._groups import job_group
from deadline.client.config import config_file
from ..shared_constants import (
    MOCK_FARM_ID,
    MOCK_JOB_ID,
    MOCK_QUEUE_ID,
    MOCK_STEP_ID,
)


MOCK_TASK_ID_PREFIX = MOCK_STEP_ID.replace("step-", "task-")


def add_mocks_for_job_requeue_tasks(deadline_mock):
    """
    Adds mock return values to the deadline_mock for sharing across
    the different 'deadline job requeue-tasks' tests.
    """
    # These mock returns only contain the properties that requeue-tasks needs
    deadline_mock.get_job.return_value = {
        "jobId": MOCK_JOB_ID,
        "name": "Mock Job",
        "taskRunStatus": "RUNNING",
        "taskRunStatusCounts": {
            "SUCCEEDED": 1,
            "SUSPENDED": 1,
            "CANCELED": 1,
            "FAILED": 1,
            "NOT_COMPATIBLE": 1,
            "RUNNING": 1,
        },
    }
    deadline_mock.list_steps.return_value = {
        "steps": [
            {
                "stepId": MOCK_STEP_ID,
                "name": "Step Name",
                "taskRunStatus": "RUNNING",
                "taskRunStatusCounts": {
                    "SUCCEEDED": 1,
                    "SUSPENDED": 1,
                    "CANCELED": 1,
                    "FAILED": 1,
                    "NOT_COMPATIBLE": 1,
                    "RUNNING": 1,
                },
            }
        ]
    }
    deadline_mock.list_tasks.return_value = {
        "tasks": [
            {
                "taskId": f"{MOCK_TASK_ID_PREFIX}-0",
                "runStatus": "SUCCEEDED",
                "parameters": {"TestCase": {"string": "SUCCEEDED task"}},
            },
            {
                "taskId": f"{MOCK_TASK_ID_PREFIX}-1",
                "runStatus": "SUSPENDED",
                "parameters": {"TestCase": {"string": "SUSPENDED task"}},
            },
            {
                "taskId": f"{MOCK_TASK_ID_PREFIX}-2",
                "runStatus": "CANCELED",
                "parameters": {"TestCase": {"string": "CANCELED task"}},
            },
            {
                "taskId": f"{MOCK_TASK_ID_PREFIX}-3",
                "runStatus": "FAILED",
                "parameters": {"TestCase": {"string": "FAILED task"}},
            },
            {
                "taskId": f"{MOCK_TASK_ID_PREFIX}-4",
                "runStatus": "NOT_COMPATIBLE",
                "parameters": {"TestCase": {"string": "NOT_COMPATIBLE task"}},
            },
            {
                "taskId": f"{MOCK_TASK_ID_PREFIX}-5",
                "runStatus": "RUNNING",
                "parameters": {"TestCase": {"string": "RUNNING task"}},
            },
        ]
    }
    deadline_mock.update_task.return_value = {}


def test_cli_job_requeue_tasks(fresh_deadline_config, deadline_mock):
    """
    Tests that 'deadline job requeue-tasks' requeues all the tasks of the correct run status.
    """
    add_mocks_for_job_requeue_tasks(deadline_mock)

    with patch.object(click, "confirm") as mock_confirm:
        mock_confirm.return_value = True
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "job",
                "requeue-tasks",
                "--farm-id",
                MOCK_FARM_ID,
                "--queue-id",
                MOCK_QUEUE_ID,
                "--job-id",
                MOCK_JOB_ID,
            ],
        )

    assert (
        result.output
        == f"""Job: Mock Job ({MOCK_JOB_ID})
taskRunStatusCounts:
  SUCCEEDED: 1
  SUSPENDED: 1
  CANCELED: 1
  FAILED: 1
  NOT_COMPATIBLE: 1
  RUNNING: 1

Requeuing all tasks with run status among: CANCELED, FAILED, SUSPENDED
This action will requeue an estimated 3 total tasks (1 SUSPENDED tasks, 1 CANCELED tasks, 1 FAILED tasks)
Requeuing tasks...

Step: Step Name ({MOCK_STEP_ID})
  Requeuing an estimated 3 total tasks (1 SUSPENDED tasks, 1 CANCELED tasks, 1 FAILED tasks)...
    SUSPENDED TestCase=SUSPENDED task ({MOCK_TASK_ID_PREFIX}-1)
    CANCELED TestCase=CANCELED task ({MOCK_TASK_ID_PREFIX}-2)
    FAILED TestCase=FAILED task ({MOCK_TASK_ID_PREFIX}-3)

Requeued a total of 3 tasks.
"""
    )
    mock_confirm.assert_called_once_with(
        "Are you sure you want to requeue these tasks?", default=None
    )
    assert deadline_mock.update_task.call_args_list == [
        call(
            farmId=MOCK_FARM_ID,
            queueId=MOCK_QUEUE_ID,
            jobId=MOCK_JOB_ID,
            stepId=MOCK_STEP_ID,
            taskId=f"{MOCK_TASK_ID_PREFIX}-1",
            targetRunStatus="PENDING",
        ),
        call(
            farmId=MOCK_FARM_ID,
            queueId=MOCK_QUEUE_ID,
            jobId=MOCK_JOB_ID,
            stepId=MOCK_STEP_ID,
            taskId=f"{MOCK_TASK_ID_PREFIX}-2",
            targetRunStatus="PENDING",
        ),
        call(
            farmId=MOCK_FARM_ID,
            queueId=MOCK_QUEUE_ID,
            jobId=MOCK_JOB_ID,
            stepId=MOCK_STEP_ID,
            taskId=f"{MOCK_TASK_ID_PREFIX}-3",
            targetRunStatus="PENDING",
        ),
    ]


def test_cli_job_requeue_tasks_user_says_no(fresh_deadline_config, deadline_mock):
    """
    Tests that 'deadline job requeue-tasks' requeues nothing if the user says "no" to the confirmation prompt.
    """
    add_mocks_for_job_requeue_tasks(deadline_mock)

    with patch.object(click, "confirm") as mock_confirm:
        mock_confirm.return_value = False
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "job",
                "requeue-tasks",
                "--farm-id",
                MOCK_FARM_ID,
                "--queue-id",
                MOCK_QUEUE_ID,
                "--job-id",
                MOCK_JOB_ID,
            ],
        )

    assert (
        result.output
        == f"""Job: Mock Job ({MOCK_JOB_ID})
taskRunStatusCounts:
  SUCCEEDED: 1
  SUSPENDED: 1
  CANCELED: 1
  FAILED: 1
  NOT_COMPATIBLE: 1
  RUNNING: 1

Requeuing all tasks with run status among: CANCELED, FAILED, SUSPENDED
This action will requeue an estimated 3 total tasks (1 SUSPENDED tasks, 1 CANCELED tasks, 1 FAILED tasks)
No tasks were requeued.
"""
    )
    mock_confirm.assert_called_once_with(
        "Are you sure you want to requeue these tasks?", default=None
    )
    assert deadline_mock.update_task.call_args_list == []


@pytest.mark.parametrize(
    "run_status,task_id",
    [
        ("SUCCEEDED", f"{MOCK_TASK_ID_PREFIX}-0"),
        ("SUSPENDED", f"{MOCK_TASK_ID_PREFIX}-1"),
        ("CANCELED", f"{MOCK_TASK_ID_PREFIX}-2"),
        ("FAILED", f"{MOCK_TASK_ID_PREFIX}-3"),
        ("NOT_COMPATIBLE", f"{MOCK_TASK_ID_PREFIX}-4"),
    ],
)
def test_cli_job_requeue_tasks_of_each_status(
    run_status, task_id, fresh_deadline_config, deadline_mock
):
    """
    Tests that 'deadline job requeue-tasks' requeues the one task of each selected run status.
    """
    add_mocks_for_job_requeue_tasks(deadline_mock)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "job",
            "requeue-tasks",
            "--farm-id",
            MOCK_FARM_ID,
            "--queue-id",
            MOCK_QUEUE_ID,
            "--job-id",
            MOCK_JOB_ID,
            "--yes",
            "--run-status",
            run_status,
        ],
    )

    assert (
        result.output
        == f"""Job: Mock Job ({MOCK_JOB_ID})
taskRunStatusCounts:
  SUCCEEDED: 1
  SUSPENDED: 1
  CANCELED: 1
  FAILED: 1
  NOT_COMPATIBLE: 1
  RUNNING: 1

Requeuing all tasks with run status among: {run_status}
Estimated 1 total tasks (1 {run_status} tasks) to requeue.

Step: Step Name ({MOCK_STEP_ID})
  Requeuing an estimated 1 total tasks (1 {run_status} tasks)...
    {run_status} TestCase={run_status} task ({task_id})

Requeued a total of 1 tasks.
"""
    )
    assert deadline_mock.update_task.call_args_list == [
        call(
            farmId=MOCK_FARM_ID,
            queueId=MOCK_QUEUE_ID,
            jobId=MOCK_JOB_ID,
            stepId=MOCK_STEP_ID,
            taskId=task_id,
            targetRunStatus="PENDING",
        ),
    ]


@pytest.mark.parametrize(
    "configured_region,expected_region_name",
    [("eu-west-1", "eu-west-1"), (None, None)],
)
def test_cli_job_requeue_tasks_scopes_requeues_client_to_region(
    configured_region, expected_region_name, fresh_deadline_config, deadline_mock
):
    """
    The requeues client (with the adaptive-retry config) is created with region_name
    scoped to the farm's region when defaults.farm_region is set, while still keeping
    the custom retry config. When no region is configured, region_name is not passed.
    """
    add_mocks_for_job_requeue_tasks(deadline_mock)

    config_file.set_setting("defaults.farm_id", MOCK_FARM_ID)
    config_file.set_setting("defaults.queue_id", MOCK_QUEUE_ID)
    config_file.set_setting("defaults.job_id", MOCK_JOB_ID)
    if configured_region:
        config_file.set_setting("defaults.farm_region", configured_region)

    # Replace the boto3 session used for the requeues client so we can inspect how the
    # client is constructed. The read client (get_job/list_steps/list_tasks) still uses
    # the real, moto-backed path through api.get_boto3_client.
    fake_session = MagicMock()
    with (
        patch.object(job_group.api, "get_boto3_session", return_value=fake_session),
        patch.object(click, "confirm", return_value=True),
    ):
        runner = CliRunner()
        result = runner.invoke(main, ["job", "requeue-tasks", "--run-status", "FAILED"])

    assert result.exit_code == 0, result.output

    # The requeues client must be built off the provided session with the adaptive retry config.
    fake_session.client.assert_called_once()
    client_call = fake_session.client.call_args
    assert client_call.args[0] == "deadline"
    requeues_config = client_call.kwargs["config"]
    assert requeues_config.retries == {"mode": "adaptive", "total_max_attempts": 5}

    if expected_region_name is None:
        assert "region_name" not in client_call.kwargs
    else:
        assert client_call.kwargs["region_name"] == expected_region_name


def test_cli_job_requeue_tasks_multiple_statuses(fresh_deadline_config, deadline_mock):
    """
    Tests that 'deadline job requeue-tasks' requeues all statuses provided with repeated
    --run-status options.
    """
    add_mocks_for_job_requeue_tasks(deadline_mock)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "job",
            "requeue-tasks",
            "--farm-id",
            MOCK_FARM_ID,
            "--queue-id",
            MOCK_QUEUE_ID,
            "--job-id",
            MOCK_JOB_ID,
            "--run-status",
            "CANCELED",
            "--yes",
            "--run-status",
            "FAILED",
        ],
    )

    assert (
        result.output
        == f"""Job: Mock Job ({MOCK_JOB_ID})
taskRunStatusCounts:
  SUCCEEDED: 1
  SUSPENDED: 1
  CANCELED: 1
  FAILED: 1
  NOT_COMPATIBLE: 1
  RUNNING: 1

Requeuing all tasks with run status among: CANCELED, FAILED
Estimated 2 total tasks (1 CANCELED tasks, 1 FAILED tasks) to requeue.

Step: Step Name ({MOCK_STEP_ID})
  Requeuing an estimated 2 total tasks (1 CANCELED tasks, 1 FAILED tasks)...
    CANCELED TestCase=CANCELED task ({MOCK_TASK_ID_PREFIX}-2)
    FAILED TestCase=FAILED task ({MOCK_TASK_ID_PREFIX}-3)

Requeued a total of 2 tasks.
"""
    )
    assert deadline_mock.update_task.call_args_list == [
        call(
            farmId=MOCK_FARM_ID,
            queueId=MOCK_QUEUE_ID,
            jobId=MOCK_JOB_ID,
            stepId=MOCK_STEP_ID,
            taskId=f"{MOCK_TASK_ID_PREFIX}-2",
            targetRunStatus="PENDING",
        ),
        call(
            farmId=MOCK_FARM_ID,
            queueId=MOCK_QUEUE_ID,
            jobId=MOCK_JOB_ID,
            stepId=MOCK_STEP_ID,
            taskId=f"{MOCK_TASK_ID_PREFIX}-3",
            targetRunStatus="PENDING",
        ),
    ]
