# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""
Stack trace sanitizer for Deadline Cloud client telemetry.

**Design tenet:** no customer-provided content (file paths, bucket names,
exception message text, source code lines, local variable values, etc.) may
appear in telemetry. Only an allowlist of structured fields is emitted:
sanitized filename, line number, function name, and exception type. The
exception's message string and source-line context are dropped entirely
because we have no control over what third-party libraries put in them.
"""

import functools
import importlib.util
import os
import traceback
from typing import Dict, FrozenSet, List

# Packages we author or vendor — emitting paths relative to these is safe
# because the path itself only reveals which of our own modules raised.
_KNOWN_PACKAGES: FrozenSet[str] = frozenset(
    {
        "deadline",
        "openjd",
        "boto3",
        "botocore",
    }
)


def _normalize(path: str) -> str:
    """Normalize a path for equality comparison across OSes.

    Collapses Windows separators to forward slashes, strips any trailing
    separator, and applies case folding on case-insensitive filesystems so
    two spellings of the same location compare equal.
    """
    return os.path.normcase(path.replace("\\", "/").rstrip("/"))


@functools.lru_cache(maxsize=None)
def _known_package_install_dirs() -> Dict[str, List[str]]:
    """Map each known package name to the normalized directories it is installed in.

    These are the genuine on-disk locations of the package root (the directory
    that *contains* the package, e.g. ``.../site-packages``). They are used to
    distinguish a real framework frame from a customer directory that merely
    shares a package's name (e.g. a customer project tree rooted at
    ``~/deadline/...``). Only packages that are importable with a real
    filesystem location contribute an anchor; anything else (frozen modules
    without a file) is simply omitted.

    ``deadline`` and ``openjd`` are PEP 420 namespace packages, so a single
    namespace can be contributed by several distributions installed into
    different ``sys.path`` roots. ``submodule_search_locations`` may therefore
    hold more than one path; we record all of them so a genuine framework frame
    under any of those roots is recognized.

    The install locations are fixed for the life of the process, so the result
    is memoized with ``lru_cache`` (a deep traceback would otherwise recompute
    it, hitting ``find_spec`` once per known package per frame).
    """
    dirs: Dict[str, List[str]] = {}
    for name in _KNOWN_PACKAGES:
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError, AttributeError, ModuleNotFoundError):
            continue
        if spec is None:
            continue
        # Prefer the package directories themselves. A regular/namespace package
        # reports them via submodule_search_locations (e.g.
        # ".../site-packages/deadline"); a single-file module only has an origin
        # (".../foo.py"), whose parent directory is the container.
        origin = spec.origin
        locations = list(spec.submodule_search_locations or [])
        pkg_dirs: List[str] = []
        if locations:
            pkg_dirs = list(locations)
        elif origin and origin not in ("built-in", "frozen"):
            pkg_dirs = [os.path.dirname(origin)]
        normalized = [_normalize(os.path.abspath(p)) for p in pkg_dirs if p]
        if not normalized:
            continue
        dirs[name] = normalized
    return dirs


def _sanitize_path(filepath: str) -> str:
    """Replace a full file path with the package-relative portion or bare filename."""
    # Synthetic frame sources like "<string>", "<stdin>", or
    # "<frozen importlib._bootstrap>" don't reference the filesystem and are
    # already non-identifying, so pass them through unchanged.
    if filepath.startswith("<"):
        return filepath

    # Normalize Windows separators so the rest of the function only deals
    # with forward slashes.
    parts = filepath.replace("\\", "/").split("/")

    # Keep a package-relative path only when the reconstructed absolute prefix
    # equals the package's real install directory on this machine. A bare name
    # match is insufficient because a customer directory can share a package
    # name (e.g. ~/deadline/...) and keeping its segments would leak customer
    # content (violating the no-customer-content tenet). Scan right-to-left so
    # the deepest genuine match wins. (Ordinary pip installs under
    # site-packages are also covered by the generic site-packages branch
    # below; this branch additionally handles embedded / custom-sys.path
    # layouts where the package isn't under a site-packages directory.)
    install_dirs = _known_package_install_dirs()
    for i in range(len(parts) - 1, -1, -1):
        stem = parts[i].split(".")[0]
        anchors = install_dirs.get(stem)
        if anchors is not None and _normalize("/".join(parts[: i + 1])) in anchors:
            return "/".join(parts[i:])

    # Unknown third-party library installed into a venv: keep the
    # library-relative subpath but drop everything above site-packages
    # (which would otherwise leak the customer's home / venv layout).
    # Same right-to-left rationale as above — the real site-packages
    # directory is always at the tail.
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "site-packages" and i + 1 < len(parts):
            return "/".join(parts[i + 1 :])

    # Anything else (customer scripts, project trees) — keep only the
    # bare filename.
    return parts[-1]


def _sanitize_traceback(te: traceback.TracebackException) -> List[str]:
    """Recursively format a TracebackException chain using only allowlisted fields."""
    lines: List[str] = []

    # Handle chained exceptions (cause or context)
    if te.__cause__ is not None:
        lines.extend(_sanitize_traceback(te.__cause__))
        lines.append("\nThe above exception was the direct cause of the following exception:\n")
    elif te.__context__ is not None and not te.__suppress_context__:
        lines.extend(_sanitize_traceback(te.__context__))
        lines.append("\nDuring handling of the above exception, another exception occurred:\n")

    lines.append("Traceback (most recent call last):")
    for frame in te.stack:
        safe_path = _sanitize_path(frame.filename)
        lines.append(f'  File "{safe_path}", line {frame.lineno}, in {frame.name}')
        # Intentionally omit frame.line — source code context could
        # contain credentials, customer data, or other sensitive values

    # Only emit the exception type, not the message
    exc_name = te.exc_type.__qualname__ if te.exc_type else "UnknownException"
    lines.append(exc_name)

    return lines


def sanitize_exception(exc: BaseException) -> str:
    """Format and sanitize a live exception using only allowlisted fields."""
    te = traceback.TracebackException.from_exception(exc)
    return "\n".join(_sanitize_traceback(te))
