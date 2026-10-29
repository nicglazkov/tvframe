#!/usr/bin/env python3
"""How each clip looks: how it was shot (from the camera's own tags, tools/real_videos.py "source") and how rich its
colour and contrast come out on the TV (measured on the 4K TV encode). Writes real/clip_look.json:
  {"clips": {id: {"shot": "iPhone HDR (Dolby Vision)", "settings": "4K · 60 fps · Dolby Vision", "color": 0..100,
                  "contrast": 0..100, "look": "Rich color", ...}}}
The colour numbers come from ffmpeg signalstats on the middle third of the frame at 1 fps (tall clips sit on a
blurred backdrop, so the middle is the clip itself), 8-bit: SATAVG (mean saturation) and YHIGH - YLOW (the
10th to 90th percentile luma spread). Cached per encode (size + mtime)."""
import json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402
import encoders  # noqa: E402

T = tvconf.ROOT
OUT = f'{T}/real/clip_look.json'
V = f'{T}/photos/videos-real/videos.json'


def settings(src):
    """The rough settings line: '4K · 60 fps · Dolby Vision', '4K · 24 fps · S-Log3', '1080p · 240 fps slow motion'."""
    if not src:
        return ''
    w, h, fps = max(src.get('width') or 0, src.get('height') or 0), min(src.get('width') or 0, src.get('height') or 0), src.get('fps') or 0
    res = '4K' if w >= 3800 else '2.7K' if w >= 2600 else '1080p' if h >= 1000 else '720p' if h >= 700 else ''
    f = round(fps)
    rate = f'{f} fps' if f else ''
    if rate and src.get('slowmo') and f >= 100:
        rate += ' slow motion'
    return ' · '.join(x for x in (res, rate, shot_tag(src)) if x)


def shot_tag(src):
    """Short colour tag for the settings line, '' for plain SDR."""
    t = src.get('transfer') or ''
    if t in ('arib-std-b67', 'smpte2084'):
        return 'Dolby Vision' if src.get('dovi') else 'HDR'
    if t in ('', 'unknown') and src.get('pix_fmt') == 'yuv422p10le':
        return 'S-Log3'
    return ''


def shot(src):
    """How it was shot or made, for the manager."""
    if not src:
        return 'Unknown'
    tag = shot_tag(src)
    if tag == 'S-Log3':
        return 'Sony S-Log3, graded here with a LUT'
    if tag:
        who = 'iPhone' if src.get('make') == 'Apple' else 'Camera'
        return f"{who} {'Dolby Vision' if tag == 'Dolby Vision' else 'HDR'}, tone mapped for the TV"
    if not src.get('make') and not src.get('model') and src.get('brand') != 'XAVC':
        return 'Edited export (graded before it got here)'
    return 'Standard color, straight from the camera'


def measure(path):
    g = 'fps=1,crop=iw/3:ih:iw/3:0,scale=320:-2,format=yuv420p,signalstats,metadata=print:file=-'
    r = subprocess.run(encoders.ffmpeg(['ffmpeg', '-v', 'error', '-hwaccel', 'cuda', '-i', path, '-an', '-vf', g, '-f', 'null', '-']), capture_output=True, text=True)
    sat, lo, hi = [], [], []
    for line in r.stdout.splitlines():
        m = re.search(r'lavfi\.signalstats\.(SATAVG|YLOW|YHIGH)=([\d.]+)', line)
        if m:
            {'SATAVG': sat, 'YLOW': lo, 'YHIGH': hi}[m.group(1)].append(float(m.group(2)))
    if not sat:
        raise RuntimeError(r.stderr.strip()[-200:] or 'no frames')
    n = min(len(lo), len(hi))
    return {'sat': round(sum(sat) / len(sat), 2), 'spread': round(sum(hi[i] - lo[i] for i in range(n)) / max(n, 1), 2)}


def pct(xs, x):
    """Percentile of x among xs, 0..100."""
    if not xs:
        return None
    return round(100 * sum(1 for v in xs if v <= x) / len(xs))


def main():
    vids = json.load(open(V)).get('videos', [])
    try:
        old = json.load(open(OUT)).get('clips', {})
    except (OSError, ValueError):
        old = {}

    def one(v):
        f = (v.get('files') or {}).get('tv')
        if not f or not os.path.exists(f):
            return v['id'], None
        key = [os.path.getsize(f), int(os.path.getmtime(f))]
        o = old.get(v['id'])
        if o and o.get('key') == key and 'sat' in o:
            m = {'sat': o['sat'], 'spread': o['spread']}
        else:
            try:
                m = measure(f)
            except Exception as e:
                print('clip look failed', v['id'], e, flush=True)
                return v['id'], None
        src = v.get('source') or {}
        return v['id'], dict(key=key, **m, shot=shot(src), settings=settings(src))

    with ThreadPoolExecutor(6) as ex:
        res = dict(r for r in ex.map(one, vids) if r[1])
    sats = [c['sat'] for c in res.values()]; spreads = [c['spread'] for c in res.values()]
    for c in res.values():
        c['color'] = pct(sats, c['sat']); c['contrast'] = pct(spreads, c['spread'])
        # descriptions, not verdicts: a muted, soft graded look can be the prettiest clip (the R8 onboard is one)
        tone = 'Vivid' if c['color'] >= 75 else 'Muted' if c['color'] < 25 else ''
        punch = 'punchy' if c['contrast'] >= 80 else 'soft contrast' if c['contrast'] < 25 else ''
        c['look'] = (f'{tone}, {punch}' if tone and punch else tone or punch.capitalize() or 'Natural')
    tmp = OUT + '.tmp'
    json.dump({'generated': __import__('datetime').datetime.now().isoformat(timespec='seconds'), 'clips': res}, open(tmp, 'w'), indent=1)
    os.replace(tmp, OUT)
    print(len(res), 'clips looked at')


if __name__ == '__main__':
    main()
