"""E2E: drive DCM's Tauri GUI via xa11y (works if Orca is running so webkit2gtk
exposes its a11y tree); then drive Firefox for IdC sign-in; deep-link returns to DCM."""
import os
import subprocess
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


def find_app(substr):
    for a in xa11y.App.list():
        if substr.lower() in (a.name or "").lower():
            return a
    return None


def wait_for(pred, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        r = pred()
        if r:
            return r
        time.sleep(0.5)
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


def click(app, **kw):
    el = wait_for(lambda: find_in_tree(app, **kw))
    el.actions["press"]()


def fill(app, name, value):
    el = wait_for(lambda: find_in_tree(app, role="entry", name_contains=name))
    el.set_value(value)


def main():
    cli = subprocess.Popen(["deadline", "auth", "login"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        dcm = wait_for(lambda: find_app("deadline"))
        print(f"DCM: {dcm.name}")
        # Wait for webview content to load; the Launch Portal push_button should appear
        click(dcm, role="push_button", name_contains="Launch")

        firefox = wait_for(lambda: find_app("firefox"), timeout=60)
        print(f"Firefox: {firefox.name}")
        fill(firefox, "Username", USERNAME)
        click(firefox, role="push_button", name_contains="Next")
        fill(firefox, "Password", PASSWORD)
        click(firefox, role="push_button", name_contains="Sign in")
        try:
            click(firefox, role="push_button", name_contains="Allow")
        except TimeoutError:
            pass

        out, _ = cli.communicate(timeout=180)
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
