"""E2E: drive DCM via xa11y (falls back to xdotool for webview click), then Firefox for IdC sign-in."""
import os
import subprocess
import sys
import time
import xa11y

sys.stdout.reconfigure(line_buffering=True)

MONITOR_URL = os.environ["MONITOR_URL"].rstrip("/")
USERNAME = os.environ["MONITOR_USERNAME"]
PASSWORD = os.environ["MONITOR_PASSWORD"]


def run(cmd, check=True, timeout=180):
    print(f"$ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    print(r.stdout, r.stderr, flush=True)
    if check and r.returncode:
        raise SystemExit(f"failed: {cmd}")
    return r


def screenshot(name):
    subprocess.run(["import", "-window", "root", f"/tmp/{name}.png"], check=False)
    subprocess.run(["ls", "-la", f"/tmp/{name}.png"], check=False)


def iter_apps(substr):
    return [a for a in xa11y.App.list() if substr.lower() in (a.name or "").lower()]


def walk(root):
    stack = [root]
    while stack:
        el = stack.pop()
        yield el
        try:
            stack.extend(el.children())
        except Exception:
            pass


def find(roots, role=None, name_contains=None):
    for r in roots:
        for el in walk(r):
            try:
                er, en = el.role, (el.name or "")
            except Exception:
                continue
            if (role is None or er == role) and (name_contains is None or name_contains.lower() in en.lower()):
                return el
    return None


def wait_for(fn, timeout=60, desc=""):
    end = time.time() + timeout
    while time.time() < end:
        r = fn()
        if r:
            return r
        time.sleep(0.5)
    raise TimeoutError(desc)


def dump(el, d=0, maxd=20):
    if d > maxd:
        return
    try:
        print("  " * d + f"{el.role} name={(el.name or '')[:100]!r}")
    except Exception:
        return
    try:
        for c in el.children():
            dump(c, d + 1, maxd)
    except Exception:
        pass


def dump_all(substr):
    print(f"=== apps matching {substr!r} ===")
    for a in iter_apps(substr):
        print(f"APP {a.name}")
        try:
            for c in a.children():
                dump(c)
        except Exception as e:
            print(f"  (no children: {e})")


def xdotool_click_center_of_dcm_window():
    """Fallback: click center-ish of DCM window where Launch Portal button sits."""
    # Query DCM window geometry via xdotool
    r = subprocess.run(["xdotool", "search", "--name", "Deadline Cloud monitor"],
                       capture_output=True, text=True)
    wid = (r.stdout.strip().split("\n") or [""])[-1]
    if not wid:
        raise RuntimeError("no DCM window")
    subprocess.run(["xdotool", "windowactivate", wid], check=False)
    subprocess.run(["xdotool", "windowsize", wid, "1200", "800"], check=False)
    subprocess.run(["xdotool", "windowmove", wid, "100", "100"], check=False)
    time.sleep(1)
    # Click centre of the window (Launch Portal button is typically horizontally centred
    # around y=~400 in a 1200x800 "sign in" view).
    subprocess.run(["xdotool", "mousemove", "700", "500", "click", "1"], check=True)
    print("xdotool clicked 700,500", flush=True)


def main():
    print("starting")
    cli = subprocess.Popen(["deadline", "auth", "login"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(f"auth login pid={cli.pid}")
    try:
        wait_for(lambda: iter_apps("deadline") or None, timeout=30, desc="DCM")
        time.sleep(8)  # Let webview render
        screenshot("dcm_before_click")

        roots = iter_apps("deadline") + iter_apps("webkit")
        btn = find(roots, role="push_button", name_contains="Launch")
        if btn:
            btn.actions["press"]()
            print("clicked Launch Portal via xa11y")
        else:
            print("xa11y didn't find Launch Portal, dumping and falling back to xdotool")
            dump_all("deadline")
            dump_all("webkit")
            xdotool_click_center_of_dcm_window()
        time.sleep(2)
        screenshot("dcm_after_click")

        wait_for(lambda: iter_apps("firefox") or None, timeout=60, desc="firefox")
        ff = iter_apps("firefox")
        user_el = wait_for(lambda: find(ff, role="entry", name_contains="Username"), desc="Username")
        user_el.set_value(USERNAME)
        find(ff, role="push_button", name_contains="Next").actions["press"]()
        pw_el = wait_for(lambda: find(ff, role="entry", name_contains="Password"), desc="Password")
        pw_el.set_value(PASSWORD)
        find(ff, role="push_button", name_contains="Sign in").actions["press"]()
        try:
            el = wait_for(lambda: find(iter_apps("firefox"), role="push_button", name_contains="Allow"),
                          timeout=15, desc="Allow")
            el.actions["press"]()
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
