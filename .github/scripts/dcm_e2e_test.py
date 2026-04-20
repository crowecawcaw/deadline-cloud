"""Drive the DCM GUI via xa11y to sign in end-to-end."""
import os
import subprocess
import time
import xa11y


def list_apps():
    for app in xa11y.App.list():
        print(f"APP: {app.name}")


def find_dcm():
    for app in xa11y.App.list():
        n = (app.name or "").lower()
        if "deadline" in n or "dcm" in n:
            return app
    return None


def main():
    # Launch DCM with the profile (same invocation deadline-cli uses)
    dcm_proc = subprocess.Popen(
        ["deadline-cloud-monitor", "login", "--profile", "dcm-test"]
    )
    # Wait for DCM window to appear in AT-SPI
    dcm = None
    for i in range(40):
        time.sleep(1)
        dcm = find_dcm()
        if dcm:
            print(f"Found DCM after {i+1}s: {dcm.name}", flush=True)
            break
    if not dcm:
        print("=== accessibility tree ===")
        list_apps()
        raise SystemExit("DCM not visible to xa11y")

    # Print full DCM subtree
    print("=== DCM subtree ===")
    for el in dcm.locator("*").all():
        try:
            role = getattr(el, "role", "?")
            name = getattr(el, "name", "")
            print(f"  {role} name={name!r}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
