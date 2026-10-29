#!/usr/bin/env python3
"""Copy the photo set, curation and video outputs into the site, rewrite paths to site URLs,
write data/manifest.json. Run any time:

    python3 tools/sync_data.py              # your photos (default)
    python3 tools/sync_data.py --unsplash   # the old Unsplash stand-in set

tools/library_sync.sh runs it as one step of the full rebuild. Live glance data is separate: tools/fetch_live.py, on cron."""
import json, os, re, shutil, sys, datetime
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402
T = tvconf.ROOT; S = f'{T}/site'; D = f'{S}/data'
UNSPLASH = '--unsplash' in sys.argv
man = {'set': 'unsplash' if UNSPLASH else 'real'}

def named(title):
    """A clip with no place goes by its file name when someone named it ('R8 Onboard.mp4' -> 'R8 Onboard'),
    never by a camera's own name (IMG_1234, C0138, DSC01234, PXL_..., GOPR...)."""
    stem = os.path.splitext(title)[0].strip()
    if not stem or re.fullmatch(r"(?i)(img|dsc|dji|gopr|gx|pxl|vid|mvi|c|mah|clip)[_-]?\d[\w-]*|[\d_ -]+", stem):
        return ""
    return stem


def load(p):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return None

if UNSPLASH:
    B = f'{T}/backup-2026-10-24-unsplash/data'
    srcs = {'media': f'{B}/media.json', 'captions': f'{B}/captions.json', 'scores': f'{T}/curate/out/scores.json',
            'sim': f'{T}/curate/out/sim.json', 'videos': f'{T}/photos/videos/videos.json'}
else:
    srcs = {'media': f'{T}/real/media.json', 'captions': f'{T}/real/captions.json', 'scores': f'{T}/curate/out-real/scores.json',
            'sim': f'{T}/curate/out-real/sim.json', 'videos': f'{T}/photos/videos-real/videos.json'}
for k in ('media', 'captions'):
    shutil.copyfile(srcs[k], f'{D}/{k}.json')

caps = load(srcs['captions'])
for k in ('scores', 'sim'):
    j = load(srcs[k])
    if j is None:
        man[k] = False
        if os.path.exists(f'{D}/{k}.json'): os.remove(f'{D}/{k}.json')
        continue
    if k == 'scores' and not UNSPLASH:
        # When the scorer keeps a copy with no metadata over an identical copy that has camera and date
        # (an export of the same edit), show the one with metadata instead.
        ph = j['photos']; meta = caps['photos']
        rich = lambda i: bool(meta.get(i, {}).get('camera')) + bool(meta.get(i, {}).get('date'))
        same = {(p['a'], p['b']) for p in j.get('closest_pairs', []) if p['clip_cos'] >= 0.99 and p['thumb_corr'] >= 0.99}
        for a, b in list(same):
            for x, y in ((a, b), (b, a)):
                if ph.get(x, {}).get('is_rep') and ph.get(y, {}).get('rep_id') == x and rich(y) > rich(x):
                    for i, v in ph.items():
                        if v.get('rep_id') == x: v['rep_id'] = y
                    ph[y]['is_rep'] = True; ph[x]['is_rep'] = False
                    # the shown copy takes over the group's weight and reason
                    for f in ('weight', 'reason'): ph[x][f], ph[y][f] = ph[y][f], ph[x][f]
                    ph[x]['reason'] = re.sub(r'(best of \d+ near-duplicates|near-duplicate of [\w-]+, hidden)', f'same picture as {y} without camera and date, hidden', ph[x]['reason'])
                    ph[y]['reason'] = re.sub(r'near-duplicate of [\w-]+, hidden', 'best of 2 near-duplicates, kept for its camera and date', ph[y]['reason'])
                    ph[y]['swap_note'] = f'shown instead of {x}: same picture, but this copy keeps camera and date'
        with open(f'{D}/scores.json', 'w') as fh: json.dump(j, fh, indent=1)
    else:
        shutil.copyfile(srcs[k], f'{D}/{k}.json')
    man[k] = True

