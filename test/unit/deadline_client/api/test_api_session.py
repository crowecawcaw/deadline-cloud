# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
tests the deadline.client.api functions relating to boto3.Client
"""

from typing import Optional
from unittest.mock import call, patch, MagicMock, ANY

import boto3  # type: ignore[import]
import pytest
from deadline.client import api, config
from deadline.client.api._session import (
    create_client,
    get_boto3_client,
    get_default_client_config,
    get_session_client,
    precache_clients,
    _resolve_region,
)


def test_get_boto3_session(fresh_deadline_config):
    """Confirm that api.get_boto3_session gets a session for the configured profile"""
    config.set_setting("defaults.aws_profile_name", "SomeRandomProfileName")

    mock_session = MagicMock()
    with patch.object(boto3, "Session", return_value=mock_session) as boto3_session:
        # Testing this function
        result = api.get_boto3_session()

        # Confirm it returned the mocked value, and was called with the correct args.
        # region_name=None preserves the profile's default region (no region requested).
        assert result == mock_session
        boto3_session.assert_called_once_with(
            profile_name="SomeRandomProfileName", region_name=None
        )


def test_get_boto3_session_caching_behavior(fresh_deadline_config):
    """
    Confirm that api.get_boto3_session caches the session, and refreshes if
    the configured profile name changes
    """

    # mock boto3.Session to return a fresh object based on the input profile name
    def mock_create_session(profile_name: Optional[str], region_name: Optional[str] = None):
        session = MagicMock()
        session._profile_name = profile_name
        return session

    with patch.object(boto3, "Session", side_effect=mock_create_session) as boto3_session:
        # This is a session with the default profile name
        session0 = api.get_boto3_session()

        assert session0._profile_name is None

        # This should return the cached object, and not call boto3.Session
        session1 = api.get_boto3_session()

        assert session1 is session0

        # Configuring a new session name should result in a new Session object
        config.set_setting("defaults.aws_profile_name", "SomeRandomProfileName")
        session2 = api.get_boto3_session()

        assert session2 is not session0
        assert session2._profile_name == "SomeRandomProfileName"

        # This should return the cached object, and not call boto3.Session
        session3 = api.get_boto3_session()

        assert session3 is session2

        # boto3.Session should have been called exactly twice, once for each
        # value of AWS profile name that was configured.
        boto3_session.assert_has_calls(
            [
                call(profile_name=None, region_name=None),
                call(profile_name="SomeRandomProfileName", region_name=None),
            ]
        )


def test_get_check_authentication_status_authenticated(fresh_deadline_config):
    """Confirm that check_authentication_status returns AUTHENTICATED (non-DCM profile)."""
    with (
        patch.object(api._session, "get_boto3_client") as boto3_client_mock,
        patch.object(api._list_apis, "get_user_and_identity_store_id", return_value=(None, None)),
    ):
        config.set_setting("defaults.aws_profile_name", "SomeRandomProfileName")
        boto3_client_mock.return_value.list_farms.return_value = {"farms": []}

        assert api.check_authentication_status() == api.AwsAuthenticationStatus.AUTHENTICATED
        # Without a DCM-provided user_id, principalId must not be injected.
        boto3_client_mock.return_value.list_farms.assert_called_once_with(maxResults=1)


def test_get_check_authentication_status_authenticated_injects_principal_id(
    fresh_deadline_config,
):
    """For Deadline Cloud monitor profiles, check_authentication_status must pass
    the IdC user id as principalId so the ListFarms probe is scoped to the
    caller's user membership (avoids AccessDenied that would otherwise leave
    the auth-login poll loop stuck in NEEDS_LOGIN)."""
    with (
        patch.object(api._session, "get_boto3_client") as boto3_client_mock,
        patch.object(
            api._list_apis,
            "get_user_and_identity_store_id",
            return_value=("user-1234", "d-abcdef0123"),
        ),
    ):
        config.set_setting("defaults.aws_profile_name", "dcm-profile")
        boto3_client_mock.return_value.list_farms.return_value = {"farms": []}

        assert api.check_authentication_status() == api.AwsAuthenticationStatus.AUTHENTICATED
        boto3_client_mock.return_value.list_farms.assert_called_once_with(
            maxResults=1, principalId="user-1234"
        )


def test_get_check_authentication_status_configuration_error(fresh_deadline_config):
    """Confirm that check_authentication_status returns CONFIGURATION_ERROR"""
    with (
        patch.object(api._session, "get_boto3_client") as boto3_client_mock,
        patch.object(api._list_apis, "get_user_and_identity_store_id", return_value=(None, None)),
    ):
        config.set_setting("defaults.aws_profile_name", "SomeRandomProfileName")
        boto3_client_mock.return_value.list_farms.side_effect = Exception("some uncaught exception")

        assert api.check_authentication_status() == api.AwsAuthenticationStatus.CONFIGURATION_ERROR


def test_get_queue_user_boto3_session_no_profile(fresh_deadline_config):
    """Make sure that boto3.Session gets called with profile_name=None for the default profile."""
    session_mock = MagicMock()
    # The value returned when no profile was selected is "default"
    session_mock.profile_name = "default"
    session_mock.region_name = "us-west-2"
    deadline_mock = MagicMock()
    mock_botocore_session = MagicMock()
    mock_botocore_session.get_config_variable = lambda name: (
        "default" if name == "profile" else None
    )

    with (
        patch.object(api._session, "get_boto3_session", return_value=session_mock),
        patch("botocore.session.Session", return_value=mock_botocore_session),
        patch("boto3.Session") as boto3_session_mock,
    ):
        api.get_queue_user_boto3_session(
            deadline_mock,
            farm_id="farm-1234",
            queue_id="queue-1234",
            queue_display_name="queue",
        )
        boto3_session_mock.assert_called_once_with(
            botocore_session=ANY, profile_name=None, region_name="us-west-2"
        )


def test_check_deadline_api_available(fresh_deadline_config):
    # check_deadline_api_available is a deprecated shim that delegates to
    # check_authentication_status using the same deadline:ListFarms probe.
    with patch.object(api._session, "get_boto3_session") as session_mock:
        session_mock().client("deadline").list_farms.return_value = {"farms": []}

        # Call the function under test
        with pytest.warns(DeprecationWarning):
            result = api.check_deadline_api_available()

        assert result is True
        # It should have called list_farms to check the API
        session_mock().client("deadline").list_farms.assert_called_once_with(maxResults=1)


def test_check_deadline_api_available_injects_principal_id(fresh_deadline_config):
    """For DCM profiles, check_deadline_api_available must pass principalId."""
    with (
        patch.object(api._session, "get_boto3_client") as boto3_client_mock,
        patch.object(
            api._list_apis,
            "get_user_and_identity_store_id",
            return_value=("user-1234", "d-abcdef0123"),
        ),
    ):
        boto3_client_mock.return_value.list_farms.return_value = {"farms": []}

        with pytest.warns(DeprecationWarning):
            assert api.check_deadline_api_available() is True
        boto3_client_mock.return_value.list_farms.assert_called_once_with(
            maxResults=1, principalId="user-1234"
        )


def test_check_deadline_api_available_fails(fresh_deadline_config):
    # When the probe fails for a non-DCM profile, the shim resolves to
    # CONFIGURATION_ERROR (not AUTHENTICATED) and therefore returns False.
    with (
        patch.object(api._session, "get_boto3_client") as boto3_client_mock,
        patch.object(api._list_apis, "get_user_and_identity_store_id", return_value=(None, None)),
    ):
        config.set_setting("defaults.aws_profile_name", "SomeRandomProfileName")
        boto3_client_mock.return_value.list_farms.side_effect = Exception()

        # Call the function under test
        with pytest.warns(DeprecationWarning):
            result = api.check_deadline_api_available()

        assert result is False
        # It should have called list_farms to check the API
        boto3_client_mock.return_value.list_farms.assert_called_once_with(maxResults=1)


def test_resolve_region_precedence(fresh_deadline_config):
    """_resolve_region: explicit region > defaults.farm_region > None."""
    # Nothing configured -> None (preserves single-region behavior).
    assert _resolve_region() is None

    # defaults.farm_region set -> used when no explicit region.
    config.set_setting("defaults.farm_region", "eu-west-1")
    assert _resolve_region() == "eu-west-1"

    # Explicit region wins over farm_region.
    assert _resolve_region(region="ap-south-1") == "ap-south-1"


def test_resolve_region_backcompat_empty(fresh_deadline_config):
    """A config without farm_region (default empty) resolves region to None."""
    # No defaults.farm_region set at all -> default "" -> None.
    assert _resolve_region() is None


def test_get_boto3_client_with_region(fresh_deadline_config):
    """get_boto3_client passes region_name to the client when a region is given."""
    mock_session = MagicMock()
    with patch.object(api._session, "get_boto3_session", return_value=mock_session):
        # Make sure no stale cache entry interferes.
        get_session_client.cache_clear()
        get_boto3_client("deadline", region="eu-west-1")

    mock_session.client.assert_called_once_with("deadline", config=ANY, region_name="eu-west-1")


def test_get_boto3_client_with_farm_region_config(fresh_deadline_config):
    """get_boto3_client resolves region from defaults.farm_region when not passed."""
    config.set_setting("defaults.farm_region", "ap-south-1")
    mock_session = MagicMock()
    with patch.object(api._session, "get_boto3_session", return_value=mock_session):
        get_session_client.cache_clear()
        get_boto3_client("deadline")

    mock_session.client.assert_called_once_with("deadline", config=ANY, region_name="ap-south-1")


def test_get_boto3_client_without_region_backcompat(fresh_deadline_config):
    """Without any region, get_boto3_client does not pass region_name (single-region behavior)."""
    mock_session = MagicMock()
    with patch.object(api._session, "get_boto3_session", return_value=mock_session):
        get_session_client.cache_clear()
        get_boto3_client("deadline")

    # region_name must NOT be passed to preserve existing single-region behavior.
    mock_session.client.assert_called_once_with("deadline", config=ANY)


# ---- settings.https_proxy / settings.ca_bundle wiring (issue #1046) --------


def test_default_client_config_no_proxy_by_default(fresh_deadline_config):
    """With settings.https_proxy unset, the Config carries no proxies."""
    cfg = get_default_client_config()
    # botocore stores configured proxies on the .proxies attribute (None/empty
    # when unset).
    assert not cfg.proxies


def test_default_client_config_applies_https_proxy(fresh_deadline_config):
    """settings.https_proxy is applied to the botocore Config for both schemes."""
    config.set_setting("settings.https_proxy", "http://proxy.example.com:8080")
    cfg = get_default_client_config()
    assert cfg.proxies == {
        "http": "http://proxy.example.com:8080",
        "https": "http://proxy.example.com:8080",
    }


def test_default_client_config_explicit_proxies_win(fresh_deadline_config):
    """An explicit proxies kwarg is not overridden by the config setting."""
    config.set_setting("settings.https_proxy", "http://from-config:8080")
    cfg = get_default_client_config(proxies={"https": "http://explicit:9090"})
    assert cfg.proxies == {"https": "http://explicit:9090"}


def test_get_session_client_applies_ca_bundle(fresh_deadline_config):
    """settings.ca_bundle is forwarded as the client's verify kwarg."""
    config.set_setting("settings.ca_bundle", "/etc/ssl/my-ca.pem")
    # ca_bundle is an is_path setting, so the stored value is normalized to the
    # native path form on read (backslashes on Windows). Assert against what the
    # config layer actually returns rather than the raw forward-slash input.
    expected_ca = config.get_setting("settings.ca_bundle")
    mock_session = MagicMock()
    with patch.object(api._session, "get_boto3_session", return_value=mock_session):
        get_session_client.cache_clear()
        get_boto3_client("deadline")

    mock_session.client.assert_called_once_with("deadline", config=ANY, verify=expected_ca)


