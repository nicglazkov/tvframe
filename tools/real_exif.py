#!/usr/bin/env python3
"""How each photo was taken, for the TV's details panel (OK on the remote): focal length, aperture, shutter, ISO,
exposure compensation, flash, size and the editor it went through. Plain facts from EXIF, nothing judged.
Reads ~/tvframe/real/items.json, writes ~/tvframe/real/exif.json {"photos": {id: {"lines": [...], ...}}}.
Only files that changed since the last run are read again (exiftool, batched)."""
import json, os, re, subprocess
T = os.path.expanduser('~/tvframe'); OUT = f'{T}/real/exif.json'
TAGS = ['FNumber', 'ExposureTime', 'ISO', 'FocalLength', 'FocalLengthIn35mmFormat', 'LensModel', 'ExposureCompensation',
        'Flash', 'ImageWidth', 'ImageHeight', 'Software', 'Make', 'Model']


def num(v):
    try: return float(str(v).split()[0])
    except (TypeError, ValueError): return None


def shutter(v):
    s = str(v or '').strip()
    if not s: return ''
    if '/' in s: return f'{s} s'
    f = num(s)
    if f is None or f <= 0: return ''
    if f >= 1: return f'{f:g} s'
    return f'1/{round(1 / f)} s'


def editor(sw):
    sw = str(sw or '')
    for k, name in (('Lightroom', 'Lightroom'), ('Photoshop', 'Photoshop'), ('Capture One', 'Capture One'), ('DxO', 'DxO PhotoLab'),
                    ('Pixelmator', 'Pixelmator'), ('Luminar', 'Luminar'), ('darktable', 'darktable')):
        if k.lower() in sw.lower(): return name
    return ''


def lines(x):
    """[exposure, size and editor] with empty parts left out, e.g. ['123 mm · f/2.8 · 1/160 s · ISO 200', '33 MP · Lightroom']."""
    phone = 'iphone' in str(x.get('Model', '')).lower() or 'pixel' in str(x.get('Model', '')).lower()
    fl, fl35 = num(x.get('FocalLength')), num(x.get('FocalLengthIn35mmFormat'))
    # phones: the real focal length means nothing to people, the full-frame equivalent does
    mm = f'{round(fl35)} mm equiv.' if phone and fl35 else (f'{fl:g} mm' if fl else '')
    fn = num(x.get('FNumber'))
    iso = num(x.get('ISO'))
    ev = num(x.get('ExposureCompensation'))
    a = [mm, f'f/{round(fn, 1):g}' if fn else '', shutter(x.get('ExposureTime')), f'ISO {round(iso)}' if iso else '',
         f'{ev:+.1f} EV'.replace('.0 EV', ' EV') if ev else '',
         'Flash' if re.match(r'(On|Fired|Auto, Fired)', str(x.get('Flash', ''))) else '']
    w, h = num(x.get('ImageWidth')), num(x.get('ImageHeight'))
    mp = f'{round(w * h / 1e6)} MP' if w and h and w * h >= 1.5e6 else ''
    b = [mp, editor(x.get('Software'))]
    return [s for s in (' · '.join(p for p in a if p), ' · '.join(p for p in b if p)) if s]


def main():
    items = [it for it in json.load(open(f'{T}/real/items.json')) if it['kind'] == 'photo']
    try: old = json.load(open(OUT)).get('photos', {})
    except (OSError, ValueError): old = {}
    out, todo = {}, []
    for it in items:
        p = f"{T}/{it['rel']}"
        try: st = os.stat(p)
        except OSError: continue
        key = f'{st.st_size}:{int(st.st_mtime)}'
        if old.get(it['id'], {}).get('key') == key: out[it['id']] = old[it['id']]
        else: todo.append((it['id'], p, key))
    for i in range(0, len(todo), 200):
        chunk = todo[i:i + 200]
        r = subprocess.run(['exiftool', '-j', '-n', '-fast2', *[f'-{t}' for t in TAGS], *[p for _, p, _ in chunk]],
                           capture_output=True, text=True)
        # -n gives plain numbers; ExposureTime is printed as a fraction instead (1/160 reads better than 0.00625)
        r2 = subprocess.run(['exiftool', '-j', '-fast2', '-ExposureTime', '-Flash', *[p for _, p, _ in chunk]], capture_output=True, text=True)
        try: rows = {os.path.realpath(x['SourceFile']): x for x in json.loads(r.stdout or '[]')}
        except ValueError: rows = {}
        try: pretty = {os.path.realpath(x['SourceFile']): x for x in json.loads(r2.stdout or '[]')}
        except ValueError: pretty = {}
        for vid, p, key in chunk:
            x = dict(rows.get(os.path.realpath(p), {}))
            x.update({k: v for k, v in pretty.get(os.path.realpath(p), {}).items() if k in ('ExposureTime', 'Flash')})
            out[vid] = {'key': key, 'lines': lines(x)}
    tmp = OUT + '.tmp'
    json.dump({'photos': out}, open(tmp, 'w'), indent=0)
    os.replace(tmp, OUT)
    print(f'{len(out)} photos, {len(todo)} read now, {sum(1 for v in out.values() if v["lines"])} with settings')


if __name__ == '__main__':
    main()
