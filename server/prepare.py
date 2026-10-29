#!/usr/bin/env python3
"""Prepare TV Frame media for the playlist server.

For every photo (from ~/tvframe/curate/out/scores.json when it exists, else every
*.jpg in ~/tvframe/photos/raw) this writes, under server/cache/:

  frames/<id>.jpg   3840x2160 composed frame (cover crop on the focal point for
                    near-16:9 shots, a soft mat for portraits / squares / panoramas)
  kb/<id>.jpg       the same frame at 1.15x (4416x2484), source for Ken Burns in the loop video
  hevc/<id>.mp4     one-frame 10-bit HEVC Main10 stream of frames/<id>.jpg (video-plane path)
  av1/<id>.mp4      one-frame 10-bit AV1 stream (only with --av1)
  index.json        what was built, with sizes, weights, focal points, native size

Only rebuilds what is missing or older than its source, or whose framing changed. Encoding uses NVENC.
Framing you pick in the photo manager (site/data/framing.json, photos_api.py /bulk frame) wins over the rule below:
"whole" shows the whole photo on its backdrop, "fill" fills the screen around the focal point, a box fills it
with that part of the photo. With --only id,id just those photos are built and index.json keeps the rest.
"""
import argparse, json, os, subprocess, sys, time
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageFilter, ImageOps

HOME = os.path.expanduser("~")
ROOT = os.path.join(HOME, "tvframe")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import photoset
REAL = photoset.name() == "real"
CACHE = photoset.cache()
W, H = 3840, 2160
KB = 1.15


def load_real():
    """your photos: weights, focal points and duplicate groups from the site's scores (rep swap applied),
    4K sources the site already made (sRGB, HEIC decoded, rotation applied), captions as on the site."""
    sc = json.load(open(os.path.join(ROOT, "site", "data", "scores.json")))["photos"]
    caps = json.load(open(os.path.join(ROOT, "site", "data", "captions.json")))["photos"]
    try:
        fits = json.load(open(os.path.join(ROOT, "site", "data", "framing.json"))).get("frame", {}) or {}
    except (OSError, ValueError):
        fits = {}
    photos = []
    for pid, r in sc.items():
        f = os.path.join(ROOT, "site", "media", "p", f"{pid}-4k.jpg")
        if not os.path.exists(f):
            print("no 4K source for", pid, file=sys.stderr)
            continue
        c = caps.get(pid) or {}
        photos.append(dict(
            id=pid, src=f, weight=float(r.get("weight", 50.0)),
            focal=r.get("focal_point") or [0.5, 0.5], crop=r.get("crop_16x9"), mat=r.get("mat_color"),
            cluster=r.get("cluster_id"), rep=r.get("is_rep", True), date=c.get("date") or r.get("date"),
            reason=r.get("reason"), caption=photoset.caption(c), author=None, fit=fits.get(pid)))
    return photos, "real"


def load_photos():
    if REAL:
        return load_real()
    scores = os.path.join(ROOT, "curate", "out", "scores.json")
    photos = []
    if os.path.exists(scores):
        d = json.load(open(scores))
        recs = d.get("photos", d)
        recs = recs.values() if isinstance(recs, dict) else recs
        for r in recs:
            f = r.get("file") or r.get("path")
            if f and f.startswith("/photos/"):
                f = os.path.join(ROOT, "photos", f[len("/photos/"):])
            if not f or not os.path.exists(f):
                f = os.path.join(ROOT, "photos", "raw", f"{r['id']}.jpg")
            if not os.path.exists(f):
                continue
            photos.append(dict(
                id=str(r["id"]), src=f, weight=float(r.get("weight", 50.0)),
                focal=r.get("focal_point") or [0.5, 0.5],
                crop=r.get("crop_16x9"), mat=r.get("mat_color"),
                cluster=r.get("cluster_id"), rep=r.get("is_rep", True),
                date=r.get("date"), reason=r.get("reason")))
        source = "scores"
    else:
        raw = os.path.join(ROOT, "photos", "raw")
        for n in sorted(os.listdir(raw)):
            if n.lower().endswith((".jpg", ".jpeg", ".png")):
                photos.append(dict(id=os.path.splitext(n)[0], src=os.path.join(raw, n), weight=50.0,
                                   focal=[0.5, 0.5], crop=None, mat=None, cluster=None, rep=True,
                                   date=None, reason=None))
        source = "uniform"
    meta = {}
    sel = os.path.join(ROOT, "photos", "selected.json")
    if os.path.exists(sel):
        for m in json.load(open(sel)):
            meta[str(m["id"])] = m
    for p in photos:
        m = meta.get(p["id"], {})
        p["author"] = m.get("author")
    return photos, source


