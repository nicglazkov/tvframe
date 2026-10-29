#!/usr/bin/env python3
"""Build the TV playlist (prod.json) for Glance.

Photos use the 4K frames in cache-real/frames. Tall photos also come as pairs and threes, and a wide photo
now and then leads a trio from the same trip: these spreads are composed here at 3840x2160 and encoded as
one-frame HEVC stills, so the TV only decodes and crossfades. All text is drawn by the app on the UI plane,
so the frames carry no text; this file tells the app what to write and where (4K pixels).

  python3 prod_build.py [--force]     writes cache-real/prod/ and cache-real/prod/prod.json
"""
import argparse, colorsys, hashlib, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageFilter, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TVFRAME_SET", "real")
import photoset, prepare  # noqa: E402
import shade  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import season  # noqa: E402
import tvconf  # noqa: E402

ROOT = photoset.ROOT
SITE = os.path.join(ROOT, "site")
CACHE = photoset.cache()
OUT = os.path.join(CACHE, "prod")
W, H = 3840, 2160


def load():
    caps = json.load(open(os.path.join(SITE, "data", "captions.json")))
    media = {m["id"]: m for m in json.load(open(os.path.join(SITE, "data", "media.json")))}
    scores = json.load(open(os.path.join(SITE, "data", "scores.json")))["photos"]
    idx = {p["id"]: p for p in json.load(open(os.path.join(CACHE, "index.json")))["photos"]}
    chapters = {c["key"]: c for c in caps["chapters"]}
    hidden = hidden_ids() | out_of_season()
    photos = []
    for pid, m in media.items():
        if pid not in idx or pid in hidden:
            continue
        c = caps["photos"].get(pid, {})
        s = scores.get(pid, {})
        fp = s.get("focal_point") or [0.5, 0.45]
        if isinstance(fp, dict):
            fp = [fp.get("x", .5), fp.get("y", .45)]
        photos.append(dict(
            id=pid, w=m["w4k"], h=m["h4k"], aspect=m["w4k"] / m["h4k"], avg=m.get("avg") or "#333333",
            edge=m.get("edge"), place=c.get("place") if c.get("place_src") != "album" else "",   # an album name is not a place
            date=c.get("date") or "",
            time=c.get("time") or "", camera=c.get("camera") or "", chapter=c.get("chapter"),
            weight=float(s.get("weight", 50.0)), cluster=s.get("cluster_id"), rep=s.get("is_rep", True) is not False,
            focal=fp, layout=idx[pid].get("layout", "cover"), idx=idx[pid]))
    keep_override(photos)
    promote(photos)
    boost = boosts()
    for p in photos:
        p["weight"] = round(p["weight"] * LEVELS.get(boost.get(p["id"]), 1.0), 2)
    return photos, chapters


def album_of():
    try:
        return {i["id"]: i.get("album") for i in json.load(open(os.path.join(ROOT, "real", "items.json")))}
    except Exception:
        return {}


def out_of_season():
    """Photos and videos in folders whose schedule keeps them off the TV today (site/data/schedule.json)."""
    off = set(season.off_albums())
    return {i for i, a in album_of().items() if a in off} if off else set()


def hidden_ids():
    """Photos you took off the TV on photos.html (site/data/hidden.json, written by tune_api.py)."""
    try:
        return set(json.load(open(os.path.join(SITE, "data", "hidden.json"))).get("hidden", {}))
    except (OSError, ValueError):
        return set()


LEVELS = {"less": 0.4, "normal": 1.0, "more": 2.5, "most": 5.0, "fav": 8.0}   # fav = your favorites, the most of all


def boosts():
    """How often you want a photo to come up, set on photos.html (site/data/boost.json): id -> less, more or most."""
    try:
        return json.load(open(os.path.join(SITE, "data", "boost.json"))).get("boost", {})
    except (OSError, ValueError):
        return {}


def keeps():
    """Near-duplicate picks you made in the manager with Use this one (site/data/keep.json, photos_api.py /keep): id -> {at}."""
    try:
        return json.load(open(os.path.join(SITE, "data", "keep.json"))).get("keep", {})
    except (OSError, ValueError):
        return {}


