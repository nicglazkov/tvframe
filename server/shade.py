#!/usr/bin/env python3
"""Caption shade: a graduated ND filter baked into bright 4K frames so the TV's white text stays readable.

The clock and tile row sit along the bottom of the screen and the caption at the top left. On a bright photo
(sky, sand, a white car park) the text washes out. Rather than a UI bar, the frame itself gets the edit a
photographer would make with a graduated ND filter: the top and bottom edges fall off by up to one stop,
easing to nothing a quarter of the way in, so the middle of the photo is untouched. How strong each band is
comes from how bright the photo is under the text (75th percentile luma), so dark photos are left as they are.

Used by prod_build.py: for_item() measures a frame, and when it needs shade writes the shaded copy next to the
spreads as cache-real/prod/{frames,hevc,hevc8}/<id>.shade.{jpg,mp4} (served by serve.py as /media/prod/...; the
original frames are never changed) and returns its urls.
Caption Lab (backups/r14/captions) renders its "Graduated ND" mockups with apply() from this file.
"""
import hashlib, json, os, threading
from PIL import Image, ImageChops

W, H = 3840, 2160
DEPTH = 0.5            # light taken away at the very edge at full strength: 0.5 = one stop
TOP_END = 520          # top band eases out by here (caption lines end near y 300)
BOT_START = 1340       # bottom band starts here (tile glyphs start near y 1600)
BOT_FULL = H - 120     # and is at full strength from here down
MIN_STRENGTH = 0.05    # below this the photo is left alone
VERSION = 1            # bump to rebuild every shaded frame

# 4K regions under the text (FrameView.kt: caption at 200,150; clock from x 200; tiles from x 888 to 3640, bottom 1990)
CAP = (200, 140, 2400, 300)
CLOCK = (200, 1600, 780, 1990)
TILES = (880, 1640, 3640, 1990)


def _p75(im, box):
    h = im.crop(tuple(v // 4 for v in box)).convert("L").histogram()
    n, a = sum(h), 0
    for i, c in enumerate(h):
        a += c
        if a >= n * .75:
            return i
    return 255


def _strength(v):
    return max(0.0, min(1.0, (v - 105) / 110))


def measure(path):
    """(top, bottom) shade strength 0..1 and the raw 75th percentile brightness under caption, clock and tiles."""
    im = Image.open(path)
    im.draft("RGB", (960, 540))
    im = im.convert("RGB").resize((960, 540))
    cap, clock, tiles = _p75(im, CAP), _p75(im, CLOCK), _p75(im, TILES)
    return round(_strength(cap), 3), round(_strength(max(clock, tiles)), 3), dict(cap=cap, clock=clock, tiles=tiles)


def _smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def mask(top, bot):
    """Graduated filter mask (L): 255 = full shade. Uniform across the width, like a real filter."""
    col = Image.new("L", (1, H))
    for y in range(H):
        a = top * _smooth(1 - y / TOP_END) if y < TOP_END else 0.0
        b = bot * _smooth((y - BOT_START) / (BOT_FULL - BOT_START)) if y > BOT_START else 0.0
        col.putpixel((0, y), int(round(255 * max(a, b))))
    return col.resize((W, H))


def apply(img, top, bot):
    """The frame with the graduated ND applied (multiplies the encoded values, so it is a gentle exposure pull)."""
    m = mask(top, bot).point(lambda v: int(round(255 - v * DEPTH)))
    return ImageChops.multiply(img.convert("RGB"), Image.merge("RGB", (m, m, m)))


_lock = threading.Lock()


def _ver(path):
    st = os.stat(path)
    return hashlib.sha1(f"{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:10]


def for_item(pid, frame, out, encode, cache):
    """Shade info for one photo. Returns dict(shade, top, bottom, frame, hevc, hevc8) where frame/hevc/hevc8 are the
    shaded files (None when the photo needs no shade, then the originals are used as before).
    cache: a dict loaded with load_cache(out) and saved with save_cache(out, cache)."""
    key = f"{VERSION}:{_ver(frame)}"
    c = cache.get(pid)
    if not c or c.get("key") != key:
        top, bot, raw = measure(frame)
        c = dict(key=key, top=top, bottom=bot, raw=raw)
        with _lock:
            cache[pid] = c
    top, bot = c["top"], c["bottom"]
    res = dict(shade=round(max(top, bot), 2), top=top, bottom=bot, frame=None, hevc=None, hevc8=None)
    if max(top, bot) < MIN_STRENGTH:
        return res
    d = {k: os.path.join(out, k) for k in ("frames", "hevc", "hevc8")}
    for v in d.values():
        os.makedirs(v, exist_ok=True)
    jpg = os.path.join(d["frames"], pid + ".shade.jpg")
    h10 = os.path.join(d["hevc"], pid + ".shade.mp4")
    h8 = os.path.join(d["hevc8"], pid + ".shade.mp4")
    fresh = all(os.path.exists(p) and os.path.getmtime(p) >= os.path.getmtime(frame) for p in (jpg, h10, h8)) and c.get("built") == key
    if not fresh:
        with Image.open(frame) as im:
            apply(im, top, bot).save(jpg, quality=95, subsampling=0)
        encode(jpg, h10, "hevc")
        encode(jpg, h8, "hevc8")
        with _lock:
            c["built"] = key
    res.update(frame=jpg, hevc=h10, hevc8=h8)
    return res


def load_cache(out):
    try:
        return json.load(open(os.path.join(out, "shade.json")))
    except (OSError, ValueError):
        return {}


def save_cache(out, cache):
    tmp = os.path.join(out, "shade.json.tmp")
    json.dump(cache, open(tmp, "w"), indent=0)
    os.replace(tmp, os.path.join(out, "shade.json"))
