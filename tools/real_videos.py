#!/usr/bin/env python3
"""Encode the real-album videos for the site (skips files already made with the current recipe).
4K HEVC Main 10 for the TV, 1080p H.264 for browsers, a poster. Long clips are cut to a 30 s excerpt,
unless the manager says to play the whole clip or a part you trimmed (site/data/trim.json).
Every clip comes out 3840x2160 and SDR BT.709, like the stills:
- wide clips (16:10 and wider) fill the screen, centre-cropped;
- tall and 4:3 clips sit centred on a blurred, darkened copy of themselves, the same look and 1.2% gap as
  a tall photo on its backdrop (real_media.py backdrop(), prod_build.py photo_rect());
- iPhone HDR (HLG, PQ) is tone mapped to SDR; ungraded Sony S-Log3 clips get tools/slog3_709.cube.
Reads real/items.json, writes photos/videos-real/videos.json (same schema sync_data.py reads)."""
import json, os, subprocess
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402
import encoders  # noqa: E402
T = tvconf.ROOT; V = f'{T}/photos/videos-real'
EXCERPT = {'v-bumperskid4corn': (40, 30)}      # start s, length s
RECIPE = 2                                     # bump to re-encode every clip
W, H = 3840, 2160
GAP = round(H * 0.012)
LUT = f'{T}/tools/slog3_709.cube'
# clips whose rotation tag is wrong (the phone thought it was held upright): use the frames as recorded
NO_ROTATE = set(tvconf.get_json('TVFRAME_NO_ROTATE', []))     # clip ids whose rotation tag is wrong (config.env)
TONEMAP = 'zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=mobius:desat=0,zscale=t=bt709:m=bt709:r=tv'


def probe(p, vid=None):
    j = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_streams', '-show_format', p]))
    s = next(x for x in j['streams'] if x['codec_type'] == 'video'); n, d = map(int, s['avg_frame_rate'].split('/'))
    rot = 0 if vid in NO_ROTATE else next((int(sd.get('rotation', 0)) for sd in s.get('side_data_list', []) if 'rotation' in sd), 0)
    w, h = (s['height'], s['width']) if abs(rot) % 180 == 90 else (s['width'], s['height'])
    return {'codec': s['codec_name'], 'profile': s.get('profile'), 'width': w, 'height': h, 'fps': round(n / d, 3) if d else 0,
            'duration': float(j['format']['duration']), 'size_bytes': int(j['format']['size']),
            'transfer': s.get('color_transfer', ''), 'pix_fmt': s.get('pix_fmt', ''),
            'make': (j['format'].get('tags') or {}).get('com.apple.quicktime.make', ''),
            'model': (j['format'].get('tags') or {}).get('com.apple.quicktime.model', ''),
            'brand': (j['format'].get('tags') or {}).get('major_brand', '').strip(),
            'encoder': (s.get('tags') or {}).get('encoder', ''),
            'slowmo': (j['format'].get('tags') or {}).get('com.apple.quicktime.full-frame-rate-playback-intent') == '0',
            'dovi': any(sd.get('side_data_type') == 'DOVI configuration record' for sd in s.get('side_data_list', []))}


def rotate_args(vid): return ['-noautorotate'] if vid in NO_ROTATE else []


def even(x): return max(2, int(round(x / 2)) * 2)


def color(src):
    """The colour step of the graph ('' or filters ending in a comma): HDR tone map or the S-Log3 LUT.
    tools/clip_score.py uses the same step so clips are judged as they will look."""
    if src['transfer'] in ('arib-std-b67', 'smpte2084'): return TONEMAP + ','
    if src['transfer'] in ('', 'unknown') and src['pix_fmt'] == 'yuv422p10le' and os.path.exists(LUT):
        return f'lut3d=file={LUT}:interp=tetrahedral,'           # Sony XAVC S-I/HS, shot in S-Log3
    return ''


def graph(src):
    """Filter graph from the source to a 3840x2160 SDR frame, 10-bit."""
    col = color(src)
    w, h = src['width'], src['height']
    if w / h >= 1.55:
        return f'[0:v]{col}scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1,format=p010le[v]'
    s = min((W - 2 * GAP) / w, (H - 2 * GAP) / h); fw, fh = even(w * s), even(h * s)
    return (f'[0:v]{col}format=yuv420p10le,split[a][b];'
            f'[a]scale=480:270:force_original_aspect_ratio=increase,crop=480:270,gblur=sigma=18,eq=saturation=0.72,'
            f'colorlevels=romax=0.52:gomax=0.52:bomax=0.52,scale=960:540:flags=bicubic,gblur=sigma=3,scale={W}:{H}:flags=bicubic[bg];'
            f'[b]scale={fw}:{fh}:flags=lanczos[fg];[bg][fg]overlay=({W}-w)/2:({H}-h)/2,setsar=1,format=p010le[v]')


SCORES = f'{T}/real/clip_scores.json'                # tools/clip_score.py: the best 30 s of each long clip
try: BEST = {k: v.get('best') for k, v in json.load(open(SCORES)).get('clips', {}).items()}
except (OSError, ValueError): BEST = {}


