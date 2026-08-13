# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Real-SMB validation of UNC path containment.

Every other test of the path helpers models Windows lexically through ``ntpath``, which
is faithful to Windows' string rules but says nothing about SMB: whether a host-level UNC
path can be listed, whether ``realpath`` rewrites a mapped drive back to UNC form, or
whether a share walks like a directory. These run against a loopback share, so the
verdicts are checked against a real redirector.

Requires Windows and administrator rights; see .github/workflows/windows_smb_test.yml.
Regression coverage for https://github.com/aws-deadline/deadline-cloud/issues/1321.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Iterator

import pytest

from deadline.client._path_summary import common_ancestor
from deadline.client._path_utils import is_path_contained
from deadline.client.api._submit_job_bundle import (
    _filter_redundant_known_paths,
    _is_known_path,
)
from deadline.client.job_bundle.loader import validate_directory_symlink_containment
from deadline.client.exceptions import DeadlineOperationError

pytestmark = [
    pytest.mark.integ,
    pytest.mark.skipif(sys.platform != "win32", reason="SMB shares require Windows."),
]


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def smb_share(tmp_path_factory) -> Iterator[tuple[str, Path]]:
    """Share a local directory over SMB and yield ``(unc_root, local_path)``.

    ``unc_root`` is the share path (``\\\\<host>\\<share>``); the host-level root is
    derived from it by the tests that need one.
    """
    local_path = tmp_path_factory.mktemp("smb_export")
    share_name = f"dltest{uuid.uuid4().hex[:8]}"

    created = _run("net", "share", f"{share_name}={local_path}", "/GRANT:Everyone,FULL")
    if created.returncode != 0:
        pytest.skip(
            f"could not create an SMB share: {created.stdout.strip()} {created.stderr.strip()}"
        )

    # The loopback host name matters: 'localhost' and '127.0.0.1' are both valid UNC
    # hosts, but the machine name is what a real farm would use.
    unc_root = rf"\\{socket.gethostname()}\{share_name}"
    try:
        # Fail fast and clearly if the redirector cannot reach the new share, rather
        # than letting every assertion below fail with a confusing error.
        if not os.path.isdir(unc_root):
            pytest.skip(f"SMB share {unc_root} is not reachable from this host")
        yield unc_root, local_path
    finally:
        _run("net", "share", share_name, "/DELETE", "/Y")


def test_host_level_root_contains_share_contents(smb_share):
    """The reported bug: a '\\\\server' root must contain files on its shares."""
    unc_root, local_path = smb_share
    host_root = unc_root.rsplit("\\", 1)[0]

    asset = Path(unc_root) / "assets" / "scene.c4d"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_text("scene", encoding="utf8")

    assert os.path.isfile(asset), f"{asset} was not written through the share"
    assert is_path_contained(asset, host_root)
    assert is_path_contained(asset, unc_root)
    assert _is_known_path(asset, [host_root])
    assert _is_known_path(asset, [unc_root])


def test_host_level_root_survives_redundancy_filtering(smb_share):
    """A host-level root must reach the containment check intact.

    It is absolute per ``os.path.isabs`` and must subsume its own shares, so the
    filter keeps the host and drops the share.
    """
    unc_root, _ = smb_share
    host_root = unc_root.rsplit("\\", 1)[0]

    assert _filter_redundant_known_paths([host_root]) == [host_root]
    assert _filter_redundant_known_paths([host_root, unc_root]) == [host_root]


def test_neighbouring_host_is_not_contained(smb_share):
    """A different host must not be contained, even one sharing a string prefix."""
    unc_root, _ = smb_share
    host_root = unc_root.rsplit("\\", 1)[0]

    assert not is_path_contained(rf"{host_root}2\share\file", host_root)
    assert not is_path_contained(r"\\other-host\share\file", host_root)


