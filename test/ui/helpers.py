# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""Page-object helpers for UI tests.

Wraps a ``deadline`` GUI subprocess + its xa11y ``App`` handle so tests
can drive the real CLI through the accessibility tree.
"""

from __future__ import annotations

import atexit
import subprocess
import sys
import time
import weakref
from typing import Optional, Sequence, TypeVar

import xa11y

# All times are in seconds. These are tuned for local mock-backend runs:
# the GUI binds to localhost HTTP, no real AWS calls happen, and Qt
# startup under xa11y is typically 1-3s. CI on Xvfb is a bit slower but
# still well under these ceilings.
STARTUP_TIMEOUT = 5.0
CLOSE_TIMEOUT = 2.0
TERMINATE_TIMEOUT = 2.0
# How long to wait for ``CreateJob`` + one ``GetJob`` to complete through
# the local mock before the success dialog's Ok button appears.
SUBMIT_TIMEOUT = 8.0
CANCEL_TIMEOUT = 3.0
# How long to wait for the async farm/queue name refresh to land in the
# accessibility tree after the submitter window opens.
FARM_RESOLVE_TIMEOUT = 5.0
# How long to wait for the export-bundle success dialog.
EXPORT_TIMEOUT = 3.0

_T = TypeVar("_T", bound="DeadlineApp")

# Track every process we launch so we can reap orphans at interpreter exit
# (or in the ``_reap_orphans`` fixture) even if a test forgets to clean up.
_LIVE_PROCS: "weakref.WeakSet[subprocess.Popen]" = weakref.WeakSet()


def _register(proc: subprocess.Popen) -> None:
    _LIVE_PROCS.add(proc)


def _terminate(proc: subprocess.Popen, timeout: float = TERMINATE_TIMEOUT) -> None:
    """SIGKILL a subprocess (and its process group on POSIX) and reap it.

    On POSIX the subprocess is started with ``start_new_session=True``, so
    SIGKILL'ing the group also kills any dbus / AT-SPI / LaunchServices
    helpers Qt spawned. On Windows ``Popen.kill()`` calls TerminateProcess
    which is synchronous enough for our purposes.
    """
    if proc.poll() is not None:
        return
    if sys.platform != "win32":
        import os
        import signal

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass
    else:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass


def reap_all() -> None:
    """Terminate any subprocesses still tracked in ``_LIVE_PROCS``.

    Exposed both for the pytest session fixture and the atexit hook.
    """
    for proc in list(_LIVE_PROCS):
        _terminate(proc)


atexit.register(reap_all)


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
        capture_stdio: bool = False,
        dialog_name: Optional[str] = None,
    ) -> _T:
        """Spawn ``deadline <args>`` and attach to its accessibility tree.

        Args:
            args: Argv after ``deadline`` (e.g. ``["bundle", "gui-submit", path]``).
            env: Env vars for the subprocess.
            timeout: Seconds to wait for the window + dialog to appear.
            capture_stdio: When True, pipe stdout/stderr into the Popen so the
                test can read them. Defaults to False (DEVNULL) because leaving
                stdio connected to pytest's captured fds risks blocking on a
                full write buffer during teardown.
            dialog_name: Override the expected dialog window title. Used for
                launches that set ``--submitter-name`` (which changes the title).
        """
        cmd = [sys.executable, "-m", "deadline", *args]
        baseline = {a.name for a in xa11y.App.list()}
        # ``start_new_session`` puts the child into its own process group on
        # POSIX so ``_terminate`` can SIGKILL the whole group and tear down
        # any dbus / AT-SPI helpers Qt spawned alongside the main process.
        popen_kwargs: dict = dict(
            env=env,
            stdout=subprocess.PIPE if capture_stdio else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture_stdio else subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **popen_kwargs)
        _register(proc)
        try:
            app = _find_app(proc.pid, baseline, timeout)
            instance = cls(proc, app)
            if dialog_name is not None:
                instance._dialog_name = dialog_name  # type: ignore[attr-defined]
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

    @property
    def dialog_name(self) -> str:
        return getattr(self, "_dialog_name", self.DIALOG)

    def dialog(self) -> xa11y.Locator:
        # Qt's top-level QDialog is exposed as a Dialog role on macOS/Linux,
        # but on Windows it often registers as a Window without IsDialog set.
        name = self.dialog_name
        selector = (
            f'window[name="{name}"]' if sys.platform.startswith("win") else f'dialog[name="{name}"]'
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
        """Click a dismiss button; always ensure the subprocess is reaped.

        Tries the accessibility-driven path first so Qt can unwind cleanly,
        but never blocks on it — if the dialog or process doesn't respond
        within a couple of seconds we SIGKILL the process group
        unconditionally. ``_terminate`` is a no-op when the process has
        already exited, so this is safe to call in either order.
        """
        try:
            self.button(button_name).press()
            self.dialog().wait_detached(timeout=CLOSE_TIMEOUT)
            self.proc.wait(timeout=CLOSE_TIMEOUT)
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
        return self._combo_text(group="General settings", index=1)

    @property
    def conflict_resolution(self) -> str:
        return self._combo_text(group="General settings", index=0)

    def _combo_text(self, *, group: str, index: int) -> str:
        """Return the visible text of the Nth combo box in a group.

        General settings order: 0=conflict resolution, 1=log level, 2=language.
        """
        root = self.locator(f'group[name="{group}"]').element()
        combos = list(_iter_by_role(root, "combo_box"))
        combo = combos[index]
        value = getattr(combo, "value", None)
        if value:
            return value
        return combo.name or ""

    @property
    def auth_profile_text(self) -> str:
        """Text shown on the auth-status widget's profile button.

        Returns the best-matching push_button text; the widget has a few
        buttons (profile button, Log in, More info, Switch profile). The
        profile button is the only one whose text is not one of those
        fixed labels.
        """
        known = {"Log in", "Log out", "Switch profile", "More info"}
        for btn in _iter_by_role(self._app, "push_button"):
            name = btn.name or ""
            if name and name not in known and name not in {"Ok", "Cancel", "Apply"}:
                # Profile name shows either "(default)" or something containing
                # a profile string — there's only one such button.
                return name
        return ""


class SubmitterDialog(DeadlineApp):
    """Page object for ``deadline bundle gui-submit``."""

    DIALOG = "Deadline Cloud JobBundle Submitter"

    @classmethod
    def open(
        cls,
        bundle_dir: str,
        env: Optional[dict] = None,
        extra_args: Sequence[str] = (),
        dialog_name: Optional[str] = None,
        capture_stdio: bool = False,
    ) -> "SubmitterDialog":
        args = ["bundle", "gui-submit", *extra_args, bundle_dir]
        return cls.launch(
            args,
            env=env,
            dialog_name=dialog_name,
            capture_stdio=capture_stdio,
        )

    @property
    def job_name(self) -> str:
        return self.locator('text_field[name="Name"]').element().value or ""

    def export_bundle(self) -> None:
        """Click 'Export bundle' and dismiss the confirmation dialog."""
        self.button("Export bundle").press()
        ok = self.button("OK")
        ok.wait_visible(timeout=EXPORT_TIMEOUT)
        ok.press()

    def submit_and_ok(self, timeout: float = SUBMIT_TIMEOUT) -> None:
        """Click Submit, wait for the success dialog, click Ok.

        Uses the progress dialog's Ok button, which only appears once the
        mock backend has acknowledged CreateJob and GetJob returns a
        non-``CREATE_IN_PROGRESS`` status.
        """
        self.button("Submit").press()
        # Scope to the progress dialog so we don't match the parent
        # submitter's Ok/Cancel buttons (Qt exposes both simultaneously).
        ok = self._progress_button("Ok")
        ok.wait_visible(timeout=timeout)
        ok.press()

    def submit_then_cancel(self, cancel_timeout: float = CANCEL_TIMEOUT) -> None:
        """Click Submit then immediately Cancel on the progress dialog.

        Note: clicking Cancel only closes the progress dialog; the parent
        submitter window stays open because ``job_id`` is ``None`` on
        cancellation. Callers must close the submitter separately (e.g. by
        exiting the ``with`` block, which triggers ``close("Cancel")``).
        """
        self.button("Submit").press()
        cancel = self._progress_button("Cancel")
        cancel.wait_visible(timeout=cancel_timeout)
        cancel.press()

    def _progress_button(self, name: str) -> xa11y.Locator:
        """Locate a button inside the submission progress dialog."""
        progress_title = "AWS Deadline Cloud submission"
        dialog_selector = (
            f'window[name="{progress_title}"]'
            if sys.platform.startswith("win")
            else f'dialog[name="{progress_title}"]'
        )
        return self.locator(f'{dialog_selector} button[name="{name}"]')


def _iter_by_role(root, role: str):
    """Depth-first traversal yielding elements whose role matches ``role``."""
    try:
        if getattr(root, "role", None) == role:
            yield root
        for child in root.children():
            yield from _iter_by_role(child, role)
    except Exception:
        return
