"""Windows GUI driver using pywinauto (BSD-3 license).

pip install pywinauto

pywinauto uses either win32 API (via comtypes/pywin32) or Microsoft UI Automation.
We use the 'uia' backend which maps to UIA — the same accessibility framework
that Windows Narrator and Inspect.exe use. Qt exposes widgets through UIA
automatically on Windows.
"""

import time

import pywinauto
from pywinauto import Desktop

from gui_driver._base import BaseDriver

WINDOW_TITLE = "AWS Deadline Cloud workstation configuration"


class WindowsDriver(BaseDriver):
    def __init__(self, pid: int):
        self._pid = pid
        self._app = pywinauto.Application(backend="uia").connect(process=pid)

    def _window(self):
        return self._app.window(title=WINDOW_TITLE)

    def _group(self, title: str):
        return self._window().child_window(title=title, control_type="Group")

    def _combos(self, group_title: str):
        return self._group(group_title).children(control_type="ComboBox")

    def _checkboxes(self, group_title: str):
        return self._group(group_title).children(control_type="CheckBox")

    def get_combo_value(self, group_title: str, combo_index: int) -> str:
        combo = self._combos(group_title)[combo_index]
        # pywinauto reads the selected item text from UIA
        return combo.selected_text()

    def set_combo_value(self, group_title: str, combo_index: int, value: str):
        combo = self._combos(group_title)[combo_index]
        combo.select(value)
        time.sleep(0.5)

    def get_checkbox_value(self, group_title: str, checkbox_index: int) -> bool:
        return self._checkboxes(group_title)[checkbox_index].get_toggle_state() == 1

    def click_checkbox(self, group_title: str, checkbox_index: int):
        self._checkboxes(group_title)[checkbox_index].toggle()
        time.sleep(0.3)

    def click_button(self, name: str):
        self._window().child_window(title=name, control_type="Button").click()
        time.sleep(0.5)

    def window_exists(self) -> bool:
        try:
            return self._window().exists()
        except Exception:
            return False
