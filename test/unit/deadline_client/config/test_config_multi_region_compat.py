# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Forwards/backwards compatibility tests for the multi-region config changes.

The multi-region feature adds two settings to ``~/.deadline/config``:
  - ``defaults.farm_region`` (per-farm; depends on ``defaults.farm_id``)
  - ``settings.deadline_regions`` (global override of the fan-out region set)

Both are purely additive with empty defaults, so:
  - An OLD client reading a NEW config must keep working (and must not destroy the
    new keys when it rewrites the file).
  - A NEW client reading an OLD config (no farm_region) must behave exactly as the
    single-region client did, falling back to the session/profile region.

These tests pin down those guarantees and the edge cases around them, including the
external-tool (Deadline Cloud Monitor) rewrite path and the ``AWS_ENDPOINT_URL_DEADLINE``
override interaction with the region fan-out.
"""

import os
from configparser import ConfigParser
from unittest.mock import MagicMock, patch

import pytest

from deadline.client import config
from deadline.client.config import config_file
from deadline.client.api import _list_apis
from deadline.client.api._session import _resolve_region
from deadline.client.exceptions import DeadlineOperationError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_raw_config(path: str, contents: str) -> None:
    """
    Writes verbatim INI contents to the config file at ``path`` and forces the next
    read to come from disk.

    read_config() caches by mtime, so a rewrite within the same filesystem mtime tick
    could otherwise return stale cached content (and mask the very compatibility behavior
    these tests check). Resetting the cached mtime makes the re-read deterministic.
    """
    with open(path, "w", encoding="utf8") as fh:
        fh.write(contents)
    # Clear the mtime cache so the next read_config() reparses the file from disk.
    setattr(config_file, "__config_mtime", None)
    config_file.read_config()


# A realistic OLD-format config produced by a pre-multi-region client / Deadline Cloud
# Monitor: a monitor profile with credentials metadata, a default farm + queue, and
# NO farm_region / deadline_regions keys anywhere.
_OLD_FORMAT_CONFIG = """\
[defaults]
aws_profile_name = sandbox-us-east-1

[telemetry]
identifier = 00000000-0000-0000-0000-000000000000

[profile-sandbox-us-east-1]
monitor_id = monitor-abc123
user_id = user-abc
identity_store_id = d-1234567890

[profile-sandbox-us-east-1 defaults]
farm_id = farm-OLDFARM0000000000000000000001

