#!/usr/bin/env python3
"""TV Frame playlist server (stdlib only).

  GET /playlist.json        playlist built from cache/index.json (+ videos, + loop video)
  GET /media/<kind>/<file>  frames | hevc | av1 | kb | loop  (from cache/)
  GET /media/videos/<file>  muted 4K clips from photos/videos/tv (if present)
  GET /media/videoloop/<file>  their seamless loops and first-frame posters (tools/clip_loops.py)
  GET /status               plain health text

Byte ranges are supported (Android MediaPlayer / MediaExtractor need them).
Settings live in server/config.json (mode, timings); every request re-reads it.
"""
import argparse, json, mimetypes, os, re, socketserver, sys, time
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import photoset
CACHE = photoset.cache()      # cache/ for the Unsplash set, cache-real/ for your photos
VIDEOS = photoset.videos()
EXTRA_VIDEOS = []
DEFAULTS = {
    "mode": "b",            # a = UI-plane bitmaps, b = video-plane stills, c = pre-rendered loop
    "photo_seconds": 12,
    "transition_ms": 1600,
    "video_weight": 45,     # sampling weight for a muted clip (photos are 0..100)
    "video_max_seconds": 30,
    "ken_burns": True,      # mode a only (b shows the still exactly as encoded)
    "captions": True,
    "clock": True,
}


def config():
    c = dict(DEFAULTS)
    try:
        c.update(json.load(open(os.path.join(HERE, "config.json"))))
    except FileNotFoundError:
        pass
    except Exception as e:
        print("config.json unreadable:", e, file=sys.stderr)
    return c


def video_items(cfg):
    items, seen = [], set()
    vj = os.path.join(VIDEOS, "videos.json")
    if os.path.exists(vj):
        try:
            for v in json.load(open(vj)).get("videos", []):
                f = (v.get("files") or {}).get("tv")
                if f and os.path.exists(f):
                    name = os.path.basename(f)
                    seen.add(name)
                    tv = v.get("tv") or {}
                    items.append(dict(id="video:" + v["id"], type="video", url="/media/videos/" + name,
                                      duration=min(float(v.get("duration") or tv.get("duration") or 15), cfg["video_max_seconds"]),
                                      width=tv.get("width"), height=tv.get("height"), codec=tv.get("codec"),
                                      caption=v.get("caption") or photoset.caption(v), weight=float(v.get("weight", cfg["video_weight"]))))
        except Exception as e:
            print("videos.json unreadable:", e, file=sys.stderr)
    for d in [os.path.join(VIDEOS, "tv")] + EXTRA_VIDEOS:
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            if n.endswith(".mp4") and n not in seen and not n.endswith(".tmp.mp4"):
                seen.add(n)
                items.append(dict(id="video:" + n[:-4], type="video", url="/media/videos/" + n, duration=15.0,
                                  caption=None, weight=float(cfg["video_weight"])))
    return items