def test_get_session_client_no_ca_bundle_omits_verify(fresh_deadline_config):
    """Without settings.ca_bundle, verify is not passed (default behavior)."""
    mock_session = MagicMock()
    with patch.object(api._session, "get_boto3_session", return_value=mock_session):
        get_session_client.cache_clear()
        get_boto3_client("deadline")

    mock_session.client.assert_called_once_with("deadline", config=ANY)


def test_get_session_client_proxy_and_ca_bundle_together(fresh_deadline_config):
    """Both settings apply on the same client: proxy via Config, CA via verify."""
    config.set_setting("settings.https_proxy", "http://proxy.example.com:8080")
    config.set_setting("settings.ca_bundle", "/etc/ssl/my-ca.pem")
    # See note in test_get_session_client_applies_ca_bundle re: path normalization.
    expected_ca = config.get_setting("settings.ca_bundle")
    mock_session = MagicMock()
    with patch.object(api._session, "get_boto3_session", return_value=mock_session):
        get_session_client.cache_clear()
        get_boto3_client("deadline")

    mock_session.client.assert_called_once_with("deadline", config=ANY, verify=expected_ca)
    passed_config = mock_session.client.call_args.kwargs["config"]
    assert passed_config.proxies == {
        "http": "http://proxy.example.com:8080",
        "https": "http://proxy.example.com:8080",
    }


