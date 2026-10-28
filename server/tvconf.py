"""Settings that differ from one home to the next: addresses, Home Assistant, the weather location.

They live outside the repo in ~/.config/tvframe/config.env (KEY=value lines; see config.example.env), so the code
carries nothing about the house it runs in. An environment variable of the same name wins over the file, and
TVFRAME_CONFIG points at a different file. Everything is optional: a missing value turns its feature off.
"""
import json, os

PATH = os.path.expanduser(os.environ.get("TVFRAME_CONFIG", "~/.config/tvframe/config.env"))


def _load():
    d = {}
    try:
        for line in open(PATH):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) > 1 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                d[k.strip()] = v
    except OSError:
        pass
    d.update({k: v for k, v in os.environ.items() if k in d or k.startswith(("TVFRAME_", "HA_", "LIVE_"))})
    return d


_C = _load()


def get(key, default=""):
    return _C.get(key) or default


def get_json(key, default):
    try:
        return json.loads(_C[key]) if _C.get(key) else default
    except ValueError:
        return default


def path(key, default):
    return os.path.expanduser(get(key, default))


# this server, as the TV and phones on the LAN reach it (photo manager, map and thumbnail links)
HOST = get("TVFRAME_HOST", "127.0.0.1")
# the TV, for adb: host:port
TV = get("TVFRAME_TV")
# local time for schedules, logs and the glance screen
TZ = get("TVFRAME_TZ", "UTC")
# Home Assistant (optional): base URL and a long-lived access token in a 0600 file
HA_URL = get("HA_URL").rstrip("/")
HA_TOKEN_FILE = path("HA_TOKEN_FILE", "~/.config/tvframe/ha_token")
# Glance weather, air quality and sun times (optional)
LIVE_LAT, LIVE_LON, LIVE_PLACE = get("LIVE_LAT"), get("LIVE_LON"), get("LIVE_PLACE")


def ha_token():
    try:
        return open(HA_TOKEN_FILE).read().strip()
    except OSError:
        return ""