def also_ids():
    """Similar shots you want shown too, on their own, next to the group's pick (keep.json "also")."""
    try:
        return set(json.load(open(os.path.join(SITE, "data", "keep.json"))).get("also", {}))
    except (OSError, ValueError):
        return set()


def keep_override(photos):
    """your pick becomes its group's photo at full weight and the scorer's pick steps back (the newest pick wins).
    A pick that is off is not in photos, so promote() then falls back to the best one left, as before.
    A photo marked "show this one too" shows at full weight as if it had no similar shots."""
    keep, also = keeps(), also_ids()
    for p in photos:
        if p["id"] in also and not p["rep"]:
            p["rep"], p["weight"], p["also"] = True, round(p["weight"] / 0.05, 2), True
    if not keep:
        return
    groups = {}
    for p in photos:
        if p["cluster"] is not None:
            groups.setdefault(p["cluster"], []).append(p)
    for ps in groups.values():
        kept = sorted((str(keep[p["id"]].get("at", "")), p["id"]) for p in ps if p["id"] in keep)
        if not kept:
            continue
        pick = kept[-1][1]
        for p in ps:
            if p["id"] == pick and not p["rep"]:
                p["rep"], p["weight"] = True, round(p["weight"] / 0.05, 2)
            elif p["id"] != pick and p["rep"] and not p.get("also"):
                p["rep"], p["weight"] = False, round(p["weight"] * 0.05, 2)


def clip_scores():
    """tools/clip_score.py: per clip score 0-100, best window, auto_hide for junk (real/clip_scores.json)."""
    try:
        return json.load(open(os.path.join(ROOT, "real", "clip_scores.json"))).get("clips", {})
    except (OSError, ValueError):
        return {}


def shown_ids():
    """Clips you put on the TV although clip_score.py kept them off (hidden.json "shown"; a how often level counts too)."""
    try:
        return set(json.load(open(os.path.join(SITE, "data", "hidden.json"))).get("shown", {}))
    except (OSError, ValueError):
        return set()


def clip_weight(cs, level):
    """How often a clip comes up, on the photo scale (curate.py: 100 * quality ** 1.3) from its clip score,
    times the same how often level as photos. A clip not scored yet gets the photo default, 50."""
    w = 100 * (float(cs["score"]) / 100) ** 1.3 if cs.get("score") is not None else 50.0
    return round(max(w, 0.5) * LEVELS.get(level, 1.0), 2)


def promote(photos):
    """A near-duplicate group whose pick was hidden falls back to its best remaining photo, at full weight."""
    groups = {}
    for p in photos:
        if p["cluster"] is not None:
            groups.setdefault(p["cluster"], []).append(p)
    for ps in groups.values():
        if not any(p["rep"] for p in ps):
            best = max(ps, key=lambda p: p["weight"])
            best["rep"], best["weight"] = True, round(best["weight"] / 0.05, 2)


def when(p):
    return " · ".join(x for x in [photoset.fmt_date(p["date"]), photoset.fmt_clock(p["time"])] if x)


def exif_lines():
    """Per photo: how it was taken, for the TV's details panel (tools/real_exif.py), e.g. ['123 mm · f/2.8 · 1/160 s · ISO 200', '33 MP · Lightroom']."""
    global _EXIF
    if _EXIF is None:
        try:
            _EXIF = {k: v.get("lines", []) for k, v in json.load(open(os.path.join(ROOT, "real", "exif.json"))).get("photos", {}).items()}
        except (OSError, ValueError):
            _EXIF = {}
    return _EXIF


_EXIF = None


def parts(p):
    return {"place": p["place"], "when": when(p), "camera": p["camera"], "more": exif_lines().get(p["id"], [])}


def mmss(s):
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


CLIP_SHORT_S = 15.0


