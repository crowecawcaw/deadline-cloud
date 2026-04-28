# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""UI tests for ``deadline bundle gui-submit`` — submission flows.

Covers manual test cases:
  * "Submit a render job using `deadline bundle gui-submit`" — job bundle
    submits successfully and the mock backend records a CreateJob call.
  * "Submit a render job using --output json (success)" — stdout contains
    `{"status":"SUBMITTED","jobId":...}` after clicking Submit + Ok.
  * "Submit a render job using --output json (cancel)" — stdout contains
    `{"status":"CANCELED"}` after clicking Submit then Cancel.
  * "Submit a render job using a submitter name" — window title reflects
    `--submitter-name Testing` and the process exits after Submit + Ok.
"""

from __future__ import annotations

import json
import os

import pytest

from helpers import FARM_RESOLVE_TIMEOUT, SubmitterDialog

SAMPLE_TEMPLATE = {
    "specificationVersion": "jobtemplate-2023-09",
    "name": "Test Render Job",
    "description": "A test job for UI verification",
    "steps": [
        {
            "name": "RenderStep",
            "script": {
                "actions": {"onRun": {"command": "bash", "args": ["-c", "echo hello"]}},
            },
        }
    ],
}


@pytest.fixture
def bundle_dir(tmp_path) -> str:
    d = tmp_path / "bundle"
    d.mkdir()
    (d / "template.json").write_text(json.dumps(SAMPLE_TEMPLATE))
    return str(d)


@pytest.fixture
def submitter_env(deadline_env, tmp_path) -> dict:
    """Seed a farm + queue and point the deadline config at them."""
    backend, env = deadline_env
    farm = backend.create_farm(displayName="TestFarm", description="")
    queue = backend.create_queue(farmId=farm["farmId"], displayName="TestQueue", description="")

    job_history_dir = tmp_path / "job_history"
    config = env["DEADLINE_CONFIG_FILE_PATH"]
    with open(config, "w") as f:
        f.write(
            "[defaults]\n"
            "aws_profile_name = (default)\n"
            "\n"
            "[profile-(default) defaults]\n"
            f"farm_id = {farm['farmId']}\n"
            "\n"
            f"[profile-(default) {farm['farmId']} defaults]\n"
            f"queue_id = {queue['queueId']}\n"
            "\n"
            "[profile-(default) settings]\n"
            f"job_history_dir = {job_history_dir}\n"
        )

    env["_JOB_HISTORY_DIR"] = str(job_history_dir)
    return env


def _wait_farm_resolved(app: SubmitterDialog) -> None:
    """Block until the async farm/queue refresh has populated the UI.

    Without this, Submit is disabled (it requires ``api_availability`` to
    be ``True`` plus farm/queue configured).
    """
    try:
        app.locator(
            'group[name="Deadline Cloud settings"] static_text[name="TestFarm"]'
        ).wait_attached(timeout=FARM_RESOLVE_TIMEOUT)
    except Exception:
        app.dump_tree()
        raise


class TestSubmitJobSuccess:
    """A successful submission hits the mock backend and writes a bundle."""

    def test_submit_creates_job_history_bundle(
        self, bundle_dir, submitter_env, deadline_env
    ) -> None:
        backend, _ = deadline_env
        job_history_dir = submitter_env["_JOB_HISTORY_DIR"]
        with SubmitterDialog.open(bundle_dir, env=submitter_env) as app:
            _wait_farm_resolved(app)
            app.submit_and_ok()

        assert backend.call_counts.get("CreateJob", 0) == 1, backend.call_counts
        assert os.path.isdir(job_history_dir)
        templates: list[str] = []
        for root, _dirs, files in os.walk(job_history_dir):
            for fn in files:
                if fn.startswith("template."):
                    templates.append(os.path.join(root, fn))
        assert templates, "No template file found in job-history bundle"


class TestOutputJsonSuccess:
    """--output json prints SUBMITTED JSON after a successful submission."""

    def test_json_output_contains_submitted_status_and_job_id(
        self, bundle_dir, submitter_env, deadline_env
    ) -> None:
        backend, _ = deadline_env
        with SubmitterDialog.open(
            bundle_dir,
            env=submitter_env,
            extra_args=["--output", "json"],
            capture_stdio=True,
        ) as app:
            _wait_farm_resolved(app)
            app.submit_and_ok()
            # With no --submitter-name, the submitter does not auto-close
            # on success; close it ourselves to drain stdout.
            app.close("Cancel")
            stdout, _ = app.proc.communicate(timeout=3)

        text = stdout.decode() if isinstance(stdout, bytes) else stdout
        payload = _last_json_object(text)
        assert payload["status"] == "SUBMITTED", payload
        assert payload["jobId"].startswith("job-")
        assert "jobHistoryBundleDirectory" in payload
        assert backend.call_counts.get("CreateJob", 0) == 1


class TestOutputJsonCancel:
    """--output json prints CANCELED JSON when submission is canceled."""

    def test_json_output_reports_canceled(self, bundle_dir, submitter_env, deadline_env) -> None:
        backend, _ = deadline_env
        # Force CreateJob to stall briefly so we have time to click Cancel.
        backend.create_job_delay = 3.0
        with SubmitterDialog.open(
            bundle_dir,
            env=submitter_env,
            extra_args=["--output", "json"],
            capture_stdio=True,
        ) as app:
            _wait_farm_resolved(app)
            app.submit_then_cancel()
            # The main submitter window stays open after canceling the
            # progress dialog; close() dismisses it so the subprocess exits.
            app.close("Cancel")
            stdout, _ = app.proc.communicate(timeout=5)

        text = stdout.decode() if isinstance(stdout, bytes) else stdout
        payload = _last_json_object(text)
        assert payload == {"status": "CANCELED"}, payload


class TestSubmitterName:
    """--submitter-name changes the window title and auto-closes on submit."""

    def test_window_title_includes_submitter_name(self, bundle_dir, submitter_env) -> None:
        dialog_title = "Deadline Cloud Testing Submitter"
        with SubmitterDialog.open(
            bundle_dir,
            env=submitter_env,
            extra_args=["--submitter-name", "Testing"],
            dialog_name=dialog_title,
        ) as app:
            assert app.dialog().element().visible
            assert app.dialog_name == dialog_title

    def test_submit_exits_process(self, bundle_dir, submitter_env) -> None:
        """With --submitter-name set, close-on-success is enabled and the
        whole subprocess exits after clicking Ok on the progress dialog."""
        dialog_title = "Deadline Cloud Testing Submitter"
        with SubmitterDialog.open(
            bundle_dir,
            env=submitter_env,
            extra_args=["--submitter-name", "Testing"],
            dialog_name=dialog_title,
        ) as app:
            _wait_farm_resolved(app)
            app.submit_and_ok()
            rc = app.proc.wait(timeout=5)
            assert rc == 0, f"unexpected exit code {rc}"


def _last_json_object(text: str) -> dict:
    """Return the last JSON object emitted on stdout.

    ``deadline bundle gui-submit --output json`` prints a single object at
    the end of the run. Other libraries may log to stdout; this helper
    tolerates them by scanning for every ``{...}`` block that
    ``json.JSONDecoder.raw_decode`` can parse and returning the last one.
    """
    decoder = json.JSONDecoder()
    last: dict | None = None
    i = 0
    while i < len(text):
        if text[i] != "{":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            last = obj
        i = end
    if last is None:
        raise AssertionError(f"No JSON object found in stdout: {text!r}")
    return last
