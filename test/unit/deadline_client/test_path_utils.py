# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Tests for the path containment helpers.

Windows semantics go through an explicit ``ntpath`` so these run on every platform. UNC
paths cannot be built with ``os.path.join(os.sep, ...)``, so tests written against the
native path module silently skip them on POSIX.
"""

import itertools
import ntpath
import posixpath
import sys
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

import pytest

from deadline.client._path_utils import (
    _splitroot,
    is_absolute_path,
    is_any_path_contained,
    is_path_contained,
    normalized_path,
    path_components,
)


@pytest.mark.parametrize(
    "candidate, root, expected",
    [
        # Regression for https://github.com/aws-deadline/deadline-cloud/issues/1321:
        # a host-level UNC root contains the shares beneath it. os.path.commonpath
        # rejects this pair because it reads '\\192.168.20.20' as having no drive but
        # '\\192.168.20.20\projects' as being a drive.
        (
            r"\\192.168.20.20\projects\assets\FA_Anim\260304_FA_Anim.c4d",
            r"\\192.168.20.20",
            True,
        ),
        (r"\\host\share\file", r"\\host", True),
        (r"\\host\share", r"\\host", True),
        (r"\\host", r"\\host", True),
        # A trailing separator on a host-level root does not change containment.
        (r"\\host\share\file", "\\\\host\\", True),
        # A different host is not contained.
        (r"\\other\share\file", r"\\host", False),
        # A host that merely shares a string prefix is not contained.
        (r"\\host2\share\file", r"\\host", False),
        # Share-level roots behave like directories.
        (r"\\host\share\file", r"\\host\share", True),
        (r"\\host\share", r"\\host\share", True),
        (r"\\host\share2\file", r"\\host\share", False),
        # The host is not contained by one of its shares.
        (r"\\host", r"\\host\share", False),
        # Forward slashes are accepted on Windows.
        ("//host/share/file", r"\\host", True),
        (r"\\host\share\file", "//host/share", True),
        # Case-insensitive, matching the filesystem.
        (r"\\HOST\Share\File", r"\\host\share", True),
        # Drive letters.
        (r"C:\trusted\project\sub\file", r"C:\trusted\project", True),
        (r"C:\trusted\project", r"C:\trusted\project", True),
        (r"C:\trusted\project-secret\file", r"C:\trusted\project", False),
        (r"C:\trusted\projectextra", r"C:\trusted\project", False),
        (r"C:\trusted", r"C:\trusted\project", False),
        (r"c:\trusted\project\file", r"C:\TRUSTED\Project", True),
        # '..' is resolved before comparing.
        (r"C:\trusted\project\..\project-secret\f", r"C:\trusted\project", False),
        (r"C:\trusted\project\sub\..\f", r"C:\trusted\project", True),
        (r"\\host\share\a\..\..\b\f", r"\\host\share\a", False),
        # Windows clamps '..' at a share root, so this stays inside the share.
        (r"\\host\share\sub\..\..\other\f", r"\\host\share", True),
        # A '..' that normpath cannot resolve (there is no share to clamp against)
        # fails closed rather than being read as a component named '..'.
        (r"\\host\..\other\share\f", r"\\host", False),
        # Mismatched drives are simply not contained; no exception.
        (r"D:\trusted\project\file", r"C:\trusted\project", False),
        (r"\\host\share\file", r"C:\trusted\project", False),
        (r"C:\trusted\project\file", r"\\host\share", False),
        # A drive-relative path ('C:file' means 'file' relative to the cwd on C:)
        # cannot be resolved here, so it fails closed.
        ("C:file", "C:\\", False),
        # Relative paths are not contained by absolute roots and vice versa.
        (r"relative\file", r"C:\trusted", False),
        (r"C:\trusted\file", r"relative", False),
        # An extended-length prefix only turns off Win32 normalization; it denotes an
        # ordinary location, so it folds to the plain spelling and compares equal to it in
        # either direction. job-attachments carries the '\\?\' form through its internals
        # and strips it only at display boundaries, so a prefixed path does reach here.
        (r"\\?\C:\trusted\project\file", r"C:\trusted\project", True),
        (r"C:\trusted\project\file", r"\\?\C:\trusted\project", True),
        (r"\\?\UNC\host\share\file", r"\\host\share", True),
        (r"\\host\share\file", r"\\?\UNC\host\share", True),
        (r"\\?\UNC\host\share\file", r"\\host", True),
        (r"\\?\C:\trusted\project\file", r"\\?\C:\trusted\project", True),
        (r"\\?\UNC\host\share\file", r"\\?\UNC\host\share", True),
        # Folding does not weaken component anchoring: a sibling that merely shares a
        # string prefix is still outside the root.
        (r"\\?\C:\trusted\project-secret\f", r"\\?\C:\trusted\project", False),
        # A rooted, driveless root ('\') is a different path space than the UNC
        # namespace, so it must not contain remote paths -- nor they it.
        (r"\\attacker\share\evil", "\\", False),
        ("\\x", "\\\\", False),
        ("\\x", "\\", True),
        # The bare anchor names no server, so it is an ancestor of nothing -- treating it as
        # POSIX '/' would trust every reachable share. It counts as fully qualified, so a
        # caller filtering roots on is_absolute_path lets it reach here; '//' and
        # '\\?\UNC\' normalize to it.
        (r"\\host\share\file", "\\\\", False),
        (r"\\host\share\file", "\\\\\\\\", False),
        (r"\\host\share\file", "//", False),
        (r"\\host\share\file", "\\\\?\\UNC\\", False),
        (r"\\host", "\\\\", False),
        # The anchor is still reflexive, and a root naming an actual server still works.
        ("\\\\", "\\\\", True),
        (r"\\host\share\file", r"\\host", True),
        # 'C:' means the cwd on drive C:, so it contains drive-relative paths but not
        # the drive root's absolute contents.
        (r"C:\Windows", "C:", False),
        ("C:foo", "C:", True),
        (r"C:\a", "C:\\", True),
        # A prefixed drive with no plain spelling keeps its own space: a device path
        # must not alias the drive it resembles, in either direction.
        (r"\\.\C:\secret", "C:\\", False),
        (r"C:\secret", r"\\.\C:", False),
        (r"\\?\Volume{abc}\trusted\f", r"Volume{abc}\trusted", False),
        (r"\\?\Volume{abc}\trusted\f", r"\\?\Volume{abc}\trusted", True),
        # '\\?\C:' folds to the drive-relative 'C:' space and '\\?\C:\' to the drive root,
        # so each behaves as the plain spelling it denotes -- including keeping those two
        # spaces apart, which is why the last two disagree.
        ("C:foo", r"\\?\C:", True),
        (r"C:\a", r"\\?\C:", False),
        (r"C:\a\f", "\\\\?\\C:\\", True),
        (r"\\?\C:\a", "\\\\?\\C:\\", True),
        (r"\\?\C:\a", r"\\?\C:", False),
        # A relative path is contained in itself even when normpath leaves a leading
        # '..' it cannot cancel; only a '..' below the root can climb back out.
        (r"..\a", r"..\a", True),
        (r"..\a\b", r"..\a", True),
        (r"..\a\..\b", r"..\a", False),
    ],
)
def test_is_path_contained_windows(candidate, root, expected):
    assert is_path_contained(candidate, root, path_module=ntpath) is expected


@pytest.mark.parametrize(
    "candidate, root, expected",
    [
        ("/trusted/project", "/trusted/project", True),
        ("/trusted/project/sub/file", "/trusted/project", True),
        ("/trusted/project/file", "/trusted/project/", True),
        ("/trusted/project-secret/f", "/trusted/project", False),
        ("/trusted/projectextra", "/trusted/project", False),
        ("/trusted", "/trusted/project", False),
        ("/somewhere/else", "/trusted/project", False),
        ("/trusted/project/../project-secret/f", "/trusted/project", False),
        ("/trusted/project/sub/../f", "/trusted/project", True),
        ("relative/file", "/trusted/project", False),
        ("/trusted/file", "relative", False),
        # Everything absolute is contained by the root directory.
        ("/trusted/project", "/", True),
        # POSIX paths are case-sensitive, so a case variant fails closed.
        ("/Trusted/Project/f", "/trusted/project", False),
        # A backslash is an ordinary filename character on POSIX, so a Windows-style
        # UNC string is just a relative filename and matches nothing.
        (r"\\host\share\file", r"\\host", False),
        # A doubled root names the same file, so containment does not depend on how
        # many leading slashes either side was spelled with.
        ("//mnt/shared/f", "/mnt/shared", True),
        ("///mnt/shared/f", "/mnt/shared", True),
        ("/mnt/shared/f", "//mnt/shared", True),
        # Reflexive, and tolerant of a leading '..' shared with the root.
        ("../a", "../a", True),
        ("../a/b", "../a", True),
        ("../a/../b", "../a", False),
        ("..", "..", True),
        # A root of '.' is not the working directory: normpath renders it as a lone '.'
        # component, which prefixes nothing. Unreachable (callers pass absolute roots) and
        # fails closed, so it is pinned as a known limitation rather than fixed.
        ("rel", ".", False),
        ("rel/f", ".", False),
        ("/abs/f", ".", False),
        (".", ".", True),
        (".", "rel", False),
    ],
)
def test_is_path_contained_posix(candidate, root, expected):
    assert is_path_contained(candidate, root, path_module=posixpath) is expected


@pytest.mark.parametrize("path_module", [ntpath, posixpath])
def test_is_path_contained_is_reflexive(path_module):
    """Every path contains itself, whatever space it is in."""
    paths = (
        [
            r"\\host",
            r"\\host\share\a",
            "C:",
            "C:\\",
            r"C:\a",
            "\\",
            r"..\a",
            r"rel\f",
            ".",
            r"C:..\x",
            r"C:..\..\x",
            r"\\?\C:",
        ]
        if path_module is ntpath
        else ["/", "//", "/a/b", "../a", "../../a", "rel/f", "."]
    )
    for path in paths:
        assert is_path_contained(path, path, path_module=path_module) is True, path


@pytest.mark.parametrize(
    "root, contained",
    [
        # The reported case: a host-level root and a file on one of its shares. Before
        # Python 3.11 both normpath and splitdrive strip a share-less UNC path down to a
        # rooted-driveless one ('\\host' -> '\host', splitdrive -> no drive), which put the
        # root in a different path space than the candidate and left #1321 unfixed on 3.9
        # and 3.10.
        (r"\\host", True),
        ("\\\\host\\", True),
        (r"\\host\share", True),
        # A different server, and the bare anchor that names none, must not contain it.
        (r"\\host2", False),
        ("\\\\", False),
        ("\\", False),
    ],
)
def test_host_level_unc_root_containment_is_version_independent(root, contained):
    """Issue #1321 on every supported interpreter, not just 3.10+."""
    assert is_path_contained(r"\\host\share\f", root, path_module=ntpath) is contained


