#!/usr/bin/env python3
"""Render the endless 4K slideshow video for mode (c): the TV just loops one file.

Picks photos by curation weight (no near-duplicates), optionally mixes in muted clips,
renders sub-pixel Ken Burns + crossfades with PIL in parallel workers, encodes each
chunk with NVENC (10-bit HEVC), then joins the chunks without re-encoding.
The timeline is circular: the last item fades into the first, so the loop point
lands on identical frames.

  python3 make_loop.py [--photos 16] [--videos 1] [--hold 10] [--fade 1.5] [--fps 30]
Output: cache/loop/loop.mp4 + cache/loop/loop.json (served at /media/loop/loop.mp4).
"""
import argparse, json, os, random, subprocess, sys, time
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402
from concurrent.futures import ProcessPoolExecutor
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import photoset
CACHE = photoset.cache()
W, H = 3840, 2160
VIDEOS = photoset.videos()


def pick(args):
    idx = json.load(open(os.path.join(CACHE, "index.json")))
    photos = [p for p in idx["photos"] if p.get("rep", True)]
    rnd = random.Random(args.seed)
    chosen = []
    pool = list(photos)
    while pool and len(chosen) < args.photos:
        tot = sum(max(p["weight"], 0.1) for p in pool)
        r = rnd.uniform(0, tot)
        for i, p in enumerate(pool):
            r -= max(p["weight"], 0.1)
            if r <= 0:
                chosen.append(pool.pop(i))
                break
    items = [dict(kind="photo", id=p["id"], src=os.path.join(CACHE, "kb", p["id"] + ".jpg"),
                  layout=p.get("layout", "cover"), focal=p.get("frame_focal") or [0.5, 0.5],
                  hold=args.hold) for p in chosen]
    vids = []
    vj = os.path.join(VIDEOS, "videos.json")
    if os.path.exists(vj):
        for v in json.load(open(vj)).get("videos", []):
            f = (v.get("files") or {}).get("tv")
            if f and os.path.exists(f):
                vids.append(dict(kind="video", id=v["id"], src=f,
                                 hold=min(float(v.get("duration", 15)), args.video_max) - args.fade))
    rnd.shuffle(vids)
    for k, v in enumerate(vids[:args.videos]):
        pos = (k + 1) * len(items) // (args.videos + 1) + k
        items.insert(pos, v)
    t = 0.0
    for it in items:
        it["start"] = t
        it["dir"] = rnd.choice([1, -1])
        t += it["hold"]
    return items, t


def kb_rect(it, u, total):
    """Source rectangle in the kb master (4416x2484) for local time u."""
    kw, kh = int(W * 1.15), int(H * 1.15) + 1
    p = min(max(u / total, 0.0), 1.0)
    if it["layout"] == "cover":
        z0, z1 = 1.0, 1.15            # 1.15 = one source pixel per screen pixel
    else:
        z0, z1 = 1.0, 1.06
    if it["dir"] < 0:
        z0, z1 = z1, z0
    z = z0 + (z1 - z0) * p
    rw, rh = kw / z, kh / z
    fx, fy = it["focal"]
    cx0, cy0 = kw / 2, kh / 2
    # drift the center toward the focal point while zoomed in
    k = (z - 1.0) / 0.15
    cx = cx0 + (fx * kw - cx0) * k * 0.6
    cy = cy0 + (fy * kh - cy0) * k * 0.6
    x0 = min(max(cx - rw / 2, 0), kw - rw)
    y0 = min(max(cy - rh / 2, 0), kh - rh)
    return x0, y0, rw, rh


