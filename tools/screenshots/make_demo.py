#!/usr/bin/env python3
"""Build a demo library for screenshots: stock landscapes only, nothing from a real home.

    python3 make_demo.py <photo-dir> <out-dir> [captions.json]

<photo-dir> holds JPEGs (any size). captions.json maps a file name (no extension) to
{"place": ..., "credit": ...}; files without an entry get a caption from the file name.
Writes <out-dir>/prod.json and the 4K frames and HEVC stills the TV app plays, in the same
layout server/prod_build.py uses, so demo_server.py can serve it to the app on an emulator."""
import json, os, subprocess, sys
from datetime import date, datetime, timedelta
from PIL import Image, ImageOps

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "server"))
from prepare import encode  # noqa: E402

W, H = 3840, 2160


def frame(src, dst):
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    ImageOps.fit(im, (W, H), Image.LANCZOS, centering=(0.5, 0.45)).save(dst, quality=92)


def main():
    src, out = sys.argv[1], sys.argv[2]
    caps = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else {}
    for d in ("frames", "hevc", "hevc8", "thumbs"):
        os.makedirs(os.path.join(out, d), exist_ok=True)
    names = sorted(f for f in os.listdir(src) if f.lower().endswith((".jpg", ".jpeg")))
    today, items = date.today(), []
    for k, f in enumerate(names):
        pid = "demo-" + os.path.splitext(f)[0]
        jpg = os.path.join(out, "frames", pid + ".jpg")
        if not os.path.exists(jpg):
            frame(os.path.join(src, f), jpg)
        thumb = os.path.join(out, "thumbs", pid + "-720.webp")
        if not os.path.exists(thumb):
            im = Image.open(jpg); im.thumbnail((720, 720)); im.save(thumb, quality=82)
        for codec in ("hevc", "hevc8"):
            mp4 = os.path.join(out, codec, pid + ".mp4")
            if not os.path.exists(mp4):
                encode(jpg, mp4, codec)
        c = caps.get(os.path.splitext(f)[0], {})
        # spread the dates over past years, a few in this very week so "N years ago" has something to show
        d = today.replace(year=today.year - 1 - k % 6) - timedelta(days=(k * 37) % 300 if k % 4 else k % 5)
        when = datetime(d.year, d.month, d.day, 7 + (k * 5) % 12, (k * 17) % 60)
        items.append({
            "id": pid, "layout": "cover", "portrait": False, "weight": 50 + (k * 13) % 40, "cluster": None, "rep": True,
            "caption": {"place": c.get("place") or os.path.splitext(f)[0].replace("-", " ").title(),
                        "when": when.strftime("%B %-d, %Y · %-I:%M %p"), "camera": c.get("camera", ""),
                        "more": [x for x in (c.get("settings"), c.get("credit")) if x]},
            "band": None, "date": d.isoformat(), "chapter": None, "rect": [0, 0, W, H],
            "url": f"/media/frames/{pid}.jpg", "hevc": f"/media/hevc/{pid}.mp4", "hevc8": f"/media/hevc8/{pid}.mp4", "thumb": f"/media/thumbs/{pid}-720.webp",
            "shade": 0.3, "bytes": os.path.getsize(jpg)})
        print(pid, flush=True)
    prod = {"version": 2, "generated": datetime.now().astimezone().isoformat(timespec="seconds"), "hidden": [], "boost": {},
            "keep": [], "also": [], "off_season": [], "photo_seconds": 60, "clip_share": 0,
            "transition_ms": {"glance": 2600}, "chapters": {}, "items": items,
            "glance_spreads": [], "videos": [], "clips_auto_hidden": []}
    json.dump(prod, open(os.path.join(out, "prod.json"), "w"), indent=1)
    print(len(items), "demo photos in", out)


if __name__ == "__main__":
    main()
