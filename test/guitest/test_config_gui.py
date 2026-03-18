"""
GUI tests for `deadline config gui`.

Verifies that:
  1. Changing settings and clicking OK persists them to ~/.deadline/config.
  2. Reopening the GUI shows the previously saved settings.
  3. Cancel discards unsaved changes.
  4. Apply saves without closing the dialog.
"""

import time

import pytest

from conftest import get_config_value

# General settings group — combo box indices
COMBO_CONFLICT_RESOLUTION = 0
COMBO_LOG_LEVEL = 1
# Checkbox indices
CB_AUTO_ACCEPT = 0

GROUP = "General settings"


class TestLogLevel:
    @pytest.mark.parametrize("target_level", ["INFO", "DEBUG", "ERROR"])
    def test_change_persists(self, config_gui, backup_config, target_level):
        config_gui.launch()
        d = config_gui.driver

        d.set_combo_value(GROUP, COMBO_LOG_LEVEL, target_level)
        d.click_button("Ok")
        time.sleep(1)
        config_gui.close()

        assert get_config_value("log_level") == target_level

        # Reopen — GUI should show the saved value
        config_gui.launch()
        assert config_gui.driver.get_combo_value(GROUP, COMBO_LOG_LEVEL) == target_level
        config_gui.driver.click_button("Cancel")


class TestConflictResolution:
    @pytest.mark.parametrize("target", ["CREATE_COPY", "OVERWRITE", "SKIP"])
    def test_change_persists(self, config_gui, backup_config, target):
        config_gui.launch()
        d = config_gui.driver

        d.set_combo_value(GROUP, COMBO_CONFLICT_RESOLUTION, target)
        d.click_button("Ok")
        time.sleep(1)
        config_gui.close()

        assert get_config_value("conflict_resolution") == target

        config_gui.launch()
        assert config_gui.driver.get_combo_value(GROUP, COMBO_CONFLICT_RESOLUTION) == target
        config_gui.driver.click_button("Cancel")


class TestAutoAccept:
    def test_toggle_on(self, config_gui, backup_config):
        config_gui.launch()
        d = config_gui.driver

        # Ensure it starts unchecked
        if d.get_checkbox_value(GROUP, CB_AUTO_ACCEPT):
            d.click_checkbox(GROUP, CB_AUTO_ACCEPT)
            d.click_button("Ok")
            time.sleep(1)
            config_gui.close()
            config_gui.launch()
            d = config_gui.driver

        assert not d.get_checkbox_value(GROUP, CB_AUTO_ACCEPT)
        d.click_checkbox(GROUP, CB_AUTO_ACCEPT)
        d.click_button("Ok")
        time.sleep(1)
        config_gui.close()

        assert get_config_value("auto_accept") in ("true", "True", "yes", "1")

        config_gui.launch()
        assert config_gui.driver.get_checkbox_value(GROUP, CB_AUTO_ACCEPT)
        config_gui.driver.click_button("Cancel")


class TestCancelDiscardsChanges:
    def test_cancel_does_not_save(self, config_gui, backup_config):
        original = get_config_value("log_level") or "WARNING"
        new_level = "DEBUG" if original != "DEBUG" else "INFO"

        config_gui.launch()
        config_gui.driver.set_combo_value(GROUP, COMBO_LOG_LEVEL, new_level)
        config_gui.driver.click_button("Cancel")
        time.sleep(1)
        config_gui.close()

        assert get_config_value("log_level") in (original, None)


class TestApplyButton:
    def test_apply_saves_without_closing(self, config_gui, backup_config):
        config_gui.launch()
        d = config_gui.driver

        d.set_combo_value(GROUP, COMBO_LOG_LEVEL, "DEBUG")
        d.click_button("Apply")
        time.sleep(1)

        assert get_config_value("log_level") == "DEBUG"
        assert d.window_exists()

        # Change again then cancel — Apply'd value should stick
        d.set_combo_value(GROUP, COMBO_LOG_LEVEL, "ERROR")
        d.click_button("Cancel")
        time.sleep(1)
        config_gui.close()

        assert get_config_value("log_level") == "DEBUG"
