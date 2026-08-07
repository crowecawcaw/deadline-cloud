# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Path containment helpers that understand Windows UNC paths.

``os.path.commonpath`` raises ``ValueError`` rather than comparing a host-level UNC path
(``\\\\server``) with a path under one of its shares: ``splitdrive`` reports no drive for
the former and ``\\\\server\\share`` for the latter. It raises the same way for two shares
on one host. Callers that read that exception as "not contained" reject valid paths.

These helpers compare paths component by component instead, so a UNC host is an ordinary
ancestor of its shares. Every function takes an explicit ``path_module``
(``ntpath``/``posixpath``), so Windows semantics stay testable on non-Windows hosts.

Comparisons are lexical -- pass ``realpath`` output in if symlinks must be resolved -- and
never raise. Anything unresolvable fails closed, since callers use containment to decide
whether a path is trusted.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Sequence

__all__ = [
    "common_ancestor",
    "is_any_path_contained",
    "is_path_contained",
    "path_components",
]

# Anchors the UNC path space. It names no server on its own, so unlike POSIX '/' it is not
# a directory and contains nothing.
_UNC_ANCHOR = "\\\\"

_PARDIR = ".."


def _splitroot(text: str, path_module: Any) -> tuple[str, str, str]:
    """``path_module.splitroot``, backported for Python < 3.12.

    ``(drive, root)`` is what distinguishes one path space from another: ``('', '\\\\')``
    (rooted but driveless) and ``('\\\\\\\\', '')`` (UNC) both consist only of separators.
    """
    splitroot = getattr(path_module, "splitroot", None)
    if splitroot is not None:
        return splitroot(text)

    drive, rest = path_module.splitdrive(text)
    separators = path_module.sep + (getattr(path_module, "altsep", None) or "")
    if rest[:1] not in separators or not rest:
        return drive, "", rest
    leading = len(rest) - len(rest.lstrip(separators))
    # POSIX gives '//' its own root spelling, but collapses three or more.
    root_length = 2 if (leading == 2 and path_module.sep == "/") else 1
    return drive, rest[:root_length], rest[root_length:]


def _split_anchored(path: Any, path_module: Any, normalize_case: bool) -> tuple[str, list[str]]:
    """Return ``(anchor, parts)``, where ``anchor + sep.join(parts)`` reconstructs ``path``.

    The anchor names the path space and carries its own trailing separator. A UNC anchor is
    the bare ``\\\\`` marker, leaving the server and share as ordinary parts -- which is what
    lets a host-level root contain the shares beneath it.
    """
    text = str(path)
    windows = path_module.sep == "\\"
    if windows:
        text = text.replace("/", "\\")
    text = path_module.normpath(text)
    if normalize_case:
        text = path_module.normcase(text)

    drive, root, tail = _splitroot(text, path_module)
    parts = [part for part in tail.split(path_module.sep) if part]

    if not windows:
        # '//foo' and '/foo' are the same file on the platforms this client targets.
        return (path_module.sep if root else ""), parts

    if drive[:4] in ("\\\\?\\", "\\\\.\\"):
        # These prefixes disable normalization: the drive is a whole anchor, so it never
        # aliases the plain path it resembles, and a share root and its files -- which
        # differ only by a trailing separator -- still take the same anchor.
        return drive + path_module.sep, parts
    if drive.startswith(_UNC_ANCHOR):
        return _UNC_ANCHOR, [p for p in drive[len(_UNC_ANCHOR) :].split("\\") if p] + parts
    return drive + root, parts


def _leading_pardir_count(parts: list[str]) -> int:
    """Count the leading '..' run that ``normpath`` could not resolve.

    Counted on parts rather than whole components because an anchor can precede the run
    ('C:..\\x' is the parent of the working directory on drive C:).
    """
    count = 0
    for part in parts:
        if part != _PARDIR:
            break
        count += 1
    return count


def path_components(
    path: Any,
    *,
    path_module: Any = os.path,
    normalize_case: bool = True,
) -> list[str]:
    """Split ``path`` into the components used for ancestor comparisons.

    ``..`` segments are resolved first. The first component is the path space (``'/'``,
    ``'C:\\'``, ``'C:'`` for drive-relative, ``'\\\\'`` for UNC, absent when relative) and
    the rest are the path's parts, so comparing these lists component-wise confuses neither
    one path space for another nor a string prefix for a directory prefix.

    ``normalize_case`` lowercases components on Windows to match the filesystem.
    """
    anchor, parts = _split_anchored(path, path_module, normalize_case)
    return ([anchor] if anchor else []) + parts


def is_path_contained(
    path: Any,
    root: Any,
    *,
    path_module: Any = os.path,
) -> bool:
    """Return True iff ``path`` equals or is a descendant of ``root``.

    Containment is anchored on whole components, so a sibling that merely shares a string
    prefix (root ``/trusted/project`` vs path ``/trusted/project-secret``) is outside the
    root. Paths in unrelated spaces -- different drives, different UNC hosts, one relative
    and one absolute -- are not contained.
    """
    root_components = path_components(root, path_module=path_module)
    candidate_components = path_components(path, path_module=path_module)
    # A bare '\\' root names no server, so it is an ancestor of nothing -- otherwise it
    # would prefix, and so trust, every reachable share. ntpath.isabs lets it reach here.
    if root_components == [_UNC_ANCHOR]:
        return candidate_components == [_UNC_ANCHOR]
    if candidate_components[: len(root_components)] != root_components:
        return False
    # Shared leading '..' belongs to the root; one below it could climb back out.
    return _PARDIR not in candidate_components[len(root_components) :]


def is_any_path_contained(
    path: Any,
    roots: Iterable[Any],
    *,
    path_module: Any = os.path,
) -> bool:
    """Return True iff ``path`` is contained by any root in ``roots``."""
    return any(is_path_contained(path, root, path_module=path_module) for root in roots)


def common_ancestor(paths: Sequence[Any], *, path_module: Any = os.path) -> str:
    """Return the deepest directory containing every path in ``paths``.

    This is ``os.path.commonpath`` without the exceptions: paths in unrelated spaces return
    ``""`` rather than raising, and a UNC host is a valid answer for paths on different
    shares of one server. The result keeps the first path's spelling and, like
    ``commonpath``, is purely lexical.
    """
    if not paths:
        return ""

    split = [_split_anchored(p, path_module, normalize_case=True) for p in paths]
    normalized = [([a] if a else []) + parts for a, parts in split]
    anchor, spelled_parts = _split_anchored(paths[0], path_module, normalize_case=False)
    spelled = ([anchor] if anchor else []) + spelled_parts

    # '..' and '../..' are rooted at different unknown places, so runs of differing depth
    # share nothing. Comparing them positionally would return the shallower path, which is
    # not an ancestor of the deeper one -- os.path.commonpath has that bug.
    if len({_leading_pardir_count(parts) for _, parts in split}) > 1:
        return ""

    shared = min(len(components) for components in normalized)
    while shared > 0 and any(other[:shared] != normalized[0][:shared] for other in normalized):
        shared -= 1
    if shared == 0:
        return ""
    # Matching only the bare anchor means different servers, so no shared directory.
    if shared == 1 and normalized[0][0] == _UNC_ANCHOR:
        return ""

    # The anchor carries its own separator, so it abuts the first part directly.
    if anchor:
        return anchor + path_module.sep.join(spelled[1:shared])
    return path_module.sep.join(spelled[:shared])
