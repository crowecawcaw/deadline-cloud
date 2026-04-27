# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""Page-object helpers for UI tests.

Wraps a ``deadline`` GUI subprocess + its xa11y ``App`` handle so tests
can drive the real CLI through the accessibility tree.
"""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Optional, Sequence, TypeVar

import xa11y

STARTUP_TIMEOUT = 15.0

_T = TypeVar("_T", bound="DeadlineApp")


def _find_app(pid: int, baseline_names: set, timeout: float) -> xa11y.App:
    """Wait for an ``xa11y.App`` to appear for ``pid``.

    Falls back to matching by name because AT-SPI on Linux sometimes reports
    the wrong PID (typically 1) for child processes.
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        apps = xa11y.App.list()
        for a in apps:
            if a.pid == pid:
                return xa11y.App.by_name(a.name)
        for a in apps:
            if a.name not in baseline_names:
                return xa11y.App.by_name(a.name)
        time.sleep(0.25)
    raise TimeoutError(f"No accessibility app found for PID {pid}")


class DeadlineApp:
    """Launches a deadline subprocess and exposes its accessibility tree."""

    DIALOG: str = ""  # overridden by subclasses

    def __init__(self, proc: subprocess.Popen, app: xa11y.App):
        self.proc = proc
        self._app = app

    @classmethod
    def launch(
        cls: type[_T],
        args: Sequence[str],
        env: Optional[dict] = None,
        timeout: float = STARTUP_TIMEOUT,
    ) -> _T:
        cmd = [sys.executable, "-m", "deadline", *args]
        baseline = {a.name for a in xa11y.App.list()}
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            app = _find_app(proc.pid, baseline, timeout)
            instance = cls(proc, app)
            instance.dialog().wait_visible(timeout=timeout)
            return instance
        except Exception:
            proc.kill()
            proc.wait()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def locator(self, selector: str) -> xa11y.Locator:
        return self._app.locator(selector)

    def dialog(self) -> xa11y.Locator:
        return self.locator(f'dialog[name="{self.DIALOG}"]')

    def button(self, name: str) -> xa11y.Locator:
        return self.locator(f'button[name="{name}"]')

    def close(self, button_name: str = "Cancel"):
        """Click a dismiss button then wait for the process to exit."""
        try:
            self.button(button_name).press()
            self.dialog().wait_detached(timeout=5)
            self.proc.wait(timeout=5)
            return
        except (xa11y.XA11yError, subprocess.TimeoutExpired):
            pass
        self.proc.kill()
        self.proc.wait()


class ConfigDialog(DeadlineApp):
    """Page object for ``deadline config gui``."""

    DIALOG = "AWS Deadline Cloud workstation configuration"

    @classmethod
    def open(cls, env: Optional[dict] = None) -> "ConfigDialog":
        return cls.launch(["config", "gui"], env=env)

    @property
    def log_level(self) -> str:
        """Text shown in the log-level combo box."""
        general = self.locator('group[name="General settings"]').element()
        combos = [c for c in general.children() if c.role == "combo_box"]
        # 1st = conflict resolution, 2nd = log level, 3rd = language
        return combos[1].name


class SubmitterDialog(DeadlineApp):
    """Page object for ``deadline bundle gui-submit``."""

    DIALOG = "Deadline Cloud JobBundle Submitter"

    @classmethod
    def open(cls, bundle_dir: str, env: Optional[dict] = None) -> "SubmitterDialog":
        return cls.launch(["bundle", "gui-submit", bundle_dir], env=env)

    @property
    def job_name(self) -> str:
        return self.locator('text_field[name="Name"]').element().value or ""

    def export_bundle(self) -> None:
        """Click 'Export bundle' and dismiss the confirmation dialog."""
        self.button("Export bundle").press()
        ok = self.button("OK")
        ok.wait_visible(timeout=10)
        ok.press()