def test_create_client_applies_both_settings(fresh_deadline_config):
    """create_client applies BOTH proxy (via Config) and ca_bundle (via verify)."""
    config.set_setting("settings.https_proxy", "http://proxy.example.com:8080")
    config.set_setting("settings.ca_bundle", "/etc/ssl/my-ca.pem")
    expected_ca = config.get_setting("settings.ca_bundle")

    mock_session = MagicMock()
    create_client(mock_session, "logs")

    mock_session.client.assert_called_once_with("logs", config=ANY, verify=expected_ca)
    passed_config = mock_session.client.call_args.kwargs["config"]
    assert passed_config.proxies == {
        "http": "http://proxy.example.com:8080",
        "https": "http://proxy.example.com:8080",
    }


def test_create_client_no_settings_omits_verify_and_proxy(fresh_deadline_config):
    """With neither setting, create_client passes no verify and an empty-proxy Config."""
    mock_session = MagicMock()
    create_client(mock_session, "logs")

    mock_session.client.assert_called_once_with("logs", config=ANY)
    assert not mock_session.client.call_args.kwargs["config"].proxies


def test_create_client_passes_region_when_set(fresh_deadline_config):
    """region is forwarded as region_name only when not None."""
    mock_session = MagicMock()
    create_client(mock_session, "logs", region="eu-west-1")
    mock_session.client.assert_called_once_with("logs", config=ANY, region_name="eu-west-1")


