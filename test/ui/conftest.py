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

# Make the upstream mock-backend module importable (it lives under
# `test/unit/deadline_client/` on mainline).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit" / "deadline_client"))
# Allow test modules to `from helpers import ...` under --confcutdir.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_deadline_backend import MockDeadlineBackend, start_server  # noqa: E402

# Strip botocore's `management.` host-prefix so the CLI subprocess talks
# directly to 127.0.0.1. Mirrors the shim used in `test/cli_e2e/conftest.py`.
_SITECUSTOMIZE = """
import botocore.awsrequest as _ar
_orig = _ar._urljoin
def _urljoin(endpoint_url, url_path, host_prefix):
    return _orig(endpoint_url, url_path, None)
_ar._urljoin = _urljoin
"""


@pytest.fixture
def mock_backend() -> Iterator[tuple[MockDeadlineBackend, str]]:
    """Fresh MockDeadlineBackend + HTTP server per test."""
    backend = MockDeadlineBackend()
    server, base_url, _ = start_server(backend)
    try:
        yield backend, base_url
    finally:
        server.shutdown()
        server.server_close()


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
        "PYTHONPATH": str(shim_dir) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    return backend, env
