"""Folder schedules: a folder can show on the TV only between two dates that repeat every year
(site/data/schedule.json, set from the photo manager). Shared by server/photos_api.py and server/prod_build.py."""
import json, os
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

SCHEDULE = os.path.join(tvconf.ROOT, "site/data/schedule.json")
LA = ZoneInfo(tvconf.TZ)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Ready-made ranges the Schedule menu offers, as (name, from, to) in MM-DD.
PRESETS = [
    ("Holidays", "11-20", "01-06"),
    ("Thanksgiving", "11-15", "11-30"),
    ("Halloween", "10-01", "10-31"),
    ("Fall", "09-22", "11-20"),
    ("Winter", "12-01", "02-28"),
    ("Valentine's Day", "02-01", "02-14"),
    ("Spring", "03-20", "06-20"),
    ("Summer", "06-01", "08-31"),
    ("Fourth of July", "06-28", "07-05"),
]


def load():
    try:
        return json.load(open(SCHEDULE)).get("schedule", {})
    except Exception:
        return {}


def save(s):
    tmp = SCHEDULE + ".tmp"
    json.dump({"schedule": s}, open(tmp, "w"), indent=1)
    os.replace(tmp, SCHEDULE)


def today():
    return datetime.now(LA).date()


def doy(mmdd):
    """Day of a leap year (1..366) for MM-DD, so Feb 29 has a place."""
    m, d = (int(x) for x in mmdd.split("-"))
    return date(2000, m, d).timetuple().tm_yday


def in_range(frm, to, day):
    a, b, x = doy(frm), doy(to), doy(day.strftime("%m-%d"))
    return a <= x <= b if a <= b else x >= a or x <= b          # a range can wrap past New Year


def off_albums(day=None):
    """Folders whose schedule keeps them off the TV today."""
    day = day or today()
    return sorted(a for a, r in load().items() if not in_range(r["from"], r["to"], day))


def fmt(mmdd):
    m, d = (int(x) for x in mmdd.split("-"))
    return f"{MONTHS[m - 1]} {d}"


def describe(r, day=None):
    """'Nov 24 to Jan 2 · on the TV now' or '... · back on Nov 24'."""
    day = day or today()
    span = f"{fmt(r['from'])} to {fmt(r['to'])}"
    return span + (" · on the TV now" if in_range(r["from"], r["to"], day) else f" · back on {fmt(r['from'])}")


def from_photos(dates):
    """The shortest yearly window that holds most of a folder's photos (by month and day, any year), padded a few
    days, or None when they are spread over too much of the year to make a season."""
    days = sorted({doy(d[5:10]) for d in dates if d and len(d) >= 10 and d[4] == "-"})
    if len(days) < 2:
        return None
    need = max(2, -(-len(days) * 8 // 10))                       # 80%, rounded up
    best = None
    ext = days + [d + 366 for d in days]
    for i in range(len(days)):
        span = ext[i + need - 1] - ext[i]
        if best is None or span < best[0]:
            best = (span, ext[i], ext[i + need - 1])
    span, a, b = best
    if span > 120:
        return None
    pad = 3
    base = date(2000, 1, 1)
    f = base + timedelta(days=(a - 1 - pad) % 366)
    t = base + timedelta(days=(b - 1 + pad) % 366)
    return {"from": f.strftime("%m-%d"), "to": t.strftime("%m-%d")}


def suggestions(dates):
    """Ranges for the Schedule menu: one from the folder's own photos first, then presets that overlap the photo
    dates, then the rest, each as {name, from, to, label}."""
    out = []
    own = from_photos(dates)
    if own:
        out.append(dict(own, name="From these photos", label=f"{fmt(own['from'])} to {fmt(own['to'])}"))
    ds = [d for d in dates if d and len(d) >= 10]

    def hits(p):
        return sum(1 for d in ds if in_range(p[1], p[2], date(2000, int(d[5:7]), int(d[8:10]))))
    for name, f, t in sorted(PRESETS, key=lambda p: -hits(p)):
        out.append({"name": name, "from": f, "to": t, "label": f"{fmt(f)} to {fmt(t)}"})
    return out
