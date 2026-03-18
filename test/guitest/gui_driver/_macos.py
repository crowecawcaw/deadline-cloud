"""macOS GUI driver using atomacos + pyautogui."""

import time

import atomacos
import pyautogui

from gui_driver._base import BaseDriver

WINDOW_TITLE = "AWS Deadline Cloud workstation configuration"


def _click_element(elem):
    pos, size = elem.AXPosition, elem.AXSize
    pyautogui.click(int(pos.x + size.width / 2), int(pos.y + size.height / 2))


class MacOSDriver(BaseDriver):
    def __init__(self, pid: int):
        self._pid = pid
        self._app = atomacos.getAppRefByPid(pid)

    def _window(self):
        for w in self._app.windows():
            if w.AXTitle == WINDOW_TITLE:
                return w
        raise RuntimeError("Config window not found")

    def _group(self, title: str):
        for g in self._window().findAll(AXRole="AXGroup"):
            if getattr(g, "AXTitle", "") == title:
                return g
        raise RuntimeError(f"Group '{title}' not found")

    def _combos(self, group_title: str):
        return self._group(group_title).findAll(AXRole="AXMenuButton")

    def _checkboxes(self, group_title: str):
        return self._group(group_title).findAll(AXRole="AXCheckBox")

    def get_combo_value(self, group_title: str, combo_index: int) -> str:
        return self._combos(group_title)[combo_index].AXTitle

    def set_combo_value(self, group_title: str, combo_index: int, value: str):
        combo = self._combos(group_title)[combo_index]
        combo.Press()
        time.sleep(1)

        lst = combo.AXChildren[0]
        for row in lst.AXChildren:
            if row.AXRole != "AXRow":
                continue
            cell = row.AXChildren[0]
            if cell.AXTitle == value:
                _click_element(cell)
                time.sleep(0.5)
                return
        raise RuntimeError(f"Combo item '{value}' not found")

    def get_checkbox_value(self, group_title: str, checkbox_index: int) -> bool:
        return self._checkboxes(group_title)[checkbox_index].AXValue == 1

    def click_checkbox(self, group_title: str, checkbox_index: int):
        cb = self._checkboxes(group_title)[checkbox_index]
        _click_element(cb)
        time.sleep(0.3)

    def click_button(self, name: str):
        win = self._window()
        for g in win.findAll(AXRole="AXGroup"):
            for btn in g.findAll(AXRole="AXButton"):
                if getattr(btn, "AXTitle", "") == name:
                    _click_element(btn)
                    time.sleep(0.5)
                    return
        raise RuntimeError(f"Button '{name}' not found")

    def window_exists(self) -> bool:
        try:
            self._window()
            return True
        except Exception:
            return False
