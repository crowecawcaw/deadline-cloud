"""
Platform abstraction for desktop GUI automation.

macOS: atomacos (accessibility) + pyautogui (clicks/keyboard)
Linux: pyatspi2 (accessibility) + pyautogui (clicks/keyboard)
"""

import sys
import time


def _get_backend():
    if sys.platform == "darwin":
        from gui_driver._macos import MacOSDriver
        return MacOSDriver
    elif sys.platform == "linux":
        from gui_driver._linux import LinuxDriver
        return LinuxDriver
    elif sys.platform == "win32":
        from gui_driver._win import WindowsDriver
        return WindowsDriver
    else:
        raise NotImplementedError(f"No GUI driver for {sys.platform}")


def create_driver(pid: int):
    return _get_backend()(pid)
