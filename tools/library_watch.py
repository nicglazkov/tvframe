#!/usr/bin/env python3
"""Watch ~/tvframe/library and run tools/library_sync.sh after it changes.

Checks every 15 s. A failed build is retried every 10 minutes. A change (a file added, removed, renamed or rewritten) starts a 45 s quiet period so a big
copy from Finder finishes first; every further change restarts it. Runs as the systemd --user unit
tvframe-library. Status for the photos page: ~/.cache/tvframe-library.json."""
import json, os, subprocess, time
from datetime import datetime

T = os.path.expanduser("~/tvframe")
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
