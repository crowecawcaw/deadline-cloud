"""End-to-end test: DCM profile creation, sign in, then deadline CLI auth flow.

Flow:
  1. Launch DCM GUI.
  2. Drive DCM's "Create new Profile" wizard via xa11y: enter MONITOR_URL, click through
     the 3 steps, set profile as default, click "Create and launch".
  3. DCM opens the system browser via xdg-open (shadowed by our driver), which hands
     the IAM Identity Center URL to agent-browser. Agent-browser signs in and the 302
     redirect completes DCM's OAuth callback.
  4. Run deadline auth login / logout / login / farm list and assert each succeeds.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import xa11y

MONITOR_URL = os.environ["MONITOR_URL"]
USERNAME = os.environ["MONITOR_USERNAME"]
PASSWORD = os.environ["MONITOR_PASSWORD"]
DRIVER_LOG = Path("/tmp/drive-auth.log")


def run(cmd, check=True, env=None, timeout=60):
    print(f"$ {' '.join(cmd)}", flush=True)
    try:
        r = subprocess.run(cmd, text=True, env=env, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except subprocess.TimeoutExpired as e:
        print(f"TIMEOUT after {timeout}s. Partial output:\n{e.output}", flush=True)
        raise SystemExit(f"Command timed out: {cmd}")
    print(r.stdout, flush=True)
    if check and r.returncode != 0:
        raise SystemExit(f"Command failed ({r.returncode})")
    return r


def make_browser_driver() -> str:
    """Shadow xdg-open with a script that drives IAM Identity Center via agent-browser."""
    bin_dir = Path("/tmp/fake-browser")
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "drive-auth.sh"
    script.write_text(f"""#!/usr/bin/env bash
set -x
URL="$1"
exec >>{DRIVER_LOG} 2>&1
echo "=== drive-auth invoked: $URL ==="
{{
  agent-browser open "$URL"
  agent-browser wait --load networkidle
  agent-browser find role textbox fill --name Username {USERNAME!r}
  agent-browser find role button click --name Next
  agent-browser wait --load networkidle
  agent-browser find role textbox fill --name Password {PASSWORD!r}
  agent-browser find role button click --name 'Sign in'
  agent-browser wait --load networkidle
  agent-browser find role button click --name Allow 2>/dev/null || true
  agent-browser wait --load networkidle 2>/dev/null || true
  echo "=== sign-in complete ==="
}} &
exit 0
""")
    script.chmod(0o755)
    for name in ("xdg-open", "kde-open", "kde-open5"):
        link = bin_dir / name
        if link.exists():
            link.unlink()
        link.symlink_to(script)
    return str(bin_dir)


def create_profile_via_gui():
    """Drive DCM's Create-Profile wizard with xa11y."""
    print("Waiting for DCM window...", flush=True)
    dcm = None
    for _ in range(30):
        try:
            dcm = xa11y.App.by_name("deadline-cloud-monitor")
            break
        except Exception:
            try:
                dcm = xa11y.App.by_name("Deadline Cloud Monitor")
                break
            except Exception:
                time.sleep(1)
    if dcm is None:
        raise SystemExit("DCM window never appeared")
    dcm.locator("window").wait_visible(timeout=30)

    dcm.locator("button[name='Create new Profile']").wait_visible(timeout=30)
    dcm.locator("button[name='Create new Profile']").press()

    url_field = dcm.locator("text_field[name*='URL']")
    url_field.wait_visible(timeout=10)
    url_field.set_value(MONITOR_URL)
    dcm.locator("button[name='Next']").press()  # Step 1 → 2

    dcm.locator("text_field[name*='profile name']").wait_visible(timeout=10)
    dcm.locator("button[name='Next']").press()  # Step 2 → 3

    dcm.locator("check_box[name*='default for Deadline Cloud tools']").press()
    dcm.locator("button[name='Create and launch']").press()
    print("DCM profile created and launched; browser should now open.", flush=True)


def dump_driver_log():
    if DRIVER_LOG.exists():
        print("=== driver log ===", flush=True)
        print(DRIVER_LOG.read_text(), flush=True)


def assert_authenticated():
    r = run(["deadline", "auth", "status", "--output", "json"])
    if '"AUTHENTICATED"' not in r.stdout:
        raise SystemExit(f"Expected AUTHENTICATED, got: {r.stdout}")


def assert_not_authenticated():
    r = run(["deadline", "auth", "status", "--output", "json"])
    if '"AUTHENTICATED"' in r.stdout:
        raise SystemExit(f"Expected NOT authenticated, got: {r.stdout}")


def main():
    bin_dir = make_browser_driver()
    env = {**os.environ, "BROWSER": f"{bin_dir}/drive-auth.sh",
           "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    # Launch DCM in the background; drive its wizard
    dcm_proc = subprocess.Popen(["deadline-cloud-monitor"], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(3)
        create_profile_via_gui()

        # Wait for DCM to report authenticated. Poll deadline auth status.
        print("Waiting for authentication to complete...", flush=True)
        deadline = time.time() + 180
        while time.time() < deadline:
            r = subprocess.run(["deadline", "auth", "status", "--output", "json"],
                               capture_output=True, text=True, env=env)
            if '"AUTHENTICATED"' in r.stdout:
                break
            time.sleep(3)
        else:
            dump_driver_log()
            raise SystemExit("Never authenticated after create-and-launch")

        # Full CLI flow
        run(["deadline", "auth", "status", "--output", "json"], env=env)
        run(["deadline", "auth", "logout"], env=env, timeout=60)
        assert_not_authenticated()
        run(["deadline", "auth", "login"], env=env, timeout=180)
        assert_authenticated()
        run(["deadline", "farm", "list"], env=env, timeout=30)
        print("\nAll checks passed.")
    finally:
        dump_driver_log()
        dcm_proc.terminate()


if __name__ == "__main__":
    main()
