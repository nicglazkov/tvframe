#!/usr/bin/env python3
"""Read your photo folders (EXIF only, via exiftool), turn GPS into place names, and write
real/items.json and real/captions.json.

Place names come from OpenStreetMap: Nominatim reverse lookups (zoom 17 and 14) plus an Overpass
query for named parks, preserves, piers and landmarks that contain the point. Only coordinates are
sent, at most about one request a second, and every answer is kept in real/geocode-cache.json so
reruns make no requests. Photos without GPS get no place; the caption falls back to the album name.

Run: python3 tools/real_meta.py  (add --offline to use only the cache)"""
import json, os, re, subprocess, sys, time, math, urllib.request, urllib.parse
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

T = tvconf.ROOT; R = f'{T}/real'; C = f'{R}/geocode-cache.json'
# Photos come from library/ (the "TV Photos" share and the photos page uploads). Each subfolder is
# an album; files right in the library go in "Library". The first two albums keep their old id prefixes
# (n-, v-) so hidden photos and scores carry over from the zips they came from; see tools/library_ids.py.
LIB = 'library'
import library_ids                    # album folder -> id prefix, kept across renames (tools/library_ids.py)
def _albums():
    root = f'{T}/{LIB}'
    m = library_ids.load()
    out = [(library_ids.prefix(n, m), f'{LIB}/{n}', n)
           for n in sorted(os.listdir(root)) if os.path.isdir(f'{root}/{n}') and not n.startswith('.')]
    library_ids.save(m)
    if any(os.path.isfile(f'{root}/{f}') and not f.startswith('.') for f in os.listdir(root)):
        out.append(('lib', LIB, 'Library'))
    return out
ALBUMS = _albums()
HOME = (float(tvconf.get('HOME_LAT', tvconf.LIVE_LAT or 0)), float(tvconf.get('HOME_LON', tvconf.LIVE_LON or 0)))  # trips: more than ~40 km away
OFFLINE = '--offline' in sys.argv or not tvconf.PLACES   # place lookups only with TVFRAME_PLACES=on
UA = 'tvframe-home-preview/1.0 (private LAN photo frame, low volume)'
os.makedirs(R, exist_ok=True)
cache = json.load(open(C)) if os.path.exists(C) else {}

# ---------------------------------------------------------------- cameras
SONY = {'ILCE-7RM5': 'A7R V', 'ILCE-7RM4': 'A7R IV', 'ILCE-7RM4A': 'A7R IV', 'ILCE-7RM3': 'A7R III', 'ILCE-7M3': 'A7 III',
        'ILCE-7M4': 'A7 IV', 'ILCE-7CM2': 'A7C II', 'ILCE-7C': 'A7C', 'ILCE-1': 'A1', 'ILCE-1M2': 'A1 II', 'ILCE-9M3': 'A9 III',
        'ILCE-7SM3': 'A7S III', 'ILCE-6700': 'A6700', 'ILCE-6400': 'A6400', 'ILCE-7RM2': 'A7R II'}

def num(s):
    s = s.rstrip('0').rstrip('.') if '.' in s else s
    return s

def lens_name(lens):
    """Body-agnostic plain lens name: focal range and aperture, plus Fujifilm's XF/XC prefix.
    Maker codes (Tamron A058, Sigma DG DN OS | Sports 023, Fujifilm R LM OIS WR) are dropped."""
    if not lens: return ''
    s = lens.strip()
    pre = 'XF' if re.match(r'^XF\s*\d', s) else ('XC' if re.match(r'^XC\s*\d', s) else '')
    m = re.search(r'(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)', s)          # "AF 27/1.2 XF" style
    if m and 'mm' not in s:
        return f'{num(m.group(1))}mm F{num(m.group(2))}'
    f = re.search(r'(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?\s*mm', s)
    if not f: return ''
    foc = num(f.group(1)) + (f'-{num(f.group(2))}' if f.group(2) else '') + 'mm'
    a = re.search(r'(f/|F)\s*(\d+(?:\.\d+)?)(?:\s*-\s*(?:f/|F)?\s*(\d+(?:\.\d+)?))?', s[f.end():])
    ap = ''
    if a:
        ap = ('f/' if a.group(1) == 'f/' else 'F') + num(a.group(2)) + (f'-{num(a.group(3))}' if a.group(3) else '')
    return f'{pre}{foc}{" " + ap if ap else ""}'

