"""E2E: DCM background + Firefox (driven by xa11y) signs into IdC.
Firefox follows deadline-cloud-monitor:// scheme to DCM's handle-url;
CLI login polls AUTHENTICATED."""
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
        f.write('''user_pref("network.protocol-handler.external.deadline-cloud-monitor", true);
user_pref("network.protocol-handler.warn-external.deadline-cloud-monitor", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("accessibility.force_disabled", 0);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("privacy.trackingprotection.enabled", false);
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


def dump(el, d=0, maxd=14):
    if d > maxd: return
    try:
        print("  " * d + f"{el.role} name={(el.name or '')[:100]!r}")
    except Exception:
        return
    try:
        for c in el.children():
            dump(c, d + 1, maxd)
    except Exception:
        pass


def fill(app, name, value):
    el = wait_for(lambda: find_in_tree(app, role="entry", name_contains=name))
    el.set_value(value)


def click(app, name):
    el = wait_for(lambda: find_in_tree(app, role="push_button", name_contains=name))
    el.actions["press"]()


def main():
    # Start DCM in background — sets up PKCE local server for the portal handshake
    dcm = subprocess.Popen(["deadline-cloud-monitor", "login", "--profile", "dcm-test"])
    time.sleep(10)  # Let DCM init PKCE server

    ffp = make_firefox_profile()
    login_url = f"{MONITOR_URL}/"  # plain, no fragment
    subprocess.Popen(["firefox", "--no-remote", "--profile", ffp, login_url])
    firefox = wait_for(lambda: find_app("firefox"), timeout=30)
    print(f"Firefox: {firefox.name}")

    try:
        el = wait_for(lambda: find_in_tree(firefox, role="entry", name_contains="Username"),
                      timeout=90)
    except TimeoutError:
        print("=== dump nodes on DCM tab ===", flush=True)
        # Find the first web_area whose parent/tab is 'AWS Deadline Cloud'
        stack = list(firefox.children())
        while stack:
            el = stack.pop()
            try:
                if el.role == "web_area":
                    print(f"\n--- web_area name={el.name!r} ---")
                    dump(el, maxd=25)
            except Exception:
                continue
            try:
                stack.extend(el.children())
            except Exception:
                pass
        raise

    el.set_value(USERNAME)
    click(firefox, "Next")
    fill(firefox, "Password", PASSWORD)
    click(firefox, "Sign in")
    try:
        click(firefox, "Allow")
    except TimeoutError:
        pass

    # Now the browser should redirect to deadline-cloud-monitor://launch?...
    # which our .desktop handler dispatches to DCM handle-url.
    # DCM stores creds. CLI check succeeds.
    time.sleep(10)

    run(["deadline", "auth", "status"])
    run(["deadline", "auth", "logout"])
    r = run(["deadline", "auth", "status"], check=False)
    assert "AUTHENTICATED" not in r.stdout
    # Second login reuses profile → should succeed silently without browser
    run(["deadline", "auth", "login"])
    run(["deadline", "farm", "list"])
    print("SUCCESS")


if __name__ == "__main__":
    main()
