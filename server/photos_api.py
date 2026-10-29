"""TV Frame photos API (stdlib only), port 8153, LAN and tailnet. Backs the photo manager (site/index.html on 8150).

The library is library/ in the checkout: each folder is an album. tools/library_watch.py notices file changes there and
rebuilds what the TV shows (tools/library_sync.sh, a few minutes). Settings changes here (off, how often) only need
the playlist rebuilt (server/prod_build.py, about 15 s), which this API starts by itself.

  GET  /lib                          folders, their photos and videos with state and settings, build status
  GET  /albums                       folder names, for the iPhone Shortcut's folder picker
  GET  /rank?album=A                 the folder's photos ranked by the curation scores (site/data/scores.json)
  GET  /progress                     how far a library rebuild is, step by step, and what the GPU is doing
  GET  /all                          every photo and clip in every folder, with scores, similar-shot groups and clip scores
  POST /bulk    {action, ids, level} one change for many photos: hide, show, level, remove (to library-trash), or
                                     restore {prev} / untrash {moved} to undo one. Answers with what to send to undo it
  POST /keep    {id}                 "Use this one": the TV shows this photo from its group of similar shots
                                     (site/data/keep.json, honored by prod_build.py)
  POST /upload?album=A&name=F        raw file body, saved as library/A/F (a new name if F exists). With &sniff=1 (the
                                     Shortcut) a name without a usable extension gets one from the file's first bytes
  POST /hide    {id, hidden}         take a photo off the TV or put it back (site/data/hidden.json)
  POST /boost   {id, level}          how often it comes up: less, normal, more, most or fav (site/data/boost.json)
  POST /bulk    {action: "frame", ids, frame} how each photo fills the TV (site/data/framing.json): "whole", "fill" (around
                                     its focal point), "auto" or {box: [x0, y0, x1, y1]} (0..1 of the photo, made 16:9).
                                     Their 4K frames are made again (server/prepare.py --only), a minute or two
  POST /bulk    {action: "trim", ids, trim}  which part of each clip the TV plays (site/data/trim.json): trim is
                                     "whole", "auto" (the best 30 s, as scored) or {start, end} in seconds of the original.
                                     The changed clips are cut again (tools/real_videos.py, clip_loops.py), a few minutes
  GET  /full?id=I                    a small full-length copy of a clip for the trim editor (site/media/v/full), made the
                                     first time it is asked for: {state: "building", pct} until it is {state: "ready", url}
  POST /move    {album, file, to}    move a file to another folder (its settings follow it)
  POST /remove  {album, file}        move a file to library-trash/<date>/<album>/ (recoverable)
  POST /folder/new     {name}
  POST /folder/rename  {from, to}    keeps the photos' ids, so nothing is rebuilt (tools/library_ids.py)
  POST /folder/remove  {name}        moves the whole folder to library-trash
  POST /folder/schedule {name, from, to} | {name, always: true}
                                     show a folder only between two dates each year (tools/season.py); the playlist is
                                     rebuilt now and again by itself whenever a folder comes in or goes out of season
"""
import fcntl, json, os, re, shutil, subprocess, sys, threading, time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tvconf  # noqa: E402

HOME = tvconf.ROOT
sys.path.insert(0, os.path.join(HOME, "tools"))
import library_ids, season  # noqa: E402
import real_videos  # noqa: E402
os.environ.setdefault("TVFRAME_SET", "real")
sys.path.insert(0, os.path.join(HOME, "server"))
import prepare  # noqa: E402
import encoders

SITE = os.path.join(HOME, "site")
LIB = os.path.join(HOME, "library")
TRASH = os.path.join(HOME, "library-trash")
HIDDEN = os.path.join(SITE, "data/hidden.json")
BOOST = os.path.join(SITE, "data/boost.json")
FRAMING = os.path.join(SITE, "data/framing.json")    # photo id -> {fit: whole|fill} or {box}: how it fills the TV
INDEX = os.path.join(HOME, "server/cache-real/index.json")   # server/prepare.py: each photo's 4K frame, size, focal point
TRIM = os.path.join(SITE, "data/trim.json")    # clip id -> {whole} or {start, end}: the part the TV plays
FULL = os.path.join(SITE, "media/v/full")       # full-length 960 px copies for the trim editor
MIX = os.path.join(SITE, "data/mix.json")      # clips per 100 slides, the manager's Photos and clips slider
SCORES = os.path.join(SITE, "data/scores.json")
KEEP = os.path.join(SITE, "data/keep.json")           # id -> {at}: your pick in a group of similar shots
LOOK = os.path.join(HOME, "real/clip_look.json")      # tools/clip_look.py: how each clip was shot and how its colour reads
CLIPS = os.path.join(HOME, "real/clip_scores.json")   # written by tools/clip_score.py (may not exist yet)
ITEMS = os.path.join(HOME, "real/items.json")
PROD = os.path.join(HOME, "server/cache-real/prod/prod.json")
WATCH = os.path.expanduser("~/.cache/tvframe-library.json")
LOCK = os.path.expanduser("~/.cache/tvframe-library.lock")      # shared with tools/library_sync.sh
LEVELS = ("less", "normal", "more", "most", "fav")                       # multipliers live in prod_build.py
EXTS = {".jpg", ".jpeg", ".heic", ".png", ".mp4", ".mov"}
lock = threading.Lock()


def load(path, empty):
    try:
        return json.load(open(path))
    except Exception:
        return empty


def write(d, path):
    tmp = path + ".tmp"
    json.dump(d, open(tmp, "w"), indent=1)
    os.replace(tmp, path)


def now():
    return datetime.now(ZoneInfo(tvconf.TZ)).isoformat(timespec="seconds")


def clean_name(s, n=120):
    """A file or folder name that stays inside the library: no slashes, no leading dot, printable."""
    s = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "", str(s or "")).strip().lstrip(".")
    return s[:n]


def folders():
    try:
        return sorted((d for d in os.listdir(LIB) if os.path.isdir(os.path.join(LIB, d)) and not d.startswith(".")), key=str.lower)
    except OSError:
        return []


def lib_path(album, file=None):
    """library/<album>[/<file>] if both names are clean and it exists, else None."""
    a = clean_name(album, 60)
    if not a or a != album or not os.path.isdir(os.path.join(LIB, a)):
        return None
    if file is None:
        return os.path.join(LIB, a)
    f = clean_name(file)
    p = os.path.join(LIB, a, f)
    return p if f and f == file and os.path.isfile(p) else None


