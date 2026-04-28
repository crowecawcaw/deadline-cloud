# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Shared fixtures for `test/ui/` — launches the real `deadline` GUI as a
subprocess pointed at an in-process MockDeadlineBackend and drives it
through the accessibility tree via xa11y.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterator

import pytest

# Allow test modules to `from helpers import ...` under --confcutdir.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common.mock_deadline_backend import MockDeadlineBackend, start_server  # noqa: E402
from helpers import reap_all  # noqa: E402


@pytest.fixture(autouse=True)
def _reap_ui_subprocesses() -> Iterator[None]:
    """Kill any GUI subprocesses still alive at test end.

    Tests that forget ``with ... as app`` or whose ``try/finally`` fails to
    run would otherwise leave ``deadline`` subprocesses hanging around in
    the macOS dock (and on the pid table everywhere). Belt-and-suspenders:
    ``helpers.reap_all`` is also registered via ``atexit``.
    """
    yield
    reap_all()


# Subprocess shim run before the CLI imports. Three responsibilities:
#
#   1. Strip botocore's ``management.`` host-prefix so the CLI subprocess
#      talks directly to 127.0.0.1 (mirrors ``test/cli_e2e/conftest.py``).
#   2. Stub ``TelemetryClient.get_account_id`` so the background telemetry
#      thread doesn't hit the real STS endpoint with the fake AWS creds
#      the fixture sets. On failure, the production code ``print()``s an
#      ``InvalidClientTokenId`` message to **stdout**, which pollutes the
#      capture used by the ``--output json`` tests. Stubbing here (not in
#      production) keeps the fix test-scoped.
#   3. Install a POSIX ``SIGTERM`` handler that calls
#      ``QApplication.quit()`` so tests can ask a running ``deadline``
#      subprocess to unwind cleanly (run ``_print_response``, flush
#      stdout) before exiting. By default Python's SIGTERM handler
#      terminates the process immediately, so the CLI's trailing
#      ``click.echo(json.dumps(...))`` never runs and the ``--output
#      json`` tests see empty stdout.
_SITECUSTOMIZE = """
# Test shim: runs before the CLI imports. Keep imports lazy — this file
# gets loaded by every Python subprocess whose PYTHONPATH starts with
# this directory, including the non-GUI ``deadline config get/set``
# subprocesses our tests use as helpers.
import botocore.awsrequest as _ar
_orig_urljoin = _ar._urljoin
def _urljoin(endpoint_url, url_path, host_prefix):
    return _orig_urljoin(endpoint_url, url_path, None)
_ar._urljoin = _urljoin

try:
    from deadline.client.api import _telemetry as _t

    def _stub_get_account_id(self, boto3_session):
        return None

    _t.TelemetryClient.get_account_id = _stub_get_account_id
except Exception:
    pass

import signal as _signal


def _on_sigterm(signum, frame):
    # Ask the Qt event loop to exit so the CLI's trailing
    # ``_print_response`` can run + flush stdout before exit.
    try:
        from qtpy.QtWidgets import QApplication as _QApp
        _inst = _QApp.instance()
        if _inst is not None:
            _inst.quit()
            return
    except Exception:
        pass
    import sys as _sys
    _sys.exit(0)


try:
    _signal.signal(_signal.SIGTERM, _on_sigterm)
except (ValueError, OSError):
    pass

# Qt's event loop blocks in C++, so Python signal handlers only fire at
# bytecode boundaries — which don't happen inside ``app.exec()`` unless
# something forces a Python callback. Arrange for a no-op 100ms
# ``QTimer`` to start alongside any ``QApplication`` the CLI
# instantiates, giving Python a regular chance to run pending signal
# handlers. Deferred via a ``sys.meta_path`` finder so non-GUI CLI
# subprocesses (``deadline config get/set`` helpers) don't pay the Qt
# import cost at interpreter startup.
import sys as _sys


class _QtPyPostImportPatcher:
    \"\"\"Patch QApplication.__init__ the first time qtpy.QtWidgets is imported.\"\"\"

    def find_spec(self, fullname, path, target=None):
        if fullname != "qtpy.QtWidgets":
            return None
        # Unhook before importing so find_spec isn't called recursively.
        try:
            _sys.meta_path.remove(self)
        except ValueError:
            return None
        try:
            from qtpy import QtCore as _qc
            from qtpy import QtWidgets as _qw
        except ImportError:
            return None

        _orig_qa_init = _qw.QApplication.__init__

        def _qa_init(self, *args, **kwargs):
            _orig_qa_init(self, *args, **kwargs)
            self._sigterm_pulse = _qc.QTimer(self)
            self._sigterm_pulse.setInterval(100)
            self._sigterm_pulse.timeout.connect(lambda: None)
            self._sigterm_pulse.start()

        _qw.QApplication.__init__ = _qa_init
        # Returning None lets the normal import machinery find the
        # already-loaded module via sys.modules.
        return None


_sys.meta_path.insert(0, _QtPyPostImportPatcher())
"""


@pytest.fixture(scope="session")
def _mock_backend_server() -> Iterator[tuple[MockDeadlineBackend, str]]:
    """Session-scoped MockDeadlineBackend + HTTP server."""
    backend = MockDeadlineBackend()
    server, base_url, _ = start_server(backend)
    try:
        yield backend, base_url
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def mock_backend(_mock_backend_server) -> Iterator[tuple[MockDeadlineBackend, str]]:
    """Per-test backend that clears state between tests."""
    backend, base_url = _mock_backend_server
    backend.clear()
    yield backend, base_url


@pytest.fixture
def deadline_env(tmp_path: Path, mock_backend) -> tuple[MockDeadlineBackend, dict]:
    """Env vars pointing the GUI subprocess at the mock backend with an
    isolated HOME and config file. Returns (backend, env)."""
    backend, deadline_url = mock_backend

    config_file = tmp_path / "deadline.config"
    config_file.write_text("")
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    (shim_dir / "sitecustomize.py").write_text(_SITECUSTOMIZE)
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    env = {
        **os.environ,
        "HOME": str(fake_home),
        "AWS_ENDPOINT_URL_DEADLINE": deadline_url,
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "us-west-2",
        "DEADLINE_CONFIG_FILE_PATH": str(config_file),
        # Belt-and-braces: opt out of telemetry so the client's
        # background thread never tries to hit ``/2023-10-12/telemetry``
        # (not implemented by the mock — 404s can cascade into
        # BrokenPipes on stderr). The sitecustomize shim above also
        # stubs ``TelemetryClient.get_account_id`` so the stdout of the
        # ``--output json`` tests isn't polluted by STS-call failures.
        "DEADLINE_CLOUD_TELEMETRY_OPT_OUT": "true",
        # Force the subprocess's stdout/stderr to line-buffer (and flush
        # on newline) so --output json tests that capture stdout via a
        # subprocess pipe actually see the JSON payload written by
        # ``click.echo`` before the subprocess terminates. Without this,
        # Python detects the non-TTY destination and switches stdout to
        # block buffering, so the final JSON can stay in the in-process
        # buffer and be lost when we SIGKILL on shutdown.
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": str(shim_dir) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    return backend, env
