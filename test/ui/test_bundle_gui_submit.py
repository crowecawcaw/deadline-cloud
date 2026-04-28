# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""UI tests for ``deadline bundle gui-submit`` against the mock backend."""

from __future__ import annotations

import json
import os
from typing import Generator

import pytest

from helpers import SubmitterDialog

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


@pytest.fixture
def gui_submit(bundle_dir, submitter_env) -> Generator[SubmitterDialog, None, None]:
    with SubmitterDialog.open(bundle_dir, env=submitter_env) as app:
        app.wait_farm_resolved()
        yield app


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
        import xa11y

        def _exists(selector: str) -> bool:
            # xa11y on Windows occasionally raises PlatformError 0x80040201
            # ("An event was unable to invoke any of the subscribers") from
            # UIA subscribers on the tab widget; treat those as "not found"
            # and rely on the other role fallbacks below to match.
            try:
                return gui_submit.locator(selector).exists()
            except xa11y.PlatformError:
                return False

        tab_group = gui_submit.locator("tab_group")
        assert tab_group.exists()
        for tab_name in (
            "Shared job settings",
            "Job-specific settings",
            "Job attachments",
            "Host requirements",
        ):
            # Qt exposes tabs as radio_button on macOS, page_tab or tab on
            # Linux/Windows.
            assert (
                _exists(f'radio_button[name="{tab_name}"]')
                or _exists(f'page_tab[name="{tab_name}"]')
                or _exists(f'tab[name="{tab_name}"]')
            ), f"Tab {tab_name!r} not found"

    def test_has_submit_and_export_buttons(self, gui_submit: SubmitterDialog):
        assert gui_submit.button("Submit").exists()
        assert gui_submit.button("Export bundle").exists()


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
