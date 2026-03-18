"""Linux GUI driver using dbus-python (MIT) + pyautogui (BSD-3).

Talks to the AT-SPI2 accessibility service over D-Bus directly,
avoiding the LGPL pyatspi2 bindings.

Requires:
  pip install dbus-python pyautogui
  sudo apt install at-spi2-core   # the AT-SPI D-Bus service
  QT_ACCESSIBILITY=1 or QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 for the Qt app
"""

import time

import dbus
import pyautogui

from gui_driver._base import BaseDriver

WINDOW_TITLE = "AWS Deadline Cloud workstation configuration"

ATSPI_BUS_NAME = "org.a11y.atspi"
ATSPI_REGISTRY = "/org/a11y/atspi/accessible/root"
IFACE_ACCESSIBLE = "org.a11y.atspi.Accessible"
IFACE_COMPONENT = "org.a11y.atspi.Component"
IFACE_VALUE = "org.a11y.atspi.Value"
IFACE_ACTION = "org.a11y.atspi.Action"


def _get_atspi_bus():
    """Connect to the AT-SPI accessibility bus."""
    session = dbus.SessionBus()
    proxy = session.get_object("org.a11y.Bus", "/org/a11y/bus")
    addr = proxy.GetAddress(dbus_interface="org.a11y.Bus")
    return dbus.bus.BusConnection(addr)


class _Element:
    """Lightweight wrapper around an AT-SPI D-Bus accessible object."""

    def __init__(self, bus, bus_name, path):
        self._bus = bus
        self._bus_name = bus_name
        self._path = path
        self._proxy = bus.get_object(bus_name, path)
        self._acc = dbus.Interface(self._proxy, IFACE_ACCESSIBLE)

    @property
    def name(self) -> str:
        return str(self._proxy.Get(IFACE_ACCESSIBLE, "Name", dbus_interface=dbus.PROPERTIES_IFACE))

    @property
    def role(self) -> int:
        return self._acc.GetRole()

    @property
    def role_name(self) -> str:
        return str(self._acc.GetRoleName())

    @property
    def child_count(self) -> int:
        return int(self._proxy.Get(IFACE_ACCESSIBLE, "ChildCount", dbus_interface=dbus.PROPERTIES_IFACE))

    def child(self, index: int) -> "_Element":
        bus_name, path = self._acc.GetChildAtIndex(index)
        return _Element(self._bus, bus_name, path)

    @property
    def children(self) -> list["_Element"]:
        return [self.child(i) for i in range(self.child_count)]

    @property
    def extents(self) -> tuple[int, int, int, int]:
        """Returns (x, y, width, height) in screen coordinates."""
        comp = dbus.Interface(self._proxy, IFACE_COMPONENT)
        # coord_type 0 = screen coordinates
        return comp.GetExtents(dbus.UInt32(0))

    @property
    def state(self) -> set:
        states = self._acc.GetState()
        result = set()
        for word in states:
            for bit in range(32):
                if word & (1 << bit):
                    result.add(bit)
        return result

    def do_action(self, index: int = 0):
        action = dbus.Interface(self._proxy, IFACE_ACTION)
        action.DoAction(index)

    def click(self):
        x, y, w, h = self.extents
        pyautogui.click(x + w // 2, y + h // 2)


# AT-SPI role constants (from atspi-constants.h)
ROLE_FRAME = 22          # window/frame
ROLE_PANEL = 25          # group box
ROLE_PUSH_BUTTON = 42
ROLE_CHECK_BOX = 7
ROLE_COMBO_BOX = 9
ROLE_LIST = 26
ROLE_LIST_ITEM = 28
ROLE_MENU_ITEM = 30
# State constant for "checked"
STATE_CHECKED = 10


def _find_all(elem: _Element, role: int) -> list[_Element]:
    results = []
    for child in elem.children:
        if child.role == role:
            results.append(child)
        results.extend(_find_all(child, role))
    return results


class LinuxDriver(BaseDriver):
    def __init__(self, pid: int):
        self._bus = _get_atspi_bus()
        self._app = self._find_app(pid)

    def _find_app(self, pid: int) -> _Element:
        registry = _Element(self._bus, ATSPI_BUS_NAME, ATSPI_REGISTRY)
        for app in registry.children:
            try:
                app_pid = app._proxy.Get(
                    "org.a11y.atspi.Application", "Id",
                    dbus_interface=dbus.PROPERTIES_IFACE
                )
                if int(app_pid) == pid:
                    return app
            except dbus.DBusException:
                continue
        raise RuntimeError(f"App with pid {pid} not found on AT-SPI bus")

    def _window(self) -> _Element:
        for child in self._app.children:
            if child.role == ROLE_FRAME and child.name == WINDOW_TITLE:
                return child
        raise RuntimeError("Config window not found")

    def _group(self, title: str) -> _Element:
        for panel in _find_all(self._window(), ROLE_PANEL):
            if panel.name == title:
                return panel
        raise RuntimeError(f"Group '{title}' not found")

    def _combos(self, group_title: str) -> list[_Element]:
        return _find_all(self._group(group_title), ROLE_COMBO_BOX)

    def _checkboxes(self, group_title: str) -> list[_Element]:
        return _find_all(self._group(group_title), ROLE_CHECK_BOX)

    def get_combo_value(self, group_title: str, combo_index: int) -> str:
        return self._combos(group_title)[combo_index].name

    def set_combo_value(self, group_title: str, combo_index: int, value: str):
        combo = self._combos(group_title)[combo_index]
        combo.click()
        time.sleep(1)

        # Search for the popup list item
        for item in _find_all(combo, ROLE_LIST_ITEM):
            if item.name == value:
                item.click()
                time.sleep(0.5)
                return

        # Fallback: search the whole window
        for item in _find_all(self._window(), ROLE_LIST_ITEM):
            if item.name == value:
                item.click()
                time.sleep(0.5)
                return
        raise RuntimeError(f"Combo item '{value}' not found")

    def get_checkbox_value(self, group_title: str, checkbox_index: int) -> bool:
        return STATE_CHECKED in self._checkboxes(group_title)[checkbox_index].state

    def click_checkbox(self, group_title: str, checkbox_index: int):
        self._checkboxes(group_title)[checkbox_index].click()
        time.sleep(0.3)

    def click_button(self, name: str):
        for btn in _find_all(self._window(), ROLE_PUSH_BUTTON):
            if btn.name == name:
                btn.click()
                time.sleep(0.5)
                return
        raise RuntimeError(f"Button '{name}' not found")

    def window_exists(self) -> bool:
        try:
            self._window()
            return True
        except Exception:
            return False