class _PreThreeElevenNtpath:
    """``ntpath`` as it behaved before Python 3.11 for a UNC path that names no share.

    Both ``normpath`` and ``splitdrive`` stripped such a path down to a rooted, driveless
    one. Injecting this exercises that branch on any interpreter, rather than only on the
    3.9 and 3.10 jobs -- the same reason the rest of this file injects ``ntpath``.
    """

    # Forces the _splitroot backport, which is what those versions had.
    splitroot = None

    @staticmethod
    def _is_shareless_unc(text: str) -> bool:
        return text.startswith("\\\\") and "\\" not in text[2:]

    @staticmethod
    def normpath(text: str) -> str:
        result = ntpath.normpath(text)
        if _PreThreeElevenNtpath._is_shareless_unc(result):
            return result[1:]
        return result

    @staticmethod
    def splitdrive(text: str):
        if _PreThreeElevenNtpath._is_shareless_unc(text):
            return "", text
        return ntpath.splitdrive(text)

    def __getattr__(self, name):
        return getattr(ntpath, name)


def test_host_level_unc_root_survives_pre_3_11_normpath():
    """A host-level root stays in the UNC space even when normpath collapses its anchor."""
    legacy: Any = _PreThreeElevenNtpath()
    # Confirm the proxy actually reproduces the old behavior, so this cannot pass vacuously.
    assert legacy.normpath("\\\\host") == "\\host"
    assert legacy.splitdrive("\\\\host") == ("", "\\\\host")

    assert path_components(r"\\host", path_module=legacy) == ["\\\\", "host"]
    assert is_path_contained(r"\\host\share\f", r"\\host", path_module=legacy) is True
    assert normalized_path(r"\\host", path_module=legacy) == r"\\host"
    # A rooted, driveless path must not be promoted into the UNC space by the restore.
    assert path_components(r"\host", path_module=legacy) == ["\\", "host"]
    assert is_path_contained(r"\\host\share\f", "\\", path_module=legacy) is False


