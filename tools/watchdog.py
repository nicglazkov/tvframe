#!/usr/bin/env python3
"""TV Frame watchdog: every 5 minutes (systemd --user timer tvframe-watchdog), check the Streamer still runs
TV Frame the way tools/deploy.sh left it, put back anything an update or a reset turned off, and say what was
fixed in a plain phone notification through Home Assistant (HA_NOTIFY in config.env, e.g. notify.mobile_app_phone).

Checks: the Home button service is on and running, the screensaver is TV Frame after 5 idle minutes, and the
app is installed at the version built on the server. It never installs anything: a missing or older app is only
reported. Pause it with `touch ~/.config/tvframe/watchdog.pause` (for example while changing settings on
purpose); delete the file to resume. State and the last result: ~/.cache/tvframe-watchdog.json.
"""
import json, os, re, subprocess, sys, time, urllib.request
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402

HOME = os.path.expanduser("~")
ADB = [os.environ.get("ADB_BIN", f"{HOME}/android/sdk/platform-tools/adb"), "-s", tvconf.TV]
SVC = "app.tvframe/app.tvframe.HomeKeyService"
DREAM = "app.tvframe/.FrameDreamService"
TIMEOUT = "300000"
APK_META = f"{HOME}/tvframe/android/app/build/outputs/apk/release/output-metadata.json"
PAUSE = f"{HOME}/.config/tvframe/watchdog.pause"
STATE = f"{HOME}/.cache/tvframe-watchdog.json"
HA = f"{tvconf.HA_URL}/api/services/{tvconf.get('HA_NOTIFY').replace('.', '/')}" if tvconf.HA_URL and tvconf.get("HA_NOTIFY") else None
QUIET = 6 * 3600          # the same problem is reported at most once in 6 hours
OFFLINE_AFTER = 3600      # say the Streamer is unreachable only after an hour of failed checks


def log(*a):
    print(datetime.now().strftime("%m-%d %H:%M:%S"), *a, flush=True)


def sh(*cmd, timeout=15):
    r = subprocess.run(ADB + ["shell", *cmd], capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[:200])
    return r.stdout.replace("\r", "").strip()


def notify(msg):
    if not HA:
        return log("notify (no HA_NOTIFY set):", msg)
    token = tvconf.ha_token()
    req = urllib.request.Request(HA, method="POST", data=json.dumps({"title": "TV Frame", "message": msg}).encode(),
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def built_version():
    """The version of the last APK built, once it is half an hour old, so a build that is about to be installed
    is not reported as missing from the Streamer."""
    try:
        if time.time() - os.path.getmtime(APK_META) < 1800:
            return None
        return json.load(open(APK_META))["elements"][0]["versionName"]
    except Exception:
        return None


def vkey(v):
    return [int(x) for x in re.findall(r"\d+", v or "")]


def check():
    """Returns (fixed, problems): what was put back, and what needs a person."""
    fixed, problems = [], []
    m = re.search(r"versionName=(\S+)", sh("dumpsys", "package", "app.tvframe"))
    if not m:
        return fixed, ["The TV Frame app is not installed on the Streamer any more. Nothing was changed."]
    want = built_version()
    if want and vkey(m.group(1)) < vkey(want):
        problems.append(f"The Streamer has TV Frame {m.group(1)}, older than {want}. Nothing was changed.")

    cur = sh("settings", "get", "secure", "enabled_accessibility_services")
    parts = [p for p in cur.split(":") if p and p != "null"]
    on = sh("settings", "get", "secure", "accessibility_enabled") == "1"
    if SVC not in parts or not on:
        sh("settings", "put", "secure", "enabled_accessibility_services", ":".join([p for p in parts if p != SVC] + [SVC]))
        sh("settings", "put", "secure", "accessibility_enabled", "1")
        fixed.append("the Home button service was off")
    elif "TV Frame home button" not in sh("dumpsys", "accessibility").split("Bound services:")[-1].split("\n")[0]:
        # turned on but not running (crashed or not rebound after an update): turning it off and on rebinds it
        sh("settings", "put", "secure", "enabled_accessibility_services", ":".join([p for p in parts if p != SVC]) or "null")
        time.sleep(1)
        sh("settings", "put", "secure", "enabled_accessibility_services", ":".join(parts))
        fixed.append("the Home button service had stopped")

    if sh("settings", "get", "secure", "screensaver_components") != DREAM or sh("settings", "get", "secure", "screensaver_enabled") != "1":
        sh("settings", "put", "secure", "screensaver_components", DREAM)
        sh("settings", "put", "secure", "screensaver_enabled", "1")
        fixed.append("the screensaver was not TV Frame")
    if sh("settings", "get", "system", "screen_off_timeout") != TIMEOUT:
        sh("settings", "put", "system", "screen_off_timeout", TIMEOUT)
        fixed.append("the screensaver delay was changed")
    return fixed, problems


def main():
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    st.setdefault("sent", {})
    now = time.time()
    if os.path.exists(PAUSE):
        st.update(at=now, result="paused")
    else:
        subprocess.run([ADB[0], "connect", ADB[2]], capture_output=True, timeout=15)
        try:
            fixed, problems = check()
        except Exception as e:
            st.setdefault("offline_since", now)
            st.update(at=now, result=f"unreachable: {e}")
            log("Streamer unreachable:", e)
            if now - st["offline_since"] > OFFLINE_AFTER and not st.get("offline_told"):
                try:
                    notify("The Streamer has not answered for an hour, so its settings are not being checked.")
                    st["offline_told"] = True
                except Exception as e2:
                    log("notify failed:", e2)
        else:
            if st.pop("offline_told", None):
                try:
                    notify("The Streamer answers again.")
                except Exception as e2:
                    log("notify failed:", e2)
            st.pop("offline_since", None)
            msgs = []
            if fixed:
                log("fixed:", "; ".join(fixed))
                msgs.append("Put back: " + "; ".join(fixed) + ". Probably an update.")
            for p in problems:
                log("problem:", p)
                if now - st["sent"].get(p, 0) > QUIET:
                    msgs.append(p)
                    st["sent"][p] = now
            for m in msgs:
                try:
                    notify(m)
                except Exception as e:
                    log("notify failed:", e)
            st.update(at=now, result="ok" if not fixed and not problems else "; ".join(fixed + problems))
            if fixed:
                st["last_fix"] = {"at": now, "what": fixed}
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE)


if __name__ == "__main__":
    sys.exit(main())