# near-duplicate test files (Unsplash set only): small previews so the lab can show what was hidden
dups = []
if UNSPLASH and os.path.isdir(f'{T}/photos/dups'):
    os.makedirs(f'{S}/media/dups', exist_ok=True)
    for f in sorted(os.listdir(f'{T}/photos/dups')):
        if f.endswith('.jpg'):
            dst = f'{S}/media/dups/{f}'
            if not os.path.exists(dst): shutil.copyfile(f'{T}/photos/dups/{f}', dst)
            dups.append({'id': f[:-4], 'src': f'media/dups/{f}'})
man['dups'] = dups

# videos: hard-link files into the site (same disk, no extra space) and rewrite paths
PLACES = {
    'sunset_timelapse': 'Sunset over the hills', 'city_timelapse': 'City lights, time-lapse',
    'rheinfall': 'Rhine Falls, Schaffhausen', 'la_caleta': 'La Caleta beach, Cadiz',
    'after_storm': 'After the storm', 'earth_iss': 'Earth from the space station',
}
v = load(srcs['videos'])
if v and v.get('videos'):
    out = []
    for x in v['videos']:
        f = x.get('files', {})
        rec = {'id': x['id'], 'title': x.get('title', ''), 'place': x.get('place') or PLACES.get(x['id'], named(x.get('title', ''))),
               'date': x.get('date', ''), 'time': x.get('time', ''), 'camera': x.get('camera', ''),
               'duration': x.get('duration'), 'license': x.get('license', ''), 'license_url': x.get('license_url', ''),
               'page': x.get('source_url', ''), 'width': 3840, 'height': 2160}
        a = x.get('author', '') or ''
        m = re.search(r'youtube\.com/user/([^/]+)', a)
        rec['author'] = m.group(1) if m else a
        for key, sub in [('web', 'web'), ('tv', 'tv'), ('poster', 'posters')]:
            src = f.get(key)
            if not src or not os.path.exists(src): continue
            os.makedirs(f'{S}/media/v/{sub}', exist_ok=True)
            dst = f'{S}/media/v/{sub}/{os.path.basename(src)}'
            if os.path.exists(dst) and os.path.getsize(dst) != os.path.getsize(src): os.remove(dst)
            if not os.path.exists(dst):
                try: os.link(src, dst)
                except OSError: shutil.copyfile(src, dst)
            rec[{'web': 'h264', 'tv': 'hevc', 'poster': 'poster'}[key]] = f'media/v/{sub}/{os.path.basename(src)}'
        if isinstance(x.get('source'), dict): rec['source'] = x['source']     # how it was shot (real_videos.py probe)
        for key in ('web', 'tv'):
            if isinstance(x.get(key), dict):
                rec[f'{key}_info'] = {k2: x[key].get(k2) for k2 in ('codec', 'profile', 'width', 'height', 'fps', 'size_bytes')}
        if rec.get('h264'): out.append(rec)
    with open(f'{D}/videos.json', 'w') as fh: json.dump({'generated': v.get('generated'), 'videos': out}, fh, indent=1)
    man['videos'] = bool(out)
else:
    man['videos'] = False
    if os.path.exists(f'{D}/videos.json'): os.remove(f'{D}/videos.json')

# App banners pulled from the Streamer into media/apps (apps.json lists them by package name).
apps = {}
A = f'{S}/media/apps'
for x in (load(f'{A}/apps.json') or []):
    if x.get('banner') and os.path.exists(f"{S}/{x['banner']}"):
        apps[x['package']] = {k: x.get(k) for k in ('label', 'activity', 'banner', 'icon')}
man['apps'] = apps
man['live'] = os.path.exists(f'{D}/live.json')

man['updated'] = datetime.datetime.now(__import__('zoneinfo').ZoneInfo(tvconf.TZ)).isoformat(timespec='seconds')
with open(f'{D}/manifest.json', 'w') as fh: json.dump(man, fh, indent=1)
print(json.dumps({k: (v if not isinstance(v, (list, dict)) else len(v)) for k, v in man.items()}))
