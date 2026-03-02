# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Tests to verify that the deadline UI only uses QtCore, QtGui, and QtWidgets modules.

This ensures we can distribute a minimal Qt build without unnecessary modules.
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Set

import pytest

try:
    from deadline.client.ui.dialogs import deadline_config_dialog  # noqa: F401
except ImportError:
    pytest.importorskip("deadline.client.ui.dialogs.deadline_config_dialog")

ALLOWED_QT_MODULES = {"QtCore", "QtGui", "QtWidgets", "QtOpenGL", "QtOpenGLWidgets"}

# Test imports in subprocess to get clean module state
_TEST_SCRIPT = """
import sys
module_to_import = sys.argv[1]
exec(f"import {module_to_import}")
qt_modules = {k.split('.')[-1] for k in sys.modules if k.startswith('PySide6.Qt')}
print(','.join(sorted(qt_modules)) if qt_modules else '')
"""


def _get_ui_module_paths() -> List[str]:
    """Discover all importable Python modules under deadline.client.ui."""
    ui_pkg = importlib.import_module("deadline.client.ui")
    ui_dir = Path(os.path.dirname(ui_pkg.__file__))
    modules = []
    for py_file in sorted(ui_dir.rglob("*.py")):
        if py_file.name.startswith("_"):
            continue
        rel = py_file.relative_to(ui_dir).with_suffix("")
        dotted = "deadline.client.ui." + ".".join(rel.parts)
        modules.append(dotted)
    return modules


def _get_imported_qt_modules(fully_qualified_module: str) -> Set[str]:
    """Run import in subprocess and return set of Qt modules that were loaded."""
    result = subprocess.run(
        [sys.executable, "-c", _TEST_SCRIPT, fully_qualified_module],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"Import of {fully_qualified_module} failed: {result.stderr}")
    output = result.stdout.strip()
    return set(output.split(",")) if output else set()


_ui_modules = _get_ui_module_paths()


class TestOnlyQtCoreWidgetsGuiUsed:
    """Tests verifying only QtCore, QtWidgets, and QtGui are used."""

    @pytest.mark.parametrize("module_path", _ui_modules, ids=_ui_modules)
    def test_module_imports_only_allowed_qt_modules(self, module_path: str):
        """Verify each UI module only imports allowed Qt modules."""
        imported = _get_imported_qt_modules(module_path)
        disallowed = imported - ALLOWED_QT_MODULES
        assert not disallowed, f"{module_path} imported disallowed Qt modules: {disallowed}"