def playlist():
    cfg = config()
    items = []
    try:
        idx = json.load(open(os.path.join(CACHE, "index.json")))
    except FileNotFoundError:
        idx = {"photos": [], "source": "none"}
    for p in idx["photos"]:
        it = dict(id=p["id"], type="photo", url=f"/media/frames/{p['id']}.jpg",
                  hevc=f"/media/hevc/{p['id']}.mp4", width=3840, height=2160,
                  native=p.get("native"), layout=p.get("layout"), focal=p.get("frame_focal") or [0.5, 0.5],
                  weight=p.get("weight", 50.0), cluster=p.get("cluster"),
                  caption=p["caption"] if "caption" in p else
                  (" · ".join(x for x in [p.get("author"), (p.get("date") or "")[:4]] if x) or None))
        if p.get("hevc8_bytes"):
            it["hevc8"] = f"/media/hevc8/{p['id']}.mp4"
        if p.get("av1_bytes"):
            it["av1"] = f"/media/av1/{p['id']}.mp4"
        items.append(it)
    for v in video_items(cfg):
        name = v["url"].rsplit("/", 1)[1]
        if os.path.exists(os.path.join(CACHE, "video8", name)):
            v["url8"] = "/media/video8/" + name     # 8-bit copy for decoders without Main10
        items.append(v)
    loop = None
    lj = os.path.join(CACHE, "loop", "loop.json")
    if os.path.exists(lj):
        try:
            loop = json.load(open(lj))
            loop["url"] = "/media/loop/" + loop["file"]
            if os.path.exists(os.path.join(CACHE, "loop", "loop8.mp4")):
                loop["url8"] = "/media/loop/loop8.mp4"
        except Exception:
            loop = None
    return dict(version=1, generated=__import__("datetime").datetime.now(__import__("zoneinfo").ZoneInfo(tvconf.TZ)).isoformat(timespec="seconds"), weights=idx.get("source"),
                mode=cfg["mode"], photo_seconds=cfg["photo_seconds"], transition_ms=cfg["transition_ms"],
                ken_burns=cfg["ken_burns"], captions=cfg["captions"], clock=cfg["clock"],
                loop=loop, items=items)


class H(BaseHTTPRequestHandler):
    server_version = "tvframe/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % a))

    def send_bytes(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/playlist.json", "/"):
            return self.send_bytes(json.dumps(playlist(), indent=1).encode(), "application/json")
        if path == "/prod.json":
            f = os.path.join(CACHE, "prod", "prod.json")
            if not os.path.isfile(f):
                return self.send_bytes(b"not built\n", "text/plain", 404)
            return self.send_bytes(open(f, "rb").read(), "application/json")
        mp = re.fullmatch(r"/media/prod/(frames|hevc|hevc8)/([A-Za-z0-9_.+\-]+)", path)
        if mp:
            f = os.path.join(CACHE, "prod", mp.group(1), mp.group(2))
            if not os.path.isfile(f):
                return self.send_bytes(b"not found\n", "text/plain", 404)
            return self.send_file(f)
        if path == "/status":
            return self.send_bytes(b"ok\n", "text/plain")
        m = re.fullmatch(r"/media/(frames|hevc|hevc8|av1|kb|loop|videos|videoloop|video8|test)/([A-Za-z0-9_.\-]+)", path)
        if not m:
            return self.send_bytes(b"not found\n", "text/plain", 404)
        kind, name = m.groups()
        if kind == "videoloop":      # seamless loops and their first-frame posters (tools/clip_loops.py)
            f = os.path.join(VIDEOS, "tvloop", name)
        elif kind == "videos":
            cands = [os.path.join(VIDEOS, "tv", name)] + [os.path.join(d, name) for d in EXTRA_VIDEOS]
            f = next((c for c in cands if os.path.isfile(c)), None)
        else:
            f = os.path.join(CACHE, kind, name)
        if not f or not os.path.isfile(f):
            return self.send_bytes(b"not found\n", "text/plain", 404)
        self.send_file(f)

    def send_file(self, f):
        size = os.path.getsize(f)
        ctype = mimetypes.guess_type(f)[0] or "application/octet-stream"
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
            if m:
                a, b = m.groups()
                if a == "":
                    start = max(size - int(b), 0)
                else:
                    start = int(a)
                    end = min(int(b), size - 1) if b else size - 1
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(f, "rb") as fh:
            fh.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = fh.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8151)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--extra-videos", action="append", default=[], help="another folder of ready .mp4 clips")
    a = ap.parse_args()
    EXTRA_VIDEOS.extend(os.path.abspath(os.path.expanduser(d)) for d in a.extra_videos)
    mimetypes.add_type("video/mp4", ".mp4")
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer.request_queue_size = 64
    srv = ThreadingHTTPServer((a.bind, a.port), H)
    print(f"tvframe server on {a.bind}:{a.port}, photo set {photoset.name()} ({CACHE})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
