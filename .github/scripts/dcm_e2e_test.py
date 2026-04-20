"""End-to-end: deadline auth login → logout → login → farm list.

DCM's create-profile CLI wrote the profile at workflow setup time. `deadline auth login`
invokes `deadline-cloud-monitor login --profile dcm-test`, which opens the system
browser via xdg-open. We shadow xdg-open with a script that drives the IAM Identity
Center sign-in via agent-browser; the 302 redirect completes DCM's OAuth callback.
"""
import os
import subprocess
import sys
from pathlib import Path

USERNAME = os.environ["MONITOR_USERNAME"]
PASSWORD = os.environ["MONITOR_PASSWORD"]
DRIVER_LOG = Path("/tmp/drive-auth.log")


def run(cmd, check=True, env=None, timeout=180):
    print(f"$ {' '.join(cmd)}", flush=True)
    try:
        r = subprocess.run(cmd, text=True, env=env, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except subprocess.TimeoutExpired as e:
        print(f"TIMEOUT after {timeout}s:\n{e.output}", flush=True)
        dump_driver_log()
        raise SystemExit(f"Command timed out: {cmd}")
    print(r.stdout, flush=True)
    if check and r.returncode != 0:
        dump_driver_log()
        raise SystemExit(f"Command failed ({r.returncode})")
    return r


def dump_driver_log():
    if DRIVER_LOG.exists():
        print("=== driver log ===", flush=True)
        print(DRIVER_LOG.read_text(), flush=True)


def make_browser_driver() -> str:
    bin_dir = Path("/tmp/fake-browser")
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "drive-auth.sh"
    script.write_text(f"""#!/usr/bin/env bash
set -x
URL="$1"
exec >>{DRIVER_LOG} 2>&1
echo "=== drive-auth: $URL ==="
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
  echo "=== done ==="
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


def assert_authenticated(env):
    r = run(["deadline", "auth", "status", "--output", "json"], env=env)
    if '"AUTHENTICATED"' not in r.stdout:
        raise SystemExit(f"Expected AUTHENTICATED: {r.stdout}")


def assert_not_authenticated(env):
    r = run(["deadline", "auth", "status", "--output", "json"], env=env)
    if '"AUTHENTICATED"' in r.stdout:
        raise SystemExit(f"Expected NOT authenticated: {r.stdout}")


def main():
    bin_dir = make_browser_driver()
    env = {**os.environ, "BROWSER": f"{bin_dir}/drive-auth.sh",
           "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    try:
        run(["deadline", "auth", "login"], env=env)
        assert_authenticated(env)
        run(["deadline", "auth", "logout"], env=env, timeout=60)
        assert_not_authenticated(env)
        run(["deadline", "auth", "login"], env=env)
        assert_authenticated(env)
        run(["deadline", "farm", "list"], env=env, timeout=30)
        print("\nAll checks passed.")
    finally:
        dump_driver_log()


if __name__ == "__main__":
    main()
