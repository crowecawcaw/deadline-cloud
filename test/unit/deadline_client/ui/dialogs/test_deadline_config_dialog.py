# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

from configparser import ConfigParser
from unittest.mock import patch, MagicMock

import pytest

try:
    from deadline.client.ui.widgets._deadline_list_combo_boxes import (
        DeadlineFarmListComboBoxController,
    )
    from deadline.client.ui.dialogs.deadline_config_dialog import (
        DeadlineWorkstationConfigWidget,
    )
except ImportError:
    pytest.importorskip("deadline.client.ui.widgets._deadline_list_combo_boxes")


class TestDeadlineResourceListComboBoxController:
    """Tests for _DeadlineResourceListComboBoxController.refresh_selected_id()"""

    @patch("deadline.client.ui.widgets._deadline_list_combo_boxes.DeadlineUIController.getInstance")
    @patch("deadline.client.ui.widgets._deadline_list_combo_boxes.config_file")
    def test_shows_id_when_not_in_list(self, mock_config_file, mock_get_instance, qtbot):
        """
        When user has a configured ID but lacks permission to list resources,
        the combobox should display the raw ID instead of '<none selected>'.

        This happens when a user has permission to use a queue but lacks
        permission to call ListFarms.
        """
        mock_controller = MagicMock()
        mock_get_instance.return_value = mock_controller
        mock_config_file.get_setting.return_value = "farm-abc123"

        widget = DeadlineFarmListComboBoxController()
        qtbot.addWidget(widget)
        widget.set_config(ConfigParser())

        widget.refresh_selected_id()

        assert widget.box.currentText() == "farm-abc123"
        assert widget.box.currentData() == "farm-abc123"

    @patch("deadline.client.ui.widgets._deadline_list_combo_boxes.DeadlineUIController.getInstance")
    @patch("deadline.client.ui.widgets._deadline_list_combo_boxes.config_file")
    def test_shows_none_selected_when_no_id_configured(
        self, mock_config_file, mock_get_instance, qtbot
    ):
        """When no ID is configured, should show '<none selected>'."""
        mock_controller = MagicMock()
        mock_get_instance.return_value = mock_controller
        mock_config_file.get_setting.return_value = ""

        widget = DeadlineFarmListComboBoxController()
        qtbot.addWidget(widget)
        widget.set_config(ConfigParser())

        widget.refresh_selected_id()

        assert widget.box.currentText() == "<none selected>"


class TestFarmRegionPersistence:
    """Tests that selecting a farm persists defaults.farm_region with defaults.farm_id."""

    def test_default_farm_changed_persists_region(self):
        """default_farm_changed records both farm_id and the farm's region in changes."""
        # Use a lightweight stand-in (plain MagicMock auto-creates the instance
        # attributes the method touches) so we don't construct the whole
        # (boto3-backed) dialog; we only exercise the selection-persistence logic.
        stub = MagicMock()
        stub.changes = {}
        stub.default_farm_box.box.itemData.return_value = "farm-a"
        stub.default_farm_box.region_for_id.return_value = "us-west-2"

        DeadlineWorkstationConfigWidget.default_farm_changed(stub, 0)

        assert stub.changes["defaults.farm_id"] == "farm-a"
        assert stub.changes["defaults.farm_region"] == "us-west-2"
        # The queue id is cleared on farm change.
        assert stub.changes["defaults.queue_id"] == ""
        stub.default_farm_box.region_for_id.assert_called_once_with("farm-a")

    def test_aws_profile_changed_clears_region(self):
        """Switching AWS profile clears the persisted farm region."""
        stub = MagicMock()
        stub.changes = {}

        DeadlineWorkstationConfigWidget.aws_profile_changed(stub, "other-profile")

        assert stub.changes["defaults.farm_id"] == ""
        assert stub.changes["defaults.farm_region"] == ""
