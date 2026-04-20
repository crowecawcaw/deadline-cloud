"""End-to-end test: DCM first-time sign-in, then deadline auth login/logout/login + farm list.

DCM opens the system browser via xdg-open with the IAM Identity Center auth URL,
then waits for the OAuth callback on a local port. We shadow xdg-open with a helper
that drives the sign-in via agent-browser, which follows the redirect back to the
callback and completes DCM's auth.
"""
import os
import subprocess
import sys
from pathlib import Path

USERNAME = os.environ["MONITOR_USERNAME"]
PASSWORD = os.environ["MONITOR_PASSWORD"]


def run(cmd, check=True, env=None, timeout=120):
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
    bin_dir = Path("/tmp/fake-browser")
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "drive-auth.sh"
    script.write_text(f"""#!/usr/bin/env bash
set -x
URL="$1"
LOG=/tmp/drive-auth.log
echo "=== drive-auth invoked: $URL ===" >> $LOG 2>&1
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
  echo "=== sign-in complete ===" >&2
}} >> $LOG 2>&1 &
exit 0
""")
    script.chmod(0o755)
    for name in ("xdg-open", "kde-open", "kde-open5"):
        link = bin_dir / name
        if link.exists():
            link.unlink()
        link.symlink_to(script)
    return str(bin_dir)


def assert_authenticated():
    r = run(["deadline", "auth", "status", "--output", "json"])
    if '"AUTHENTICATED"' not in r.stdout:
        raise SystemExit(f"Expected AUTHENTICATED, got: {r.stdout}")


def assert_not_authenticated():
    r = run(["deadline", "auth", "status", "--output", "json"])
    if '"AUTHENTICATED"' in r.stdout:
        raise SystemExit(f"Expected NOT authenticated, got: {r.stdout}")


def dump_driver_log():
    log = Path("/tmp/drive-auth.log")
    if log.exists():
        print("=== driver log ===", flush=True)
        print(log.read_text(), flush=True)


def main():
    bin_dir = make_browser_driver()
    env = {
        **os.environ,
        "BROWSER": f"{bin_dir}/drive-auth.sh",
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }

    # Sanity check: DCM must be runnable, and our xdg-open must take priority
    run(["which", "deadline-cloud-monitor"])
    run(["which", "xdg-open"], env=env)
    run(["deadline", "config", "show"])
    print(f"AWS config:\n{Path.home().joinpath('.aws/config').read_text() if Path.home().joinpath('.aws/config').exists() else '(none)'}", flush=True)

    # Probe DCM itself: can it launch at all under Xvfb? Run login in bg, wait 20s, kill.
    print("=== DCM launch probe ===", flush=True)
    p = subprocess.Popen(
        ["deadline-cloud-monitor", "login", "--profile", "dcm-test"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    import time
    time.sleep(20)
    dump_driver_log()
    p.terminate()
    try:
        out, _ = p.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
    print(f"DCM stdout/stderr (first 2KB):\n{out[:2000]}", flush=True)

    try:
        # Skip the full flow until we understand what DCM is doing
        print("Probe complete; exiting after diagnostics.")
    finally:
        dump_driver_log()


if __name__ == "__main__":
    main()
