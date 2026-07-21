# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Tests for deadline_yaml_dump, which dumps YAML like pyyaml's safe_dump but
saves multi-line strings with the "|" style and defaults to sort_keys=False.
"""

from deadline.client.job_bundle import deadline_yaml_dump


def test_deadline_yaml_dump_default_preserves_key_order():
    """By default, keys are not sorted."""
    result = deadline_yaml_dump({"b": 1, "a": 2})
    assert result == "b: 1\na: 2\n"


def test_deadline_yaml_dump_multiline_uses_pipe_style():
    """Multi-line strings use the '|' block style."""
    result = deadline_yaml_dump({"script": "line1\nline2\n"})
    assert result == "script: |\n  line1\n  line2\n"


def test_deadline_yaml_dump_accepts_sort_keys_override():
    """A caller may pass sort_keys explicitly without triggering a TypeError."""
    result = deadline_yaml_dump({"b": 1, "a": 2}, sort_keys=True)
    assert result == "a: 2\nb: 1\n"
