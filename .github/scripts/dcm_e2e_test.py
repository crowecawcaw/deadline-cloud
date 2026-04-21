"""E2E: deadline auth login auto-opens Firefox; drive the IdC sign-in via xa11y."""
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


def dump(el, d=0, maxd=25):
    if d > maxd:
        return
    try:
        print("  " * d + f"{el.role} name={(el.name or '')[:80]!r}")
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


def sign_in():
    ff = wait_for(lambda: iter_apps("firefox") or None, timeout=60, desc="firefox app")
    # Wait for Username field (AWS sign-in page)
    try:
        user_el = wait_for(lambda: find(ff, role="entry", name_contains="Username"),
                           timeout=90, desc="Username field")
    except TimeoutError:
        screenshot("firefox_no_username")
        dump_all("firefox")
        raise
    user_el.set_value(USERNAME)
    find(ff, role="push_button", name_contains="Next").actions["press"]()
    pw_el = wait_for(lambda: find(ff, role="entry", name_contains="Password"),
                     timeout=60, desc="Password field")
    pw_el.set_value(PASSWORD)
    find(ff, role="push_button", name_contains="Sign in").actions["press"]()
    try:
        el = wait_for(lambda: find(iter_apps("firefox"), role="push_button",
                                   name_contains="Allow"),
                      timeout=15, desc="Allow")
        el.actions["press"]()
    except TimeoutError:
        pass


def main():
    cli = subprocess.Popen(["deadline", "auth", "login"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(f"auth login pid={cli.pid}", flush=True)
    try:
        time.sleep(10)
        screenshot("after_auth_login")
        sign_in()
        screenshot("after_sign_in")
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
    # Re-login
    cli2 = subprocess.Popen(["deadline", "auth", "login"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(10)
        sign_in()
        out, _ = cli2.communicate(timeout=180)
        print(out, flush=True)
        if cli2.returncode:
            raise SystemExit(f"re-login failed: {cli2.returncode}")
    finally:
        if cli2.poll() is None:
            cli2.kill()

    run(["deadline", "farm", "list"])
    print("SUCCESS")


if __name__ == "__main__":
    main()
