"""Which photo set the frame server uses: "real" (your photos) or "unsplash" (the earlier test set).

The choice lives in server/photoset (one word). Each set has its own cache folder, so switching
back is just writing the other word and restarting serve.py:
  unsplash -> server/cache/        photos from ~/tvframe/curate/out/scores.json, clips from photos/videos
  real     -> server/cache-real/   photos from ~/tvframe/site/data/scores.json + site/media/p/<id>-4k.jpg,
                                   captions from site/data/captions.json, clips from photos/videos-real
TVFRAME_SET=real|unsplash in the environment overrides the file.
"""
import os

HOME = os.path.expanduser("~")
ROOT = os.path.join(HOME, "tvframe")
HERE = os.path.dirname(os.path.abspath(__file__))
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def name():
    s = os.environ.get("TVFRAME_SET")
    if not s:
        try:
            s = open(os.path.join(HERE, "photoset")).read().strip()
        except FileNotFoundError:
            s = "unsplash"
    return "real" if s == "real" else "unsplash"


def cache():
    return os.path.join(HERE, "cache-real" if name() == "real" else "cache")


def videos():
    return os.path.join(ROOT, "photos", "videos-real" if name() == "real" else "videos")


def fmt_date(iso):
    try:
        y, m, d = (int(x) for x in iso.split("-"))
        return f"{MONTHS[m - 1]} {d}, {y}"
    except Exception:
        return ""


def fmt_clock(hhmm):
    try:
        h, m = (int(x) for x in hhmm.split(":")[:2])
        return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"
    except Exception:
        return ""


def caption(c):
    """Same wording as the site: place, date and time, camera. Only what the file recorded."""
    if not c:
        return None
    parts = [c.get("place") or c.get("title") or "", fmt_date(c.get("date") or ""),
             fmt_clock(c.get("time") or ""), c.get("camera") or ""]
    return " · ".join(x for x in parts if x) or None
