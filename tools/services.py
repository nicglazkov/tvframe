#!/usr/bin/env python3
"""Everything TV Frame runs, in one process tree: the main process of the TV Frame image (compose.yaml).

  serve.py         :8151  the playlist and media for the TV
  photos_api.py    :8153  the photo manager's API
  bridge.py        :8154  what the TV shows, live data and Home Assistant
  library_watch.py        rebuilds the TV photos when library/ changes
  every 20 minutes        tools/fetch_live.py (weather for Glance), when LIVE_LAT and LIVE_LON are set
  every 5 minutes         tools/watchdog.py (puts the TV's settings back after an update), when TVFRAME_TV is set

A server that runs these as systemd --user units instead (deploy/systemd/) does not need this file.
Each one's output goes to the log the systemd units use and, with a name in front, to this process's output
(docker compose logs).
"""
import os, signal, subprocess, sys, threading, time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402

T = tvconf.ROOT
PY = sys.executable
SERVICES = [  # name, command, working folder, log file
    ("photos", [PY, "serve.py", "--port", "8151"], "server", "server/serve.log"),
    ("api", [PY, "photos_api.py"], "server", "server/tune_api.log"),
    ("bridge", [PY, "bridge.py", "--port", "8154"], "server", "server/bridge.log"),
    ("library", [PY, "tools/library_watch.py"], ".", "real/library.log"),
]
EVERY = [  # name, command, seconds, the settings it needs (read again each time: ./tvframe tv adds the TV later)
    ("live", [PY, "tools/fetch_live.py"], 20 * 60, ("LIVE_LAT", "LIVE_LON"), "real/live.log"),
    ("watchdog", [PY, "tools/watchdog.py"], 5 * 60, ("TVFRAME_TV",), "real/watchdog.log"),
]
lock = threading.Lock()
stopping = False


def say(name, line):
    with lock:
        sys.stdout.write(f"{name:8} {line}")
        sys.stdout.flush()


def pump(name, proc, logfile):
    with open(os.path.join(T, logfile), "a") as f:
        for line in proc.stdout:
            f.write(line)
            f.flush()
            say(name, line)


def keep(name, cmd, cwd, logfile):
    """Run a service; start it again 5 s after it stops."""
    while not stopping:
        p = subprocess.Popen(cmd, cwd=os.path.join(T, cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        procs[name] = p
        pump(name, p, logfile)
        p.wait()
        if not stopping:
            say(name, f"stopped with {p.returncode}, starting again in 5 s\n")
            time.sleep(5)


def every(name, cmd, secs, needs, logfile):
    time.sleep(60)                                   # let the services come up first
    while not stopping:
        conf = tvconf._load()
        if all(conf.get(k) for k in needs):
            p = subprocess.Popen(cmd, cwd=T, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            pump(name, p, logfile)
            p.wait()
        time.sleep(secs)


def stop(*_):
    global stopping
    stopping = True
    for p in procs.values():
        if p.poll() is None:
            p.terminate()
    sys.exit(0)


procs = {}
if __name__ == "__main__":
    os.umask(0o022)                                  # some Docker setups start containers with 0000
    for d in ("real", "server", "library", "site/data", os.path.expanduser("~/.cache")):
        os.makedirs(os.path.join(T, d), exist_ok=True)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"{datetime.now():%m-%d %H:%M:%S} TV Frame in {T}, encoder {__import__('encoders').backend()}", flush=True)
    for s in SERVICES:
        threading.Thread(target=keep, args=s, daemon=True).start()
    for e in EVERY:
        threading.Thread(target=every, args=e, daemon=True).start()
    while True:
        time.sleep(3600)
