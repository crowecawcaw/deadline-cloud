# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""Tests for the DeadlineLoginDialog."""

from unittest.mock import patch

import pytest

try:
    from qtpy.QtWidgets import QDialog, QMessageBox
    from deadline.client.ui.dialogs.deadline_login_dialog import DeadlineLoginDialog
except ImportError:
    pytest.importorskip("deadline.client.ui.dialogs.deadline_login_dialog")


def _make_dialog_without_init():
    """
    Build a DeadlineLoginDialog instance without running its __init__.

    DeadlineLoginDialog.__init__ starts a background login thread and connects
    several signals (including Signal(BaseException)). We only want to exercise
    the exec_() return-value logic here, so we initialize just the QMessageBox
    base and leave the login machinery untouched.
    """
    dialog = DeadlineLoginDialog.__new__(DeadlineLoginDialog)
    QMessageBox.__init__(dialog)
    return dialog


class TestDeadlineLoginDialogReturnValue:
    """Tests for the truthiness returned by exec_()/login()."""

    def test_exec_returns_true_on_successful_login(self, qtbot):
        """
        A successful login calls self.accept(), which makes the underlying
        QMessageBox.exec_() return QDialog.Accepted. DeadlineLoginDialog.exec_()
        must report this as True.
        """
        dialog = _make_dialog_without_init()
        qtbot.addWidget(dialog)

        with patch.object(QMessageBox, "exec_", return_value=QDialog.DialogCode.Accepted):
            assert dialog.exec_() is True

    def test_exec_returns_false_on_rejected_login(self, qtbot):
        """A canceled/rejected dialog (QDialog.Rejected) must report False."""
        dialog = _make_dialog_without_init()
        qtbot.addWidget(dialog)

        with patch.object(QMessageBox, "exec_", return_value=QDialog.DialogCode.Rejected):
            assert dialog.exec_() is False