def camera_name(make, model, lens):
    """Plain names only: 'iPhone 17 Pro', 'Sony A7R V · 35-150mm F2-2.8'. Phones get no lens text."""
    make = (make or '').strip(); model = (model or '').strip()
    if not model: return ''
    mk = make.lower()
    if mk.startswith('apple') or model.startswith('iPhone'): return model
    if mk.startswith('google') or model.startswith('Pixel'): return model
    if mk.startswith('samsung'): return model
    if mk.startswith('sony'): body = 'Sony ' + SONY.get(model, model.replace('ILCE-', 'a'))
    elif mk.startswith('nikon'): body = 'Nikon ' + re.sub(r'^NIKON\s+', '', model, flags=re.I)
    elif mk.startswith('fujifilm'): body = 'Fujifilm ' + model
    elif mk.startswith('canon'): body = model if model.lower().startswith('canon') else 'Canon ' + model
    else: body = (make.title() + ' ' + model).strip()
    ln = lens_name(lens)
    return f'{body} · {ln}' if ln else body

# ---------------------------------------------------------------- places
def fetch(url, data=None, timeout=40):
    req = urllib.request.Request(url, data=data, headers={'User-Agent': UA})
    return json.load(urllib.request.urlopen(req, timeout=timeout))

OVP_FAILS = 0

def geo(lat, lon):
    k = f'{lat:.3f},{lon:.3f}'
    for z in (17, 14):
        ck = f'{k}@{z}'
        if ck not in cache and not OFFLINE:
            try: cache[ck] = fetch(f'https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat:.3f}&lon={lon:.3f}&zoom={z}&addressdetails=1&accept-language=en')
            except Exception as e: print('nominatim failed', ck, e)
            time.sleep(1.1)
    ck = f'{k}@ovp'
    global OVP_FAILS
    # the public Overpass servers are often full; after two failed cells skip it for this run (a later run fills it in)
    if ck not in cache and not OFFLINE and OVP_FAILS < 2:
        la, lo = k.split(',')
        q = f'''[out:json][timeout:25];is_in({la},{lo})->.a;
(area.a[name][leisure~"^(park|nature_reserve|garden|stadium|marina)$"];area.a[name][boundary~"^(protected_area|national_park)$"];
 area.a[name][tourism];area.a[name][man_made=pier];area.a[name][place~"^(island|islet)$"];area.a[name][highway=raceway];);out tags;'''
        for ep in ('https://overpass-api.de/api/interpreter', 'https://overpass.kumi.systems/api/interpreter'):
            try: cache[ck] = fetch(ep, urllib.parse.urlencode({'data': q}).encode(), 30); json.dump(cache, open(C, 'w'), indent=1); break
            except Exception as e: print('overpass failed', ck, ep, e)
        else: OVP_FAILS += 1
        time.sleep(1.5)
    return k

def area_rank(t):
    # smaller, more specific features first
    if t.get('man_made') == 'pier' or t.get('leisure') == 'marina': return 0
    if t.get('tourism') in ('attraction', 'museum', 'zoo', 'theme_park'): return 1
    if t.get('leisure') in ('garden', 'stadium') or t.get('highway') == 'raceway': return 2
    if t.get('leisure') in ('park', 'nature_reserve') or t.get('boundary') in ('protected_area',): return 3
    if t.get('place') in ('island', 'islet'): return 4
    if t.get('boundary') == 'national_park': return 5
    return 9

BAD_AREA = re.compile(r'(Golden Gate National Recreation Area|Bay Area Ridge Trail|Biosphere|Coastal Zone|Wildlife Area$|Critical Habitat)', re.I)

def place_for(k):
    """'Golden Gate Park, San Francisco', 'Financial District, San Francisco', 'Blackhawk, CA'."""
    a17 = (cache.get(f'{k}@17') or {}).get('address', {}); a14 = (cache.get(f'{k}@14') or {}).get('address', {})
    a = {**a14, **a17}
    city = a.get('city') or a.get('town') or a.get('village')
    areas = [e['tags'] for e in (cache.get(f'{k}@ovp') or {}).get('elements', []) if e.get('tags', {}).get('name')]
    areas = [t for t in areas if area_rank(t) < 9 and not BAD_AREA.search(t['name'])]
    areas.sort(key=area_rank)
    spot = areas[0]['name'] if areas else None
    if not spot:
        spot = a.get('neighbourhood') if a.get('neighbourhood') and city == 'San Francisco' else None
        spot = spot or a14.get('quarter') or a14.get('suburb') or a.get('hamlet') or a14.get('village')
    if spot and city and spot != city:
        return f'{spot}, {city}' if city in ('San Francisco', 'San Jose', 'Los Angeles', 'Oakland') else f'{spot}, {city}'
    if city: return f'{city}, CA' if a.get('state') == 'California' else city
    if spot: return f'{spot}, CA'
    county = a.get('county')
    return f'{county}, CA' if county else ''