def hex_rgb(s, fallback=(24, 24, 24)):
    try:
        s = s.lstrip("#")
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


def is_cover(iw, ih):
    if REAL:
        # same rule as the site (tv.js fitMode): fill the screen when the crop keeps at least 74% of the
        # photo and needs no more than 2.2x upscale, else show it whole on a mat
        a, ar = iw / ih, W / H
        kept = a / ar if a < ar else ar / a
        return kept >= 0.74 and max(W / iw, H / ih) / kept <= 2.2
    return 1.45 <= iw / ih <= 2.0


def cover_box(iw, ih, focal, aspect):
    """Largest box of the given aspect around the focal point, clamped to the image."""
    if iw / ih > aspect:
        ch, cw = ih, ih * aspect
    else:
        cw, ch = iw, iw / aspect
    fx, fy = focal
    x0 = min(max(fx * iw - cw / 2, 0), iw - cw)
    y0 = min(max(fy * ih - ch / 2, 0), ih - ch)
    return (x0, y0, x0 + cw, y0 + ch)


def fills(iw, ih, p):
    """True when the photo fills the screen: your framing if you picked one, else is_cover()."""
    f = p.get("fit") or {}
    if f.get("fit") == "whole":
        return False
    if f.get("fit") == "fill" or f.get("box"):
        return True
    return is_cover(iw, ih)


def fill_box(iw, ih, p, aspect):
    """The part of the photo that fills the screen: your box (0..1 of the photo, made exactly the screen's shape
    around its centre and kept inside the photo) or the largest box around the focal point."""
    b = (p.get("fit") or {}).get("box")
    if not b:
        return cover_box(iw, ih, p["focal"], aspect)
    x0, y0, x1, y1 = b[0] * iw, b[1] * ih, b[2] * iw, b[3] * ih
    cx, cy, w = (x0 + x1) / 2, (y0 + y1) / 2, max(16.0, x1 - x0)
    w = min(w, iw, ih * aspect)
    h = w / aspect
    x0, y0 = min(max(cx - w / 2, 0), iw - w), min(max(cy - h / 2, 0), ih - h)
    return (x0, y0, x0 + w, y0 + h)


def fit_sig(p):
    f = p.get("fit") or {}
    return json.dumps({k: f[k] for k in ("fit", "box") if k in f}, sort_keys=True)


def frame_focal(iw, ih, focal, p=None):
    """Focal point in composed-frame coordinates (0..1)."""
    if not (fills(iw, ih, p) if p else is_cover(iw, ih)):
        return [0.5, 0.5]
    x0, y0, x1, y1 = fill_box(iw, ih, p, W / H) if p else cover_box(iw, ih, focal, W / H)
    if not (x0 <= focal[0] * iw <= x1 and y0 <= focal[1] * ih <= y1):
        return [0.5, 0.5]
    return [round((focal[0] * iw - x0) / (x1 - x0), 4), round((focal[1] * ih - y0) / (y1 - y0), 4)]