@pytest.mark.parametrize(
    "prefixed, plain",
    [
        (r"\\?\C:\proj\a.txt", r"C:\proj\a.txt"),
        (r"\\?\UNC\host\share\a.txt", r"\\host\share\a.txt"),
    ],
)
def test_extended_length_prefix_agrees_with_plain_spelling(prefixed, plain):
    """A prefixed path is contained by exactly the roots its plain spelling is.

    job-attachments carries the '\\\\?\\' form through its internals and strips it only at
    display boundaries, so a prefixed path can reach a containment check. Treating it as its
    own path space would report it outside a root that plainly contains it.
    """
    roots = [
        plain,
        ntpath.dirname(plain),
        r"C:\proj",
        "C:\\",
        r"\\host\share",
        r"\\host",
        r"D:\other",
    ]
    for root in roots:
        assert is_path_contained(prefixed, root, path_module=ntpath) is is_path_contained(
            plain, root, path_module=ntpath
        ), root
        # A prefixed *root* folds the same way, so it behaves like its plain spelling.
        assert is_path_contained(plain, root, path_module=ntpath) is is_path_contained(
            plain, _prefixed_form(root), path_module=ntpath
        ), root


def _prefixed_form(path: str) -> str:
    """Spell ``path`` in extended-length form."""
    if path.startswith("\\\\"):
        return "\\\\?\\UNC" + path[1:]
    return "\\\\?\\" + path