class Source:
    def __init__(self, it, fps, fade, offset):
        self.it, self.fps, self.fade = it, fps, fade
        self.total = it["hold"] + fade
        if it["kind"] == "photo":
            self.img = Image.open(it["src"]).convert("RGB")
            self.img.load()
            self.proc = None
        else:
            self.proc = subprocess.Popen(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{offset:.3f}", "-i", it["src"], "-an",
                 "-vf", f"fps={fps},scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2",
                 "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
            self.last = None

    def frame(self, u):
        if self.proc is None:
            x0, y0, rw, rh = kb_rect(self.it, u, self.total)
            return self.img.transform((W, H), Image.AFFINE, (rw / W, 0, x0, 0, rh / H, y0), Image.BICUBIC)
        buf = self.proc.stdout.read(W * H * 3)
        if len(buf) == W * H * 3:
            self.last = Image.frombytes("RGB", (W, H), buf)
        return self.last if self.last is not None else Image.new("RGB", (W, H))

    def close(self):
        if self.proc:
            self.proc.kill()


def render_chunk(job):
    """Render [start, start+hold) of item i (its fade-in from the previous item included)."""
    i, items, fps, fade, out = job
    it, prev = items[i], items[i - 1]
    n = int(round(it["hold"] * fps))
    nf = int(round(fade * fps))
    cur = Source(it, fps, fade, 0.0)
    prv = Source(prev, fps, fade, prev["hold"]) if nf else None
    enc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
         "-vf", "scale=in_color_matrix=bt709:out_color_matrix=bt709:in_range=pc:out_range=tv,format=p010le",
         "-c:v", "hevc_nvenc", "-profile:v", "main10", "-preset", "p6", "-tune", "hq",
         "-rc", "vbr", "-cq", "23", "-b:v", "30M", "-maxrate", "45M", "-bufsize", "60M", "-g", str(fps * 2),
         "-bf", "2", "-tag:v", "hvc1", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
         "-an", out], stdin=subprocess.PIPE)
    t0 = time.time()
    for f in range(n):
        u = f / fps
        img = cur.frame(u)
        if f < nf:
            a = (f + 0.5) / nf
            a = a * a * (3 - 2 * a)          # smoothstep
            img = Image.blend(prv.frame(prev["hold"] + u), img, a)
        enc.stdin.write(img.tobytes())
    enc.stdin.close()
    enc.wait()
    cur.close()
    if prv:
        prv.close()
    return i, n, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", type=int, default=16)
    ap.add_argument("--videos", type=int, default=1)
    ap.add_argument("--video-max", type=float, default=15)
    ap.add_argument("--hold", type=float, default=10)
    ap.add_argument("--fade", type=float, default=1.5)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=6, help="NVENC sessions in parallel")
    args = ap.parse_args()
    items, total = pick(args)
    d = os.path.join(CACHE, "loop")
    tmp = os.path.join(d, "chunks")
    os.makedirs(tmp, exist_ok=True)
    print(f"{len(items)} items, {total:.1f} s loop at {args.fps} fps")
    t = time.time()
    jobs = [(i, items, args.fps, args.fade, os.path.join(tmp, f"{i:03d}.mp4")) for i in range(len(items))]
    with ProcessPoolExecutor(args.workers) as ex:
        for i, n, dt in ex.map(render_chunk, jobs):
            print(f"  chunk {i:3d} {items[i]['kind']:5} {items[i]['id']:>18}  {n} frames  {n/dt:5.1f} fps", flush=True)
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w") as f:
        for j in jobs:
            f.write(f"file '{j[4]}'\n")
    out = os.path.join(d, "loop.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                    "-c", "copy", "-movflags", "+faststart", out + ".tmp.mp4"], check=True)
    os.replace(out + ".tmp.mp4", out)
    for j in jobs:
        os.remove(j[4])
    meta = dict(file="loop.mp4", duration=round(total, 3), fps=args.fps, width=W, height=H, codec="hevc",
                profile="main10", items=[f"{x['kind']}:{x['id']}" for x in items],
                size_bytes=os.path.getsize(out), render_s=round(time.time() - t, 1),
                generated=__import__("datetime").datetime.now(__import__("zoneinfo").ZoneInfo(tvconf.TZ)).isoformat(timespec="seconds"))
    json.dump(meta, open(os.path.join(d, "loop.json"), "w"), indent=1)
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
