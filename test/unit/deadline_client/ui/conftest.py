# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
import pytest

try:
    from pytestqt.plugin import qtbot  # noqa: F401
except ImportError:

    @pytest.fixture
    def qtbot():
        pytest.skip("pytest-qt not installed")
