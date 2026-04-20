"""Probe DCM a11y tree on Linux CI, find the Launch Portal button."""
import subprocess
import time
import xa11y


def find_dcm():
    for app in xa11y.App.list():
        if "deadline" in (app.name or "").lower():
            return app
    return None


def dump(el, depth=0, max_depth=10):
    try:
        name = el.name
        role = el.role
    except Exception:
        return
    pad = "  " * depth
    print(f"{pad}{role} name={name!r}")
    if depth < max_depth:
        try:
            for c in el.children():
                dump(c, depth+1, max_depth)
        except Exception as e:
            print(f"{pad}  <children error: {e}>")


def main():
    subprocess.Popen(["deadline-cloud-monitor", "login", "--profile", "dcm-test"])
    dcm = None
    for i in range(40):
        time.sleep(1)
        dcm = find_dcm()
        if dcm:
            print(f"Found DCM after {i+1}s")
            break
    if not dcm:
        raise SystemExit("DCM not visible")

    # Dump tree several times as webview content loads
    for i, delay in enumerate([0, 5, 10, 20]):
        time.sleep(delay)
        print(f"\n=== tree at t+{sum([0,5,15,35][:i+1])}s ===")
        for c in dcm.children():
            dump(c)


if __name__ == "__main__":
    main()
