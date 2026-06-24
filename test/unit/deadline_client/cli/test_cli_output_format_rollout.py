# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Tests for the CLI-wide JSON output rollout and the non-interactive confirmation
contract (see AGENTS.md "CLI output & confirmation contract").

Covers:
  * The shared ``_echo_result`` and ``_confirm_or_abort`` helpers.
  * ``--output json`` on the read commands (farm/fleet/queue/worker/job list+get).
  * The destructive-confirmation contract on ``job cancel`` / ``job requeue-tasks``
    in non-interactive (json) mode.

As in ``test_cli_output_format.py``, the autouse ``stdout_is_tty`` fixture from
``conftest.py`` pins stdout to a TTY; these tests patch ``_stdout_is_tty`` to
``False`` to exercise the non-interactive path.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from deadline.client import api, config
from deadline.client.cli import main
from deadline.client.cli import _common

from ..shared_constants import MOCK_FARM_ID, MOCK_QUEUE_ID

MOCK_JOB_ID = "job-0123456789abcdefabcdefabcdefabcd"


def _non_tty():
    return patch.object(_common, "_stdout_is_tty", return_value=False)


def _tty():
    return patch.object(_common, "_stdout_is_tty", return_value=True)


# ---------------------------------------------------------------------------
# _echo_result
# ---------------------------------------------------------------------------


def test_echo_result_json_emits_single_document(capsys):
    obj = {"a": 1, "items": [{"b": 2}]}
    _common._echo_result(obj, "json")
    out = capsys.readouterr().out
    assert json.loads(out) == obj


def test_echo_result_verbose_emits_yaml(capsys):
    _common._echo_result({"a": 1}, "verbose")
    out = capsys.readouterr().out
    # YAML, not JSON.
    assert "a: 1" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_echo_result_json_serializes_unknown_types_with_str(capsys):
    import datetime

    obj = {"createdAt": datetime.datetime(2023, 1, 1, tzinfo=datetime.timezone.utc)}
    _common._echo_result(obj, "json")
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["createdAt"].startswith("2023-01-01")


# ---------------------------------------------------------------------------
# _confirm_or_abort
# ---------------------------------------------------------------------------


def test_confirm_or_abort_auto_accept_proceeds():
    # auto_accept short-circuits regardless of tty/output.
    with _non_tty():
        _common._confirm_or_abort("ok?", output="json", auto_accept=True)  # no raise


def test_confirm_or_abort_non_interactive_destructive_errors(capsys):
    with _non_tty():
        with pytest.raises(SystemExit) as exc:
            _common._confirm_or_abort("ok?", output="json", auto_accept=False, destructive=True)
    assert exc.value.code == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["status"] == "error"
    assert "--yes" in parsed["error"]


def test_confirm_or_abort_non_interactive_warning_proceeds():
    with _non_tty():
        # destructive=False (a warning) is auto-accepted non-interactively.
        _common._confirm_or_abort("fyi?", output="json", auto_accept=False, destructive=False)


def test_confirm_or_abort_interactive_prompts_and_proceeds_on_yes():
    with _tty():
        with patch.object(_common.click, "confirm", return_value=True) as mock_confirm:
            _common._confirm_or_abort("ok?", output="verbose", auto_accept=False)
        mock_confirm.assert_called_once()


def test_confirm_or_abort_interactive_aborts_on_no(capsys):
    with _tty():
        with patch.object(_common.click, "confirm", return_value=False):
            with pytest.raises(SystemExit) as exc:
                _common._confirm_or_abort(
                    "ok?", output="verbose", auto_accept=False, abort_message="Nope."
                )
    assert exc.value.code == 1
    assert "Nope." in capsys.readouterr().out


# ---------------------------------------------------------------------------
# read commands: farm / fleet / queue / worker
# ---------------------------------------------------------------------------


def _mock_deadline_client():
    """Patches api.get_boto3_client and returns the mock client."""
    client = MagicMock()
    return patch.object(api, "get_boto3_client", return_value=client), client