def test_extended_length_prefix_resolves_dot_segments_uniformly():
    """normpath leaves '..' alone inside a '\\\\?\\' path before 3.10 and collapses it after.

    Folding to the plain spelling first makes the components the same on every supported
    interpreter, so containment does not depend on the running Python.
    """
    assert path_components(r"\\?\C:\a\..\b", path_module=ntpath) == ["c:\\", "b"]
    assert path_components(r"\\?\UNC\host\share\a\..\b", path_module=ntpath) == [
        "\\\\",
        "host",
        "share",
        "b",
    ]


@pytest.mark.parametrize("path_module", [ntpath, posixpath])
def test_splitroot_backport_matches_stdlib(path_module):
    """The Python < 3.12 shim must agree with splitroot on every path space.

    Python 3.12 added ``splitroot``; this project supports 3.9, so on older
    interpreters the shim is what distinguishes one path space from another. Hiding
    ``splitroot`` exercises the shim on any interpreter.
    """
    if not hasattr(path_module, "splitroot"):
        pytest.skip("stdlib splitroot unavailable, nothing to compare against")

    cases = (
        [
            "\\\\",
            "\\",
            r"\\srv",
            r"\\srv\share",
            r"\\srv\share\a",
            "C:",
            "C:\\",
            "C:foo",
            r"C:\a",
            "C:\\\\a",
            r"\\?\C:\a",
            r"\\?\UNC\srv\sh\a",
            r"\\?\Volume{abc}\a",
            r"\\.\C:\a",
            "",
            r"rel\f",
            "\\\\\\srv",
        ]
        if path_module is ntpath
        else ["/", "//", "///", "////", "/a", "//a/b", "///a", "rel", "rel/f", ""]
    )

    class _NoSplitroot:
        """Proxy that hides splitroot so the backport path is taken."""

        splitroot = None

        def __getattr__(self, name):
            return getattr(path_module, name)

    for case in cases:
        assert _splitroot(case, _NoSplitroot()) == path_module.splitroot(case), case