def clip_show_s(f):
    """How long a clip stays on the TV: it plays twice, three times when it is 15 s or shorter, and the app always lets
    the last play finish. Same rule as the app's clipShowMs (FrameView.kt)."""
    try:
        n = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f],
                                 capture_output=True, text=True, timeout=30).stdout.strip())
    except Exception:
        return None
    return round(n * (3 if n <= CLIP_SHORT_S else 2), 2) if n > 0 else None


def clip_more(v, src_len):
    """Extra lines for a clip's details panel: the original's length when the TV shows an excerpt, slow motion (the settings line already has size and frame rate)."""
    src = v.get("source") or {}
    a = []
    if src_len and src_len > float(v.get("duration") or 0) + 1:
        a.append(f"From a {mmss(src_len)} clip")
    if src.get("slowmo"):
        a.append("Slow motion")
    return [" · ".join(a)] if a else []


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def cdist(a, b):
    x, y = rgb(a["avg"]), rgb(b["avg"])
    return sum((i - j) ** 2 for i, j in zip(x, y))


def fit_cover(img, w, h, focal):
    box = prepare.cover_box(img.width, img.height, focal, w / h)
    return img.resize((w, h), Image.LANCZOS, box=box, reducing_gap=3.0)


def shadowed(bg, cells, blur=35, dy=24, alpha=72):
    """Soft drop shadow under each cell (CSS: 0 24px 70px rgba(0,0,0,.28))."""
    sh = Image.new("L", (W, H), 0)
    for (x, y, w, h) in cells:
        sh.paste(alpha, (x, y + dy, x + w, y + h + dy))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    bg.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), sh)
    return bg


def src(p):
    return Image.open(os.path.join(SITE, "media", "p", f"{p['id']}-4k.jpg")).convert("RGB")


def cams(ps):
    out = []
    for p in ps:
        if p["camera"] and p["camera"] not in out:
            out.append(p["camera"])
    return " and ".join(out)


def shared_caption(ps):
    """Two caption lines for a glance spread: the places, then the day and camera."""
    line = shared_line(ps)
    head, _, sub = line.partition(" · ")
    return head, sub


def shared_line(ps):
    """One caption line for a glance spread: shared place and day once, otherwise each place."""
    places = []
    for p in ps:
        if p["place"] and p["place"] not in places:
            places.append(p["place"])
    places = places[:2]
    tails = {x.rsplit(", ", 1)[-1] for x in places if ", " in x}
    if len(places) == 2 and len(tails) == 1 and all(", " in x for x in places):
        tail = tails.pop()   # "Telegraph Hill and Pier 39 Marina, San Francisco"
        places = [places[0].rsplit(", ", 1)[0], places[1].rsplit(", ", 1)[0] + ", " + tail]
    days = sorted({p["date"] for p in ps if p["date"]})
    when_ = photoset.fmt_date(days[0]) if len(days) == 1 else (
        f"{photoset.fmt_date(days[0])} to {photoset.fmt_date(days[-1])}" if days else "")
    return " · ".join(x for x in [" and ".join(places), when_, cams(ps)] if x)


def glance_pair(a, b):
    hh = G_BOT - G_TOP
    w1, w2 = round(hh * max(a["aspect"], PAIR_MIN)), round(hh * max(b["aspect"], PAIR_MIN))
    x0 = round((W - (w1 + w2 + G_GUT)) / 2)
    ia, ib = src(a), src(b)
    cells = [(x0, G_TOP, w1, hh), (x0 + w1 + G_GUT, G_TOP, w2, hh)]
    bg = shadowed(prepare.backdrop(ia, W, H), cells)
    bg.paste(fit_cover(ia, w1, hh, a["focal"]), (x0, G_TOP))
    bg.paste(fit_cover(ib, w2, hh, b["focal"]), (x0 + w1 + G_GUT, G_TOP))
    return bg, cells


