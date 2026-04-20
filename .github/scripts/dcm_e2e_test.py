"""End-to-end test: DCM first-time sign-in, then deadline auth login/logout/login + farm list.

DCM opens the system browser via $BROWSER or xdg-open with the IAM Identity Center auth URL,
then waits for the OAuth callback on a local port. We set BROWSER to a helper script that
drives the sign-in via agent-browser, which follows the redirect back to the callback and
completes DCM's auth.
"""
import os
import subprocess
import sys
from pathlib import Path

USERNAME = os.environ["MONITOR_USERNAME"]
PASSWORD = os.environ["MONITOR_PASSWORD"]


def run(cmd, check=True, env=None):
    print(f"$ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.stdout:
        print(r.stdout)
    if r.stderr:
        print(r.stderr, file=sys.stderr)
    if check and r.returncode != 0:
        raise SystemExit(f"Command failed ({r.returncode})")
    return r


def make_browser_driver() -> tuple[str, str]:
    """Create a shell script that acts as the system browser.

    Returns (bin_dir, path_to_script). We shadow `xdg-open`, `kde-open`, and `BROWSER`
    so DCM's auth URL is handed to this script, which drives agent-browser to complete
    IAM Identity Center sign-in. The 302 redirect agent-browser follows hits DCM's
    http://127.0.0.1:PORT/oauth/callback and completes auth.
    """
    bin_dir = Path("/tmp/fake-browser")
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "drive-auth.sh"
    script.write_text(f"""#!/usr/bin/env bash
set -e
URL="$1"
echo "[auth driver] URL: $URL" >&2
agent-browser open "$URL"
agent-browser wait --load networkidle
agent-browser find role textbox fill --name Username {USERNAME!r}
agent-browser find role button click --name Next
agent-browser wait --load networkidle
agent-browser find role textbox fill --name Password {PASSWORD!r}
agent-browser find role button click --name 'Sign in'
agent-browser wait --load networkidle
# Optional consent screen for the same-device devtools client
agent-browser find role button click --name Allow 2>/dev/null || true
agent-browser wait --load networkidle 2>/dev/null || true
echo "[auth driver] done" >&2
""")
    script.chmod(0o755)
    # Shadow xdg-open + kde-open so DCM invokes our driver
    for name in ("xdg-open", "kde-open", "kde-open5"):
        link = bin_dir / name
        if link.exists():
            link.unlink()
        link.symlink_to(script)
    return str(bin_dir), str(script)


def assert_authenticated():
    r = run(["deadline", "auth", "status", "--output", "json"])
    if '"AUTHENTICATED"' not in r.stdout:
        raise SystemExit(f"Expected AUTHENTICATED, got: {r.stdout}")


def assert_not_authenticated():
    r = run(["deadline", "auth", "status", "--output", "json"])
    if '"AUTHENTICATED"' in r.stdout:
        raise SystemExit(f"Expected NOT authenticated, got: {r.stdout}")


def main():
    bin_dir, driver = make_browser_driver()
    env = {
        **os.environ,
        "BROWSER": driver,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }

    # 1. First-time login
    run(["deadline", "auth", "login"], env=env)
    assert_authenticated()

    # 2. Logout
    run(["deadline", "auth", "logout"], env=env)
    assert_not_authenticated()

    # 3. Login again
    run(["deadline", "auth", "login"], env=env)
    assert_authenticated()

    # 4. Farm list
    run(["deadline", "farm", "list"], env=env)

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