def test_create_client_forwards_config_kwargs(fresh_deadline_config):
    """Extra config_kwargs reach the botocore Config (e.g. retries)."""
    mock_session = MagicMock()
    create_client(mock_session, "deadline", retries={"mode": "adaptive", "total_max_attempts": 5})
    passed_config = mock_session.client.call_args.kwargs["config"]
    assert passed_config.retries["mode"] == "adaptive"


def test_create_client_honors_explicit_config_parser(fresh_deadline_config):
    """
    create_client resolves both settings from an explicitly-supplied ConfigParser,
    not just the on-disk default config. This is the per-config path that the
    @lru_cache'd get_session_client cannot offer.
    """
    # Put values ONLY in a standalone in-memory ConfigParser, leaving the on-disk
    # (default) config empty. Use a fresh ConfigParser rather than read_config(),
    # which returns the cached global the default get_setting() also reads.
    from configparser import ConfigParser

    cfg = ConfigParser()
    config.set_setting("settings.https_proxy", "http://in-memory:7070", config=cfg)
    config.set_setting("settings.ca_bundle", "/etc/ssl/in-memory.pem", config=cfg)
    expected_ca = config.get_setting("settings.ca_bundle", config=cfg)

    # Sanity: the on-disk default config has neither set.
    assert config.get_setting("settings.https_proxy") == ""
    assert config.get_setting("settings.ca_bundle") == ""

    mock_session = MagicMock()
    create_client(mock_session, "deadline", config=cfg)

    mock_session.client.assert_called_once_with("deadline", config=ANY, verify=expected_ca)
    passed_config = mock_session.client.call_args.kwargs["config"]
    assert passed_config.proxies == {
        "http": "http://in-memory:7070",
        "https": "http://in-memory:7070",
    }


