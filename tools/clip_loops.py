#!/usr/bin/env python3
"""Seamless loops of the TV clips, so a clip can stay up for minutes and loop back on itself with no cut.
For a clip of length L, the loop is L - F long: it starts F seconds in, and its last F seconds dissolve (eased, no
linear ramp) into the clip's first F seconds, ending on the frame the loop starts with. Played on repeat it never jumps.
Also writes the loop's first frame as a 1920x1080 JPEG: the app fades that in on the UI plane before the clip starts,
because the TV's display hardware does not blend two video layers (see Renderers.kt PlaneRenderer).
Reads photos/videos-real/tv/*.mp4 (tools/real_videos.py), writes photos/videos-real/tvloop/<id>.mp4 and <id>.jpg."""
import json, os, subprocess, sys
sys.path.insert(0, os.path.expanduser('~/tvframe/tools'))
from real_videos import V, probe, progress

RECIPE = 1                    # bump to rebuild every loop
OUT = f'{V}/tvloop'


def fade_len(dur):
    return round(min(1.6, dur / 4), 2)


def build(src, dst, poster):
    info = probe(src)
    L = info['duration']; F = fade_len(L)
    fps = info['fps'] or 30
    # smoothstep of xfade's progress P (1 -> 0): the dissolve eases in and out
    ease = "st(0,P*P*(3-2*P));A*ld(0)+B*(1-ld(0))"
    g = (f'[0:v]fps={fps},split[a][b];[a]trim=start={F},setpts=PTS-STARTPTS[m];[b]trim=end={F},setpts=PTS-STARTPTS[h];'
         f'[m][h]xfade=transition=custom:expr=\'{ease}\':duration={F}:offset={round(L - 2 * F, 3)},format=p010le[v]')
    tmp = dst + '.tmp.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', src, '-an', '-filter_complex', g, '-map', '[v]',
                    '-c:v', 'hevc_nvenc', '-profile:v', 'main10', '-preset', 'p6', '-rc', 'vbr', '-cq', '24', '-b:v', '0',
                    '-maxrate', '40M', '-bufsize', '80M', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
                    '-tag:v', 'hvc1', '-movflags', '+faststart', tmp], check=True)
    os.replace(tmp, dst)
    # BT.709 limited range in, plain RGB out (the JPEG encoder does its own JFIF conversion), so it matches the video
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', dst, '-frames:v', '1',
                    '-vf', 'scale=1920:1080:flags=lanczos:in_color_matrix=bt709:in_range=tv,format=rgb24', '-q:v', '2', poster], check=True)
    return L, F


def main():
    os.makedirs(OUT, exist_ok=True)
    names = sorted(n for n in os.listdir(f'{V}/tv') if n.endswith('.mp4'))
    made = 0
    for i, n in enumerate(names):
        progress(i, len(names))
        src = f'{V}/tv/{n}'; vid = n[:-4]
        dst, poster, rec = f'{OUT}/{n}', f'{OUT}/{vid}.jpg', f'{OUT}/{vid}.json'
        st = os.stat(src)
        want = {'recipe': RECIPE, 'src': f'{st.st_size}:{int(st.st_mtime)}'}
        try:
            if os.path.exists(rec) and json.load(open(rec)) == want and os.path.exists(dst) and os.path.exists(poster):
                continue
            L, F = build(src, dst, poster)
            json.dump(want, open(rec, 'w'))
            made += 1
            print(vid, f'{L:.1f}s, dissolve {F}s', flush=True)
        except Exception as e:          # one bad clip must not stop the rest; the app falls back to the plain clip
            print('loop failed', vid, e, flush=True)
    for n in os.listdir(OUT):           # loops of clips that are gone
        base = n.split('.')[0]
        if not os.path.exists(f'{V}/tv/{base}.mp4'):
            os.remove(f'{OUT}/{n}')
    progress(len(names), len(names))
    print(len(names), 'clips,', made, 'loops made')


if __name__ == '__main__':
    main()