[profile-sandbox-us-east-1 farm-OLDFARM0000000000000000000001 defaults]
queue_id = queue-OLDQUEUE000000000000000000001
"""


# ---------------------------------------------------------------------------
# 1. Data preservation: new keys survive an old-client rewrite
# ---------------------------------------------------------------------------


def test_unknown_future_keys_survive_roundtrip(fresh_deadline_config):
    """
    Backwards compat: a client that does not know about a key must not drop it when it
    reads and rewrites the config. ConfigParser preserves unknown keys/sections, which
    is what keeps an OLD client from destroying farm_region written by a NEW client.
    """
    _write_raw_config(
        fresh_deadline_config,
        "[profile-p farm-abc defaults]\n"
        "farm_region = us-west-2\n"
        "queue_id = queue-1\n"
        "a_future_key = some-future-value\n",
    )

    # Simulate an arbitrary client touching an unrelated setting and saving the file.
    config.set_setting("defaults.aws_profile_name", "p")

    raw = open(fresh_deadline_config, encoding="utf8").read()
    assert "farm_region = us-west-2" in raw
    assert "a_future_key = some-future-value" in raw
    assert "queue_id = queue-1" in raw


def test_old_client_rewrite_preserves_farm_region(fresh_deadline_config):
    """
    Backwards compat (the core guarantee): simulate an OLD client that has no concept of
    farm_region by deleting it from the in-memory SETTINGS, then have it read the config
    and write an unrelated change. The farm_region line must remain intact.
    """
    _write_raw_config(
        fresh_deadline_config,
        "[profile-p defaults]\n"
        "farm_id = farm-abc\n"
        "[profile-p farm-abc defaults]\n"
        "farm_region = eu-west-1\n",
    )

    # Build an "old schema" SETTINGS dict without the multi-region keys.
    old_settings = {
        k: v
        for k, v in config_file.SETTINGS.items()
        if k not in ("defaults.farm_region", "settings.deadline_regions")
    }
    with patch.object(config_file, "SETTINGS", old_settings):
        # The old client doesn't know farm_region exists...
        with pytest.raises(DeadlineOperationError):
            config_file.get_setting("defaults.farm_region")
        # ...but it can still operate on settings it does know, and rewrite the file.
        config.set_setting("defaults.aws_profile_name", "p")

    # After the old client rewrote the file, the new key is still there.
    raw = open(fresh_deadline_config, encoding="utf8").read()
    assert "farm_region = eu-west-1" in raw


# ---------------------------------------------------------------------------
# 2. Deadline Cloud Monitor-style rewrite (external tool, separate release)
# ---------------------------------------------------------------------------


def test_dcm_style_credential_write_preserves_farm_region(fresh_deadline_config):
    """
    Deadline Cloud Monitor writes credential metadata (monitor_id/user_id) into a profile
    section. Simulate that write path (via ConfigParser, as DCM-equivalent tooling would)
    and assert it does not clobber a farm_region stored in a child section.
    """
    _write_raw_config(
        fresh_deadline_config,
        "[profile-mon defaults]\n"
        "farm_id = farm-xyz\n"
        "[profile-mon farm-xyz defaults]\n"
        "farm_region = ap-south-1\n"
        "queue_id = queue-9\n",
    )

    # DCM-equivalent: read the whole file, add credential metadata to the profile
    # section, write it back.
    parser = ConfigParser()
    parser.read(fresh_deadline_config)
    section = "profile-mon"
    if section not in parser:
        parser[section] = {}
    parser[section]["monitor_id"] = "monitor-xyz"
    parser[section]["user_id"] = "user-1"
    parser[section]["identity_store_id"] = "d-99"
    with open(fresh_deadline_config, "w", encoding="utf8") as fh:
        parser.write(fh)

    # A new client reads the rewritten file: farm_region survived, and is resolvable.
    config_file.read_config()
    config.set_setting("defaults.aws_profile_name", "mon")
    assert config.get_setting("defaults.farm_region") == "ap-south-1"


# ---------------------------------------------------------------------------
# 3. Forward compat: NEW client reading an OLD-format config
# ---------------------------------------------------------------------------


def test_new_client_reads_old_format_config_without_error(fresh_deadline_config):
    """
    Forward compat: a config with no farm_region / deadline_regions keys (old format)
    must be fully readable by the new client, and every known setting resolves to a value
    (defaults for the new ones) without raising.
    """
    _write_raw_config(fresh_deadline_config, _OLD_FORMAT_CONFIG)

    # All settings, including the new ones, resolve.
    for setting_name in config_file.SETTINGS.keys():
        # Should not raise.
        config_file.get_setting(setting_name)

    # The new settings resolve to their (empty) defaults on an old config.
    assert config.get_setting("defaults.farm_region") == ""
    assert config.get_setting("settings.deadline_regions") == ""
    # Existing values are still read correctly.
    assert config.get_setting("defaults.farm_id") == "farm-OLDFARM0000000000000000000001"
    assert config.get_setting("defaults.queue_id") == "queue-OLDQUEUE000000000000000000001"


def test_old_config_resolves_region_to_none(fresh_deadline_config):
    """
    Forward compat: with an old config (farm set, no farm_region), region resolution
    returns None so the client uses the session/profile/AWS-default region exactly as the
    single-region client did.
    """
    _write_raw_config(fresh_deadline_config, _OLD_FORMAT_CONFIG)
    assert _resolve_region(config=config_file.read_config()) is None


# ---------------------------------------------------------------------------
# 4. Resolution precedence / fallback matrix
# ---------------------------------------------------------------------------


def test_resolve_region_falls_back_to_none_when_farm_section_absent(fresh_deadline_config):
    """
    A default farm_id whose farm section does not exist at all (so it has no farm_region)
    must resolve to None, not raise.
    """
    config.set_setting("defaults.farm_id", "farm-no-section")
    # No farm_region was ever written for this farm.
    assert _resolve_region() is None


def test_resolve_region_is_per_farm_no_stale_leak(fresh_deadline_config):
    """
    farm_region is keyed per farm. Switching the default farm_id must not leak the
    previous farm's region; a farm without its own region resolves to None.
    """
    config.set_setting("defaults.farm_id", "farm-west")
    config.set_setting("defaults.farm_region", "us-west-2")
    assert _resolve_region() == "us-west-2"

    # Switch to a farm that has no stored region -> must NOT inherit us-west-2.
    config.set_setting("defaults.farm_id", "farm-unknown")
    assert _resolve_region() is None

    # And a third farm with its own region resolves to that region.
    config.set_setting("defaults.farm_id", "farm-eu")
    config.set_setting("defaults.farm_region", "eu-west-1")
    assert _resolve_region() == "eu-west-1"

    # Switching back to the first farm restores its region (no data was lost).
    config.set_setting("defaults.farm_id", "farm-west")
    assert _resolve_region() == "us-west-2"


def test_explicit_region_overrides_stored_farm_region(fresh_deadline_config):
    """Explicit region argument beats a stored farm_region beats None."""
    config.set_setting("defaults.farm_id", "farm-x")
    config.set_setting("defaults.farm_region", "us-east-1")
    assert _resolve_region(region="eu-central-1") == "eu-central-1"


def test_resolve_region_uses_passed_farm_id_not_default(fresh_deadline_config):
    """
    When an explicit farm_id is passed, _resolve_region resolves THAT farm's stored
    region, not the default farm's. farm_region is keyed per farm, so a programmatic
    caller working with a non-default farm must get the right region.
    """
    # Default farm is in us-west-2; a different (non-default) farm is in eu-west-1.
    config.set_setting("defaults.farm_id", "farm-default")
    config.set_setting("defaults.farm_region", "us-west-2")
    config.set_setting("defaults.farm_id", "farm-other")
    config.set_setting("defaults.farm_region", "eu-west-1")
    # Restore the default farm so the default-farm lookup would yield us-west-2.
    config.set_setting("defaults.farm_id", "farm-default")

    # Resolving for the non-default farm must yield ITS region, not the default's.
    assert _resolve_region(farm_id="farm-other") == "eu-west-1"
    # And for a farm with no stored region, None (not the default farm's region).
    assert _resolve_region(farm_id="farm-no-region") is None
    # Explicit region still wins over the per-farm lookup.
    assert _resolve_region(region="ap-south-1", farm_id="farm-other") == "ap-south-1"


def test_resolve_region_default_farm_when_no_farm_id_passed(fresh_deadline_config):
    """With no farm_id passed, _resolve_region falls back to the default farm's region."""
    config.set_setting("defaults.farm_id", "farm-default")
    config.set_setting("defaults.farm_region", "us-west-2")
    assert _resolve_region() == "us-west-2"


