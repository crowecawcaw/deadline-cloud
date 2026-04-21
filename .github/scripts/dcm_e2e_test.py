"""E2E: DCM background + Firefox (driven by xa11y) signs into IdC; Firefox follows
deadline-cloud-monitor:// scheme to DCM's handle-url; CLI login polls AUTHENTICATED."""
import os
import subprocess
import tempfile
import time
import xa11y

MONITOR_URL = os.environ["MONITOR_URL"].rstrip("/")
USERNAME = os.environ["MONITOR_USERNAME"]
PASSWORD = os.environ["MONITOR_PASSWORD"]


def run(cmd, check=True, timeout=120):
    print(f"$ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    print(r.stdout, r.stderr, flush=True)
    if check and r.returncode:
        raise SystemExit(f"failed: {cmd}")
    return r


def make_firefox_profile() -> str:
    p = tempfile.mkdtemp(prefix="ffp-")
    with open(f"{p}/user.js", "w") as f:
        # Auto-open deadline-cloud-monitor:// without prompting
        f.write('''user_pref("network.protocol-handler.external.deadline-cloud-monitor", true);
user_pref("network.protocol-handler.warn-external.deadline-cloud-monitor", false);
user_pref("network.protocol-handler.expose.deadline-cloud-monitor", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("accessibility.force_disabled", 0);
user_pref("browser.tabs.remote.force-enable", false);
''')
    return p


def find_app(substr):
    for a in xa11y.App.list():
        if substr.lower() in (a.name or "").lower():
            return a
    return None


def wait_for(pred, timeout=60, interval=0.5):
    end = time.time() + timeout
    while time.time() < end:
        r = pred()
        if r:
            return r
        time.sleep(interval)
    raise TimeoutError


def find_in_tree(root, role=None, name_contains=None):
    stack = [root]
    while stack:
        el = stack.pop()
        try:
            er, en = el.role, (el.name or "")
        except Exception:
            continue
        if (role is None or er == role) and (name_contains is None or name_contains.lower() in en.lower()):
            return el
        try:
            stack.extend(el.children())
        except Exception:
            pass
    return None


def fill(firefox, name, value):
    el = wait_for(lambda: find_in_tree(firefox, role="entry", name_contains=name))
    el.set_value(value)


def click(firefox, name):
    el = wait_for(lambda: find_in_tree(firefox, role="push_button", name_contains=name))
    el.actions["press"]()


def sign_in():
    ffp = make_firefox_profile()
    login_url = f"{MONITOR_URL}/?lng=en#dcmProfile=dcm-test"
    subprocess.Popen(["firefox", "--no-remote", "--profile", ffp, login_url])
    firefox = wait_for(lambda: find_app("firefox"), timeout=30)
    print(f"Firefox: {firefox.name}")
    time.sleep(15)  # wait for page load
    print("=== firefox tree ===", flush=True)
    def dump(el, d=0):
        if d > 12: return
        try:
            print("  " * d + f"{el.role} name={(el.name or '')[:80]!r}")
        except Exception:
            return
        try:
            for c in el.children():
                dump(c, d + 1)
        except Exception:
            pass
    for c in firefox.children():
        dump(c)
    fill(firefox, "Username", USERNAME)
    click(firefox, "Next")
    fill(firefox, "Password", PASSWORD)
    click(firefox, "Sign in")
    # consent page may or may not appear
    try:
        click(firefox, "Allow")
    except TimeoutError:
        pass


def main():
    cli = subprocess.Popen(["deadline", "auth", "login"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        sign_in()
        out, _ = cli.communicate(timeout=120)
        print(out)
        if cli.returncode:
            raise SystemExit(f"auth login failed: {cli.returncode}")
    finally:
        if cli.poll() is None:
            cli.kill()

    run(["deadline", "auth", "status"])
    run(["deadline", "auth", "logout"])
    r = run(["deadline", "auth", "status"], check=False)
    assert "AUTHENTICATED" not in r.stdout, "expected logged out"
    run(["deadline", "auth", "login"])  # reuses running DCM silently? if not, re-drive
    run(["deadline", "farm", "list"])
    print("SUCCESS")


if __name__ == "__main__":
    main()
