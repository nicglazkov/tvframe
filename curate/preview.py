#!/usr/bin/env python3
"""Contact sheet to eyeball the scores: each tile shows the photo with its 16:9 crop box,
focal point, face boxes, palette strip, weight and reason. Usage: preview.py out/scores.json out/preview.jpg"""
import json, sys, textwrap
from PIL import Image, ImageDraw, ImageFont, ImageOps

d = json.load(open(sys.argv[1]))
rows = sorted(d["photos"].values(), key=lambda r: -r["weight"])
TW, TH, PAD, TXT = 480, 300, 12, 64
cols = 5
n = len(rows)
sheet = Image.new("RGB", (cols * (TW + PAD) + PAD, ((n + cols - 1) // cols) * (TH + TXT + PAD) + PAD), "#16181c")
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
except Exception:
    font = ImageFont.load_default()
for k, r in enumerate(rows):
    im = ImageOps.exif_transpose(Image.open(r["file"].replace("/photos/", sys.argv[3] if len(sys.argv) > 3 else "/photos/"))).convert("RGB")
    im.thumbnail((TW, TH - 18))
    tile = Image.new("RGB", (TW, TH + TXT), "#22252b")
    ox, oy = (TW - im.width) // 2, 0
    tile.paste(im, (ox, oy))
    dr = ImageDraw.Draw(tile)
    x, y, w, h = r["crop_16x9"]
    dr.rectangle([ox + x * im.width, oy + y * im.height, ox + (x + w) * im.width, oy + (y + h) * im.height], outline="#ffd400", width=2)
    fx, fy = r["focal_point"]
    cx, cy = ox + fx * im.width, oy + fy * im.height
    dr.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], outline="#ff3b30", width=3)
    for f in r["faces"]["list"]:
        bx, by, bw, bh = f["box"]
        dr.rectangle([ox + bx * im.width, oy + by * im.height, ox + (bx + bw) * im.width, oy + (by + bh) * im.height], outline="#34c759", width=2)
    sw = TW / len(r["palette"])
    for i, c in enumerate(r["palette"]):
        dr.rectangle([i * sw, TH - 18, (i + 1) * sw, TH], fill=c)
    dr.text((8, TH + 6), f'{r["id"]}  weight {r["weight"]:.1f}  aes {r["aesthetic"]:.2f}  cluster {r["cluster_id"]}{" (rep)" if r["is_rep"] else ""}', fill="#f2f2f2", font=font)
    for li, line in enumerate(textwrap.wrap(r["reason"], 68)[:2]):
        dr.text((8, TH + 26 + 18 * li), line, fill="#b8bcc4", font=font)
    sheet.paste(tile, (PAD + (k % cols) * (TW + PAD), PAD + (k // cols) * (TH + TXT + PAD)))
sheet.save(sys.argv[2], quality=85)
print("wrote", sys.argv[2])