def region_for(k):
    a = {**(cache.get(f'{k}@14') or {}).get('address', {}), **(cache.get(f'{k}@17') or {}).get('address', {})}
    return a.get('city') or a.get('town') or a.get('village') or a.get('county') or ''

def dist_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 6371 * 2 * math.asin(math.sqrt(h))

# ---------------------------------------------------------------- read
def slug(s): return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', s.lower())).strip('-')

items = []
for pre, rel, album in ALBUMS:
    d = f'{T}/{rel}'
    # an empty album prints nothing, and one unreadable file must not stop the rest, so no check here
    ex = json.loads(subprocess.run(['exiftool', '-q', '-j', '-n', '-api', 'LargeFileSupport=1',
        '-FileName', '-FileType', '-Make', '-Model', '-LensModel', '-Lens', '-DateTimeOriginal', '-OffsetTimeOriginal',
        '-CreationDate', '-CreateDate', '-GPSLatitude', '-GPSLongitude', '-ImageWidth', '-ImageHeight', '-Duration', d],
        capture_output=True).stdout or b'[]')
    stems = {os.path.splitext(r['FileName'])[0].lower() for r in ex if r.get('FileType') in ('JPEG', 'HEIC', 'PNG')}
    seen = set()
    for r in sorted(ex, key=lambda r: r['FileName']):
        fn = r['FileName']; stem, ext = os.path.splitext(fn)
        # only photos and videos; skips Finder's ._ and .DS_Store files and anything else dropped in the share
        if fn.startswith('.') or r.get('FileType') not in ('JPEG', 'HEIC', 'PNG', 'MP4', 'MOV'): continue
        kind = 'video' if r.get('FileType') in ('MP4', 'MOV') else 'photo'
        # Live Photo motion files (same name as a still) are 2-second clips; the still is used instead.
        if kind == 'video' and stem.lower() in stems: continue
        pid = f'{pre}-{slug(stem)}'
        while pid in seen: pid += '-b'
        seen.add(pid)
        it = {'id': pid, 'kind': kind, 'album': album, 'file': fn, 'rel': f'{rel}/{fn}', 'w': r.get('ImageWidth'), 'h': r.get('ImageHeight')}
        dt = r.get('DateTimeOriginal'); off = r.get('OffsetTimeOriginal')
        if kind == 'video':
            if isinstance(r.get('CreationDate'), str) and len(r['CreationDate']) >= 19:
                dt = r['CreationDate'][:19]
            elif isinstance(r.get('CreateDate'), str) and not r['CreateDate'].startswith('0000'):
                # QuickTime CreateDate is UTC; show it in Pacific time (assumption: shot in California)
                u = datetime.strptime(r['CreateDate'][:19], '%Y:%m:%d %H:%M:%S').replace(tzinfo=timezone.utc)
                dt = u.astimezone(ZoneInfo(tvconf.TZ)).strftime('%Y:%m:%d %H:%M:%S'); it['time_note'] = 'converted from UTC'
            it['duration'] = r.get('Duration')
        if isinstance(dt, str) and re.match(r'\d{4}:\d\d:\d\d \d\d:\d\d', dt) and not dt.startswith('0000'):
            if isinstance(off, str) and re.match(r'^[+-]\d\d:\d\d$', off):
                # The camera recorded its own UTC offset. These albums are all California, so show Pacific time
                # (a Fuji still set to Eastern otherwise reads 9:52 PM for a sunset).
                loc = datetime.strptime(dt[:19] + off, '%Y:%m:%d %H:%M:%S%z').astimezone(ZoneInfo(tvconf.TZ))
                if loc.strftime('%Y:%m:%d %H:%M:%S') != dt[:19]: it['time_note'] = f'converted from {off}'
                dt = loc.strftime('%Y:%m:%d %H:%M:%S'); it['offset'] = loc.strftime('%z')[:3] + ':' + loc.strftime('%z')[3:]
            it['date'] = dt[:10].replace(':', '-'); it['time'] = dt[11:16]
        it['camera'] = camera_name(r.get('Make'), r.get('Model'), r.get('LensModel') or r.get('Lens'))
        lat, lon = r.get('GPSLatitude'), r.get('GPSLongitude')
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and (lat or lon):
            k = geo(lat, lon); it['lat'] = round(lat, 4); it['lon'] = round(lon, 4)
            it['place'] = place_for(k); it['region'] = region_for(k); it['place_src'] = 'gps'
        items.append(it)