# ---------------------------------------------------------------------------
# 5. deadline_regions override hygiene
# ---------------------------------------------------------------------------


def test_deadline_regions_override_blank_and_whitespace(fresh_deadline_config):
    """
    A blank or whitespace-only settings.deadline_regions must not be treated as a real
    override; resolution should fall through to the curated DEADLINE_REGIONS list.
    """
    config.set_setting("settings.deadline_regions", "   ")
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DEADLINE_CLOUD_REGIONS", None)
        result = config_file.get_deadline_regions()
    # Falls back to the curated constant, not an empty/garbage list.
    assert result == config_file.DEADLINE_REGIONS


def test_deadline_regions_env_blank_falls_through(fresh_deadline_config):
    """A blank DEADLINE_CLOUD_REGIONS env var must not zero out the region set."""
    config.set_setting("settings.deadline_regions", "eu-west-1,us-east-1")
    with patch.dict(os.environ, {"DEADLINE_CLOUD_REGIONS": "  "}):
        result = config_file.get_deadline_regions()
    # Falls through to the config override (not an empty list).
    assert result == ["eu-west-1", "us-east-1"]


# ---------------------------------------------------------------------------
# 6. AWS_ENDPOINT_URL_DEADLINE interaction with the fan-out
# ---------------------------------------------------------------------------


