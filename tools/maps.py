#!/usr/bin/env python3
"""Maps for the TV's photo details panel: one 1024 px square per photo or clip that has GPS, centred on where it
was taken, written to site/media/maps/<id>.png. Esri World Imagery (satellite) with Esri's road and place-name
overlays: far easier to read from the couch than a dark street map, and a racetrack or a beach is obvious at a glance.
Light use with a User-Agent, no key; tiles are cached in ~/.cache/tvframe-tiles/esri, so a rebuild only fetches tiles
it has never seen. Credit drawn small in the corner. Run by tools/library_sync.sh
before prod_build.py; photos without GPS get no map. Only with TVFRAME_MAPS=on; turned off, it removes the maps it drew.

  python3 tools/maps.py [--force]
"""
import argparse, io, json, math, os, sys, time, urllib.request
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402

HOME = tvconf.ROOT
ITEMS = os.path.join(HOME, "real/items.json")
OUT = os.path.join(HOME, "site/media/maps")
TILES = os.path.expanduser("~/.cache/tvframe-tiles/esri")
_E = "https://server.arcgisonline.com/ArcGIS/rest/services/"
LAYERS = {                                  # bottom to top
    "imagery": _E + "World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "roads": _E + "Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}",
    "places": _E + "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
}
UA = "tvframe-maps/2 (private home photo frame; the server)"
STYLE = "esri-imagery"
ZOOM = 15           # about 3 km across
SIZE = 1024         # shown at up to about 1000 px on the 4K screen
TILE = 256


def tile(layer, z, x, y):
    f = os.path.join(TILES, layer, str(z), str(x), f"{y}.img")
    if not os.path.exists(f):
        os.makedirs(os.path.dirname(f), exist_ok=True)
        req = urllib.request.Request(LAYERS[layer].format(z=z, x=x, y=y), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            b = r.read()
        open(f + ".tmp", "wb").write(b)
        os.replace(f + ".tmp", f)
        time.sleep(0.2)                                   # gentle on the tile server
    return Image.open(f).convert("RGBA")



def world_px(lat, lon, z):
    n = TILE * 2 ** z
    s = math.sin(math.radians(lat))
    return (lon + 180) / 360 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n


def render(lat, lon):
    cx, cy = world_px(lat, lon, ZOOM)
    x0, y0 = cx - SIZE / 2, cy - SIZE / 2
    img = Image.new("RGBA", (SIZE, SIZE), (14, 14, 14, 255))
    for layer in LAYERS:
        for tx in range(int(x0 // TILE), int((x0 + SIZE) // TILE) + 1):
            for ty in range(int(y0 // TILE), int((y0 + SIZE) // TILE) + 1):
                img.alpha_composite(tile(layer, ZOOM, tx, ty), (round(tx * TILE - x0), round(ty * TILE - y0)))
    img = img.convert("RGB")
    from PIL import ImageEnhance
    img = ImageEnhance.Brightness(img).enhance(0.9)       # sits on the dark card without glaring
    # the pin: a soft accent halo, then a white dot with a dark ring, drawn 4x and scaled down for clean edges
    c = SIZE // 2
    halo = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(halo).ellipse((c - 60, c - 60, c + 60, c + 60), fill=120)
    halo = halo.filter(ImageFilter.GaussianBlur(26))
    img.paste((138, 180, 248), (0, 0), halo)
    k, r = 4, 34
    pin = Image.new("RGBA", (2 * r * k, 2 * r * k), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pin)
    pd.ellipse((0, 0, 2 * r * k - 1, 2 * r * k - 1), fill=(18, 18, 18, 255))
    pd.ellipse((8 * k, 8 * k, (2 * r - 8) * k - 1, (2 * r - 8) * k - 1), fill=(232, 234, 237, 255))
    pin = pin.resize((2 * r, 2 * r), Image.LANCZOS)
    img.paste(pin, (c - r, c - r), pin)
    d = ImageDraw.Draw(img)
    # credit, required by the tile licence; small and quiet in the corner
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
    except OSError:
        font = ImageFont.load_default()
    t = "Esri, Maxar, Earthstar Geographics"
    w = d.textlength(t, font=font)
    d.text((SIZE - w - 16, SIZE - 30), t, fill=(130, 130, 130), font=font)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if not tvconf.MAPS:
        for f in os.listdir(OUT):
            os.remove(os.path.join(OUT, f))
        print("maps: off (TVFRAME_MAPS=on in config.env turns them on)")
        return
    items = [i for i in json.load(open(ITEMS)) if i.get("lat") is not None and i.get("lon") is not None]
    keep, made = set(), 0
    for i in items:
        f = os.path.join(OUT, i["id"] + ".png")
        keep.add(os.path.basename(f))
        stamp = f"{i['lat']:.5f},{i['lon']:.5f},{ZOOM},{STYLE},{SIZE}"
        meta = f + ".at"
        if not a.force and os.path.exists(f) and os.path.exists(meta) and open(meta).read() == stamp:
            continue
        try:
            render(i["lat"], i["lon"]).save(f + ".tmp.png", optimize=True)
            os.replace(f + ".tmp.png", f)
            open(meta, "w").write(stamp)
            made += 1
        except Exception as e:
            print(f"map {i['id']}: {e}", file=sys.stderr)
    for f in os.listdir(OUT):                             # photos that left the library
        if f.endswith(".png") and f not in keep:
            os.remove(os.path.join(OUT, f))
            if os.path.exists(os.path.join(OUT, f + ".at")):
                os.remove(os.path.join(OUT, f + ".at"))
    print(f"maps: {len(items)} photos with GPS, {made} drawn")


if __name__ == "__main__":
    main()
