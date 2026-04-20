"""Drive the DCM GUI via xa11y to sign in end-to-end."""
import os
import subprocess
import time
import xa11y

MONITOR_URL = os.environ["MONITOR_URL"].rstrip("/")
USERNAME = os.environ["MONITOR_USERNAME"]
PASSWORD = os.environ["MONITOR_PASSWORD"]


def dump_tree():
    for app in xa11y.App.list():
        print(f"APP: {app.name}")
        try:
            print(app.describe())
        except Exception as e:
            print(f"  describe failed: {e}")


def find_dcm():
    for app in xa11y.App.list():
        if "deadline" in (app.name or "").lower():
            return app
    return None


def wait_for(locator_fn, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            el = locator_fn()
            if el:
                return el
        except Exception:
            pass
        time.sleep(0.5)
    raise TimeoutError("element not found")


def main():
    # Launch deadline auth login in background, which starts DCM
    subprocess.Popen(["deadline", "auth", "login"])
    time.sleep(8)

    print("=== accessibility tree ===")
    dump_tree()

    dcm = find_dcm()
    if not dcm:
        raise SystemExit("DCM not visible to xa11y")

    # Click Launch Portal in DCM's GUI
    btn = wait_for(lambda: dcm.locator("button[name*='Launch']").first)
    btn.press()

    # Now a browser opens via xdg-open (we'll need a browser shim too)
    time.sleep(10)
    dump_tree()


if __name__ == "__main__":
    main()
