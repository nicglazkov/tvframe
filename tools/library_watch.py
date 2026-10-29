#!/usr/bin/env python3
"""Watch library/ and run tools/library_sync.sh after it changes.

Checks every 15 s. A failed build is retried every 10 minutes. A change (a file added, removed, renamed or rewritten) starts a 45 s quiet period so a big
copy from Finder finishes first; every further change restarts it. Runs as the systemd --user unit
tvframe-library. Status for the photos page: ~/.cache/tvframe-library.json."""
import json, os, subprocess, time
from datetime import datetime
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402

T = tvconf.ROOT
LIB = f"{T}/library"
STATUS = os.path.expanduser("~/.cache/tvframe-library.json")
QUIET = 45
RETRY = 600


def snapshot():
    sig = []
    for root, dirs, files in os.walk(LIB):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.startswith("."):
                continue
            p = os.path.join(root, f)
            try:
                st = os.stat(p)
            except FileNotFoundError:
                continue
            sig.append((os.path.relpath(p, LIB), st.st_size, int(st.st_mtime)))
    return sorted(sig)


def status(**kw):
    kw["at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    tmp = STATUS + ".tmp"
    json.dump(kw, open(tmp, "w"))
    os.replace(tmp, STATUS)


def log(*a):
    print(datetime.now().strftime("%m-%d %H:%M:%S"), *a, flush=True)


def main():
    built = snapshot()
    status(state="idle", files=len(built))
    log(f"watching {LIB}, {len(built)} files")
    seen, changed_at = built, None
    try:
        last = json.load(open(os.path.expanduser("~/.cache/tvframe-progress.json"))).get("step")
    except (OSError, ValueError):
        last = None
    if built and not os.path.exists(f"{T}/site/data/manifest.json"):     # a new install: build what is there now
        built, changed_at = [], time.time() - QUIET
        log("nothing built yet: building now")
    elif built and last == "failed":                                     # the last build failed before a restart
        built, changed_at = [], time.time() - QUIET
        log("the last build failed: building again now")
    while True:
        time.sleep(15)
        now = snapshot()
        if now != seen:
            seen, changed_at = now, time.time()
            status(state="waiting", files=len(now))
            log(f"change seen, {len(now)} files; waiting for copies to finish")
            continue
        if changed_at and time.time() - changed_at >= QUIET and now != built:
            status(state="building", files=len(now))
            log("building")
            t = time.time()
            r = subprocess.run(["bash", f"{T}/tools/library_sync.sh"], capture_output=True, text=True)
            tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
            log(f"sync exit {r.returncode} in {time.time() - t:.0f} s:", " | ".join(tail))
            if r.returncode == 0:
                built = now
                status(state="idle", files=len(now), last="ok", took=round(time.time() - t))
                changed_at = None
            else:
                status(state="error", files=len(now), last=" | ".join(tail)[-300:])
                changed_at = time.time() + RETRY - QUIET          # try again in 10 minutes
        elif changed_at and now == built:
            changed_at = None
            status(state="idle", files=len(now))


if __name__ == "__main__":
    main()
