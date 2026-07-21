# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""Unit tests for the ``deadline job trace-schedule`` helper functions.

These focus on partial / in-flight job data that previously crashed the
command with ``ZeroDivisionError`` or ``KeyError``.
"""

from __future__ import annotations

import pytest

from deadline.client.cli._groups._trace_schedule import _print_summary


def _zero_accumulators(**overrides):
    accumulators = {
        "sessionCount": 0,
        "sessionActionCount": 0,
        "taskRunCount": 0,
        "envActionCount": 0,
        "syncJobAttachmentsCount": 0,
        "sessionDuration": 0,
        "sessionActionDuration": 0,
        "taskRunDuration": 0,
        "envActionDuration": 0,
        "syncJobAttachmentsDuration": 0,
    }
    accumulators.update(overrides)
    return accumulators


def test_print_summary_no_completed_durations_does_not_divide_by_zero(capsys):
    """An in-flight job with no accumulated duration must not crash on the
    percentage math (session_total_duration == 0)."""
    _print_summary(_zero_accumulators())

    out = capsys.readouterr().out
    assert "Task Run Count: 0" in out
    # Percentages should degrade gracefully rather than crashing.
    assert "N/A" in out


def test_print_summary_zero_session_actions_does_not_divide_by_zero(capsys):
    """Overhead-per-action must not crash when there are zero session actions
    even if there is a non-zero session duration."""
    _print_summary(
        _zero_accumulators(
            sessionCount=1,
            sessionDuration=1_000_000,
            sessionActionCount=0,
        )
    )

    out = capsys.readouterr().out
    assert "Within-session Overhead Duration Per Action" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