def glance_trio(a, b, c):
    hh = G_BOT - G_TOP
    gut = 48
    half = (hh - gut) // 2
    bigW = round(hh * min(a["aspect"], 1.6))
    sideW = round(half * min(max(b["aspect"], c["aspect"]), 1.6))
    x0 = round((W - (bigW + gut + sideW)) / 2)
    cells = [(x0, G_TOP, bigW, hh), (x0 + bigW + gut, G_TOP, sideW, half), (x0 + bigW + gut, G_TOP + half + gut, sideW, half)]
    ia = src(a)
    bg = shadowed(prepare.backdrop(ia, W, H), cells)
    for (x, y, w, h), p, im in zip(cells, [a, b, c], [ia, src(b), src(c)]):
        bg.paste(fit_cover(im, w, h, p["focal"]), (x, y))
    return bg, cells


def glance_tall3(a, b, c):
    """Three tall photos side by side, same height, filling most of the width."""
    hh = G_BOT - G_TOP
    ws = [round(hh * max(p["aspect"], TALL_MIN)) for p in (a, b, c)]
    x = round((W - (sum(ws) + 2 * TALL_GUT)) / 2)
    cells = []
    for w in ws:
        cells.append((x, G_TOP, w, hh))
        x += w + TALL_GUT
    ia = src(a)
    bg = shadowed(prepare.backdrop(ia, W, H), cells)
    for (x, y, w, h), p, im in zip(cells, [a, b, c], [ia, src(b), src(c)]):
        bg.paste(fit_cover(im, w, h, p["focal"]), (x, y))
    return bg, cells


def column_labels(ps, cells):
    """Captions over each column of a spread whose photos are from different places, so every photo says where it
    is: the place on the first line, the day on the second, left-aligned with the column (4K x and width). A column
    with two stacked photos (the trio's right side) names the top one, then the bottom one. None when every photo
    has the same place, which keeps the one shared caption."""
    if len({p["place"] for p in ps}) < 2:
        return None
    cols = {}
    for p, (x, y, w, h) in zip(ps, cells):
        cols.setdefault(x, []).append((y, w, p))
    out = []
    for x in sorted(cols):
        col = sorted(cols[x], key=lambda t: t[0])
        w = col[0][1]
        if len(col) == 1:
            p = col[0][2]
            head, sub = (p["place"], photoset.fmt_date(p["date"])) if p["place"] else (photoset.fmt_date(p["date"]), "")
        else:
            name = lambda p: p["place"] or photoset.fmt_date(p["date"]) or "No place"
            head, sub = f"Top: {name(col[0][2])}", f"Bottom: {name(col[1][2])}"
        out.append(dict(x=x, w=w, head=head, sub=sub))
    return out


def photo_rect(p):
    """Where a photo sits on screen (4K x, y, w, h): the whole screen when it fills it, else centred on its backdrop
    with the 1.2% gap (prepare.py). The TV's details panel lines up with it."""
    if p["layout"] != "mat":
        return [0, 0, W, H]
    gap = round(H * 0.012)
    s = min((W - 2 * gap) / p["w"], (H - 2 * gap) / p["h"])
    w, h = round(p["w"] * s), round(p["h"] * s)
    return [round((W - w) / 2), round((H - h) / 2), w, h]


_ITEMS = {}


def clip_rect(vid):
    """Where a clip's picture sits (4K x, y, w, h): tall clips are drawn on their backdrop like a tall photo
    (tools/real_videos.py), wide ones fill the screen. Uses the source's shown size, after rotation."""
    try:
        if not _ITEMS:
            _ITEMS.update((i["id"], i) for i in json.load(open(os.path.join(ROOT, "real", "items.json"))))
        it = _ITEMS[vid]
        import subprocess
        j = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-print_format", "json",
                                                "-show_streams", os.path.join(ROOT, it["rel"])], timeout=20))
        s = j["streams"][0]
        rot = next((int(d.get("rotation", 0)) for d in s.get("side_data_list", []) if "rotation" in d), 0)
        w, h = (s["height"], s["width"]) if abs(rot) % 180 == 90 else (s["width"], s["height"])
        return photo_rect({"layout": "cover" if w / h >= 1.55 else "mat", "w": w, "h": h})
    except Exception:
        return [0, 0, W, H]


MAPS = os.path.join(SITE, "media", "maps")