def test_get_boto3_client_honors_supplied_config(fresh_deadline_config):
    """
    get_boto3_client resolves proxy/ca_bundle from a caller-supplied ConfigParser,
    not just the on-disk default. Guards the fix that threads the resolved settings
    into the cached get_session_client.
    """
    from configparser import ConfigParser

    cfg = ConfigParser()
    config.set_setting("settings.https_proxy", "http://caller-cfg:7070", config=cfg)
    config.set_setting("settings.ca_bundle", "/etc/ssl/caller.pem", config=cfg)
    expected_ca = config.get_setting("settings.ca_bundle", config=cfg)

    # On-disk default config has neither set.
    assert config.get_setting("settings.https_proxy") == ""

    mock_session = MagicMock()
    with patch.object(api._session, "get_boto3_session", return_value=mock_session):
        get_session_client.cache_clear()
        get_boto3_client("deadline", config=cfg)

    mock_session.client.assert_called_once_with("deadline", config=ANY, verify=expected_ca)
    passed_config = mock_session.client.call_args.kwargs["config"]
    assert passed_config.proxies == {
        "http": "http://caller-cfg:7070",
        "https": "http://caller-cfg:7070",
    }


def test_get_boto3_client_custom_config_without_proxy_ignores_disk_proxy(fresh_deadline_config):
    """
    A caller-supplied config with NO proxy must NOT inherit the on-disk default
    proxy. Regression test: _build_client must be the sole proxy authority and
    must stop get_default_client_config from re-reading settings.https_proxy from
    the on-disk default config when a custom config is in play.
    """
    from configparser import ConfigParser

    # On-disk default DOES set a proxy...
    config.set_setting("settings.https_proxy", "http://on-disk:5050")
    # ...but the caller passes a config that does not.
    cfg = ConfigParser()
    assert config.get_setting("settings.https_proxy", config=cfg) == ""

    mock_session = MagicMock()
    with patch.object(api._session, "get_boto3_session", return_value=mock_session):
        get_session_client.cache_clear()
        get_boto3_client("deadline", config=cfg)

    # No proxy should be applied, and no verify either.
    mock_session.client.assert_called_once_with("deadline", config=ANY)
    assert not mock_session.client.call_args.kwargs["config"].proxies


def test_get_session_client_cache_keyed_on_settings(fresh_deadline_config):
    """
    Clients built with different proxy/ca_bundle settings must not collide in the
    lru_cache: the resolved (https_proxy, ca_bundle) tuple is part of the key.
    """
    from configparser import ConfigParser

    cfg_a = ConfigParser()
    config.set_setting("settings.https_proxy", "http://proxy-a:8080", config=cfg_a)
    cfg_b = ConfigParser()
    config.set_setting("settings.https_proxy", "http://proxy-b:9090", config=cfg_b)

    real_session = boto3.Session(region_name="us-west-2")
    get_session_client.cache_clear()
    with patch.object(api._session, "get_boto3_session", return_value=real_session):
        client_a = get_boto3_client("deadline", config=cfg_a)
        client_b = get_boto3_client("deadline", config=cfg_b)
        # Same config -> cached (same object); different settings -> distinct.
        client_a_again = get_boto3_client("deadline", config=cfg_a)

    assert client_a is client_a_again
    assert client_a is not client_b
    assert client_a.meta.config.proxies == {
        "http": "http://proxy-a:8080",
        "https": "http://proxy-a:8080",
    }
    assert client_b.meta.config.proxies == {
        "http": "http://proxy-b:9090",
        "https": "http://proxy-b:9090",
    }


def test_get_boto3_session_with_region_builds_region_scoped_session(fresh_deadline_config):
    """
    get_boto3_session(region=...) builds a region-scoped session so boto3 resolves the
    regional Deadline endpoint itself (no hand-built endpoint URL needed). The cache is
    keyed on region, so the no-region session and a region-scoped session differ.
    """
    created = []

    def mock_create_session(profile_name=None, region_name=None):
        session = MagicMock()
        session._profile_name = profile_name
        session._region_name = region_name
        created.append(session)
        return session

    with patch.object(boto3, "Session", side_effect=mock_create_session) as boto3_session:
        api._session._get_boto3_session_for_profile.cache_clear()
        default_session = api.get_boto3_session()
        scoped_session = api.get_boto3_session(region="eu-west-1")

    assert default_session is not scoped_session
    assert scoped_session._region_name == "eu-west-1"
    boto3_session.assert_any_call(profile_name=None, region_name=None)
    boto3_session.assert_any_call(profile_name=None, region_name="eu-west-1")


