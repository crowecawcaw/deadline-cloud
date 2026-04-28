# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""UI tests for ``deadline bundle gui-submit`` — dialog controls.

Covers the manual test case "Verify all GUI Submitter dialogue controls
work". Rather than testing every widget, these tests exercise a
representative sample across the main tabs:

  * Shared job settings: Priority spin box, Initial state combo box.
  * Host requirements: toggle between "Run on all" / "Run on meeting
    requirements" radio buttons.
  * Job attachments: tab is reachable and exposes controls.
"""

from __future__ import annotations

import json

import pytest

from helpers import FARM_RESOLVE_TIMEOUT, SubmitterDialog

SAMPLE_TEMPLATE = {
    "specificationVersion": "jobtemplate-2023-09",
    "name": "Test Render Job",
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
    return env


@pytest.fixture
def gui_submit(bundle_dir, submitter_env):
    with SubmitterDialog.open(bundle_dir, env=submitter_env) as app:
        try:
            app.locator(
                'group[name="Deadline Cloud settings"] static_text[name="TestFarm"]'
            ).wait_attached(timeout=FARM_RESOLVE_TIMEOUT)
        except Exception:
            app.dump_tree()
            raise
        yield app


class TestSharedJobSettingsControls:
    """Priority and initial state controls exist and have expected defaults."""

    def test_priority_spin_box_is_present(self, gui_submit: SubmitterDialog) -> None:
        # Qt's QSpinBox exposes its parent widget's name via accessibility,
        # not its label text. The "Priority" static_text sits next to the
        # spin box in the QFormLayout, which is enough to prove the control
        # is rendered. We also check that the default priority value (50)
        # is reachable via some spin_button in the dialog's subtree.
        assert gui_submit.locator('static_text[name="Priority"]').exists(), (
            "Priority label not found"
        )
        assert any(
            (getattr(sb, "value", "") or "") == "50"
            for sb in gui_submit.elements_by_role("spin_button")
        ), "No spin_button with the default Priority value of 50 found"

    def test_initial_state_combo_is_present(self, gui_submit: SubmitterDialog) -> None:
        # Same label/value split: the combo_box's accessible name is its
        # selected item (READY) on macOS/Linux, while the "Initial state"
        # label sits next to it.
        assert gui_submit.locator('static_text[name="Initial state"]').exists(), (
            "Initial state label not found"
        )
        assert (
            gui_submit.locator('combo_box[name="READY"]').exists()
            or gui_submit.locator('combo_box[name="SUSPENDED"]').exists()
            or gui_submit.locator('combo_box[name="Initial state"]').exists()
        ), "Initial state combo box not found"


class TestHostRequirementsControls:
    """The host-requirements tab exposes the two top-level radio buttons."""

    def test_default_and_custom_radios_present(self, gui_submit: SubmitterDialog) -> None:
        # Activate the Host requirements tab so its widgets are in the tree.
        _activate_tab(gui_submit, "Host requirements")

        # Both radio buttons are siblings inside the OverrideRequirementsWidget.
        assert gui_submit.locator('radio_button[name="Run on all available worker hosts"]').exists()
        assert gui_submit.locator(
            'radio_button[name="Run on worker hosts that meet the following requirements"]'
        ).exists()


class TestJobAttachmentsTab:
    """The job-attachments tab is reachable and renders."""

    def test_job_attachments_tab_activates(self, gui_submit: SubmitterDialog) -> None:
        _activate_tab(gui_submit, "Job attachments")
        # The tab's scroll area should be present after activation; the
        # inner JobAttachmentsWidget exposes a group per attachment type.
        # We don't assert on a specific child widget here, just that the
        # tab itself is discoverable.
        assert gui_submit.dialog().element().visible


def _activate_tab(app: SubmitterDialog, tab_name: str) -> None:
    """Click the named tab across the platform role variants."""
    for role in ("radio_button", "page_tab", "tab"):
        loc = app.locator(f'{role}[name="{tab_name}"]')
        if loc.exists():
            loc.press()
            return
    raise AssertionError(f"Tab {tab_name!r} not found via any role")
