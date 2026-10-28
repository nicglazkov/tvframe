import json, os, sys, io, base64, glob
from multiprocessing import Pool
from PIL import Image, ImageOps, ImageFilter
RAW = '/work/photos/raw'; OUT = '/work/site/media/p'
os.makedirs(OUT, exist_ok=True)
sel = {s['id']: s for s in json.load(open('/work/photos/selected.json'))}

def resize(im, long_edge):
    w, h = im.size
    s = min(1.0, long_edge / max(w, h))
    if s >= 1.0: return im.copy()
    return im.resize((round(w*s), round(h*s)), Image.LANCZOS)

def palette(im):
    sm = im.copy(); sm.thumbnail((160,160))
    q = sm.quantize(colors=6, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[:18]; counts = sorted(q.getcolors(), reverse=True)
    cols = []
    for c, i in counts:
        r,g,b = pal[i*3:i*3+3]; cols.append({'hex':'#%02x%02x%02x'%(r,g,b),'share':round(c/(sm.size[0]*sm.size[1]),3)})
    avg = sm.resize((1,1), Image.BOX).getpixel((0,0))
    # edge color (for mats): mean of outer 8% border
    w,h = sm.size; b = max(2,int(min(w,h)*0.08))
    edge = Image.new('RGB',(1,1))
    strips = [sm.crop((0,0,w,b)), sm.crop((0,h-b,w,h)), sm.crop((0,0,b,h)), sm.crop((w-b,0,w,h))]
    px=[0,0,0]; n=0
    for s in strips:
        c = s.resize((1,1), Image.BOX).getpixel((0,0)); px=[px[k]+c[k] for k in range(3)]; n+=1
    edge = tuple(int(v/n) for v in px)
    lum = (0.2126*avg[0]+0.7152*avg[1]+0.0722*avg[2])/255
    return cols, '#%02x%02x%02x'%avg, '#%02x%02x%02x'%edge, round(lum,3)

def work(path):
    pid = os.path.splitext(os.path.basename(path))[0]
    im = Image.open(path); im = ImageOps.exif_transpose(im).convert('RGB')
    W,H = im.size
    r = {'id': pid, 'w': W, 'h': H}
    big = resize(im, 3840); r['w4k'], r['h4k'] = big.size
    big.save(f'{OUT}/{pid}-4k.avif', quality=72, speed=6)
    big.save(f'{OUT}/{pid}-4k.jpg', quality=90, optimize=True, progressive=True, subsampling=0)
    m = resize(im, 1920); m.save(f'{OUT}/{pid}-1920.webp', quality=82, method=5); m.save(f'{OUT}/{pid}-1920.jpg', quality=86, optimize=True, progressive=True)
    s = resize(im, 1280); s.save(f'{OUT}/{pid}-1280.jpg', quality=82, optimize=True)
    t = resize(im, 720); t.save(f'{OUT}/{pid}-720.webp', quality=78, method=5)
    ph = resize(im, 32).filter(ImageFilter.GaussianBlur(1))
    b = io.BytesIO(); ph.save(b, 'WEBP', quality=50); r['ph'] = 'data:image/webp;base64,' + base64.b64encode(b.getvalue()).decode()
    r['palette'], r['avg'], r['edge'], r['lum'] = palette(im)
    meta = sel.get(pid, {}); r['author'] = meta.get('author'); r['src'] = meta.get('url')
    r['bytes'] = {k: os.path.getsize(f'{OUT}/{pid}-{k}') for k in ['4k.avif','4k.jpg','1920.webp','1920.jpg','1280.jpg','720.webp']}
    print(pid, W, H, flush=True)
    return r

if __name__ == '__main__':
    files = sorted(glob.glob(RAW + '/*.jpg'), key=lambda p: int(os.path.basename(p)[:-4]) if os.path.basename(p)[:-4].isdigit() else 0)
    with Pool(14) as p: res = p.map(work, files)
    json.dump(res, open('/work/site/data/media.json','w'), indent=0)
    print('done', len(res))