def backdrop(img, w, h, dim=0.42, sat=0.85, edge=0.35):
    """Blurred, darkened copy of the photo itself, cover-fit to the frame, with a soft vignette.
    The blur runs at 1/8 size so it is cheap; the 10-bit encode keeps the gradients smooth."""
    from PIL import ImageDraw, ImageEnhance
    small = ImageOps.fit(img, (w // 8, h // 8), Image.BILINEAR)
    small = small.filter(ImageFilter.GaussianBlur(14))
    small = ImageEnhance.Color(small).enhance(sat)
    small = ImageEnhance.Brightness(small).enhance(dim)
    bg = small.resize((w, h), Image.BICUBIC)
    vw, vh = w // 16, h // 16
    v = Image.new("L", (vw, vh), 0)
    ImageDraw.Draw(v).ellipse((-vw * 0.25, -vh * 0.35, vw * 1.25, vh * 1.35), fill=255)
    v = v.filter(ImageFilter.GaussianBlur(vh / 5)).resize((w, h), Image.BICUBIC)
    mask = v.point(lambda a: int(255 - (255 - a) * edge))   # edges about 35 percent darker
    return Image.composite(bg, Image.new("RGB", (w, h), (0, 0, 0)), mask)


def compose(img, p, w, h):
    """Return (frame, layout). layout is 'cover' or 'mat'."""
    iw, ih = img.size
    ar = iw / ih
    if fills(iw, ih, p):
        box = fill_box(iw, ih, p, w / h)
        return img.resize((w, h), Image.LANCZOS, box=box, reducing_gap=3.0), "cover"
    # mat: photo as large as it fits, with a thin even gap to the screen edge (1.2 percent of the height,
    # 26 px at 2160), on a blurred copy of itself
    gap = round(h * 0.012)
    aw, ah = w - 2 * gap, h - 2 * gap
    s = min(aw / iw, ah / ih)
    fw, fh = round(iw * s), round(ih * s)
    fg = img.resize((fw, fh), Image.LANCZOS, reducing_gap=3.0)
    bg = backdrop(img, w, h)
    # faint drop shadow so the print sits on the mat
    sh = Image.new("L", (w, h), 0)
    pad = int(h * 0.008)
    ox, oy = (w - fw) // 2, (h - fh) // 2
    sh.paste(80, (ox - pad // 2, oy, ox + fw + pad // 2, oy + fh + pad))
    sh = sh.filter(ImageFilter.GaussianBlur(pad))
    bg.paste(Image.new("RGB", (w, h), (0, 0, 0)), (0, 0), sh)
    bg.paste(fg, (ox, oy))
    return bg, "mat"


def encode(src_jpg, out_mp4, codec):
    vf = "scale=in_color_matrix=bt601:out_color_matrix=bt709:in_range=pc:out_range=tv,format=p010le"
    color = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
    if codec == "hevc":
        enc = ["-c:v", "hevc_nvenc", "-profile:v", "main10", "-preset", "p7", "-tune", "hq",
               "-rc", "constqp", "-qp", "17", "-tag:v", "hvc1"]
    elif codec == "hevc8":   # 8-bit fallback for decoders without Main10 (the emulator, older TVs)
        vf = vf.replace("format=p010le", "format=yuv420p")
        enc = ["-c:v", "hevc_nvenc", "-profile:v", "main", "-preset", "p7", "-tune", "hq",
               "-rc", "constqp", "-qp", "17", "-tag:v", "hvc1"]
    else:
        enc = ["-c:v", "av1_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "constqp", "-qp", "60"]
    tmp = out_mp4 + ".tmp.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src_jpg, "-frames:v", "1",
                    "-vf", vf, *enc, *color, "-r", "1", "-movflags", "+faststart", "-an", tmp], check=True)
    os.replace(tmp, out_mp4)


def make_test_chart():
    """3840x2160 resolution chart: 1 px and 2 px line pairs, checkers and a gray ramp.
    On a 1080p plane the 1 px patterns turn to flat gray; at true 4K they stay as lines."""
    from PIL import ImageDraw
    d = os.path.join(CACHE, "test")
    os.makedirs(d, exist_ok=True)
    png, mp4 = os.path.join(d, "chart.png"), os.path.join(d, "chart.mp4")
    mp48 = os.path.join(d, "chart8.mp4")
    if os.path.exists(png) and os.path.exists(mp4) and os.path.exists(mp48):
        return
    im = Image.new("L", (W, H), 128)
    px = im.load()
    cell = 480
    kinds = ["h1", "v1", "c1", "h2"] * 2      # same patterns on both halves
    for row in range(2):
        for col, k in enumerate(kinds):
            x0, y0 = col * cell, 360 + row * (cell + 120)
            n = int(k[1])
            for y in range(y0 + 20, y0 + cell - 20):
                for x in range(x0 + 20, x0 + cell - 20):
                    if k[0] == "h":
                        v = (y // n) % 2
                    elif k[0] == "v":
                        v = (x // n) % 2
                    else:
                        v = ((x // n) + (y // n)) % 2
                    px[x, y] = 255 if v else 0
    dr = ImageDraw.Draw(im)
    for x in range(W):
        g = int(x * 64 / W) + 96          # shallow ramp shows banding in 8-bit paths
        dr.line([(x, H - 200), (x, H - 80)], fill=g)
    im = im.convert("RGB")
    im.save(png, optimize=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", png, "-frames:v", "1",
                    "-vf", "scale=in_color_matrix=bt709:out_color_matrix=bt709:in_range=pc:out_range=tv,format=p010le",
                    "-c:v", "hevc_nvenc", "-profile:v", "main10", "-rc", "constqp", "-qp", "1", "-tag:v", "hvc1",
                    "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
                    "-r", "1", "-movflags", "+faststart", mp4], check=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", png, "-frames:v", "1",
                    "-vf", "scale=in_color_matrix=bt709:out_color_matrix=bt709:in_range=pc:out_range=tv,format=yuv420p",
                    "-c:v", "hevc_nvenc", "-profile:v", "main", "-rc", "constqp", "-qp", "1", "-tag:v", "hvc1",
                    "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
                    "-r", "1", "-movflags", "+faststart", mp48], check=True)


def stale(out, src):
    return not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(src)


def build(p, args):
    t0 = time.time()
    fr = os.path.join(CACHE, "frames", f"{p['id']}.jpg")
    kb = os.path.join(CACHE, "kb", f"{p['id']}.jpg")
    with Image.open(p["src"]) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        p["native"] = list(im.size)
        redo = args.remat and not is_cover(*im.size)   # --remat: rebuild every mat frame (layout change)
        sig, sigf = fit_sig(p), os.path.join(CACHE, "frames", f"{p['id']}.fit")
        try:
            redo = redo or open(sigf).read() != sig                       # you changed how it is framed
        except OSError:
            redo = redo or sig != "{}"
        if redo or stale(fr, p["src"]):
            f, layout = compose(im, p, W, H)
            f.save(fr, quality=93, subsampling=0 if args.q444 else 2, optimize=True)
        else:
            layout = "cover" if fills(*im.size, p) else "mat"
        p["frame_focal"] = frame_focal(im.size[0], im.size[1], p["focal"], p)
        if redo or stale(kb, p["src"]):
            f, _ = compose(im, p, int(W * KB), int(H * KB) + 1)
            f.save(kb, quality=92)
        with open(sigf, "w") as fh:
            fh.write(sig)
    p["layout"] = layout
    for codec in (["hevc", "hevc8"] + (["av1"] if args.av1 else [])):
        out = os.path.join(CACHE, codec, f"{p['id']}.mp4")
        if stale(out, fr):
            encode(fr, out, codec)
        p[codec + "_bytes"] = os.path.getsize(out)
    p["frame_bytes"] = os.path.getsize(fr)
    p["build_s"] = round(time.time() - t0, 2)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--av1", action="store_true", help="also build one-frame AV1 streams")
    ap.add_argument("--q444", action="store_true", help="4:4:4 JPEG frames")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--remat", action="store_true", help="rebuild frames for photos that do not fill 16:9")
    ap.add_argument("--only", default="", help="comma-separated ids: build just these, keep the rest of index.json")
    args = ap.parse_args()
    for d in ("frames", "kb", "hevc", "hevc8", "av1", "loop", "video8"):
        os.makedirs(os.path.join(CACHE, d), exist_ok=True)
    make_test_chart()
    photos, source = load_photos()
    only = {i for i in args.only.split(",") if i}
    if only:
        photos = [p for p in photos if p["id"] in only]
    print(f"{len(photos)} photos ({source})")
    t = time.time()
    with ThreadPoolExecutor(args.jobs) as ex:
        done = list(ex.map(lambda p: build(p, args), photos))
    for p in done:
        print(f"{p['id']:>22} {p['layout']:5} native {p['native'][0]}x{p['native'][1]} "
              f"jpg {p['frame_bytes']//1024}K hevc {p.get('hevc_bytes',0)//1024}K av1 {p.get('av1_bytes',0)//1024}K")
    if only:                                   # keep everything else as the last full build left it
        try:
            old = json.load(open(os.path.join(CACHE, "index.json")))["photos"]
        except (OSError, ValueError, KeyError):
            old = []
        new = {p["id"]: p for p in done}
        done = [new.pop(p["id"], p) for p in old] + list(new.values())
    idx = dict(generated=__import__("datetime").datetime.now(__import__("zoneinfo").ZoneInfo(tvconf.TZ)).isoformat(timespec="seconds"), source=source, width=W, height=H, photos=done)
    json.dump(idx, open(os.path.join(CACHE, "index.json"), "w"), indent=1)
    print(f"done in {time.time()-t:.1f}s")


if __name__ == "__main__":
    main()
