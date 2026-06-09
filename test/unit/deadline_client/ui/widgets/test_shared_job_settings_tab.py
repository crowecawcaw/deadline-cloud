# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

import pytest

try:
    from deadline.client import api
    from deadline.client.ui.widgets.shared_job_settings_tab import SharedJobSettingsWidget
    from deadline.client.ui.dataclasses import JobBundleSettings
except ImportError:
    # The tests in this file should be skipped if Qt UI related modules cannot be loaded
    pytest.importorskip("deadline.client.ui.widgets.shared_job_settings_tab")


@pytest.fixture(scope="function")
def shared_job_settings_tab(qtbot, temp_job_bundle_dir) -> SharedJobSettingsWidget:
    initial_settings = JobBundleSettings(input_job_bundle_dir=temp_job_bundle_dir, name="test-name")
    widget = SharedJobSettingsWidget(
        initial_settings=initial_settings,
        initial_shared_parameter_values=dict(),
    )
    qtbot.addWidget(widget)
    return widget


def test_name_should_be_truncated_to_openjd_spec_128_chars(
    shared_job_settings_tab: SharedJobSettingsWidget,
):
    expected_max_job_name_length = 128
    invalid_str = "a" * (expected_max_job_name_length + 1)
    shared_job_settings_tab.shared_job_properties_box.sub_name_edit.setText(invalid_str)
    assert (
        shared_job_settings_tab.shared_job_properties_box.sub_name_edit.text()
        == invalid_str[:expected_max_job_name_length]
    )


def test_description_should_be_truncated_to_openjd_spec_2048_chars(
    shared_job_settings_tab: SharedJobSettingsWidget,
):
    expected_max_job_description_length = 2048
    invalid_str = "a" * (expected_max_job_description_length + 1)
    shared_job_settings_tab.shared_job_properties_box.desc_edit.setText(invalid_str)
    assert (
        shared_job_settings_tab.shared_job_properties_box.desc_edit.text()
        == invalid_str[:expected_max_job_description_length]
    )


def test_priority_should_be_integer_within_range(shared_job_settings_tab: SharedJobSettingsWidget):
    shared_job_settings_tab.shared_job_properties_box.priority_box.setValue(-1)
    assert shared_job_settings_tab.shared_job_properties_box.priority_box.value() == 0

    shared_job_settings_tab.shared_job_properties_box.priority_box.setValue(101)
    assert shared_job_settings_tab.shared_job_properties_box.priority_box.value() == 100


def test_initial_state_should_be_allowed_enums(shared_job_settings_tab: SharedJobSettingsWidget):
    shared_job_settings_tab.shared_job_properties_box.initial_status_box.setCurrentText("Invalid")
    assert (
        shared_job_settings_tab.shared_job_properties_box.initial_status_box.currentText()
        == "READY"
    )


def test_max_failed_tasks_count_should_be_integer_within_range(
    shared_job_settings_tab: SharedJobSettingsWidget,
):
    shared_job_settings_tab.shared_job_properties_box.max_failed_tasks_count_box.setValue(-1)
    assert shared_job_settings_tab.shared_job_properties_box.max_failed_tasks_count_box.value() == 0


def test_max_retries_per_task_should_be_integer_within_range(
    shared_job_settings_tab: SharedJobSettingsWidget,
):
    shared_job_settings_tab.shared_job_properties_box.max_retries_per_task_box.setValue(-1)
    assert shared_job_settings_tab.shared_job_properties_box.max_retries_per_task_box.value() == 0


def test_max_worker_count_should_be_integer_within_range(
    shared_job_settings_tab: SharedJobSettingsWidget,
):
    shared_job_settings_tab.shared_job_properties_box.max_worker_count_box.setValue(-1)
    assert shared_job_settings_tab.shared_job_properties_box.max_worker_count_box.value() == 1


# ---------------------------------------------------------------------------
# Region scoping for the read-only resource display widgets.
#
# These widgets build their own deadline client to fetch farm/queue/storage-profile
# names. They must scope that client to the farm's region (defaults.farm_region) so the
# submitter summary works for a farm in a non-default region.
# ---------------------------------------------------------------------------

MOCK_FARM_ID = "farm-0123456789abcdefabcdefabcdefabcd"
MOCK_QUEUE_ID = "queue-0123456789abcdefabcdefabcdefabcd"
MOCK_STORAGE_PROFILE_ID = "sp-0123456789abcdefabcdefabcdefabcd"


def test_farm_display_get_item_scopes_client_to_farm_region(qtbot, fresh_deadline_config):
    from unittest.mock import MagicMock, patch
    from deadline.client.config import config_file
    from deadline.client.ui.widgets.shared_job_settings_tab import DeadlineFarmDisplay

    config_file.set_setting("defaults.farm_id", MOCK_FARM_ID)
    config_file.set_setting("defaults.farm_region", "eu-west-1")

    widget = DeadlineFarmDisplay()
    qtbot.addWidget(widget)

    with patch.object(api, "get_boto3_client") as mock_get_client:
        deadline = MagicMock()
        deadline.get_farm.return_value = {
            "farmId": MOCK_FARM_ID,
            "displayName": "F",
            "description": "d",
        }
        mock_get_client.return_value = deadline

        widget.get_item()

        assert mock_get_client.call_args.kwargs.get("region") == "eu-west-1"


def test_queue_display_get_item_scopes_client_to_farm_region(qtbot, fresh_deadline_config):
    from unittest.mock import MagicMock, patch
    from deadline.client.config import config_file
    from deadline.client.ui.widgets.shared_job_settings_tab import DeadlineQueueDisplay

    config_file.set_setting("defaults.farm_id", MOCK_FARM_ID)
    config_file.set_setting("defaults.farm_region", "eu-west-1")
    config_file.set_setting("defaults.queue_id", MOCK_QUEUE_ID)

    widget = DeadlineQueueDisplay()
    qtbot.addWidget(widget)

    with patch.object(api, "get_boto3_client") as mock_get_client:
        deadline = MagicMock()
        deadline.get_queue.return_value = {
            "queueId": MOCK_QUEUE_ID,
            "displayName": "Q",
            "description": "d",
        }
        mock_get_client.return_value = deadline

        widget.get_item()

        assert mock_get_client.call_args.kwargs.get("region") == "eu-west-1"


def test_storage_profile_display_get_item_scopes_client_to_farm_region(
    qtbot, fresh_deadline_config
):
    from unittest.mock import MagicMock, patch
    from deadline.client.config import config_file
    from deadline.client.ui.widgets.shared_job_settings_tab import (
        DeadlineStorageProfileNameDisplay,
    )

    config_file.set_setting("defaults.farm_id", MOCK_FARM_ID)
    config_file.set_setting("defaults.farm_region", "eu-west-1")
    config_file.set_setting("defaults.queue_id", MOCK_QUEUE_ID)
    config_file.set_setting("settings.storage_profile_id", MOCK_STORAGE_PROFILE_ID)

    widget = DeadlineStorageProfileNameDisplay()
    qtbot.addWidget(widget)

    with patch.object(api, "get_boto3_client") as mock_get_client:
        deadline = MagicMock()
        deadline.list_storage_profiles_for_queue.return_value = {
            "storageProfiles": [
                {
                    "storageProfileId": MOCK_STORAGE_PROFILE_ID,
                    "displayName": "SP",
                    "osFamily": "linux",
                }
            ]
        }
        mock_get_client.return_value = deadline

        widget.get_item()

        assert mock_get_client.call_args.kwargs.get("region") == "eu-west-1"
