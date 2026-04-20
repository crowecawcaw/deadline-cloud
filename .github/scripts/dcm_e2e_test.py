"""End-to-end test: DCM first-time sign-in, then deadline auth login/logout/login + farm list.

Drives the DCM Electron UI via xa11y (AT-SPI2) to add a monitor profile and complete
IAM Identity Center sign-in using MONITOR_URL/MONITOR_USERNAME/MONITOR_PASSWORD.
"""
import configparser
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import xa11y

MONITOR_URL = os.environ["MONITOR_URL"]
USERNAME = os.environ["MONITOR_USERNAME"]
PASSWORD = os.environ["MONITOR_PASSWORD"]
TIMEOUT = 60.0


def run(cmd, check=True):
    print(f"$ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout)
    if r.stderr:
        print(r.stderr, file=sys.stderr)
    if check and r.returncode != 0:
        raise SystemExit(f"Command failed: {cmd}")
    return r


def launch_dcm():
    """Launch DCM with Chromium accessibility enabled."""
    binary = shutil.which("deadline-cloud-monitor") or shutil.which("DeadlineCloudMonitor")
    if not binary:
        # Fallback to the install prefix
        for p in [Path.home() / "DeadlineCloudSubmitter/bin/deadline-cloud-monitor",
                  Path.home() / "DeadlineCloudSubmitter/bin/DeadlineCloudMonitor"]:
            if p.exists():
                binary = str(p)
                break
    if not binary:
        raise SystemExit("DCM binary not found on PATH or install prefix")
    print(f"Launching DCM: {binary}", flush=True)
    return subprocess.Popen([binary, "--force-renderer-accessibility", "--no-sandbox"])


def dump_tree(app, label):
    print(f"\n--- a11y tree: {label} ---", flush=True)
    try:
        subprocess.run(["xa11y", "tree", "--app", app.name], check=False, timeout=10)
    except Exception as e:
        print(f"(tree dump failed: {e})")


def sign_in_to_dcm(app):
    """First-time sign-in: enter monitor URL, then IAM Identity Center credentials."""
    # Monitor URL entry — find the first text field and type the URL
    url_field = app.locator("text_field").nth(0) if hasattr(app.locator("text_field"), "nth") else app.locator("text_field:nth(1)")
    url_field.wait_visible(timeout=TIMEOUT)
    url_field.type_text(MONITOR_URL)

    # Click the primary button (Sign in / Continue / Add)
    for name in ["Sign in", "Continue", "Add", "Next", "OK"]:
        btn = app.locator(f"button[name='{name}']")
        if btn.count() > 0:
            btn.press()
            break

    # IAM Identity Center sign-in — DCM opens an internal BrowserView or the system browser.
    # Try DCM first, then fall back to any browser window exposed via a11y.
    deadline = time.time() + TIMEOUT * 3
    while time.time() < deadline:
        for candidate in [app] + [a for a in xa11y.App.list() if "chrom" in a.name.lower() or "firefox" in a.name.lower()]:
            user = candidate.locator("text_field[name*='sername']")
            if user.count() == 0:
                user = candidate.locator("text_field[name*='mail']")
            if user.count() > 0:
                user.type_text(USERNAME)
                candidate.locator("button[name*='ext']").press()  # "Next"
                pwd = candidate.locator("text_field[name*='assword']")
                pwd.wait_visible(timeout=TIMEOUT)
                pwd.type_text(PASSWORD)
                candidate.locator("button[name*='ign in']").press()
                # Handle "Allow access" / consent screen
                allow = candidate.locator("button[name*='llow']")
                try:
                    allow.wait_visible(timeout=30)
                    allow.press()
                except Exception:
                    pass
                return
        time.sleep(1)
    dump_tree(app, "timeout waiting for sign-in form")
    raise SystemExit("Could not locate IAM Identity Center sign-in form")


def wait_for_dcm_profile():
    """Wait until DCM writes a profile into ~/.aws/config with monitor_id."""
    aws_config = Path.home() / ".aws/config"
    deadline = time.time() + TIMEOUT * 2
    while time.time() < deadline:
        if aws_config.exists():
            cp = configparser.ConfigParser()
            cp.read(aws_config)
            for section in cp.sections():
                if "monitor_id" in cp[section]:
                    profile = section.replace("profile ", "")
                    print(f"DCM created profile: {profile}")
                    return profile
        time.sleep(1)
    raise SystemExit("DCM did not create an AWS profile with monitor_id")


def configure_deadline_cli(profile):
    run(["deadline", "config", "set", "defaults.aws_profile_name", profile])


def main():
    dcm_proc = launch_dcm()
    try:
        app = xa11y.App.by_name("Deadline Cloud Monitor")
        app.locator("window").wait_visible(timeout=TIMEOUT)
        sign_in_to_dcm(app)
        profile = wait_for_dcm_profile()
    finally:
        dcm_proc.terminate()
        try:
            dcm_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            dcm_proc.kill()

    configure_deadline_cli(profile)

    # End-to-end CLI flow
    run(["deadline", "auth", "login"])
    status = run(["deadline", "auth", "status", "--output", "json"]).stdout
    assert '"AUTHENTICATED"' in status, f"Expected AUTHENTICATED, got: {status}"

    run(["deadline", "auth", "logout"])
    status = run(["deadline", "auth", "status", "--output", "json"]).stdout
    assert '"AUTHENTICATED"' not in status, f"Expected logged out, got: {status}"

    run(["deadline", "auth", "login"])
    run(["deadline", "farm", "list"])

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