@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="pathlib parses a host-only UNC path as drive-less before 3.12, so it is not a"
    " usable oracle there.",
)
@pytest.mark.parametrize("path_module", [ntpath, posixpath])
def test_agrees_with_pathlib_except_for_unc_hosts(path_module):
    """Differential check against ``PurePath.is_relative_to`` as an independent oracle.

    pathlib folds a UNC server and share into one atom, so it cannot see a host-level root
    as an ancestor of its shares -- that gap is issue #1321 and the only sanctioned
    disagreement. Elsewhere pathlib is the reference. It does not resolve '..', so the
    corpus avoids inputs needing normalization; this supplements the explicit cases above
    rather than replacing them.
    """
    if path_module is ntpath:
        flavour: Any = PureWindowsPath
        # Spans every path space, including the three where earlier versions of this
        # module wrongly reported containment: bare roots, the device namespace, and
        # drive-relative paths.
        corpus = [
            r"\\srv",
            r"\\srv\share",
            r"\\srv\share\a",
            r"\\srv\other\b",
            r"\\srv2\share",
            "C:\\",
            r"C:\a",
            r"C:\a\b",
            r"C:\a-secret",
            r"D:\a",
            "rel",
            r"rel\f",
            "\\",
            "\\\\",
            r"\x",
            "C:",
            "C:foo",
            r"\\.\C:\a",
            r"\\?\Volume{abc}\a",
        ]
    else:
        flavour = PurePosixPath
        corpus = ["/", "/a", "/a/b", "/a-secret", "rel", "rel/f"]

    for candidate, root in itertools.permutations(corpus, 2):
        ours = is_path_contained(candidate, root, path_module=path_module)
        pathlibs = flavour(candidate).is_relative_to(flavour(root))
        if ours == pathlibs:
            continue
        # The only sanctioned disagreement: a UNC root that pathlib cannot see as an
        # ancestor because it folds the server and share into one atom. We may only be
        # more permissive than pathlib here, never elsewhere and never in reverse.
        assert path_module is ntpath, (candidate, root, ours, pathlibs)
        assert ours is True and pathlibs is False, (candidate, root, ours, pathlibs)
        # The root must name an actual server. The bare '\\\\' anchor names none, so it
        # is not a sanctioned disagreement -- pathlib is right to contain nothing there.
        assert flavour(root).drive.startswith("\\\\"), (candidate, root)
        assert str(root) != "\\\\", (candidate, root)
        assert flavour(candidate).drive.startswith("\\\\"), (candidate, root)
        assert not flavour(root).parts[1:], (candidate, root)


def test_is_any_path_contained():
    assert is_any_path_contained("/a/f", ["/b", "/a"]) is True
    assert is_any_path_contained("/c/f", ["/b", "/a"]) is False
    # No roots means nothing is contained.
    assert is_any_path_contained("/a/f", []) is False
    assert (
        is_any_path_contained(r"\\host\share\f", [r"D:\other", r"\\host"], path_module=ntpath)
        is True
    )


