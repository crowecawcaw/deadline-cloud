# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Tests to verify that the deadline UI only uses QtCore, QtGui, and QtWidgets modules.

This ensures we can distribute a minimal Qt build without unnecessary modules.
"""

import subprocess
import sys

import pytest

ALLOWED_QT_MODULES = {"QtCore", "QtGui", "QtWidgets", "QtOpenGL", "QtOpenGLWidgets"}

# Test imports in subprocess to get clean module state
_TEST_SCRIPT = """
import sys
module_to_import = sys.argv[1]
exec(f"from deadline.client.ui import {module_to_import}" if "." not in module_to_import 
     else f"from deadline.client.ui.dialogs import {module_to_import.split('.')[-1]}")
qt_modules = {k.split('.')[-1] for k in sys.modules if k.startswith('PySide6.Qt')}
print(','.join(sorted(qt_modules)) if qt_modules else '')
"""


def _get_imported_qt_modules(module_path: str) -> set[str]:
    """Run import in subprocess and return set of Qt modules that were loaded."""
    result = subprocess.run(
        [sys.executable, "-c", _TEST_SCRIPT, module_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"Import failed: {result.stderr}")
    output = result.stdout.strip()
    return set(output.split(",")) if output else set()


def _assert_only_allowed_qt_modules(imported: set[str]):
    """Assert that only allowed Qt modules were imported."""
    disallowed = imported - ALLOWED_QT_MODULES
    assert not disallowed, f"Disallowed Qt modules imported: {disallowed}"


class TestOnlyQtCoreWidgetsGuiUsed:
    """Tests verifying only QtCore, QtWidgets, and QtGui are used."""

    def test_config_dialog_imports_only_allowed_qt_modules(self):
        """Verify deadline_config_dialog module only imports QtCore, QtGui, QtWidgets."""
        imported = _get_imported_qt_modules("dialogs.deadline_config_dialog")
        _assert_only_allowed_qt_modules(imported)

    def test_submit_dialog_imports_only_allowed_qt_modules(self):
        """Verify submit_job_to_deadline_dialog module only imports QtCore, QtGui, QtWidgets."""
        imported = _get_imported_qt_modules("dialogs.submit_job_to_deadline_dialog")
        _assert_only_allowed_qt_modules(imported)

    def test_job_bundle_submitter_imports_only_allowed_qt_modules(self):
        """Verify job_bundle_submitter module only imports QtCore, QtGui, QtWidgets."""
        imported = _get_imported_qt_modules("job_bundle_submitter")
        _assert_only_allowed_qt_modules(imported)