def test_get_session_client_caching():
    """Test that get_session_client properly caches clients."""
    # Create a real boto3 session for testing the cache
    session = boto3.Session()

    # First call should create a new client
    client1 = get_session_client(session, "s3")

    # Second call with same session should return the same client
    client2 = get_session_client(session, "s3")

    # Verify they're the same object
    assert client1 is client2

    # Different service should create a new client
    client3 = get_session_client(session, "sts")
    assert client1 is not client3

    # Create a new session with the same parameters
    # This should create a new client since it's a different object
    new_session = boto3.Session()
    client4 = get_session_client(new_session, "s3")
    assert client1 is not client4


def test_get_session_client_different_regions_distinct_clients():
    """
    the same session+service but DIFFERENT regions must yield DIFFERENT client
    objects (the region is part of the lru_cache key, so there is no cross-region reuse).
    """
    get_session_client.cache_clear()
    session = boto3.Session()

    client_west = get_session_client(session, "s3", region="us-west-2")
    client_east = get_session_client(session, "s3", region="us-east-1")

    assert client_west is not client_east
    # And the no-region client is also distinct from the region-scoped ones.
    client_default = get_session_client(session, "s3")
    assert client_default is not client_west
    assert client_default is not client_east


def test_get_session_client_same_region_cached():
    """
    the same (session, service, region) tuple returns the SAME cached object.
    """
    get_session_client.cache_clear()
    session = boto3.Session()

    client1 = get_session_client(session, "s3", region="eu-west-1")
    client2 = get_session_client(session, "s3", region="eu-west-1")

    assert client1 is client2


class TestGetSessionClientCrossRegion:
    """Tests for cross-region endpoint override handling in get_session_client."""

    def _make_session(self, region, endpoint_url):
        """
        Build a mock session whose .client() returns a mock with the given endpoint_url
        on meta.endpoint_url, simulating boto3's endpoint resolution behavior.
        """
        session = MagicMock()
        session.region_name = region

        client_mock = MagicMock()
        client_mock.meta.endpoint_url = endpoint_url
        session.client.return_value = client_mock
        return session

    def test_cross_region_regionalizes_override(self):
        """
        When the resolved endpoint contains the session region but not the target
        region, get_session_client recreates the client with a regionalized URL.
        """
        get_session_client.cache_clear()
        session = self._make_session("us-west-2", "https://custom.deadline.us-west-2.example.com")

        cross_region_client = MagicMock()
        # First call returns the client with the wrong region; second returns the fixed one.
        session.client.side_effect = [
            session.client.return_value,
            cross_region_client,
        ]

        result = get_session_client(session, "deadline", "us-east-1")
        assert result is cross_region_client
        assert session.client.call_count == 2
        second_call = session.client.call_args_list[1]
        assert second_call == call(
            "deadline",
            config=ANY,
            region_name="us-east-1",
            endpoint_url="https://custom.deadline.us-east-1.example.com",
        )

    def test_cross_region_no_override_needed(self):
        """
        When the resolved endpoint already contains the target region, no
        recreation is needed.
        """
        get_session_client.cache_clear()
        session = self._make_session("us-west-2", "https://deadline.us-east-1.amazonaws.com")

        result = get_session_client(session, "deadline", "us-east-1")
        assert result is session.client.return_value
        session.client.assert_called_once()

    def test_cross_region_no_session_region(self):
        """
        When the session has no region_name, the initial client is returned as-is.
        """
        get_session_client.cache_clear()
        session = self._make_session(None, "https://deadline.us-east-1.amazonaws.com")

        result = get_session_client(session, "deadline", "us-east-1")
        assert result is session.client.return_value
        session.client.assert_called_once()

    def test_cross_region_session_region_not_in_endpoint(self):
        """
        When the resolved endpoint doesn't contain the session region at all,
        no regionalization is attempted.
        """
        get_session_client.cache_clear()
        session = self._make_session("us-west-2", "https://custom-endpoint.example.com")

        result = get_session_client(session, "deadline", "us-east-1")
        assert result is session.client.return_value
        session.client.assert_called_once()

    def test_no_region_uses_session_default(self):
        """When region is None, a simple client with session defaults is returned."""
        get_session_client.cache_clear()
        session = self._make_session("us-west-2", "https://deadline.us-west-2.amazonaws.com")

        result = get_session_client(session, "deadline", None)
        assert result is session.client.return_value
        session.client.assert_called_once_with("deadline", config=ANY)


