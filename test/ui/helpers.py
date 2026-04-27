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


def _terminate(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    """Ensure a subprocess is reaped.

    Sends SIGKILL (``kill()``) and then polls until the OS delivers the exit
    status. On POSIX ``kill()`` is non-blocking but the process remains a
    zombie until ``wait()``; on Windows ``kill()`` calls TerminateProcess.
    """
    if proc.poll() is not None:
        return
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass


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
        # Discard the subprocess's stdout/stderr. Letting them inherit from
        # pytest's captured fds risks the child blocking on a full write
        # buffer if the parent worker drains them slowly, which manifests as
        # hangs during teardown (proc.kill followed by proc.wait never
        # returning). Diagnostics come from ``dump_tree`` against the live
        # accessibility tree instead.
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        try:
            app = _find_app(proc.pid, baseline, timeout)
            instance = cls(proc, app)
            instance.dialog().wait_visible(timeout=timeout)
            return instance
        except Exception:
            _terminate(proc)
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def locator(self, selector: str) -> xa11y.Locator:
        return self._app.locator(selector)

    def dialog(self) -> xa11y.Locator:
        # Qt's top-level QDialog is exposed as a Dialog role on macOS/Linux,
        # but on Windows it often registers as a Window without IsDialog set.
        selector = (
            f'window[name="{self.DIALOG}"]'
            if sys.platform.startswith("win")
            else f'dialog[name="{self.DIALOG}"]'
        )
        return self.locator(selector)

    def button(self, name: str) -> xa11y.Locator:
        return self.locator(f'button[name="{name}"]')

    def dump_tree(self) -> None:
        """Print the accessibility tree to stderr for diagnostics."""

        def walk(elt, depth=0):
            try:
                role = elt.role
                name = elt.name or ""
                value = getattr(elt, "value", None) or ""
            except Exception as e:
                sys.stderr.write(f"{'  ' * depth}<err: {e}>\n")
                return
            sys.stderr.write(f"{'  ' * depth}{role} name={name!r} value={value!r}\n")
            try:
                for child in elt.children():
                    walk(child, depth + 1)
            except Exception:
                pass

        sys.stderr.write("\n--- accessibility tree ---\n")
        try:
            for root in self._app.children():
                walk(root)
        except Exception as e:
            sys.stderr.write(f"<tree error: {e}>\n")
        sys.stderr.write("--- end tree ---\n")

    def close(self, button_name: str = "Cancel"):
        """Click a dismiss button; fall back to killing the subprocess.

        Tries the accessibility-driven path first so Qt can unwind cleanly,
        but never blocks on it — if the dialog or process does not respond
        within a few seconds we SIGKILL and reap unconditionally.
        """
        try:
            self.button(button_name).press()
            self.dialog().wait_detached(timeout=3)
            self.proc.wait(timeout=3)
            return
        except (xa11y.XA11yError, subprocess.TimeoutExpired):
            pass
        _terminate(self.proc)


class ConfigDialog(DeadlineApp):
    """Page object for ``deadline config gui``."""

    DIALOG = "AWS Deadline Cloud workstation configuration"

    @classmethod
    def open(cls, env: Optional[dict] = None) -> "ConfigDialog":
        return cls.launch(["config", "gui"], env=env)

    @property
    def log_level(self) -> str:
        """Text shown in the log-level combo box.

        Qt's QComboBox exposes the selected text differently per platform:
        macOS/AT-SPI put it in ``name``; Windows UIA puts it in ``value`` while
        ``name`` is the buddy label (e.g. "Current logging level"). Try both.
        """
        general = self.locator('group[name="General settings"]').element()

        def _iter_combo_boxes(elt):
            try:
                if elt.role == "combo_box":
                    yield elt
                for child in elt.children():
                    yield from _iter_combo_boxes(child)
            except Exception:
                return

        combos = list(_iter_combo_boxes(general))
        # 1st = conflict resolution, 2nd = log level, 3rd = language
        combo = combos[1]
        value = getattr(combo, "value", None)
        if value:
            return value
        return combo.name


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