@pytest.mark.parametrize(
    "path, path_module, expected",
    [
        # The first component is the path space; a UNC server and share are ordinary
        # parts beneath the '\\\\' anchor, which is what lets a host-level root
        # contain them.
        (r"\\host", ntpath, ["\\\\", "host"]),
        ("\\\\host\\", ntpath, ["\\\\", "host"]),
        (r"\\host\share", ntpath, ["\\\\", "host", "share"]),
        (r"\\host\share\a\b", ntpath, ["\\\\", "host", "share", "a", "b"]),
        ("C:\\", ntpath, ["c:\\"]),
        (r"C:\a", ntpath, ["c:\\", "a"]),
        # 'C:' (drive-relative, meaning the cwd on C:) is a different space than 'C:\'.
        ("C:", ntpath, ["c:"]),
        ("C:foo", ntpath, ["c:", "foo"]),
        # A rooted, driveless path is its own space, distinct from the UNC anchor.
        ("\\", ntpath, ["\\"]),
        ("\\\\", ntpath, ["\\\\"]),
        # An extended-length prefix only turns off Win32 normalization: it denotes the
        # same location, so it folds to the plain spelling rather than occupying a space
        # of its own. Otherwise a prefixed path reads as outside a root that plainly
        # contains it, which is the same false negative as issue #1321.
        (r"\\?\C:\a", ntpath, ["c:\\", "a"]),
        (r"\\?\c:\a", ntpath, ["c:\\", "a"]),
        (r"\\?\C:", ntpath, ["c:"]),
        ("//?/C:/a", ntpath, ["c:\\", "a"]),
        (r"\\?\UNC\host\share", ntpath, ["\\\\", "host", "share"]),
        (r"\\?\UNC\host\share\f", ntpath, ["\\\\", "host", "share", "f"]),
        (r"\\?\unc\host\share\f", ntpath, ["\\\\", "host", "share", "f"]),
        (r"\\?\UNC\host", ntpath, ["\\\\", "host"]),
        # 'UNC' alone names no server, so it folds to the bare anchor, which contains
        # nothing rather than prefixing every reachable share.
        (r"\\?\UNC", ntpath, ["\\\\"]),
        # These denote no plain path, so they keep their prefix and a space of their own
        # and cannot alias the drive or UNC path they resemble.
        (r"\\?\Volume{abc}\a", ntpath, ["\\\\?\\volume{abc}\\", "a"]),
        (r"\\?\GLOBALROOT\Device\X\f", ntpath, ["\\\\?\\globalroot\\", "device", "x", "f"]),
        (r"\\.\C:\a", ntpath, ["\\\\.\\c:\\", "a"]),
        ("/", posixpath, ["/"]),
        ("/a/b", posixpath, ["/", "a", "b"]),
        ("/a/b/", posixpath, ["/", "a", "b"]),
        ("a/b", posixpath, ["a", "b"]),
        # '//foo' and '/foo' are the same file on every supported platform, so a
        # doubled root collapses rather than forming a separate namespace.
        ("//a/b", posixpath, ["/", "a", "b"]),
        ("///a/b", posixpath, ["/", "a", "b"]),
    ],
)
def test_path_components(path, path_module, expected):
    assert path_components(path, path_module=path_module) == expected


