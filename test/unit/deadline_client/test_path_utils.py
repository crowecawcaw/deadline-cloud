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
    common_ancestor,
    is_any_path_contained,
    is_path_contained,
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
        # An extended-length path occupies its own path space rather than being folded into
        # the plain form it denotes, so comparing across the two spellings fails closed. No
        # caller needs the fold: every call site feeds realpath output or isabs-filtered
        # roots, neither of which carries a '\\?\' prefix.
        (r"\\?\C:\trusted\project\file", r"C:\trusted\project", False),
        (r"C:\trusted\project\file", r"\\?\C:\trusted\project", False),
        (r"\\?\UNC\host\share\file", r"\\host\share", False),
        (r"\\host\share\file", r"\\?\UNC\host\share", False),
        (r"\\?\UNC\host\share\file", r"\\host", False),
        (r"\\?\C:\trusted\project\file", r"\\?\C:\trusted\project", True),
        (r"\\?\UNC\host\share\file", r"\\?\UNC\host\share", True),
        (r"\\?\C:\trusted\project-secret\f", r"\\?\C:\trusted\project", False),
        # A rooted, driveless root ('\') is a different path space than the UNC
        # namespace, so it must not contain remote paths -- nor they it.
        (r"\\attacker\share\evil", "\\", False),
        ("\\x", "\\\\", False),
        ("\\x", "\\", True),
        # The bare anchor names no server, so it is an ancestor of nothing -- treating it as
        # POSIX '/' would trust every reachable share. ntpath.isabs('\\') is True, so a
        # caller filtering roots on that lets it through; '//' and '\\?\UNC\' normalize to it.
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
        # '\\?\C:' must contain paths in neither the drive-relative 'C:' space nor plain
        # 'C:\'. isabs reports it absolute, so a caller filtering on that lets it through.
        ("C:foo", r"\\?\C:", False),
        (r"C:\a", r"\\?\C:", False),
        (r"C:\a\f", "\\\\?\\C:\\", False),
        # Within its own space it behaves like the drive root it spells.
        (r"\\?\C:\a", r"\\?\C:", True),
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


@pytest.mark.parametrize("path_module", [ntpath, posixpath])
def test_common_ancestor_contains_its_inputs(path_module):
    """A non-empty common_ancestor must contain every path it was derived from."""
    paths = (
        [
            r"\\host\share\a",
            r"\\host\s2\b",
            r"C:\a\b",
            r"C:\a\c",
            "C:foo",
            r"..\a\b",
            r"..\a\c",
            # Drive-relative '..' puts the run behind an anchor, where a guard counting
            # from index zero would miss it.
            r"C:..\x",
            r"C:..\..\x",
            r"C:..\a\y",
            r"\\?\C:",
            r"\\?\C:\a",
        ]
        if path_module is ntpath
        else ["/a/b", "/a/c", "//a/d", "../a/b", "../a/c", "../../a/b", "rel/f", "rel/g"]
    )
    for first in paths:
        for second in paths:
            ancestor = common_ancestor([first, second], path_module=path_module)
            if not ancestor:
                continue
            assert is_path_contained(first, ancestor, path_module=path_module), (first, ancestor)
            assert is_path_contained(second, ancestor, path_module=path_module), (second, ancestor)


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
        # A prefixed drive keeps its prefix and stays whole, so it occupies a space of
        # its own and cannot alias the plain drive or UNC path it resembles.
        (r"\\?\C:\a", ntpath, ["\\\\?\\c:\\", "a"]),
        # The anchor carries its own trailing separator, so a share root and a file
        # under it share an anchor and containment holds between them.
        (r"\\?\UNC\host\share", ntpath, ["\\\\?\\unc\\host\\share\\"]),
        (r"\\?\UNC\host\share\f", ntpath, ["\\\\?\\unc\\host\\share\\", "f"]),
        (r"\\?\Volume{abc}\a", ntpath, ["\\\\?\\volume{abc}\\", "a"]),
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


def test_path_components_preserves_case_when_asked():
    assert path_components(r"\\Host\Share\File", path_module=ntpath, normalize_case=False) == [
        "\\\\",
        "Host",
        "Share",
        "File",
    ]


@pytest.mark.parametrize(
    "paths, path_module, expected",
    [
        # The common ancestor of paths under one share, spelled with its real case.
        (
            [r"\\host\Share\Proj\a.txt", r"\\host\Share\Proj\sub\b.txt"],
            ntpath,
            r"\\host\Share\Proj",
        ),
        # Different shares on one host share only the host. os.path.commonpath raises
        # ValueError for this pair.
        ([r"\\host\s1\a", r"\\host\s2\b"], ntpath, r"\\host"),
        # Different hosts share nothing. There is no location above a UNC host, so the
        # bare '\\\\' that their leading components have in common is not an answer.
        ([r"\\host1\s\a", r"\\host2\s\b"], ntpath, ""),
        ([r"\\host1", r"\\host2"], ntpath, ""),
        # Different drives share nothing.
        ([r"C:\a\b", r"D:\a\b"], ntpath, ""),
        ([r"C:\a\b", r"\\host\share\b"], ntpath, ""),
        ([r"C:\proj\a", r"C:\proj\b"], ntpath, r"C:\proj"),
        ([r"C:\proj\a"], ntpath, r"C:\proj\a"),
        (["/a/b/c", "/a/b/d"], posixpath, "/a/b"),
        (["/a/b", "/c/d"], posixpath, "/"),
        # A doubled POSIX root is the same space as '/', so these behave like ordinary
        # absolute paths rather than a separate namespace.
        (["//a/b", "//c/d"], posixpath, "/"),
        (["//a/b", "//a/c"], posixpath, "/a"),
        (["/a", "/b"], posixpath, "/"),
        (["/a/b"], posixpath, "/a/b"),
        (["a/b", "/c/d"], posixpath, ""),
        ([], posixpath, ""),
        # Paths whose unresolved leading '..' runs differ in depth are rooted at
        # different unknown directories, so they share none. Positional comparison
        # would wrongly read the shared '..' as one directory and return '..', which
        # is not an ancestor of '../../up'. os.path.commonpath has that bug.
        (["../up", "../../up"], posixpath, ""),
        (["../../up", "../up"], posixpath, ""),
        ([r"..\up", r"..\..\up"], ntpath, ""),
        # The '..' run can sit behind an anchor, where a guard counting from index 0
        # would not see it. 'C:..' is the cwd's parent on C:, 'C:..\..' its grandparent.
        ([r"C:..\x", r"C:..\..\x"], ntpath, ""),
        ([r"C:..", r"C:..\.."], ntpath, ""),
        # Equal depth behind an anchor is still comparable.
        ([r"C:..\a\x", r"C:..\a\y"], ntpath, r"C:..\a"),
        # Equal '..' depth is comparable again.
        (["../a/x", "../a/y"], posixpath, "../a"),
        (["../../a/x", "../../a/y"], posixpath, "../../a"),
        # Relative inputs keep their own spelling and gain no leading separator. A
        # Windows-style path read under posixpath semantics is one of these, since
        # 'C:' is an ordinary component there rather than a drive.
        (["a/b/c", "a/b/d"], posixpath, "a/b"),
        (
            ["C:/Users/u/renders/i1.png", "C:/Users/u/renders/i2.png"],
            posixpath,
            "C:/Users/u/renders",
        ),
    ],
)
def test_common_ancestor(paths, path_module, expected):
    assert common_ancestor(paths, path_module=path_module) == expected
