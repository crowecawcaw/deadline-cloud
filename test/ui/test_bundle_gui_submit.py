# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""UI tests for ``deadline bundle gui-submit`` against the mock backend."""

from __future__ import annotations

import copy
import json
import os

from helpers import SAMPLE_TEMPLATE, SubmitterDialog


class TestSubmitterOpens:
    def test_dialog_is_visible(self, gui_submit: SubmitterDialog):
        assert gui_submit.dialog().element().visible

    def test_farm_name_resolved(self, gui_submit: SubmitterDialog):
        assert gui_submit.tree_contains_text("TestFarm")

    def test_queue_name_resolved(self, gui_submit: SubmitterDialog):
        assert gui_submit.tree_contains_text("TestQueue")

    def test_job_name_displayed(self, gui_submit: SubmitterDialog):
        assert gui_submit.job_name == "Test Render Job"

    def test_has_tabs(self, gui_submit: SubmitterDialog):
        tab_group = gui_submit.locator("tab_group")
        assert tab_group.exists()
        for tab_name in (
            "Shared job settings",
            "Job-specific settings",
            "Job attachments",
            "Host requirements",
        ):
            assert gui_submit.tab_exists(tab_name), f"Tab {tab_name!r} not found"

    def test_has_submit_and_export_buttons(self, gui_submit: SubmitterDialog):
        assert gui_submit.button("Submit").exists()
        assert gui_submit.button("Export bundle").exists()


class TestLoadDifferentBundle:
    """Covers the manual test 'Verify Load a different job bundle button in
    GUI Submitter': open the submitter on bundle A with --browse, click the
    load-bundle button, and confirm the dialog refreshes to bundle B's name.
    """

    def test_load_bundle_button_swaps_in_second_bundle(
        self, bundle_dir, submitter_env, bundle_picker_file, tmp_path
    ):
        # Make a second bundle with a distinct template name so we can tell
        # the dialog has refreshed by reading the visible Name field.
        second = tmp_path / "second_bundle"
        second.mkdir()
        second_template = copy.deepcopy(SAMPLE_TEMPLATE)
        second_template["name"] = "Second Bundle Job"
        (second / "template.json").write_text(json.dumps(second_template))

        with SubmitterDialog.open(
            bundle_dir, env=submitter_env, extra_args=["--browse"]
        ) as app:
            app.wait_farm_resolved()
            assert app.job_name == "Test Render Job"
            assert app.button("Load Bundle").exists(), (
                "Load-bundle button should be present when --browse is set"
            )
            app.load_different_bundle(
                next_bundle_dir=str(second),
                picker_file=str(bundle_picker_file),
                expected_name="Second Bundle Job",
            )

    def test_load_bundle_button_hidden_without_browse_flag(
        self, gui_submit: SubmitterDialog
    ):
        """Without --browse, browse_enabled is False and the button is not built."""
        assert not gui_submit.button("Load Bundle").exists()


class TestExportBundle:
    def test_export_creates_bundle(self, bundle_dir, submitter_env):
        job_history_dir = submitter_env["_JOB_HISTORY_DIR"]
        with SubmitterDialog.open(bundle_dir, env=submitter_env) as app:
            app.wait_farm_resolved()
            app.export_bundle()

            assert os.path.isdir(job_history_dir), "Job history dir was not created"
            templates = []
            for root, _dirs, files in os.walk(job_history_dir):
                for fn in files:
                    if fn.startswith("template."):
                        templates.append(os.path.join(root, fn))
            assert templates, "No template file found in exported bundle"
            with open(templates[0]) as f:
                exported = json.load(f)
            assert exported["name"] == "Test Render Job"