def test_farm_list_json(fresh_deadline_config):
    with patch.object(api, "list_farms") as mock_list:
        mock_list.return_value = {
            "farms": [{"region": "us-west-2", "farmId": "farm-1", "displayName": "F1"}]
        }
        with _non_tty():
            result = CliRunner().invoke(main, ["farm", "list"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed == [{"region": "us-west-2", "farmId": "farm-1", "displayName": "F1"}]


def test_farm_get_json(fresh_deadline_config):
    patcher, client = _mock_deadline_client()
    with patcher:
        client.get_farm.return_value = {
            "farmId": "farm-1",
            "displayName": "F1",
            "ResponseMetadata": {"x": 1},
        }
        with _non_tty():
            result = CliRunner().invoke(main, ["farm", "get", "--farm-id", "farm-1"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["farmId"] == "farm-1"
    # ResponseMetadata is stripped before printing.
    assert "ResponseMetadata" not in parsed


def test_queue_list_json(fresh_deadline_config):
    with patch.object(api, "list_queues") as mock_list:
        mock_list.return_value = {"queues": [{"queueId": "queue-1", "displayName": "Q1"}]}
        with _non_tty():
            result = CliRunner().invoke(main, ["queue", "list", "--farm-id", MOCK_FARM_ID])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [{"queueId": "queue-1", "displayName": "Q1"}]


def test_worker_list_json_uses_envelope(fresh_deadline_config):
    patcher, client = _mock_deadline_client()
    with patcher:
        client.search_workers.return_value = {
            "workers": [{"workerId": "worker-1", "status": "IDLE", "createdAt": "2023-01-01"}],
            "totalResults": 1,
        }
        with _non_tty():
            result = CliRunner().invoke(
                main,
                ["worker", "list", "--farm-id", MOCK_FARM_ID, "--fleet-id", "fleet-1"],
            )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    # Pagination metadata is folded into a single object.
    assert parsed["totalResults"] == 1
    assert parsed["workers"][0]["workerId"] == "worker-1"


def test_fleet_get_by_queue_json_uses_envelope(fresh_deadline_config):
    config.set_setting("defaults.farm_id", MOCK_FARM_ID)
    config.set_setting("defaults.queue_id", MOCK_QUEUE_ID)
    patcher, client = _mock_deadline_client()
    with patcher:
        client.get_queue.return_value = {"displayName": "Q1"}
        client.get_fleet.return_value = {"fleetId": "fleet-1", "displayName": "Fleet 1"}
        with patch.object(
            api._list_apis,
            "_call_paginated_deadline_list_api",
            return_value={"queueFleetAssociations": [{"fleetId": "fleet-1", "status": "ACTIVE"}]},
        ):
            with _non_tty():
                result = CliRunner().invoke(main, ["fleet", "get"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["queueName"] == "Q1"
    assert parsed["fleets"][0]["fleetId"] == "fleet-1"
    assert parsed["fleets"][0]["queueFleetAssociationStatus"] == "ACTIVE"


def test_worker_get_verbose_still_yaml(fresh_deadline_config):
    patcher, client = _mock_deadline_client()
    with patcher:
        client.get_worker.return_value = {"workerId": "worker-1", "status": "IDLE"}
        with _tty():
            result = CliRunner().invoke(
                main,
                [
                    "worker",
                    "get",
                    "--farm-id",
                    MOCK_FARM_ID,
                    "--fleet-id",
                    "fleet-1",
                    "--worker-id",
                    "worker-1",
                ],
            )
    assert result.exit_code == 0, result.output
    assert "workerId: worker-1" in result.output


# ---------------------------------------------------------------------------
# job list / get
# ---------------------------------------------------------------------------


def test_job_list_json_uses_envelope(fresh_deadline_config):
    config.set_setting("defaults.farm_id", MOCK_FARM_ID)
    config.set_setting("defaults.queue_id", MOCK_QUEUE_ID)
    patcher, client = _mock_deadline_client()
    with patcher:
        client.search_jobs.return_value = {
            "jobs": [
                {
                    "name": "Job 1",
                    "jobId": MOCK_JOB_ID,
                    "taskRunStatus": "SUCCEEDED",
                    "taskRunStatusCounts": {},
                }
            ],
            "totalResults": 1,
        }
        with _non_tty():
            result = CliRunner().invoke(main, ["job", "list"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["totalResults"] == 1
    assert parsed["jobs"][0]["jobId"] == MOCK_JOB_ID


def test_job_get_json(fresh_deadline_config):
    config.set_setting("defaults.farm_id", MOCK_FARM_ID)
    config.set_setting("defaults.queue_id", MOCK_QUEUE_ID)
    config.set_setting("defaults.job_id", MOCK_JOB_ID)
    patcher, client = _mock_deadline_client()
    with patcher:
        client.get_job.return_value = {
            "jobId": MOCK_JOB_ID,
            "name": "Job 1",
            "taskRunStatusCounts": {},
            "ResponseMetadata": {"x": 1},
        }
        with _non_tty():
            result = CliRunner().invoke(main, ["job", "get"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["jobId"] == MOCK_JOB_ID
    assert "estimatedTimeRemaining" in parsed
    assert "ResponseMetadata" not in parsed


# ---------------------------------------------------------------------------
# job cancel: destructive confirmation contract
# ---------------------------------------------------------------------------


def _setup_cancel(client):
    client.get_job.return_value = {
        "name": "Job 1",
        "jobId": MOCK_JOB_ID,
        "taskRunStatus": "RUNNING",
        "taskRunStatusCounts": {"RUNNING": 1},
    }


def test_job_cancel_json_without_yes_errors_and_does_not_cancel(fresh_deadline_config):
    config.set_setting("defaults.farm_id", MOCK_FARM_ID)
    config.set_setting("defaults.queue_id", MOCK_QUEUE_ID)
    config.set_setting("defaults.job_id", MOCK_JOB_ID)
    patcher, client = _mock_deadline_client()
    with patcher:
        _setup_cancel(client)
        with _non_tty():
            result = CliRunner().invoke(main, ["job", "cancel"])
    assert result.exit_code == 1, result.output
    parsed = json.loads(result.output)
    assert parsed["status"] == "error"
    # The binding action must NOT have been performed.
    client.update_job.assert_not_called()


def test_job_cancel_json_with_yes_proceeds(fresh_deadline_config):
    config.set_setting("defaults.farm_id", MOCK_FARM_ID)
    config.set_setting("defaults.queue_id", MOCK_QUEUE_ID)
    config.set_setting("defaults.job_id", MOCK_JOB_ID)
    patcher, client = _mock_deadline_client()
    with patcher:
        _setup_cancel(client)
        with _non_tty():
            result = CliRunner().invoke(main, ["job", "cancel", "--yes"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["status"] == "submitted"
    assert parsed["jobId"] == MOCK_JOB_ID
    client.update_job.assert_called_once()


# ---------------------------------------------------------------------------
# job requeue-tasks: destructive confirmation contract
# ---------------------------------------------------------------------------


def _setup_requeue(client):
    client.get_job.return_value = {
        "name": "Job 1",
        "jobId": MOCK_JOB_ID,
        "taskRunStatusCounts": {"FAILED": 1},
    }
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"steps": [{"name": "Step", "stepId": "step-1", "taskRunStatusCounts": {"FAILED": 1}}]}
    ]
    client.get_paginator.return_value = paginator


def test_job_requeue_json_without_yes_errors(fresh_deadline_config):
    config.set_setting("defaults.farm_id", MOCK_FARM_ID)
    config.set_setting("defaults.queue_id", MOCK_QUEUE_ID)
    config.set_setting("defaults.job_id", MOCK_JOB_ID)
    with (
        patch.object(api, "get_boto3_client") as mock_client,
        patch.object(api, "get_boto3_session"),
    ):
        client = mock_client.return_value
        _setup_requeue(client)
        with _non_tty():
            result = CliRunner().invoke(main, ["job", "requeue-tasks"])
    assert result.exit_code == 1, result.output
    parsed = json.loads(result.output)
    assert parsed["status"] == "error"


def test_job_requeue_json_with_yes_emits_result(fresh_deadline_config):
    config.set_setting("defaults.farm_id", MOCK_FARM_ID)
    config.set_setting("defaults.queue_id", MOCK_QUEUE_ID)
    config.set_setting("defaults.job_id", MOCK_JOB_ID)
    with (
        patch.object(api, "get_boto3_client") as mock_client,
        patch.object(api, "get_boto3_session") as mock_session,
    ):
        client = mock_client.return_value
        _setup_requeue(client)
        # The task pagination for the per-step requeue loop.
        task_paginator = MagicMock()
        task_paginator.paginate.return_value = [
            {"tasks": [{"taskId": "task-1", "runStatus": "FAILED", "parameters": {}}]}
        ]

        def get_paginator(name):
            if name == "list_steps":
                return client.get_paginator.return_value
            return task_paginator

        client.get_paginator.side_effect = get_paginator
        mock_session.return_value.client.return_value = MagicMock()
        with _non_tty():
            result = CliRunner().invoke(main, ["job", "requeue-tasks", "--yes"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["status"] == "submitted"
    assert parsed["tasksRequeued"] == 1
