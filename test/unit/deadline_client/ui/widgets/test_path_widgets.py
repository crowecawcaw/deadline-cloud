# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""Tests for the path picker widgets' home-directory collapsing."""

import ntpath
import posixpath

import pytest

# importorskip, not a try/except: it binds the module when Qt is available and skips the
# file when it is not, where the except branch would leave the name unbound and every case
# would fail with NameError instead.
_path_widgets = pytest.importorskip("deadline.client.ui.widgets.path_widgets")
_collapse_user_dir = _path_widgets._collapse_user_dir


def _home_module(module, home):
    """``module`` with ``expanduser`` pinned to ``home``, so the cases do not depend on
    the home directory of whoever runs them."""

    class _PinnedHome:
        @staticmethod
        def expanduser(path):
            return home if path == "~" else path

        def __getattr__(self, name):
            return getattr(module, name)

    return _PinnedHome()


class TestCollapseUserDir:
    """
    The collapsed text is written straight into settings by the config dialog
    (job_history_dir, job_bundle_default_directory), so a path that is not really inside
    the home directory must come back untouched rather than rewritten.
    """

    @pytest.mark.parametrize(
        "path, expected",
        [
            (r"C:\Users\bob\projects\scene.ma", r"~\projects\scene.ma"),
            (r"C:\Users\bob\scene.ma", r"~\scene.ma"),
            (r"C:\Users\bob", "~"),
            # Case-insensitive, like the filesystem.
            (r"c:\users\BOB\projects\scene.ma", r"~\projects\scene.ma"),
            # A sibling that merely shares a string prefix is not inside the home
            # directory. Slicing by the home directory's length rewrote the first of
            # these to '~\r\projects\scene.ma' and the second to '\projects\scene.ma',
            # because join() drops the '~' when what follows is rooted.
            (r"C:\Users\bobby\projects\scene.ma", r"C:\Users\bobby\projects\scene.ma"),
            (r"C:\Users\bob2\projects\scene.ma", r"C:\Users\bob2\projects\scene.ma"),
            # Elsewhere entirely, including another path space.
            (r"D:\projects\scene.ma", r"D:\projects\scene.ma"),
            (r"\\host\share\scene.ma", r"\\host\share\scene.ma"),
            (r"C:\Users", r"C:\Users"),
        ],
    )
    def test_windows(self, path, expected):
        assert (
            _collapse_user_dir(path, path_module=_home_module(ntpath, r"C:\Users\bob")) == expected
        )

    @pytest.mark.parametrize(
        "path, expected",
        [
            ("/home/bob/projects/scene.ma", "~/projects/scene.ma"),
            ("/home/bob", "~"),
            ("/home/bobby/projects/scene.ma", "/home/bobby/projects/scene.ma"),
            ("/home/bob2/projects/scene.ma", "/home/bob2/projects/scene.ma"),
            ("/mnt/share/scene.ma", "/mnt/share/scene.ma"),
            ("/home", "/home"),
            # POSIX paths are case-sensitive, so a case variant is a different directory.
            ("/home/BOB/projects/scene.ma", "/home/BOB/projects/scene.ma"),
        ],
    )
    def test_posix(self, path, expected):
        assert (
            _collapse_user_dir(path, path_module=_home_module(posixpath, "/home/bob")) == expected
        )

    def test_round_trips_through_expanduser(self):
        """The collapsed text is expanded again when the widget is read back, so the two
        have to agree -- that is what makes a wrong collapse persist as a wrong path."""
        module = _home_module(posixpath, "/home/bob")
        for path in ("/home/bob/projects/scene.ma", "/home/bobby/projects/scene.ma"):
            collapsed = _collapse_user_dir(path, path_module=module)
            assert posixpath.expanduser(collapsed.replace("~", "/home/bob", 1)) == path