json.dump(cache, open(C, 'w'), indent=1)

# ---------------------------------------------------------------- chapters
def album_label(a): return ' · '.join(x.strip() for x in a.split(' - '))
dated = sorted([i for i in items if i.get('date')], key=lambda i: (i['date'], i.get('time', '')))
trips = []
for i in dated:
    if 'lat' not in i: continue
    far = dist_km(HOME, (i['lat'], i['lon'])) > 40
    d = datetime.strptime(i['date'], '%Y-%m-%d')
    if far:
        if trips and (d - trips[-1]['end']).days <= 2: trips[-1]['end'] = max(trips[-1]['end'], d); trips[-1]['gps'].append(i)
        else: trips.append({'start': d, 'end': d, 'gps': [i]})
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
def when(a, b):
    if a == b: return f'{MONTHS[a.month-1]} {a.day}, {a.year}'
    if a.month == b.month: return f'{MONTHS[a.month-1]} {a.day} to {b.day}, {a.year}'
    return f'{MONTHS[a.month-1]} {a.day} to {MONTHS[b.month-1]} {b.day}, {b.year}'
chapters = []
for t in trips:
    regs = {}
    for i in t['gps']: regs[i['region']] = regs.get(i['region'], 0) + 1
    top = [r for r, _ in sorted(regs.items(), key=lambda x: -x[1])][:2]
    title = ' and '.join(top)
    key = slug(f"{t['start']:%Y-%m-%d}-{top[0]}")
    lat = sum(i['lat'] for i in t['gps']) / len(t['gps']); lon = sum(i['lon'] for i in t['gps']) / len(t['gps'])
    chapters.append({'key': key, 'title': title, 'when': when(t['start'], t['end']), 'lat': round(lat, 3), 'lon': round(lon, 3), 'ids': [],
                     '_s': t['start'], '_e': t['end']})
home = {'key': 'home', 'title': 'Around home', 'when': '', 'lat': HOME[0], 'lon': HOME[1], 'ids': []}
albums = {}
for i in items:
    if i['kind'] != 'photo': continue
    ch = None
    if i.get('date'):
        d = datetime.strptime(i['date'], '%Y-%m-%d')
        near = 'lat' in i and dist_km(HOME, (i['lat'], i['lon'])) <= 25   # near home on a travel day still counts as home
        ch = None if near else next((c for c in chapters if c['_s'] <= d <= c['_e']), None)
        if not ch and 'lat' in i: ch = home
    if not ch:
        a = albums.setdefault(i['album'], {'key': 'album-' + slug(i['album']), 'title': album_label(i['album']), 'when': '', 'album': True, 'ids': []})
        ch = a
    ch['ids'].append(i['id']); i['chapter'] = ch['key']
hd = sorted(datetime.strptime(i['date'], '%Y-%m-%d') for i in items if i.get('chapter') == 'home' and i.get('date'))
if hd: home['when'] = str(hd[0].year) if hd[0].year == hd[-1].year else f'{hd[0].year} to {hd[-1].year}'
for c in chapters: c.pop('_s'); c.pop('_e')
allch = [c for c in chapters if c['ids']] + ([home] if home['ids'] else []) + [a for a in albums.values() if a['ids']]
for i in items:
    if not i.get('place'):
        i['place'] = album_label(i['album']); i['place_src'] = 'album'
json.dump(items, open(f'{R}/items.json', 'w'), indent=1)
caps = {'note': "your photos. Places come from each photo's GPS (OpenStreetMap names); photos without GPS show their album name.",
        'photos': {i['id']: {k: i.get(k) for k in ('place', 'place_src', 'chapter', 'date', 'time', 'offset', 'camera', 'album', 'lat', 'lon', 'file') if i.get(k) is not None}
                   for i in items if i['kind'] == 'photo'},
        'chapters': allch}
json.dump(caps, open(f'{R}/captions.json', 'w'), indent=1)
for i in items: print(f"{i['id']:34} {i.get('date','-'):10} {i.get('time','-'):5} | {i['place'][:44]:44} | {i['camera']} | {i.get('chapter')}")
for c in allch: print(c['key'], '|', c['title'], '|', c['when'], len(c['ids']))