def test_realpath_of_share_content_stays_contained(smb_share):
    """``realpath`` output must still be recognized as inside the share.

    Both containment guards resolve their operands first, so any rewriting by the
    redirector would make them silently reject valid paths.
    """
    unc_root, _ = smb_share
    host_root = unc_root.rsplit("\\", 1)[0]

    nested = Path(unc_root) / "resolve_probe" / "file.txt"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("probe", encoding="utf8")

    resolved = os.path.realpath(nested)
    assert is_path_contained(resolved, os.path.realpath(unc_root))
    assert is_path_contained(resolved, host_root), (
        f"realpath rewrote {nested} to {resolved}, which no longer resolves under {host_root}"
    )


def test_bundle_on_share_passes_symlink_containment(smb_share):
    """A job bundle living on a share must validate, including at the share root.

    ``\\\\server\\share`` vs its own files is one of the pairs ``os.path.commonpath``
    rejected outright.
    """
    unc_root, _ = smb_share

    bundle = Path(unc_root) / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "template.yaml").write_text(
        "specificationVersion: jobtemplate-2023-09\n", encoding="utf8"
    )
    validate_directory_symlink_containment(str(bundle))

    # And a bundle that IS the share root.
    root_bundle = Path(unc_root) / "root_bundle"
    root_bundle.mkdir(parents=True, exist_ok=True)
    (root_bundle / "template.yaml").write_text(
        "specificationVersion: jobtemplate-2023-09\n", encoding="utf8"
    )
    validate_directory_symlink_containment(str(root_bundle))


def test_symlink_escaping_the_share_is_rejected(smb_share):
    """A symlink out of a bundle on a share must still be caught.

    This is the security direction: the lexical tests assert it, but only a real
    filesystem exercises the ``realpath`` resolution the guard depends on.
    """
    unc_root, local_path = smb_share

    bundle = Path(unc_root) / "escape_bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    outside = local_path / "outside_secret.txt"
    outside.write_text("secret", encoding="utf8")

    link = bundle / "escape.txt"
    try:
        os.symlink(outside, link)
    except OSError as exc:  # pragma: no cover - depends on runner privileges
        pytest.skip(f"cannot create a symlink on this share: {exc}")

    with pytest.raises(DeadlineOperationError):
        validate_directory_symlink_containment(str(bundle))


def test_mapped_drive_resolves_and_compares(smb_share):
    """A mapped drive letter is a distinct path space from the UNC path it points at.

    Studios commonly map a share to a drive letter. Whichever spelling ``realpath``
    reports, containment must agree with it rather than mixing the two.
    """
    unc_root, _ = smb_share
    host_root = unc_root.rsplit("\\", 1)[0]

    drive = None
    for letter in ("Y:", "Z:"):
        if _run("net", "use", letter, unc_root).returncode == 0:
            drive = letter
            break
    if drive is None:
        pytest.skip("no free drive letter to map the share onto")

    try:
        asset = Path(drive + "\\") / "mapped_probe.txt"
        asset.write_text("mapped", encoding="utf8")

        resolved = os.path.realpath(asset)
        # Whatever spelling realpath returns, it must be contained by the matching
        # root and not by the other path space.
        if resolved.startswith("\\\\"):
            assert is_path_contained(resolved, host_root)
        else:
            assert is_path_contained(resolved, drive + "\\")
            assert not is_path_contained(resolved, host_root)
    finally:
        _run("net", "use", drive, "/DELETE", "/Y")


def test_common_ancestor_across_shares_on_one_host(smb_share):
    """Files on two shares of one host share only the host.

    ``os.path.commonpath`` raises ``ValueError: Paths don't have the same drive`` for
    this pair, which is what made the download summary crash.
    """
    unc_root, _ = smb_share
    host_root = unc_root.rsplit("\\", 1)[0]

    ancestor = common_ancestor([rf"{host_root}\share1\a.exr", rf"{host_root}\share2\b.exr"])
    assert ancestor.rstrip("\\").lower() == host_root.lower(), ancestor