def test_get_queue_user_boto3_session_uses_resolved_farm_region(fresh_deadline_config):
    """
    get_queue_user_boto3_session scopes the queue-user session to the resolved
    farm region (defaults.farm_region) rather than the base session's region.

    Reading the source: get_queue_user_boto3_session resolves the region via
    _resolve_region(config, farm_id=farm_id) and passes it to _get_queue_user_boto3_session,
    which uses ``region_name=region if region is not None else base_session.region_name``.
    So the farm's configured region IS honored over the base session region.
    """
    # farm_region is keyed per farm, so store it for the farm we'll query.
    config.set_setting("defaults.farm_id", "farm-1234")
    config.set_setting("defaults.farm_region", "ap-south-1")

    session_mock = MagicMock()
    session_mock.profile_name = "default"
    session_mock.region_name = "us-west-2"  # base session region (should be overridden)
    deadline_mock = MagicMock()
    mock_botocore_session = MagicMock()
    mock_botocore_session.get_config_variable = lambda name: (
        "default" if name == "profile" else None
    )

    # Clear the lru_cache so a prior test's queue-user session doesn't shadow this one.
    api._session._get_queue_user_boto3_session.cache_clear()

    with (
        patch.object(api._session, "get_boto3_session", return_value=session_mock),
        patch("botocore.session.Session", return_value=mock_botocore_session),
        patch("boto3.Session") as boto3_session_mock,
    ):
        api.get_queue_user_boto3_session(
            deadline_mock,
            farm_id="farm-1234",
            queue_id="queue-1234",
            queue_display_name="queue",
        )
        # The queue-user session is built for the configured farm region, not us-west-2.
        boto3_session_mock.assert_called_once_with(
            botocore_session=ANY, profile_name=None, region_name="ap-south-1"
        )


def test_get_queue_user_boto3_session_falls_back_to_base_region(fresh_deadline_config):
    """
    With NO configured farm_region, the queue-user session falls back to the base
    session's region (single-region behavior).
    """
    session_mock = MagicMock()
    session_mock.profile_name = "default"
    session_mock.region_name = "us-west-2"
    deadline_mock = MagicMock()
    mock_botocore_session = MagicMock()
    mock_botocore_session.get_config_variable = lambda name: (
        "default" if name == "profile" else None
    )

    api._session._get_queue_user_boto3_session.cache_clear()

    with (
        patch.object(api._session, "get_boto3_session", return_value=session_mock),
        patch("botocore.session.Session", return_value=mock_botocore_session),
        patch("boto3.Session") as boto3_session_mock,
    ):
        api.get_queue_user_boto3_session(
            deadline_mock,
            farm_id="farm-1234",
            queue_id="queue-1234",
            queue_display_name="queue",
        )
        boto3_session_mock.assert_called_once_with(
            botocore_session=ANY, profile_name=None, region_name="us-west-2"
        )


def test_get_session_logs_logs_client_uses_farm_region_non_dcm(fresh_deadline_config):
    """
    for a non-DCM profile, get_session_logs builds its CloudWatch ``logs`` client
    via get_boto3_client("logs", config=config), so it resolves to defaults.farm_region -
    i.e. the logs client is scoped to the farm region (the multi-region behavior).

    (For DCM profiles the logs client comes from the queue-user session, which is region-
    scoped via case 37; this test pins the non-DCM path.)
    """
    from deadline.client.api import _job_monitoring
    from deadline.client.api._session import _resolve_region

    config.set_setting("defaults.farm_region", "eu-central-1")

    captured = []

    def fake_get_boto3_client(service_name, config=None, region=None):
        resolved = _resolve_region(config=config, region=region)
        captured.append((service_name, resolved))
        client = MagicMock()
        client.get_log_events.return_value = {"events": [], "nextForwardToken": None}
        return client

    with (
        patch.object(_job_monitoring, "get_boto3_client", side_effect=fake_get_boto3_client),
        patch.object(_job_monitoring, "get_user_and_identity_store_id", return_value=(None, None)),
    ):
        _job_monitoring.get_session_logs(
            farm_id="farm-1234",
            queue_id="queue-1234",
            session_id="session-abcd",
            limit=10,
        )

    services = dict(captured)
    assert services.get("deadline") == "eu-central-1"
    assert services.get("logs") == "eu-central-1"