def map_url(pid):
    """A small dark map around where the photo was taken (tools/maps.py), if it has one."""
    f = os.path.join(MAPS, pid + ".png")
    return f"http://{tvconf.HOST}:8150/media/maps/{pid}.png?v={ver(f)}" if os.path.exists(f) else None


def side_band(p):
    """Width for a caption in the side band of a tall photo on its backdrop (site: photoSlide), else None."""
    if p["layout"] != "mat":
        return None
    gap = round(H * 0.012)
    s = min((W - 2 * gap) / p["w"], (H - 2 * gap) / p["h"])
    left = round((W - p["w"] * s) / 2)
    return max(700, left - 340) if left >= 1000 else None


def ver(path):
    st = os.stat(path)
    return hashlib.sha1(f"{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:10]


def encode_spread(sid, img, force):
    os.makedirs(os.path.join(OUT, "frames"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "hevc"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "hevc8"), exist_ok=True)
    jpg = os.path.join(OUT, "frames", sid + ".jpg")
    h10 = os.path.join(OUT, "hevc", sid + ".mp4")
    h8 = os.path.join(OUT, "hevc8", sid + ".mp4")
    if force or not (os.path.exists(h10) and os.path.exists(h8)):
        img.save(jpg, quality=95, subsampling=0)
        prepare.encode(jpg, h10, "hevc")
        prepare.encode(jpg, h8, "hevc8")
    return jpg, h10, h8


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    photos, chapters = load()
    shown = [p for p in photos if p["rep"]]
    portrait = lambda p: p["aspect"] < 0.95

    # pairs: for every tall photo, its partner by the site's rule (same day, then same chapter, then color)
    pairs, seen = [], set()
    for p in sorted([p for p in shown if portrait(p)], key=lambda p: -p["weight"]):
        pool = [q for q in shown if q is not p and portrait(q)]
        same_day = [q for q in pool if p["date"] and q["date"] == p["date"]]
        same_ch = [q for q in pool if p["chapter"] and q["chapter"] == p["chapter"]]
        if same_day:
            q = max(same_day, key=lambda q: q["weight"])
        elif same_ch:
            q = max(same_ch, key=lambda q: q["weight"])
        elif pool:
            q = min(pool, key=lambda q: cdist(p, q))
        else:
            continue
        key = tuple(sorted([p["id"], q["id"]]))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((p, q))

    # trios: a wide lead photo plus the two best other wide photos from its chapter
    trios, tseen = [], set()
    for p in sorted([p for p in shown if not portrait(p) and p["aspect"] > 1.3], key=lambda p: -p["weight"]):
        rel = sorted([q for q in shown if q is not p and q["chapter"] == p["chapter"] and not portrait(q)
                      and q["aspect"] > 1.2], key=lambda q: -q["weight"])
        rel = [q for q in rel if (p["cluster"] is None or q["cluster"] != p["cluster"])][:2]
        if len(rel) < 2:
            continue
        key = tuple(sorted([p["id"]] + [q["id"] for q in rel]))
        if key in tseen:
            continue
        tseen.add(key)
        trios.append((p, rel[0], rel[1]))

    # three tall photos across: every tall photo leads one, with the two closest by the pair rule
    talls, lseen = [], set()
    for p in sorted([p for p in shown if portrait(p)], key=lambda p: -p["weight"]):
        pool = [q for q in shown if q is not p and portrait(q)]
        rank = lambda q: (0 if p["date"] and q["date"] == p["date"] else 1 if p["chapter"] and q["chapter"] == p["chapter"] else 2,
                          cdist(p, q), -q["weight"])
        rel = sorted(pool, key=rank)[:2]
        if len(rel) < 2:
            continue
        key = tuple(sorted([p["id"]] + [q["id"] for q in rel]))
        if key in lseen:
            continue
        lseen.add(key)
        talls.append((p, rel[0], rel[1]))

    jobs = []
    for p, q in pairs:
        jobs.append(("gpair-" + p["id"] + "+" + q["id"], "gpair", [p, q]))
    for p, q, r in talls:
        jobs.append(("gtall-" + p["id"] + "+" + q["id"] + "+" + r["id"], "gtall", [p, q, r]))
    for p, q, r in trios:
        jobs.append(("gtrio-" + p["id"] + "+" + q["id"] + "+" + r["id"], "gtrio", [p, q, r]))
    draw = {"gpair": glance_pair, "gtall": glance_tall3, "gtrio": glance_trio}

    def run(job):
        sid, kind, ps = job
        img, cells = draw[kind](*ps)
        jpg, h10, h8 = encode_spread(sid, img, a.force)
        head, sub = shared_caption(ps)
        return dict(id=sid, kind=kind, members=[p["id"] for p in ps], line=shared_line(ps), head=head, sub=sub,
                    labels=column_labels(ps, cells), cells=[list(c) for c in cells],
                    weight=round(sum(p["weight"] for p in ps) / len(ps), 2),
                    hevc=f"/media/prod/hevc/{sid}.mp4?v={ver(h10)}", hevc8=f"/media/prod/hevc8/{sid}.mp4?v={ver(h8)}",
                    url=f"/media/prod/frames/{sid}.jpg?v={ver(jpg)}",
                    bytes=os.path.getsize(h10))

    with ThreadPoolExecutor(4) as ex:
        spreads = list(ex.map(run, jobs))

    # caption shade (shade.py, r14): bright frames get a graduated ND baked into a copy so the white text reads;
    # the original frames stay as they are and are used whenever a photo needs no shade
    scache = shade.load_cache(OUT)

    def shade_one(p):
        try:
            return p["id"], shade.for_item(p["id"], os.path.join(CACHE, "frames", p["id"] + ".jpg"), OUT, prepare.encode, scache)
        except Exception as e:
            print("shade failed", p["id"], e, file=sys.stderr)
            return p["id"], {}

    with ThreadPoolExecutor(4) as ex:
        shades = dict(ex.map(shade_one, photos))
    shade.save_cache(OUT, scache)

    items = []
    for p in photos:
        i = p["idx"]
        f10 = os.path.join(CACHE, "hevc", p["id"] + ".mp4")
        f8 = os.path.join(CACHE, "hevc8", p["id"] + ".mp4")
        fj = os.path.join(CACHE, "frames", p["id"] + ".jpg")
        sh = shades.get(p["id"]) or {}
        urls = dict(url=f"/media/frames/{p['id']}.jpg?v={ver(fj)}", hevc=f"/media/hevc/{p['id']}.mp4?v={ver(f10)}",
                    hevc8=f"/media/hevc8/{p['id']}.mp4?v={ver(f8)}" if os.path.exists(f8) else None)
        if sh.get("hevc"):
            f10 = sh["hevc"]
            urls = dict(url=f"/media/prod/frames/{p['id']}.shade.jpg?v={ver(sh['frame'])}",
                        hevc=f"/media/prod/hevc/{p['id']}.shade.mp4?v={ver(sh['hevc'])}",
                        hevc8=f"/media/prod/hevc8/{p['id']}.shade.mp4?v={ver(sh['hevc8'])}")
        items.append(dict(
            id=p["id"], layout=p["layout"], portrait=portrait(p), weight=p["weight"], cluster=p["cluster"], rep=p["rep"],
            caption=parts(p), band=side_band(p), date=p["date"], chapter=p["chapter"], rect=photo_rect(p), map=map_url(p["id"]),
            **urls, shade=sh.get("shade", 0.0),
            thumb=f"http://{tvconf.HOST}:8150/media/p/{p['id']}-720.webp", bytes=os.path.getsize(f10)))

    vids, auto_off = [], []
    vj = os.path.join(SITE, "data", "videos.json")
    off = hidden_ids() | out_of_season()
    cscores, cboost, shown = clip_scores(), boosts(), shown_ids()
    try:   # the settings line under a clip's caption: '4K · 60 fps · Dolby Vision' (tools/clip_look.py)
        looks = json.load(open(os.path.join(tvconf.ROOT, "real/clip_look.json"))).get("clips", {})
    except (OSError, ValueError):
        looks = {}
    try:   # each clip's original length, to say when the TV shows an excerpt
        src_len = {it["id"]: it.get("duration") for it in json.load(open(os.path.join(ROOT, "real", "items.json"))) if it.get("kind") == "video"}
    except (OSError, ValueError):
        src_len = {}
    for v in json.load(open(vj)).get("videos", []):
        if v["id"] in off:
            continue
        cs = cscores.get(v["id"]) or {}
        if cs.get("auto_hide") and v["id"] not in shown and v["id"] not in cboost:
            auto_off.append(v["id"])      # junk per tools/clip_score.py (very short, black, very shaky, nothing happens)
            continue
        cw = clip_weight(cs, cboost.get(v["id"]))
        f = os.path.join(SITE, v["hevc"])
        name = os.path.basename(f)
        if os.path.exists(os.path.join(photoset.videos(), "tv", name)):
            v8 = os.path.join(CACHE, "video8", name)
            # the seamless loop and its first frame (tools/clip_loops.py), when built; the plain clip otherwise
            lp, lj = os.path.join(photoset.videos(), "tvloop", name), os.path.join(photoset.videos(), "tvloop", name[:-4] + ".jpg")
            loop = os.path.exists(lp) and os.path.exists(lj)
            vids.append(dict(id="video:" + v["id"], url=f"/media/videoloop/{name}?v={ver(lp)}" if loop else f"/media/videos/{name}",
                             poster=f"/media/videoloop/{name[:-4]}.jpg?v={ver(lj)}" if loop else None,
                             map=map_url(v["id"]), rect=clip_rect(v["id"]),
                             url8=f"/media/video8/{name}" if os.path.exists(v8) else None,
                             weight=cw, score=cs.get("score"),
                             duration=float(v.get("duration") or 15),
                             show_s=clip_show_s(lp if loop else os.path.join(photoset.videos(), "tv", name)),
                             caption={"place": v.get("place", ""), "when": " · ".join(x for x in [photoset.fmt_date(v.get("date", "")), photoset.fmt_clock(v.get("time", ""))] if x), "camera": v.get("camera", ""),
                                      "settings": (looks.get(v["id"]) or {}).get("settings", ""),
                                      "more": clip_more(v, src_len.get(v["id"]))}))

    try:
        tune = json.load(open(os.path.join(SITE, "data", "tune.json"))).get("locked")
    except Exception:
        tune = None
    try:   # clips per 100 slides, the manager's Photos and clips slider (photos_api.py /mix)
        clip_share = max(0, min(100, int(json.load(open(os.path.join(SITE, "data", "mix.json"))).get("clips", 11))))
    except (OSError, ValueError, TypeError):
        clip_share = 11
    from datetime import datetime
    from zoneinfo import ZoneInfo
    out = dict(version=2, generated=datetime.now(ZoneInfo(tvconf.TZ)).isoformat(timespec="seconds"),
               hidden=sorted(hidden_ids()), boost=boosts(), keep=sorted(keeps()), also=sorted(also_ids()), off_season=season.off_albums(),
               photo_seconds=60, clip_share=clip_share, transition_ms={"glance": 2600}, tune=tune,
               chapters={k: {"title": c["title"], "when": c.get("when")} for k, c in chapters.items()},
               items=items, glance_spreads=spreads, videos=vids, clips_auto_hidden=sorted(auto_off))
    os.makedirs(OUT, exist_ok=True)
    tmp = os.path.join(OUT, "prod.json.tmp")
    json.dump(out, open(tmp, "w"), indent=1)
    os.replace(tmp, os.path.join(OUT, "prod.json"))
    tot = sum(i["bytes"] for i in items) + sum(s["bytes"] for s in spreads)
    print(f"{len(items)} photos ({sum(1 for i in items if i['layout']=='cover')} fill), {len(pairs)} pairs, "
          f"{len(talls)} tall threes, {len(trios)} trios, {len(vids)} clips; 10-bit stills {tot/1e6:.0f} MB; {time.time()-t0:.0f} s")


if __name__ == "__main__":
    main()
