"""E2E: deadline auth login → portal click (xdotool) → IdC sign-in (Firefox via xa11y) →
scheme handler dispatches back to DCM → CLI auth completes."""
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
    """Firefox profile that auto-opens deadline-cloud-monitor:// without prompting."""
    d = os.path.expanduser("~/.mozilla/firefox/autoprof.default")
    os.makedirs(d, exist_ok=True)
    with open(f"{d}/user.js", "w") as f:
        f.write('''user_pref("network.protocol-handler.external.deadline-cloud-monitor", true);
user_pref("network.protocol-handler.warn-external.deadline-cloud-monitor", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("accessibility.force_disabled", 0);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("privacy.trackingprotection.enabled", false);
''')
    profiles_ini = os.path.expanduser("~/.mozilla/firefox/profiles.ini")
    with open(profiles_ini, "w") as f:
        f.write(f"""[Install]
Default=autoprof.default

[Profile0]
Name=default
IsRelative=1
Path=autoprof.default
Default=1
""")
    return d


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


def fill(app, name, value):
    el = wait_for(lambda: find_in_tree(app, role="entry", name_contains=name))
    el.set_value(value)


def click(app, name):
    el = wait_for(lambda: find_in_tree(app, role="push_button", name_contains=name))
    el.actions["press"]()


def click_center_of_dcm_window():
    """Click in the center of DCM's window via xdotool, hitting 'Launch Portal'."""
    # Match on partial title
    for name in ["AWS Deadline Cloud monitor", "Deadline Cloud monitor", "deadline-cloud-monitor"]:
        r = subprocess.run(
            ["xdotool", "search", "--name", name],
            capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            window_id = r.stdout.strip().split("\n")[0]
            break
    else:
        # Show all windows for diagnosis
        r = subprocess.run(["xdotool", "search", "--onlyvisible", ""],
                           capture_output=True, text=True)
        for wid in r.stdout.strip().split("\n"):
            if wid:
                nr = subprocess.run(["xdotool", "getwindowname", wid],
                                    capture_output=True, text=True)
                print(f"  window {wid}: {nr.stdout.strip()!r}")
        raise SystemExit("DCM window not found")
    subprocess.run(["xdotool", "windowactivate", "--sync", window_id], check=True)
    subprocess.run(["xdotool", "windowsize", window_id, "1000", "700"], check=True)
    subprocess.run(["xdotool", "windowmove", window_id, "100", "100"], check=True)
    time.sleep(1)
    subprocess.run(["xdotool", "mousemove", "600", "550", "click", "1"], check=True)


def sign_in_firefox():
    # DCM opens Firefox via xdg-open when Launch Portal is clicked.
    firefox = wait_for(lambda: find_app("firefox"), timeout=60)
    print(f"Firefox: {firefox.name}")
    el = wait_for(
        lambda: find_in_tree(firefox, role="entry", name_contains="Username"),
        timeout=120)
    el.set_value(USERNAME)
    click(firefox, "Next")
    fill(firefox, "Password", PASSWORD)
    click(firefox, "Sign in")
    try:
        click(firefox, "Allow")
    except TimeoutError:
        pass


def main():
    make_firefox_profile()
    cli = subprocess.Popen(
        ["deadline", "auth", "login"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(15)  # wait for DCM to start + webview to load
    click_center_of_dcm_window()
    sign_in_firefox()

    try:
        out, _ = cli.communicate(timeout=120)
        print(out, flush=True)
        if cli.returncode:
            raise SystemExit(f"auth login failed: {cli.returncode}")
    finally:
        if cli.poll() is None:
            cli.kill()

    run(["deadline", "auth", "status"])
    run(["deadline", "auth", "logout"])
    r = run(["deadline", "auth", "status"], check=False)
    assert "AUTHENTICATED" not in r.stdout
    run(["deadline", "auth", "login"])
    run(["deadline", "farm", "list"])
    print("SUCCESS")


if __name__ == "__main__":
    main()
