#!/usr/bin/env python3
"""Make the site's image files for your photos.
Runs inside the tvframe-media:heif container (Pillow 12 + pillow-heif), see tools/real_build.sh.
Input: /work/real/items.json (from real_meta.py). Output: /work/site/media/p/<id>-*, /work/photos/real/<id>.jpg
(curation copies), /work/real/media.json."""
import json, os, io, base64
from multiprocessing import Pool
from PIL import Image, ImageOps, ImageFilter, ImageCms, ImageEnhance
import pillow_heif
pillow_heif.register_heif_opener()
W = '/work'; OUT = f'{W}/site/media/p'; CUR = f'{W}/photos/real'
os.makedirs(OUT, exist_ok=True); os.makedirs(CUR, exist_ok=True)
SRGB = ImageCms.createProfile('sRGB')

def to_srgb(im):
    icc = im.info.get('icc_profile')
    im = ImageOps.exif_transpose(im)
    if im.mode not in ('RGB', 'RGBA'): im = im.convert('RGBA' if 'A' in im.getbands() else 'RGB')
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            im = ImageCms.profileToProfile(im.convert('RGB'), src, SRGB, renderingIntent=0, outputMode='RGB')
        except Exception: pass
    return im.convert('RGB')

def resize(im, long_edge):
    w, h = im.size; s = min(1.0, long_edge / max(w, h))
    return im.copy() if s >= 1.0 else im.resize((round(w*s), round(h*s)), Image.LANCZOS)

def palette(im):
    sm = im.copy(); sm.thumbnail((160, 160))
    q = sm.quantize(colors=6, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[:18]; counts = sorted(q.getcolors(), reverse=True); n = sm.size[0]*sm.size[1]
    cols = [{'hex': '#%02x%02x%02x' % tuple(pal[i*3:i*3+3]), 'share': round(c/n, 3)} for c, i in counts]
    avg = sm.resize((1, 1), Image.BOX).getpixel((0, 0))
    w, h = sm.size; b = max(2, int(min(w, h)*0.08)); px = [0, 0, 0]
    for s in (sm.crop((0, 0, w, b)), sm.crop((0, h-b, w, h)), sm.crop((0, 0, b, h)), sm.crop((w-b, 0, w, h))):
        c = s.resize((1, 1), Image.BOX).getpixel((0, 0)); px = [px[k]+c[k] for k in range(3)]
    edge = tuple(int(v/4) for v in px)
    lum = (0.2126*avg[0]+0.7152*avg[1]+0.0722*avg[2])/255
    return cols, '#%02x%02x%02x' % avg, '#%02x%02x%02x' % edge, round(lum, 3)

def backdrop(im):
    """16:9 soft backdrop: the photo itself, cover-cropped, heavily blurred, darkened and a little desaturated."""
    small = resize(im, 640); w, h = small.size; ar = 16/9
    if w/h > ar: nw = int(h*ar); small = small.crop(((w-nw)//2, 0, (w-nw)//2+nw, h))
    else: nh = int(w/ar); small = small.crop((0, (h-nh)//2, w, (h-nh)//2+nh))
    small = small.resize((480, 270), Image.LANCZOS).filter(ImageFilter.GaussianBlur(18))
    small = ImageEnhance.Color(small).enhance(0.72); small = ImageEnhance.Brightness(small).enhance(0.52)
    return small.resize((960, 540), Image.BICUBIC).filter(ImageFilter.GaussianBlur(6))

def work(it):
    pid = it['id']
    im = to_srgb(Image.open(f"{W}/{it['rel']}"))
    Wd, Hd = im.size
    r = {'id': pid, 'w': Wd, 'h': Hd}
    big = resize(im, 3840); r['w4k'], r['h4k'] = big.size
    big.save(f'{OUT}/{pid}-4k.avif', quality=72, speed=6)
    big.save(f'{OUT}/{pid}-4k.jpg', quality=90, optimize=True, progressive=True, subsampling=0)
    m = resize(im, 1920); m.save(f'{OUT}/{pid}-1920.webp', quality=82, method=5); m.save(f'{OUT}/{pid}-1920.jpg', quality=86, optimize=True, progressive=True)
    resize(im, 1280).save(f'{OUT}/{pid}-1280.jpg', quality=82, optimize=True)
    resize(im, 720).save(f'{OUT}/{pid}-720.webp', quality=78, method=5)
    backdrop(im).save(f'{OUT}/{pid}-bg.webp', quality=80, method=5); r['bg'] = True
    resize(im, 1600).save(f'{CUR}/{pid}.jpg', quality=90)
    ph = resize(im, 32).filter(ImageFilter.GaussianBlur(1)); b = io.BytesIO(); ph.save(b, 'WEBP', quality=50)
    r['ph'] = 'data:image/webp;base64,' + base64.b64encode(b.getvalue()).decode()
    r['palette'], r['avg'], r['edge'], r['lum'] = palette(im)
    r['bytes'] = {k: os.path.getsize(f'{OUT}/{pid}-{k}') for k in ['4k.avif', '4k.jpg', '1920.webp', '1920.jpg', '1280.jpg', '720.webp', 'bg.webp']}
    print(pid, Wd, Hd, flush=True)
    return r

if __name__ == '__main__':
    items = [i for i in json.load(open(f'{W}/real/items.json')) if i['kind'] == 'photo']
    # drop stale curation copies so removed photos do not linger in the scorer input
    keep = {i['id'] for i in items}
    for f in os.listdir(CUR):
        if f[:-4] not in keep: os.remove(f'{CUR}/{f}')
    with Pool(12) as p: res = p.map(work, items)
    json.dump(res, open(f'{W}/real/media.json', 'w'), indent=0)
    print('done', len(res))