def test_list_farms_with_endpoint_override_scans_single_region(
    fresh_deadline_config,
):
    """
    When AWS_ENDPOINT_URL_DEADLINE is set, a multi-region fan-out is meaningless (boto3
    honors the override verbatim for every client, so N region-scoped clients would all
    point at the SAME endpoint, querying it N times as N different "regions"). list_farms
    suppresses the fan-out and scans only the single session/profile default region:
    get_deadline_regions is NOT consulted, and exactly one client is built.
    """
    built_for_regions = []

    def fake_get_boto3_client(service_name, config=None, region=None):
        built_for_regions.append(region)
        client = MagicMock()
        client.list_farms.return_value = {"farms": [{"farmId": "farm-only"}]}
        return client

    mock_session = MagicMock()
    mock_session.region_name = "us-east-1"

    with patch.dict(os.environ, {"AWS_ENDPOINT_URL_DEADLINE": "https://override.test/deadline"}):
        os.environ.pop("DEADLINE_CLOUD_REGIONS", None)
        with (
            patch.object(_list_apis, "get_boto3_client", side_effect=fake_get_boto3_client),
            patch.object(_list_apis, "get_boto3_session", return_value=mock_session),
            patch.object(_list_apis.config_file, "get_deadline_regions") as regions_mock,
            patch.object(_list_apis, "_apply_principal_id_filter"),
        ):
            result = _list_apis.list_farms()

    # FIXED BEHAVIOR: the override collapses the fan-out to a single-region scan.
    regions_mock.assert_not_called()
    assert built_for_regions == ["us-east-1"]
    # The single region's farm comes back, tagged with the session region.
    assert [f["region"] for f in result["farms"]] == ["us-east-1"]


def test_fanout_queries_each_region_independently(fresh_deadline_config):
    """
    The fan-out builds a separate region-scoped client per region (never shares one
    client across regions), and tags each farm with its origin region. This is the
    property that makes per-region results meaningful.
    """
    regions = ["us-west-2", "eu-west-1"]

    created = []

    def fake_get_boto3_client(service_name, config=None, region=None):
        created.append(region)
        client = MagicMock()
        client.list_farms.return_value = {"farms": [{"farmId": f"farm-{region}"}]}
        return client

    with (
        patch.object(_list_apis, "get_boto3_client", side_effect=fake_get_boto3_client),
        patch.object(_list_apis, "_apply_principal_id_filter"),
    ):
        results = list(_list_apis._iter_farms_by_region(regions=regions))

    # A distinct client was built for each region.
    assert sorted(created) == sorted(regions)
    # Each region's farm is tagged with its origin region.
    by_region = {region: payload for region, payload, exc in results}
    west = by_region["us-west-2"]
    east = by_region["eu-west-1"]
    assert west is not None and east is not None
    assert west[0]["farmId"] == "farm-us-west-2"
    assert west[0]["region"] == "us-west-2"
    assert east[0]["region"] == "eu-west-1"


# ---------------------------------------------------------------------------
# 7. SETTINGS introspection drift
# ---------------------------------------------------------------------------


def test_new_settings_are_registered_and_discoverable(fresh_deadline_config):
    """
    Anything that enumerates SETTINGS (config show, external tooling) must see the new
    settings with their documented metadata so it doesn't choke or mis-render them.
    """
    assert "defaults.farm_region" in config_file.SETTINGS
    assert "settings.deadline_regions" in config_file.SETTINGS

    farm_region = config_file.SETTINGS["defaults.farm_region"]
    assert farm_region["default"] == ""
    assert farm_region["depend"] == "defaults.farm_id"
    assert "description" in farm_region

    deadline_regions = config_file.SETTINGS["settings.deadline_regions"]
    assert deadline_regions["default"] == ""
    assert "description" in deadline_regions