def free_name(d, name):
    stem, ext = os.path.splitext(name)
    dst, i = os.path.join(d, name), 2
    while os.path.exists(dst):
        dst = os.path.join(d, f"{stem} ({i}){ext}")
        i += 1
    return dst


def to_trash(src):
    dst_dir = os.path.join(TRASH, now()[:10], os.path.relpath(os.path.dirname(src), LIB))
    os.makedirs(dst_dir, exist_ok=True)
    dst = free_name(dst_dir, os.path.basename(src))
    shutil.move(src, dst)
    return os.path.relpath(dst, HOME)


def sniff(b):
    """A file extension from a file's first bytes: what iPhone shares send without a name."""
    if b[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if b[4:8] == b"ftyp":
        brand = b[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"hevc", b"heim", b"heis"):
            return ".heic"
        return ".mov" if brand == b"qt  " else ".mp4"
    return ""


# ---------- the TV playlist: rebuilt by itself a few seconds after a settings change ----------
apply_state = {"running": False, "error": None}
apply_timer = None


def pending():
    """True when off or how-often settings differ from what the TV's playlist was built with."""
    prod = load(PROD, {})
    want = set(load(HIDDEN, {"hidden": {}}).get("hidden", {}))
    return (want != set(prod.get("hidden", [])) or load(BOOST, {"boost": {}}).get("boost", {}) != prod.get("boost", {})
            or season.off_albums() != prod.get("off_season", [])
            or sorted(load(KEEP, {"keep": {}}).get("keep", {})) != prod.get("keep", [])
            or sorted(load(KEEP, {"keep": {}}).get("also", {})) != prod.get("also", []))


def season_loop():
    """Every 10 minutes: a folder came into or went out of season (local midnight), so rebuild the playlist."""
    while True:
        time.sleep(600)
        try:
            if season.off_albums() != load(PROD, {}).get("off_season", []) and not apply_state["running"]:
                apply_soon()
        except Exception:
            pass


force_build = False      # hidden.json "shown" changed: prod.json does not record it, so build once even if nothing else is pending


def apply_soon(force=False):
    global apply_timer, force_build
    with lock:
        force_build = force_build or force
        if apply_timer:
            apply_timer.cancel()
        apply_timer = threading.Timer(3.0, apply_now)
        apply_timer.start()


def apply_now():
    global force_build
    apply_state["running"] = True
    try:
        # wait for a library rebuild in progress; its playlist step already reads the newest settings
        with open(LOCK, "w") as fd:
            fcntl.flock(fd, fcntl.LOCK_EX)
            for _ in range(3):
                forced, force_build = force_build, False
                if not forced and not pending():
                    break
                r = subprocess.run([sys.executable, os.path.join(HOME, "server/prod_build.py")], capture_output=True,
                                   text=True, timeout=900, cwd=os.path.join(HOME, "server"), env={**os.environ, "TVFRAME_SET": "real"})
                apply_state["error"] = (r.stderr or r.stdout)[-300:] if r.returncode else None
                if r.returncode:
                    break
    except Exception as e:
        apply_state["error"] = str(e)[-300:]
    finally:
        apply_state["running"] = False


# rebuilds after a trim or framing change, one kind at a time under the library lock: the ids being redone and the
# commands that redo them (a change made while one runs starts one more pass)
jobs = {k: {"ids": set(), "gen": 0, "timer": None, "running": False, "error": None} for k in ("clips", "photos")}


def job_cmds(kind, ids):
    if kind == "clips":     # cut the clips again, their loops, the site's copies, the playlist
        return [["tools/real_videos.py"], ["tools/clip_loops.py"], ["tools/sync_data.py"], ["server/prod_build.py"]]
    return [["server/prepare.py", "--only", ",".join(sorted(ids))], ["server/prod_build.py"]]    # the photos' 4K frames, the playlist


def redo_soon(kind, ids):
    j = jobs[kind]
    with lock:
        j["ids"] |= set(ids)
        j["gen"] += 1
        if j["timer"]:
            j["timer"].cancel()
        j["timer"] = threading.Timer(3.0, redo_now, (kind,))
        j["timer"].start()


def redo_now(kind):
    j = jobs[kind]
    with lock:
        if j["running"]:
            return                                       # the pass in progress loops once more for it
        j["running"] = True
    try:
        while True:
            with lock:
                gen, ids = j["gen"], set(j["ids"])
            with open(LOCK, "w") as fd:
                fcntl.flock(fd, fcntl.LOCK_EX)
                env = {**os.environ, "TVFRAME_SET": "real"}
                for cmd in job_cmds(kind, ids):
                    r = subprocess.run([sys.executable, os.path.join(HOME, cmd[0]), *cmd[1:]], capture_output=True, text=True,
                                       timeout=7200, cwd=HOME if cmd[0].startswith("tools") else os.path.join(HOME, "server"), env=env)
                    j["error"] = (r.stderr or r.stdout)[-300:] if r.returncode else None
                    if r.returncode:
                        break
            with lock:
                if j["gen"] == gen:
                    j["ids"] -= ids
                    break
    except Exception as e:
        j["error"] = str(e)[-300:]
    finally:
        with lock:
            j["running"] = False
            if j["error"]:
                j["ids"] = set()


def unfinished():
    """A restart forgets the rebuilds it had queued: find trims and framing the TV's copies do not follow yet."""
    trims, dur = load(TRIM, {}).get("trim", {}), {i["id"]: i.get("duration") for i in load(ITEMS, [])}
    real_videos.TRIMS = trims
    clips = []
    for pid in trims:
        r = load(os.path.join(real_videos.V, "recipe", f"{pid}.json"), {})
        if dur.get(pid) and r:
            ss, tt = real_videos.excerpt(pid, float(dur[pid]))
            if abs(r.get("ss", -1) - ss) > .05 or abs(r.get("tt", -1) - tt) > .05:
                clips.append(pid)
    fits, frames = load(FRAMING, {}).get("frame", {}), os.path.join(os.path.dirname(INDEX), "frames")
    want = {pid: prepare.fit_sig({"fit": f}) for pid, f in fits.items()}
    for f in os.listdir(frames) if os.path.isdir(frames) else []:
        if f.endswith(".fit"):
            want.setdefault(f[:-4], "{}")
    photos = []
    for pid, sig in want.items():
        try:
            have = open(os.path.join(frames, f"{pid}.fit")).read()
        except OSError:
            have = "{}"
        if have != sig:
            photos.append(pid)
    if clips:
        redo_soon("clips", clips)
    if photos:
        redo_soon("photos", photos)


def tv_frame(e, fit):
    """How a photo sits on the TV for the manager: its 4K source size, focal point, the box of it that fills the
    screen (0..1 of the photo, None when it is shown whole) now and by the automatic rule, and your framing."""
    if not e or not e.get("native"):
        return None
    w, h = e["native"]
    def box(p):
        if not prepare.fills(w, h, p):
            return None
        b = prepare.fill_box(w, h, p, 16 / 9)
        return [round(b[0] / w, 4), round(b[1] / h, 4), round(b[2] / w, 4), round(b[3] / h, 4)]
    p = {"focal": e.get("focal") or [.5, .5]}
    return {"w": w, "h": h, "focal": p["focal"], "box": box({**p, "fit": fit}), "auto": box(p), "fit": fit}


def cut_of(pid):
    """The part of the original the TV's copy of a clip was cut from (tools/real_videos.py recipe), as start, length."""
    r = load(os.path.join(real_videos.V, "recipe", f"{pid}.json"), {})
    return {"start": r["ss"], "len": r["tt"]} if "ss" in r and "tt" in r else None


def web_url(pid):
    f = f"{SITE}/media/v/web/{pid}.mp4"
    try:
        return f"media/v/web/{pid}.mp4?v={int(os.path.getmtime(f))}"     # a new cut must not come from the browser's cache
    except OSError:
        return f"media/v/web/{pid}.mp4"


full_jobs = {}       # clip id -> {"pct"} while its full-length copy is being made


def full_copy(pid):
    """A small full-length H.264 copy of the original for the trim editor, same color step and rotation as the TV's."""
    it = next((i for i in load(ITEMS, []) if i["id"] == pid and i["kind"] == "video"), None)
    if not it:
        return {"state": "missing"}
    src = os.path.join(HOME, it["rel"])
    dst, rec = os.path.join(FULL, pid + ".mp4"), os.path.join(FULL, pid + ".json")
    try:
        want = {"src_size": os.path.getsize(src), "recipe": 1}
    except OSError:
        return {"state": "missing"}
    if load(rec, None) == want and os.path.exists(dst):
        return {"state": "ready", "url": f"media/v/full/{pid}.mp4?v={int(os.path.getmtime(dst))}"}
    with lock:
        if pid in full_jobs:
            return {"state": "building", "pct": full_jobs[pid].get("pct", 0)}
        full_jobs[pid] = {"pct": 0}

    def run():
        try:
            os.makedirs(FULL, exist_ok=True)
            info = real_videos.probe(src, pid)
            tmp = dst + ".tmp.mp4"
            p = subprocess.Popen(encoders.ffmpeg(["nice", "-n", "10", "ffmpeg", "-v", "error", "-y", "-progress", "pipe:1", "-nostats", *real_videos.rotate_args(pid), "-i", src,
                                  "-an", "-vf", f"scale=960:540:force_original_aspect_ratio=decrease:flags=bicubic,{real_videos.color(info)}format=yuv420p",
                                  "-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "28", "-b:v", "0", "-maxrate", "6M", "-bufsize", "12M",
                                  "-g", "15", "-movflags", "+faststart", tmp]), stdout=subprocess.PIPE, text=True)
            for line in p.stdout:                       # out_time_us=..., for the editor's progress
                if line.startswith("out_time_us=") and info["duration"]:
                    try:
                        full_jobs[pid]["pct"] = min(99, int(int(line.split("=")[1]) / 1e4 / info["duration"]))
                    except ValueError:
                        pass
            if p.wait() == 0:
                os.replace(tmp, dst)
                write(want, rec)
        except Exception:
            pass
        finally:
            full_jobs.pop(pid, None)
    threading.Thread(target=run, daemon=True).start()
    return {"state": "building", "pct": 0}


def state():
    w = load(WATCH, {})
    pr = load(PROG, {})
    if pr.get("step") not in (None, "done", "failed") and alive(pr.get("pid")):
        return {"state": "building", "text": "Adding changes to the TV"}
    if w.get("state") in ("waiting", "building"):
        return {"state": "building", "text": "Adding changes to the TV" if w["state"] == "building" else "Waiting for copying to finish"}
    if w.get("state") == "error":
        return {"state": "error", "text": "The last update failed (real/library.log on the server)"}
    if apply_state["running"] or (apply_timer and apply_timer.is_alive()):
        return {"state": "building", "text": "Updating the TV"}
    if apply_state["error"]:
        return {"state": "error", "text": "The last update failed"}
    return {"state": "idle", "text": "TV is up to date"}


def listing():
    items = {i["rel"]: i for i in load(ITEMS, [])}
    hidden = load(HIDDEN, {"hidden": {}}).get("hidden", {})
    boost = load(BOOST, {"boost": {}}).get("boost", {})
    prod = load(PROD, {})
    on_tv = {i["id"] for i in prod.get("items", []) if i.get("rep", True)} | {v["id"].split(":", 1)[-1] for v in prod.get("videos", [])}
    out = []
    sched = season.load()
    off_season = set(season.off_albums())
    for a in folders():
        files = []
        names = sorted(os.listdir(os.path.join(LIB, a)), key=str.lower)
        stills = {os.path.splitext(f)[0].lower() for f in names if os.path.splitext(f)[1].lower() in (".jpg", ".jpeg", ".heic", ".png")}
        for f in names:
            stem, ext = os.path.splitext(f)
            if f.startswith(".") or ext.lower() not in EXTS:
                continue
            it = items.get(f"library/{a}/{f}")
            if not it and ext.lower() in (".mp4", ".mov") and stem.lower() in stills:
                continue                                  # a Live Photo's motion part: the still stands for it
            pid = it["id"] if it else None
            video = (it["kind"] == "video") if it else os.path.splitext(f)[1].lower() in (".mp4", ".mov")
            thumb = big = None
            if pid and not video and os.path.exists(f"{SITE}/media/p/{pid}-720.webp"):
                thumb, big = f"media/p/{pid}-720.webp", f"media/p/{pid}-1920.webp"
            elif pid and video and os.path.exists(f"{SITE}/media/v/posters/{pid}.jpg"):
                thumb, big = f"media/v/posters/{pid}.jpg", f"media/v/web/{pid}.mp4"
            st = ("new" if not thumb else "off" if pid in hidden else "on" if pid in on_tv else "similar")
            if st == "similar" and a in off_season:
                st = "season"                             # its folder is out of season, so it waits for its dates
            files.append({"file": f, "id": pid, "video": video, "thumb": thumb, "big": big, "state": st,
                          "level": boost.get(pid, "normal") if pid else "normal",
                          "place": (it or {}).get("place") if (it or {}).get("place_src") == "gps" else None,
                          "date": (it or {}).get("date")})
        r = sched.get(a)
        out.append({"name": a, "files": files, "schedule": dict(r, text=season.describe(r), on=a not in season.off_albums()) if r else None,
                    "suggest": season.suggestions([x["date"] for x in files if x.get("date")])})
    return {"folders": out, "status": state(), "mix": load(MIX, {}).get("clips", 11)}


# ---------- similar shots, clip scores and the all-photos page ----------
def group_picks(scores, hidden, keep, also=()):
    """id -> (group, the photo the TV shows from that group) for photos that have similar shots. Same rule as
    prod_build.py: your newest pick (keep.json) if it is not off, else the scorer's pick, else the best one left on.
    Photos you marked "show this one too" (keep.json "also") show on their own and are never the group's pick."""
    groups = {}
    for pid, s in scores.items():
        if s.get("cluster_size", 1) > 1 and s.get("cluster_id") is not None:
            groups.setdefault(s["cluster_id"], []).append(pid)
    out = {}
    for cid, ids in groups.items():
        live = [i for i in ids if i not in hidden and i not in also]
        kept = sorted((str(keep[i].get("at", "")), i) for i in live if i in keep)
        if kept:
            pick = kept[-1][1]
        else:
            r = [i for i in live if scores[i].get("is_rep")]
            pick = r[0] if r else None if any(i in also and i not in hidden for i in ids) else \
                max(live, key=lambda i: scores[i].get("quality", 0)) if live else None
        for i in ids:
            out[i] = (cid, pick)
    return out


def clip_scores():
    return load(CLIPS, {}).get("clips", {}) or {}


def shown_clips(hidden_doc):
    """Clips you put back on the TV although clip_score.py kept them off: hidden.json "shown", and any clip with a
    how often level (prod_build.py shown_ids and its clip loop read the same)."""
    return set(hidden_doc.get("shown", {})) | set(load(BOOST, {"boost": {}}).get("boost", {}))


def clip_info(c, pid=None, n=48):
    """A clip's scores for the manager, with its frame curve thinned to about n points, and how it looks (clip_look.py)."""
    if not isinstance(c, dict):
        return None
    lk = (load(LOOK, {}).get("clips") or {}).get(pid) or {} if pid else {}
    fr = [f for f in (c.get("frames") or []) if isinstance(f, (list, tuple)) and len(f) >= 2]
    if len(fr) > n:
        step = len(fr) / n
        fr = [fr[int(k * step)] for k in range(n)] + [fr[-1]]
    keys = ("score", "looks", "motion", "steady", "sharp", "exposure", "faces", "duration", "best", "why", "auto_hide")
    return dict({k: c.get(k) for k in keys}, frames=[[round(float(t), 2), round(float(a), 3)] for t, a, *_ in fr],
                **{k: lk.get(k) for k in ("shot", "settings", "look", "color", "contrast")})


def all_items():
    """Every photo and clip, flat, for the all-photos page: what listing() shows plus the scores behind it."""
    lst = listing()
    items = {i["id"]: i for i in load(ITEMS, [])}
    scores = load(SCORES, {}).get("photos", {})
    hidden = load(HIDDEN, {"hidden": {}}).get("hidden", {})
    kd = load(KEEP, {"keep": {}})
    keep, also = kd.get("keep", {}), kd.get("also", {})
    show = shown_clips(load(HIDDEN, {}))
    clips = clip_scores()
    picks = group_picks(scores, hidden, keep, also)
    out = []
    for f in lst["folders"]:
        for x in f["files"]:
            pid = x["id"]
            it, s = items.get(pid) or {}, scores.get(pid) if pid else None
            o = {"id": pid, "album": f["name"], "file": x["file"], "video": x["video"], "thumb": x["thumb"], "big": x["big"],
                 "state": x["state"], "level": x["level"], "place": x["place"], "date": x["date"], "time": it.get("time"),
                 "camera": it.get("camera")}
            if s:
                o.update(q=s.get("quality"), looks=s.get("aesthetic"), looks_n=s.get("aesthetic_norm"),
                         sharp=s.get("sharpness_score"), exp=s.get("exposure_score"), clean=s.get("noise_score"),
                         faces=(s.get("faces") or {}).get("count", 0), why=s.get("reason"), w=s.get("width"), h=s.get("height"))
                if pid in picks:
                    cid, pick = picks[pid]
                    o.update(group=cid, group_size=s.get("cluster_size"), pick=pick, scorer_pick=s.get("rep_id"), kept=pid in keep,
                             also=pid in also)
                    if o["state"] in ("on", "similar"):           # follows a new pick right away, before the playlist catches up
                        o["state"] = "on" if pick == pid or pid in also else "similar"
            if x["video"] and pid:
                c = clip_info(clips.get(pid), pid)
                if c:
                    o["clip"] = c
                    if c.get("score") is not None:
                        o["q"] = max(0.0, min(1.0, float(c["score"]) / 100))
                    o["why"] = c.get("why")
                    if c.get("auto_hide") and pid not in show and o["state"] not in ("off", "new"):
                        o["state"] = "auto"
                o["shown"] = pid in show
            out.append(o)
    return {"items": out, "albums": [f["name"] for f in lst["folders"]], "status": lst["status"],
            "clips_generated": load(CLIPS, {}).get("generated"), "scores_generated": load(SCORES, {}).get("generated")}


# ---------- ranking: the curation scores behind how often each photo comes up ----------
def ranking(album):
    scores = load(SCORES, {}).get("photos", {})
    items = [i for i in load(ITEMS, []) if album == "*" or i.get("album") == album]   # "*": every folder
    hidden = load(HIDDEN, {"hidden": {}}).get("hidden", {})
    boost = load(BOOST, {"boost": {}}).get("boost", {})
    prod = load(PROD, {})
    tv = {i["id"]: i for i in prod.get("items", [])}
    total_w = sum(i.get("weight", 0) for i in tv.values() if i.get("rep", True)) or 1
    allq = sorted((x.get("quality", 0) for x in scores.values()), reverse=True)
    cs, show = clip_scores(), shown_clips(load(HIDDEN, {}))
    kd = load(KEEP, {"keep": {}})
    keep, also = kd.get("keep", {}), kd.get("also", {})
    picks = group_picks(scores, hidden, keep, also)
    on_tv = {v["id"].split(":", 1)[-1] for v in prod.get("videos", [])}
    trims = load(TRIM, {}).get("trim", {})
    fits, built = load(FRAMING, {}).get("frame", {}), {p["id"]: p for p in load(INDEX, {}).get("photos", [])}
    rows, clips = [], []
    for it in items:
        pid = it["id"]
        base = {"id": pid, "file": it["file"], "album": it.get("album"), "date": it.get("date"), "camera": it.get("camera"),
                "place": it.get("place") if it.get("place_src") == "gps" else None,
                "level": boost.get(pid, "normal"), "off": pid in hidden}
        if it["kind"] == "video":
            c = clip_info(cs.get(pid), pid)
            clips.append(dict(base, thumb=f"media/v/posters/{pid}.jpg" if os.path.exists(f"{SITE}/media/v/posters/{pid}.jpg") else None,
                              big=web_url(pid), clip=c, shown=pid in show, on_tv=pid in on_tv,
                              src=it.get("duration"), cut=cut_of(pid), trim=trims.get(pid), recutting=pid in jobs["clips"]["ids"],
                              auto=bool(c and c.get("auto_hide") and pid not in show and pid not in hidden)))
            continue
        sc = scores.get(pid)
        thumb = f"media/p/{pid}-720.webp" if os.path.exists(f"{SITE}/media/p/{pid}-720.webp") else None
        base.update(fr=tv_frame(built.get(pid), {k: v for k, v in (fits.get(pid) or {}).items() if k != "at"} or None),
                    reframing=pid in jobs["photos"]["ids"])
        if not sc:
            rows.append(dict(base, thumb=thumb, scored=False)); continue
        rep = picks[pid][1] if pid in picks and picks[pid][1] else sc.get("rep_id") or pid
        t = tv.get(pid)
        rows.append(dict(base, thumb=thumb, scored=True,
            quality=sc.get("quality"), weight=sc.get("weight"), aesthetic=sc.get("aesthetic"), aesthetic_norm=sc.get("aesthetic_norm"),
            sharpness=sc.get("sharpness_score"), exposure=sc.get("exposure_score"), noise=sc.get("noise_score"),
            luma=sc.get("mean_luma"), highlights=sc.get("clip_highlights"), shadows=sc.get("clip_shadows"),
            faces=(sc.get("faces") or {}).get("count", 0), faces_sharp=(sc.get("faces") or {}).get("all_in_focus"),
            w=sc.get("width"), h=sc.get("height"), orientation=sc.get("orientation"), palette=sc.get("palette"),
            cluster_size=sc.get("cluster_size", 1), is_rep=rep == pid or pid in also, also=pid in also, rep_id=rep, kept=pid in keep,
            rep_file=next((i["file"] for i in items if i["id"] == rep), None) if rep != pid else None,
            reason=sc.get("reason"), layout=(t or {}).get("layout"),
            share=round((t or {}).get("weight", 0) / total_w * 100, 3) if t and t.get("rep", True) and pid not in hidden else 0,
            overall=next((n + 1 for n, q in enumerate(allq) if q <= sc.get("quality", 0)), len(allq))))
    rows.sort(key=lambda r: (not r["scored"], -(r.get("quality") or 0)))
    for n, r in enumerate(rows):
        r["rank"] = n + 1 if r["scored"] else None
    return {"album": album, "photos": rows, "clips": clips, "scored_total": len(allq), "recut_error": jobs["clips"]["error"], "reframe_error": jobs["photos"]["error"],
            "models": load(SCORES, {}).get("models", {}), "generated": load(SCORES, {}).get("generated")}


# ---------- rebuild progress (tools/library_sync.sh writes the step, the steps' own files give the detail) ----------
PROG = os.path.expanduser("~/.cache/tvframe-progress.json")
PROG_LAST = os.path.expanduser("~/.cache/tvframe-progress-last.json")
STEP = os.path.expanduser("~/.cache/tvframe-step.json")
STEPS = [  # key, label, uses the GPU, default share of the time (replaced by the last run's real times)
    ("meta", "Reading photo info and places", False, 8),
    ("media", "Making web and 4K images", False, 20),
    ("clips", "Watching videos for the best parts", True, 4),
    ("videos", "Encoding videos", True, 25),
    ("scoring", "Scoring photos", True, 6),
    ("frames", "Preparing TV frames", True, 18),
    ("maps", "Drawing maps", False, 3),
    ("playlist", "Building the TV playlist", True, 20),
]


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def gpu():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,encoder.stats.sessionCount",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=4).stdout.strip().split("\n")[0]
        n, u, mu, mt, enc = [x.strip() for x in out.split(",")]
        return {"name": n, "util": int(u), "mem_used": int(mu), "mem_total": int(mt), "encoders": int(enc) if enc.isdigit() else 0}
    except Exception:
        return None


def newer(d, since, suffix):
    try:
        return sum(1 for e in os.scandir(d) if e.name.endswith(suffix) and e.stat().st_mtime >= since)
    except OSError:
        return 0


def step_detail(k, since):
    """(done, total, unit) for the running step, when it can be counted."""
    items = load(ITEMS, [])
    photos = sum(1 for i in items if i["kind"] == "photo")
    if k == "media":
        return newer(f"{SITE}/media/p", since, "-bg.webp"), photos, "photos"
    if k == "videos":
        s = load(STEP, {})
        if s.get("step") == "videos":
            return s.get("done", 0), s.get("total", 0), "videos"
    s = load(STEP, {})                                  # any step can report its own count (real_videos.py does)
    if k not in ("videos",) and s.get("step") == k and s.get("total"):
        return s.get("done", 0), s.get("total", 0), s.get("unit") or ("steps" if k == "clips" else "items")
    if k == "frames":
        try:
            n = sum(1 for line in open(os.path.join(HOME, "real/prepare.log")) if " jpg " in line)
        except OSError:
            n = 0
        return n, photos, "photos"
    return None


GUESS = {"clips": ("Scoring video clips", True), "clip_score": ("Scoring video clips", True),
         "clip_scores": ("Scoring video clips", True), "clipscore": ("Scoring video clips", True)}


def steps_for(p, last):
    """The known steps plus any step library_sync.sh runs that this file does not know yet (put where it ran)."""
    runs = [[d.get("k") for d in p.get("done", [])] + [p.get("step")], [d.get("k") for d in load(PROG_LAST, {}).get("done", [])]]
    out = list(STEPS)
    for k in runs[0] + runs[1]:
        if not k or k in ("done", "failed") or k in [s[0] for s in out]:
            continue
        label, g = GUESS.get(k, (str(k).replace("_", " ").replace("-", " ").strip().capitalize() or "Another step", False))
        run = next(r for r in runs if k in r)                    # place it after what ran before it in the same run
        prev = run[:run.index(k)]
        at = max([i + 1 for i, s in enumerate(out) if s[0] in prev] or [len(out) - 1])
        out.insert(at, (k, label, g, last.get(k, 5)))
    return out


def progress():
    p = load(PROG, {})
    last = {d["k"]: d["s"] for d in load(PROG_LAST, {}).get("done", [])}
    running = bool(p) and p.get("step") not in ("done", "failed") and alive(p.get("pid"))
    STEPS = steps_for(p, last)
    shares = {k: max(1, last.get(k, d)) for k, _, _, d in STEPS} if last else {k: d for k, _, _, d in STEPS}
    total = sum(shares.values())
    done = {d["k"]: d["s"] for d in p.get("done", [])}
    out, pct = [], 0.0
    now_t = time.time()
    for k, label, uses_gpu, _ in STEPS:
        st = {"key": k, "label": label, "gpu": uses_gpu}
        if k in done:
            st.update(state="done", secs=done[k]); pct += shares[k]
        elif running and p.get("step") == k:
            el = now_t - p.get("step_started", now_t)
            det = step_detail(k, p.get("step_started", now_t))
            if det and det[1]:
                frac = min(1.0, det[0] / det[1]); st.update(count=det[0], of=det[1], unit=det[2])
            else:
                frac = min(0.95, el / last[k]) if k in last and last[k] else None
            st.update(state="running", secs=round(el))
            if frac is not None:
                st["frac"] = round(frac, 3)
            pct += shares[k] * (frac if frac is not None else 0.3)
        else:
            st["state"] = "failed" if p.get("step") == "failed" and not running and k not in done and not any(
                s["state"] == "failed" for s in out) else "waiting"
        out.append(st)
    state = "running" if running else ("failed" if p.get("step") == "failed" else "done" if p.get("step") == "done" else "idle")
    return {"state": state, "percent": round(pct / total * 100) if running else (100 if state == "done" else None),
            "started": p.get("started"), "elapsed": round(now_t - p["started"]) if p.get("started") and running else None,
            "steps": out, "gpu": gpu(),
            "gpu_done": [s["label"] for s in out if s["gpu"] and s["state"] == "done"]}


class H(BaseHTTPRequestHandler):
    server_version = "tvframe-photos/2"

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % a))

    def cors(self):
        o = self.headers.get("Origin") or ""
        # the manager on 8150, by any host name it is reached by on the LAN or tailnet
        self.send_header("Access-Control-Allow-Origin", o if re.fullmatch(r"http://[A-Za-z0-9.\-]+:8150", o) else f"http://{tvconf.HOST}:8150")
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def fail(self, msg, code=400):
        return self.send({"ok": False, "error": msg}, code)

    def do_OPTIONS(self):
        self.send_response(204); self.cors(); self.send_header("Content-Length", "0"); self.end_headers()

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/lib":
            return self.send(listing())
        if self.path.split("?", 1)[0] == "/albums":
            return self.send(folders())
        if self.path.split("?", 1)[0] == "/progress":
            return self.send(progress())
        if self.path.split("?", 1)[0] == "/all":
            return self.send(all_items())
        if self.path.split("?", 1)[0] == "/full":
            q = dict(x.split("=", 1) for x in self.path.split("?", 1)[-1].split("&") if "=" in x)
            return self.send(full_copy(unquote(q.get("id", ""))))
        if self.path.split("?", 1)[0] == "/rank":
            q = dict(x.split("=", 1) for x in self.path.split("?", 1)[-1].split("&") if "=" in x)
            album = unquote(q.get("album", ""))
            if album != "*" and album not in folders():
                return self.fail("no such folder", 404)
            return self.send(ranking(album))
        return self.fail("not found", 404)

    def upload(self):
        q = dict(x.split("=", 1) for x in self.path.split("?", 1)[-1].split("&") if "=" in x)
        album = clean_name(unquote(q.get("album", "")), 60)
        name = clean_name(unquote(q.get("name", "")))
        stem, ext = os.path.splitext(name)
        head = b""
        if q.get("sniff") and ext.lower() not in EXTS:
            head = self.rfile.read(32)
            ext = sniff(head)
            stem = stem or datetime.now().strftime("Photo %Y-%m-%d %H.%M.%S")
            name = stem + ext
        if not album:
            return self.fail("Pick a folder first.")
        if ext.lower() not in EXTS or not stem:
            return self.fail("Only photos and videos (JPEG, HEIC, PNG, MP4, MOV).")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 8 << 30:
            return self.fail("empty or too big")
        d = os.path.join(LIB, album)
        os.makedirs(d, exist_ok=True)
        dst = free_name(d, name)
        tmp = os.path.join(d, "." + os.path.basename(dst) + ".part")     # dot file: the watcher ignores it
        left = n - len(head)
        with open(tmp, "wb") as f:
            f.write(head)
            while left:
                chunk = self.rfile.read(min(1 << 20, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            os.remove(tmp)
            return self.fail("upload cut off")
        os.replace(tmp, dst)
        return self.send({"ok": True, "album": album, "file": os.path.basename(dst)})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/upload":
            try:
                return self.upload()
            except Exception as e:
                return self.fail(str(e)[-200:], 500)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 4 << 20:                                # a bulk change can name every photo in the library
                raise ValueError
            b = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self.fail("bad json")
        try:
            return self.post(path, b)
        except Exception as e:
            return self.fail(str(e)[-200:], 500)

    def bulk(self, b, known):
        """One change for many photos, written in one go. Answers with exactly what to send back to undo it."""
        act = b.get("action")
        if act == "untrash":
            back, failed = [], 0
            for m in b.get("moved") or []:
                src = os.path.realpath(os.path.join(HOME, str(m.get("trash") or "")))
                dst = os.path.realpath(os.path.join(LIB, str(m.get("album") or ""), str(m.get("file") or "")))
                if (not src.startswith(os.path.realpath(TRASH) + os.sep) or not dst.startswith(os.path.realpath(LIB) + os.sep)
                        or not os.path.isfile(src) or os.path.exists(dst) or not lib_path(m.get("album"))):
                    failed += 1
                    continue
                shutil.move(src, dst)
                back.append(m)
            return self.send({"ok": True, "count": len(back), "failed": failed})
        if act == "remove":
            rel = {i["id"]: (i["album"], i["file"]) for i in load(ITEMS, [])}
            todo = [rel[i] for i in b.get("ids") or [] if i in rel] + [(f.get("album"), f.get("file")) for f in b.get("files") or []]
            moved, failed = [], 0
            for album, file in dict.fromkeys(todo):
                src = lib_path(album, file)
                if not src:
                    failed += 1
                    continue
                moved.append({"album": album, "file": file, "trash": to_trash(src)})
            return self.send({"ok": True, "count": len(moved), "failed": failed, "undo": {"action": "untrash", "moved": moved}})
        files = {"hidden": (HIDDEN, "hidden"), "level": (BOOST, "boost"), "keep": (KEEP, "keep"), "also": (KEEP, "also"),
                 "show": (HIDDEN, "shown"), "trim": (TRIM, "trim"), "frame": (FRAMING, "frame")}
        with lock:
            docs = {f: load(f, {}) for f, _ in files.values()}      # keep and also share keep.json: load it once
            had = {f: set(doc) for f, doc in docs.items()}
            d = {k: docs[f] for k, (f, _) in files.items()}
            data = {k: d[k].setdefault(files[k][1], {}) for k in files}
            prev, changed, force, ids = {}, set(), False, []

            def remember(pid, *keys):
                for k in keys:
                    prev.setdefault(pid, {}).setdefault(k, data[k].get(pid))

            def put(pid, k, v):
                remember(pid, k)
                if v is None:
                    if pid in data[k]:
                        data[k].pop(pid); changed.add(k)
                elif data[k].get(pid) != v:
                    data[k][pid] = v; changed.add(k)

            if act == "restore":
                for pid, was in (b.get("prev") or {}).items():
                    if pid in known and isinstance(was, dict):
                        for k, v in was.items():
                            if k in files:
                                put(pid, k, v)
            else:
                ids = [i for i in dict.fromkeys(b.get("ids") or []) if i in known]
                if not ids:
                    return self.fail("None of those are on the TV list yet. Try again in a few minutes.")
                if act == "hide":
                    for pid in ids:
                        if pid not in data["hidden"]:
                            put(pid, "hidden", {"at": now()})
                elif act == "show":
                    clips = clip_scores()
                    for pid in ids:
                        put(pid, "hidden", None)
                        if (clips.get(pid) or {}).get("auto_hide") and pid not in data["show"]:
                            put(pid, "show", {"at": now()})
                elif act in ("also", "unalso"):              # show this one too, next to its group's pick
                    for pid in ids:
                        put(pid, "also", {"at": now()} if act == "also" else None)
                        if act == "also":
                            put(pid, "hidden", None)
                elif act == "trim":                          # the part of each clip the TV plays
                    t, lens = b.get("trim"), {i["id"]: i.get("duration") for i in load(ITEMS, []) if i["kind"] == "video"}
                    if any(pid not in lens for pid in ids):
                        return self.fail("Only clips can be trimmed.")
                    for pid in ids:
                        if t == "auto":
                            put(pid, "trim", None)
                        elif t == "whole":
                            put(pid, "trim", {"whole": True, "at": now()})
                        elif isinstance(t, dict):
                            try:
                                st, en, dur = float(t["start"]), float(t["end"]), float(lens[pid] or 0)
                            except (KeyError, TypeError, ValueError):
                                return self.fail("A trim needs a start and an end.")
                            st, en = max(0.0, st), min(dur, en)
                            if en - st < 2:
                                return self.fail("Keep at least 2 seconds of the clip.")
                            put(pid, "trim", {"whole": True, "at": now()} if st < 0.05 and en > dur - 0.05
                                else {"start": round(st, 2), "end": round(en, 2), "at": now()})
                        else:
                            return self.fail("bad trim")
                elif act == "frame":                         # how each photo fills the TV
                    f, photos = b.get("frame"), {i["id"] for i in load(ITEMS, []) if i["kind"] == "photo"}
                    if any(pid not in photos for pid in ids):
                        return self.fail("Only photos can be framed.")
                    if f in ("whole", "fill"):
                        v = {"fit": f}
                    elif isinstance(f, dict) and isinstance(f.get("box"), list) and len(f["box"]) == 4:
                        try:
                            x0, y0, x1, y1 = (min(1.0, max(0.0, float(x))) for x in f["box"])
                        except (TypeError, ValueError):
                            return self.fail("bad box")
                        if x1 - x0 < .05 or y1 - y0 < .05:
                            return self.fail("That part is too small.")
                        v = {"box": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]}
                    elif f == "auto":
                        v = None
                    else:
                        return self.fail("bad frame")
                    for pid in ids:
                        old = {k: x for k, x in (data["frame"].get(pid) or {}).items() if k != "at"}
                        if v is None or old != v:
                            put(pid, "frame", v if v is None else {**v, "at": now()})
                elif act == "level":
                    level = b.get("level")
                    if level not in LEVELS:
                        return self.fail("bad level")
                    for pid in ids:
                        put(pid, "level", None if level == "normal" else level)
                else:
                    return self.fail("unknown action")
            for f in {files[k][0] for k in changed}:
                for key in [key for key, v in docs[f].items() if v == {} and key not in had[f]]:
                    docs[f].pop(key)                         # don't leave empty sections behind
                write(docs[f], f)
            force = "show" in changed
        if "frame" in changed:
            redo_soon("photos", [pid for pid, was in prev.items() if "frame" in was])
        if "trim" in changed:
            redo_soon("clips", [pid for pid, was in prev.items() if "trim" in was])
        if changed - {"trim", "frame"}:
            apply_soon(force)
        return self.send({"ok": True, "count": len(ids) or len(prev), "changed": len(changed) > 0, "undo": {"action": "restore", "prev": prev}})

    def keep(self, b):
        """Use this one: the TV shows this photo from its group of similar shots; the others stay in the library."""
        pid = str(b.get("id") or "")
        scores = load(SCORES, {}).get("photos", {})
        s = scores.get(pid)
        if not s or s.get("cluster_size", 1) < 2 or s.get("cluster_id") is None:
            return self.fail("That photo has no similar shots.")
        group = [i for i, x in scores.items() if x.get("cluster_id") == s["cluster_id"] and x.get("cluster_size", 1) > 1]
        with lock:
            k, h = load(KEEP, {"keep": {}}), load(HIDDEN, {"hidden": {}})
            k.setdefault("keep", {}); h.setdefault("hidden", {})
            prev = {i: {"keep": k["keep"].get(i)} for i in group}
            prev[pid]["hidden"] = h["hidden"].get(pid)
            prev[pid]["also"] = k.get("also", {}).get(pid)
            k.get("also", {}).pop(pid, None)                 # the group's pick, not an extra any more
            for i in group:
                k["keep"].pop(i, None)
            k["keep"][pid] = {"at": now()}
            write(k, KEEP)
            if pid in h["hidden"]:                       # picking a photo that was off puts it back on
                h["hidden"].pop(pid)
                write(h, HIDDEN)
        apply_soon()
        return self.send({"ok": True, "group": group, "undo": {"action": "restore", "prev": prev}})

    def post(self, path, b):
        if path == "/mix":
            try:
                n = max(0, min(100, int(b.get("clips"))))
            except (TypeError, ValueError):
                return self.fail("Clips should be a number from 0 to 100.")
            write({"clips": n}, MIX)
            apply_soon(force=True)
            return self.send({"ok": True, "clips": n})
        known = {i["id"] for i in load(ITEMS, [])}
        if path == "/bulk":
            return self.bulk(b, known)
        if path == "/keep":
            return self.keep(b)
        if path in ("/hide", "/boost"):
            pid = str(b.get("id") or "")
            if pid not in known:
                return self.fail("That photo is still being added. Try again in a few minutes.")
            if path == "/hide":
                with lock:
                    d = load(HIDDEN, {"hidden": {}})
                    if b.get("hidden"):
                        d["hidden"][pid] = {"at": now()}
                    else:
                        d["hidden"].pop(pid, None)
                    write(d, HIDDEN)
            else:
                level = b.get("level")
                if level not in LEVELS:
                    return self.fail("bad level")
                with lock:
                    d = load(BOOST, {"boost": {}})
                    if level == "normal":
                        d["boost"].pop(pid, None)
                    else:
                        d["boost"][pid] = level
                    write(d, BOOST)
            apply_soon()
            return self.send({"ok": True})
        if path == "/remove":
            src = lib_path(b.get("album"), b.get("file"))
            if not src:
                return self.fail("That file is not in the library any more.")
            return self.send({"ok": True, "trash": to_trash(src)})
        if path == "/move":
            src, to = lib_path(b.get("album"), b.get("file")), lib_path(b.get("to"))
            if not src or not to:
                return self.fail("That file or folder is not there any more.")
            if os.path.dirname(src) == to:
                return self.send({"ok": True})
            dst = free_name(to, os.path.basename(src))
            m = library_ids.load()
            it = next((i for i in load(ITEMS, []) if i["rel"] == f"library/{b['album']}/{b['file']}"), None)
            old_id = it["id"] if it else library_ids.photo_id(b["album"], b["file"], m)
            new_id = library_ids.photo_id(b["to"], os.path.basename(dst), m)
            library_ids.save(m)
            shutil.move(src, dst)
            with lock:                                    # off and how often follow the photo to its new id
                for f, key in ((HIDDEN, "hidden"), (BOOST, "boost"), (TRIM, "trim")):
                    d = load(f, {key: {}})
                    if old_id in d.setdefault(key, {}):
                        d[key][new_id] = d[key].pop(old_id)
                        write(d, f)
            return self.send({"ok": True, "file": os.path.basename(dst)})
        if path == "/folder/new":
            name = clean_name(b.get("name"), 60)
            if not name:
                return self.fail("Type a name.")
            if os.path.exists(os.path.join(LIB, name)):
                return self.fail("There is already a folder with that name.")
            os.makedirs(os.path.join(LIB, name))
            return self.send({"ok": True, "name": name})
        if path == "/folder/rename":
            src, to = lib_path(b.get("from")), clean_name(b.get("to"), 60)
            if not src or not to:
                return self.fail("Type a name.")
            if os.path.exists(os.path.join(LIB, to)):
                return self.fail("There is already a folder with that name.")
            library_ids.rename(b["from"], to)
            os.rename(src, os.path.join(LIB, to))
            with lock:                                    # its schedule follows the folder
                s = season.load()
                if b["from"] in s:
                    s[to] = s.pop(b["from"])
                    season.save(s)
            return self.send({"ok": True, "name": to})
        if path == "/folder/remove":
            src = lib_path(b.get("name"))
            if not src:
                return self.fail("That folder is not there any more.")
            dst = os.path.join(TRASH, now()[:10])
            os.makedirs(dst, exist_ok=True)
            final = free_name(dst, os.path.basename(src))
            shutil.move(src, final)
            with lock:
                s = season.load()
                if s.pop(b["name"], None):
                    season.save(s)
            return self.send({"ok": True, "trash": os.path.relpath(final, HOME)})
        if path == "/folder/schedule":
            if not lib_path(b.get("name")):
                return self.fail("That folder is not there any more.")
            with lock:
                s = season.load()
                if b.get("always"):
                    s.pop(b["name"], None)
                else:
                    f, t = str(b.get("from") or ""), str(b.get("to") or "")
                    try:
                        season.doy(f), season.doy(t)
                    except Exception:
                        return self.fail("Pick a start and an end date.")
                    if not re.fullmatch(r"\d\d-\d\d", f) or not re.fullmatch(r"\d\d-\d\d", t) or f == t:
                        return self.fail("Pick a start and an end date.")
                    s[b["name"]] = {"from": f, "to": t}
                season.save(s)
            apply_soon()
            r = s.get(b["name"])
            return self.send({"ok": True, "text": season.describe(r) if r else "Always"})
        return self.fail("not found", 404)


if __name__ == "__main__":
    threading.Timer(10.0, unfinished).start()
    threading.Thread(target=season_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8153), H).serve_forever()