TRIM = f'{T}/site/data/trim.json'                    # the manager's trims (photos_api.py /bulk trim) win over the rest
try: TRIMS = json.load(open(TRIM)).get('trim', {}) or {}
except (OSError, ValueError): TRIMS = {}


def excerpt(vid, dur):
    t = TRIMS.get(vid) or {}
    if t.get('whole'): return 0.0, round(dur, 2)
    if t.get('end') is not None:
        s = round(min(max(0.0, float(t['start'])), max(0.0, dur - 2)), 2)
        return s, round(min(float(t['end']), dur) - s, 2)
    if vid in EXCERPT: return EXCERPT[vid]
    b = BEST.get(vid)
    if dur > 30.5 and b and b.get('start_s') is not None:      # the liveliest, best looking 30 s, as scored
        return round(min(max(0.0, float(b['start_s'])), max(0.0, dur - 30.0 - 0.1)), 1), 30.0
    if dur > 40: return round(dur * 0.25, 1), 30.0        # long clips not scored yet: skip the shaky start
    return 0.0, round(min(30.0, dur), 2)


PROG = os.path.expanduser('~/.cache/tvframe-step.json')      # the photo manager's progress bar reads this
def progress(done, total):
    try: json.dump({'step': 'videos', 'done': done, 'total': total}, open(PROG + '.tmp', 'w')); os.replace(PROG + '.tmp', PROG)
    except OSError: pass


def main():
    out = []
    todo = [it for it in json.load(open(f'{T}/real/items.json')) if it['kind'] == 'video']
    for n, it in enumerate(todo):
        progress(n, len(todo))
        src = f"{T}/{it['rel']}"; vid = it['id']
        files = {'tv': f'{V}/tv/{vid}.mp4', 'web': f'{V}/web/{vid}.mp4', 'poster': f'{V}/posters/{vid}.jpg'}
        for d in ('tv', 'web', 'posters', 'recipe'): os.makedirs(f'{V}/{d}', exist_ok=True)
        rec = f'{V}/recipe/{vid}.json'
        try:
            info = probe(src, vid)
            ss, tt = excerpt(vid, info['duration'])
            want = {'recipe': RECIPE, 'src_size': os.path.getsize(src), 'ss': ss, 'tt': tt}
            if vid in NO_ROTATE: want['no_rotate'] = True
            if not (os.path.exists(rec) and json.load(open(rec)) == want and all(os.path.exists(f) for f in files.values())):
                g = graph(info); cut = [*rotate_args(vid), '-ss', str(ss), '-t', str(tt), '-i', src, '-an', '-filter_complex']
                subprocess.run(encoders.ffmpeg(['ffmpeg', '-v', 'error', '-y', *cut, g, '-map', '[v]', '-c:v', 'hevc_nvenc', '-profile:v', 'main10',
                                '-preset', 'p6', '-rc', 'vbr', '-cq', '24', '-b:v', '0', '-maxrate', '40M', '-bufsize', '80M',
                                '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
                                '-tag:v', 'hvc1', '-movflags', '+faststart', files['tv']]), check=True)
                subprocess.run(encoders.ffmpeg(['ffmpeg', '-v', 'error', '-y', '-i', files['tv'], '-vf', 'scale=1920:1080:flags=lanczos,format=yuv420p',
                                '-c:v', 'h264_nvenc', '-profile:v', 'high', '-preset', 'p6', '-rc', 'vbr', '-cq', '23', '-b:v', '0',
                                '-maxrate', '14M', '-bufsize', '28M', '-movflags', '+faststart', files['web']]), check=True)
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(min(2.0, tt / 2)), '-i', files['tv'], '-frames:v', '1',
                                '-vf', 'scale=1920:-2', '-q:v', '3', files['poster']], check=True)
                json.dump(want, open(rec, 'w'))
                print(vid, f"{info['width']}x{info['height']}", info['transfer'] or '-', f'{ss}+{tt}s', flush=True)
            tv, web = probe(files['tv']), probe(files['web'])
        except Exception as e:                      # one bad file must not stop the rest
            print('video failed', vid, e, flush=True); continue
        out.append({'id': vid, 'title': it['file'], 'place': it.get('place', '') if it.get('place_src') != 'album' else '',   # an album name is not a place
                    'date': it.get('date', ''), 'time': it.get('time', ''),
                    'camera': it.get('camera', ''), 'album': it['album'], 'files': files, 'duration': tv['duration'], 'tv': tv, 'web': web,
                    'portrait': info['height'] > info['width'],
                    'source': {k: info[k] for k in ('codec', 'profile', 'width', 'height', 'fps', 'transfer', 'pix_fmt', 'make', 'model',
                                                    'brand', 'encoder', 'slowmo', 'dovi')},
                    'excerpt': {'start_s': ss, 'length_s': tt, 'source_duration_s': it.get('duration')}, 'author': '', 'license': ''})
    progress(len(todo), len(todo))
    json.dump({'generated': __import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo(tvconf.TZ)).isoformat(timespec='seconds'), 'videos': out}, open(f'{V}/videos.json', 'w'), indent=1)
    print(len(out), 'videos')


if __name__ == '__main__':
    main()