@pytest.mark.parametrize(
    "path, path_module, expected",
    [
        # The reason this exists rather than calling isabs directly: before Python 3.11
        # ntpath.isabs tests what splitdrive leaves behind, and for a UNC path naming a
        # share splitdrive consumes the whole string -- so isabs(r"\\host\s1") is False
        # there. Callers gate trust on this, so a valid root was dropped and a valid
        # PATH parameter value rejected.
        (r"\\host\s1", ntpath, True),
        (r"\\host\s1\f", ntpath, True),
        ("\\\\host\\", ntpath, True),
        (r"\\host", ntpath, True),
        # The bare anchor names no server but is still fully qualified, so it reaches the
        # containment check -- which rejects it, as test_is_path_contained_windows pins.
        ("\\\\", ntpath, True),
        (r"C:\a", ntpath, True),
        ("C:\\", ntpath, True),
        (r"\\?\C:\a", ntpath, True),
        (r"\\?\UNC\host\share", ntpath, True),
        (r"\\.\C:\a", ntpath, True),
        # A rooted, driveless path names the current drive's root, not the working
        # directory, so it is absolute. ntpath.isabs agrees through 3.12 and disagrees from
        # 3.13; answering from the anchor keeps the verdict version-independent, and a
        # cross-platform caller passing '/a' roots on Windows depends on this.
        ("\\", ntpath, True),
        (r"\x", ntpath, True),
        ("/a", ntpath, True),
        ("/", ntpath, True),
        # Drive-relative needs the working directory on that drive, which is exactly what
        # the known-root hardening must not let a caller supply implicitly.
        ("C:", ntpath, False),
        ("C:foo", ntpath, False),
        ("rel", ntpath, False),
        (r"rel\f", ntpath, False),
        (r"..\a", ntpath, False),
        ("", ntpath, False),
        (".", ntpath, False),
        ("/a", posixpath, True),
        ("//a", posixpath, True),
        ("rel/f", posixpath, False),
        ("../a", posixpath, False),
        ("", posixpath, False),
    ],
)
def test_is_absolute_path(path, path_module, expected):
    assert is_absolute_path(path, path_module=path_module) is expected


def test_is_absolute_path_never_accepts_a_working_directory_relative_path():
    """The property the known-root hardening depends on.

    ``ntpath.isabs`` disagrees with itself across supported versions in two places -- a UNC
    path naming a share (False before 3.11) and a rooted, driveless path (True through 3.12)
    -- so it cannot be the reference. What must hold on every version is narrower: a path
    that needs the working directory to resolve is never absolute, because such a root would
    let the directory the shell happens to be in become trusted.
    """
    relative = {
        ntpath: ["rel", r"rel\f", r"..\a", ".", "", "C:", "C:foo", r"C:..\x"],
        posixpath: ["rel", "rel/f", "../a", ".", ""],
    }
    for path_module, paths in relative.items():
        for path in paths:
            assert is_absolute_path(path, path_module=path_module) is False, (path_module, path)


@pytest.mark.parametrize(
    "path, path_module, expected",
    [
        # The reason this exists rather than calling normpath directly: before Python 3.11
        # normpath collapses the leading pair on a UNC path that names no share, which moves
        # a host-level known-asset root out of the UNC space so it matches none of its own
        # shares. _filter_redundant_known_paths feeds its output to _is_known_path.
        (r"\\host", ntpath, r"\\host"),
        ("\\\\host\\", ntpath, r"\\host"),
        ("\\\\", ntpath, "\\\\"),
        (r"\\host\share\a\..\b", ntpath, r"\\host\share\b"),
        # Case is preserved, unlike the components used for comparison.
        (r"\\Host\Share", ntpath, r"\\Host\Share"),
        (r"C:\A\.\b\..\c", ntpath, r"C:\A\c"),
        ("C:/a/b", ntpath, r"C:\a\b"),
        # An extended-length prefix folds to the plain path it denotes.
        (r"\\?\C:\a", ntpath, r"C:\a"),
        (r"\\?\UNC\host\share\f", ntpath, r"\\host\share\f"),
        ("/a/", posixpath, "/a"),
        ("/a/b/../c", posixpath, "/a/c"),
        ("/", posixpath, "/"),
    ],
)
def test_normalized_path(path, path_module, expected):
    assert normalized_path(path, path_module=path_module) == expected


def test_path_components_preserves_case_when_asked():
    assert path_components(r"\\Host\Share\File", path_module=ntpath, normalize_case=False) == [
        "\\\\",
        "Host",
        "Share",
        "File",
    ]
