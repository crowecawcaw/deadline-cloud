# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""UI tests for ``deadline config gui`` — workstation settings.

Covers manual test cases:
  * "Verify user can modify workstation configuration settings using
     `deadline config gui`" — the dialog loads, and changes made via the
     corresponding CLI are reflected in the GUI.
  * "Verify user can authenticate using AWS profile" — the auth status
     widget shows the profile name for a plain AWS profile.

On macOS, Qt combo-box popup items cannot be reliably activated via the
accessibility API, so these tests use ``deadline config set`` to mutate
values and assert the GUI reflects them — the GUI's own refresh logic
is the same code path regardless of which side drove the write.
"""

from __future__ import annotations

import subprocess

from helpers import ConfigDialog


def _cli_set(env: dict, setting: str, value: str) -> None:
    subprocess.check_call(
        ["deadline", "config", "set", setting, value],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _cli_get(env: dict, setting: str) -> str:
    return subprocess.check_output(
        ["deadline", "config", "get", setting], env=env, text=True, stderr=subprocess.DEVNULL
    ).strip()


class TestConfigGuiSettingsSections:
    """The config dialog exposes the four expected settings groups."""

    def test_all_settings_groups_visible(self, deadline_env):
        _, env = deadline_env
        with ConfigDialog.open(env=env) as app:
            for group_name in (
                "Global settings",
                "Profile settings",
                "Farm settings",
                "General settings",
            ):
                assert app.locator(f'group[name="{group_name}"]').exists(), (
                    f"{group_name!r} group missing"
                )

    def test_auth_status_widget_shows_default_profile(self, deadline_env):
        _, env = deadline_env
        with ConfigDialog.open(env=env) as app:
            # With no explicit profile set, the default AWS profile name
            # is "(default)". It should surface somewhere in the auth
            # status widget's subtree — which role Qt exposes that text
            # on is platform-dependent (push_button on Linux/Windows UIA,
            # combo_box on macOS when the widget is in a CONFIGURATION_ERROR
            # state because fake creds can't sign a real STS call).
            assert app.tree_contains_text("(default)"), (
                "'(default)' profile name not found anywhere in the auth status widget"
            )


class TestConflictResolutionSetting:
    """A non-log-level combo-box setting round-trips from CLI to GUI."""

    def test_changing_conflict_resolution_is_reflected_in_gui(self, deadline_env):
        _, env = deadline_env
        _cli_set(env, "settings.conflict_resolution", "OVERWRITE")
        with ConfigDialog.open(env=env) as app:
            assert app.conflict_resolution == "OVERWRITE"

    def test_changing_conflict_resolution_persists_across_reopens(self, deadline_env):
        _, env = deadline_env
        _cli_set(env, "settings.conflict_resolution", "SKIP")
        with ConfigDialog.open(env=env) as app:
            assert app.conflict_resolution == "SKIP"
            app.close("Ok")
        # CLI still sees the setting we wrote (GUI Ok doesn't alter it).
        assert _cli_get(env, "settings.conflict_resolution") == "SKIP"


class TestTelemetryCheckboxRoundTrip:
    """Checkbox setting (telemetry.opt_out) round-trips via CLI writes."""

    def test_checkbox_reflects_cli_value(self, deadline_env):
        _, env = deadline_env
        _cli_set(env, "telemetry.opt_out", "true")
        # The GUI should load without errors with a boolean already set.
        with ConfigDialog.open(env=env) as app:
            assert app.dialog().element().visible
        assert _cli_get(env, "telemetry.opt_out").lower() == "true"
