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

# All times are in seconds. These are tuned for mock-backend runs over
# localhost HTTP, where no real AWS calls happen. The ceilings are
# deliberately generous on top of the typical durations because Qt/
# AT-SPI startup under Xvfb on Linux CI and Windows UIA can both be
# substantially slower than a developer workstation, and a too-tight
# ceiling masks failures as timeouts instead of failing fast.
STARTUP_TIMEOUT = 15.0
CLOSE_TIMEOUT = 10.0
TERMINATE_TIMEOUT = 3.0
# How long to wait for ``CreateJob`` + ``GetJob`` polling to complete
# through the local mock before the success dialog's Ok button appears.
# The submitter has a 0.5s initial_delay + doubling backoff in its
# create-job waiter, so a single poll cycle costs ~1.5-2s on top of
# mock latency. 20s covers that plus progress-dialog paint.
SUBMIT_TIMEOUT = 20.0
# The Cancel button appears on the progress dialog immediately after
# clicking Submit, but under Xvfb the dialog paint is delayed.
CANCEL_TIMEOUT = 10.0
# How long to wait for the async farm/queue name refresh to land in the
# accessibility tree after the submitter window opens. The refresh is
# a chain of 2-3 HTTP roundtrips + a Qt signal hop.
FARM_RESOLVE_TIMEOUT = 10.0
# How long to wait for the export-bundle success dialog.
EXPORT_TIMEOUT = 10.0

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

    def elements_by_role(self, role: str) -> list:
        """Return every element in the app's accessibility tree with the
        given ``role`` (e.g. ``"spin_button"``), depth-first."""
        return list(_iter_by_role(self._app, role))

    def tree_contains_text(self, needle: str) -> bool:
        """True if any element in the app's tree has ``needle`` in its
        accessible ``name`` or ``value``.

        Useful for cross-platform checks where the same Qt widget exposes
        its text on different attributes and/or different roles depending
        on the accessibility backend (macOS AXAPI vs Linux AT-SPI vs
        Windows UIA). Callers should only reach for this when a precise
        selector is not platform-portable.
        """
        return any(_walk_contains_text(root, needle) for root in self._app.children())

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

    def close(self, button_name: str = "Cancel") -> None:
        """Dismiss the dialog and reap the subprocess.

        Strategy (in order):
        1. Try pressing the named button (e.g. Cancel/Ok) scoped to the
           main dialog, then platform close affordances (title-bar Close
           on Windows, traffic-light "close button" on macOS).
        2. If no button was found or pressed, send SIGTERM (POSIX) or
           TerminateProcess (Windows). The sitecustomize shim installs a
           SIGTERM→QApplication.quit() handler so the CLI can flush
           stdout before exiting.
        3. Wait briefly for the process to exit. Skip accessibility
           queries once the process is dead — on Linux, querying AT-SPI
           after the subprocess exits blocks for the D-Bus reply timeout
           (~25 s per call), which cascades into pytest-timeout failures.
        4. SIGKILL as last resort via ``_terminate``.
        """
        if self.proc.poll() is not None:
            _terminate(self.proc)
            return

        # Try accessibility-driven close. Scoped to the main dialog so
        # we don't hit a lingering progress-dialog button on Windows.
        for candidate in (button_name, "Close", "close button"):
            if self.proc.poll() is not None:
                break
            try:
                btn = self.dialog().descendant(f'button[name="{candidate}"]')
                if btn.exists():
                    btn.press()
                    break
            except xa11y.XA11yError:
                continue
        else:
            # No button found — graceful signal.
            self._signal_terminate()

        # Give the process a moment to exit cleanly.
        try:
            self.proc.wait(timeout=CLOSE_TIMEOUT)
        except subprocess.TimeoutExpired:
            self._signal_terminate()
            try:
                self.proc.wait(timeout=CLOSE_TIMEOUT)
            except subprocess.TimeoutExpired:
                pass
        _terminate(self.proc)

    def _signal_terminate(self) -> None:
        """Ask the subprocess to shut down gracefully via OS signals.

        POSIX: ``SIGTERM`` to the whole process group. The subprocess's
        ``sitecustomize.py`` (see ``conftest.py``) installs a handler
        that calls ``QApplication.quit()``, so ``app.exec()`` returns,
        ``_print_response`` runs, stdout is flushed and the interpreter
        exits with code 0.

        Windows: ``proc.terminate()`` (``TerminateProcess``). There is
        no portable way to ask a Qt process on Windows to unwind
        cleanly through a signal, so this is best-effort only — the
        JSON-capture tests rely on the accessibility-driven Close
        button path working on Windows.
        """
        if self.proc.poll() is not None:
            return
        if sys.platform != "win32":
            import os
            import signal

            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    self.proc.terminate()
                except OSError:
                    pass
        else:
            try:
                self.proc.terminate()
            except OSError:
                pass


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

    def wait_farm_resolved(
        self,
        farm_name: str = "TestFarm",
        timeout: float = FARM_RESOLVE_TIMEOUT,
    ) -> None:
        """Block until the async farm/queue name refresh has populated the UI.

        Without this, ``Submit`` is disabled because the submitter's
        ``api_availability`` + farm/queue resolution hasn't completed.

        Uses ``tree_contains_text`` rather than an xa11y ``wait_attached``
        locator because the Qt → UIA tree on Windows wraps the farm-name
        label in an extra ``group[name="Deadline Cloud settings"]``, and
        xa11y's descendant match + tree-staleness semantics there make
        ``wait_attached`` flake on otherwise-healthy dialogs. Falling back
        to a manual poll over the already-walked tree is portable.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.tree_contains_text(farm_name):
                return
            time.sleep(0.25)
        self.dump_tree()
        raise TimeoutError(
            f"Farm name {farm_name!r} did not appear in the submitter's "
            f"accessibility tree within {timeout}s"
        )

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

        The progress dialog mutates its bottom-row buttons between three
        states:
          * ``Cancel`` while the worker thread is running,
          * ``Ok`` after a successful submission, and
          * ``Close`` after a failed one.
        We can't just ``wait_visible('Ok')`` because on Windows the Qt
        dialog's title-bar decoration also registers a ``Close`` button
        (the window X) at the same depth as the dialog, which would
        cause a raw selector match to spuriously report "failed" state.
        Instead, poll the status_label text to distinguish success from
        failure, then press the corresponding button.
        """
        self.button("Submit").press()
        deadline = time.monotonic() + timeout
        status = ""
        while time.monotonic() < deadline:
            status = self._progress_status_label_text()
            if status and status != "Preparing files...":
                break
            time.sleep(0.2)
        if status == "Submission complete":
            for ok_name in ("Ok", "OK"):
                btn = self._progress_button(ok_name)
                if btn.exists():
                    btn.press()
                    return
            self.dump_tree()
            raise AssertionError("Submission complete but could not find Ok/OK button to press")
        if status in ("Submission error", "Submission canceled"):
            self.dump_tree()
            raise AssertionError(f"Submission failed: progress dialog status is {status!r}")
        self.dump_tree()
        raise TimeoutError(
            f"Progress dialog did not reach a terminal state within {timeout}s. "
            f"Last status_label: {status!r}"
        )

    def _progress_status_label_text(self) -> str:
        """Return the progress dialog's status_label text, or ""."""
        terminal_labels = {
            "Submission complete",
            "Submission error",
            "Submission canceled",
        }
        # Iterate the progress dialog's children to find the status label.
        # The QLabel is a direct child of the QDialog and exposes its text
        # via ``name``/``value`` on static_text elements, but under Windows
        # UIA it can also come through on an unnamed text_field's value.
        for elt in _iter_by_role(self._app, "static_text"):
            name = (getattr(elt, "name", "") or "").strip()
            if name == "Preparing files...":
                return name
            if name in terminal_labels:
                return name
        return ""

    def submit_then_cancel(self, cancel_timeout: float = CANCEL_TIMEOUT) -> None:
        """Click Submit then immediately Cancel on the progress dialog.

        Note: clicking Cancel only closes the progress dialog; the parent
        submitter window stays open because ``job_id`` is ``None`` on
        cancellation. Callers must close the submitter separately (e.g. by
        exiting the ``with`` block, which triggers ``close("Cancel")``).
        """
        self.button("Submit").press()
        cancel = self._progress_button("Cancel")
        try:
            cancel.wait_visible(timeout=cancel_timeout)
        except Exception:
            self.dump_tree()
            raise
        cancel.press()

    def dismiss_progress_close(self, timeout: float = CANCEL_TIMEOUT) -> None:
        """Wait for the progress dialog to close after cancel.

        After ``submit_then_cancel`` presses Cancel, the progress dialog's
        ``closeEvent`` cancels the worker thread, waits for it to finish,
        and then closes the dialog. This can take a moment — call this
        before ``close()`` so the progress dialog's transient Close button
        doesn't collide with the submitter's title-bar Close in the
        accessibility tree.
        """
        progress_title = "AWS Deadline Cloud submission"
        dialog_selector = (
            f'window[name="{progress_title}"]'
            if sys.platform.startswith("win")
            else f'dialog[name="{progress_title}"]'
        )
        try:
            self.locator(dialog_selector).wait_detached(timeout=timeout)
        except xa11y.XA11yError:
            pass

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


def _walk_contains_text(root, needle: str) -> bool:
    """True iff some element under ``root`` has ``needle`` in its name/value.

    Swallows per-element errors (e.g. the element was reparented mid-walk)
    rather than aborting the whole search, because the accessibility tree
    can mutate while we're iterating it.
    """
    try:
        for field in ("name", "value"):
            text = getattr(root, field, None) or ""
            if needle in text:
                return True
        for child in root.children():
            if _walk_contains_text(child, needle):
                return True
    except Exception:
        return False
    return False