@patch("deadline.client.api._session.get_s3_client")
@patch("deadline.client.api._session.get_queue_user_boto3_session")
@patch("deadline.client.api._session.get_boto3_client")
def test_precache_clients(mock_get_boto3_client, mock_get_queue_user_session, mock_get_s3_client):
    """Test that precache_clients calls the right functions."""
    # Setup mocks
    mock_deadline_client = MagicMock()
    mock_deadline_client.get_queue.return_value = {"displayName": "test-queue"}
    mock_get_boto3_client.return_value = mock_deadline_client

    mock_session = MagicMock()
    mock_get_queue_user_session.return_value = mock_session

    # Call the function
    precache_clients()

    # Verify the right calls were made
    mock_get_boto3_client.assert_called_once_with("deadline", config=None)
    mock_deadline_client.get_queue.assert_called_once()
    mock_get_queue_user_session.assert_called_once()
    mock_get_s3_client.assert_called_once_with(mock_session, s3_max_pool_connections=50)


@patch("deadline.client.api._session.get_s3_client")
@patch("deadline.client.api._session.get_queue_user_boto3_session")
@patch("deadline.client.api._session.get_boto3_client")
def test_precache_clients_with_params(
    mock_get_boto3_client, mock_get_queue_user_session, mock_get_s3_client
):
    """Test that precache_clients uses provided parameters correctly."""
    # Setup mocks
    mock_deadline_client = MagicMock()
    mock_session = MagicMock()
    mock_config = MagicMock()
    mock_get_queue_user_session.return_value = mock_session

    # Call the function with all parameters specified
    precache_clients(
        deadline=mock_deadline_client,
        config=mock_config,
        farm_id="test-farm",
        queue_id="test-queue",
        queue_display_name="Test Queue",
    )

    # Verify the right calls were made
    mock_get_boto3_client.assert_not_called()  # Should not be called since we provided a client
    mock_deadline_client.get_queue.assert_not_called()  # Should not be called since we provided queue_display_name

    # Verify queue user session was created with correct parameters
    mock_get_queue_user_session.assert_called_once_with(
        deadline=mock_deadline_client,
        config=mock_config,
        farm_id="test-farm",
        queue_id="test-queue",
        queue_display_name="Test Queue",
    )

    # Verify S3 client was initialized with the session
    mock_get_s3_client.assert_called_once_with(mock_session, s3_max_pool_connections=50)


def test_precache_clients_warms_asset_uploader_client(fresh_deadline_config):
    """
    Test that initializing the deadline and S3 client with precache_clients
    properly pre-warms the cache for subsequent job submissions.
    """
    # Setup mocks
    mock_deadline_client = MagicMock()
    mock_deadline_client.get_queue.return_value = {
        "displayName": "test-queue",
        "jobAttachmentSettings": {
            "s3BucketName": "test-bucket",
            "rootPrefix": "test-prefix",
        },
    }

    # Use a real boto3 session for proper hashability
    real_session = boto3.Session()

    # First, initialize the S3 client
    with (
        patch(
            "deadline.client.api._session.get_boto3_client",
            return_value=mock_deadline_client,
        ),
        patch(
            "deadline.client.api._session.get_queue_user_boto3_session",
            return_value=real_session,
        ),
    ):
        # Get the client from initialization
        _, s3_client1 = precache_clients(farm_id="test-farm", queue_id="test-queue")

    # Now create an S3AssetUploader with the same session
    from deadline.job_attachments.upload import S3AssetUploader

    # Create the uploader with the same session
    uploader = S3AssetUploader(
        session=real_session,
        s3_max_pool_connections=50,
        small_file_threshold_multiplier=20,
    )

    # Get the client from the uploader
    s3_client2 = uploader._s3

    # Verify that both clients are the same object (cached)
    assert s3_client1 is s3_client2
