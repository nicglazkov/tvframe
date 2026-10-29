#!/usr/bin/env python3
"""Rank the video clips by what is in them and pick the best part of each.

For every clip in real/items.json (kind video):
  1. ffmpeg decodes it small, with the same colour step as tools/real_videos.py (HLG tone map, S-Log3 LUT),
     so clips are judged as they will look on the TV: colour frames at ~2 fps, grey frames at ~8 fps.
  2. Inside the tvframe-curate container (same models as the photo scorer, curate/curate.py): CLIP ViT-L-14 +
     LAION aesthetic and YuNet faces per colour frame, sharpness and exposure the photo way, and dense optical
     flow per grey frame pair. A 6-parameter camera model is fitted to the flow; what the camera model does not
     explain is things moving in the scene (action), the camera path's quick wiggle is shake.
  3. Here, without the GPU: every 0.5 s gets a score, the best window (30 s, or the whole clip when shorter) is
     the one with the best average, and the clip gets one 0-100 score, a plain "why" and auto_hide for junk.

Writes real/clip_scores.json (schema in the r14 notes; the manager and prod_build.py read it).
Per-clip measurements are cached in real/clipcache/<id>.json and reused while the source's size and mtime match,
so a rerun only decodes new or changed clips (the scoring step itself takes well under a second).

  python3 tools/clip_score.py            score new and changed clips, rewrite clip_scores.json
  python3 tools/clip_score.py --rescore  only redo step 3 from the cache (for tuning the formula)
  python3 tools/clip_score.py --gpu ...  (internal) step 2, run inside the container
"""
import json, math, os, subprocess, sys, time
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402
import encoders  # noqa: E402
from concurrent.futures import ThreadPoolExecutor

T = tvconf.ROOT
RAWV = 1                                    # bump to re-measure every clip
LOOKS_LONG, MOTION_LONG = 512, 256          # long side of the colour and grey frames
LOOKS_FPS, MOTION_FPS = 2, 8
MAX_LOOKS, MAX_MOTION = 360, 2400           # very long clips are sampled more sparsely
WINDOW = 30.0
PROG = os.path.expanduser('~/.cache/tvframe-step.json')


def progress(done, total, cache=None):
    p = cache or PROG
    try:
        json.dump({'step': 'clips', 'done': done, 'total': total}, open(p + '.tmp', 'w')); os.replace(p + '.tmp', p)
    except OSError:
        pass


# ======================================================================= step 2: inside the container
def gpu_stage(job_dir):
    import cv2
    import numpy as np
    import torch
    from PIL import Image
    sys.path.insert(0, '/work' if os.path.isfile('/work/curate.py') else os.path.join(T, 'curate'))
    import curate                                            # the photo scorer: same models, same measures

    jobs = sorted(f for f in os.listdir(job_dir) if f.endswith('.job.json'))
    if not jobs:
        return
    dev = 'cuda' if torch.cuda.is_available() else 'cpu'
    aes = curate.Aesthetic(dev)
    faces = curate.Faces()
    cache_prog = '/cache/tvframe-step.json' if os.path.isdir('/cache') else None
    total2 = int(os.environ.get('CLIPS_TOTAL', len(jobs) * 2)); base = int(os.environ.get('CLIPS_BASE', len(jobs)))
    print(f'device {dev}, {len(jobs)} clips', flush=True)

    def aesthetic_batch(ims):
        out = []
        with torch.no_grad():
            for i in range(0, len(ims), 32):
                x = torch.stack([aes.prep(im) for im in ims[i:i + 32]]).to(dev)
                if dev == 'cuda':
                    x = x.half()
                e = aes.model.encode_image(x).float()
                e = e / e.norm(dim=-1, keepdim=True)
                out += [float(v) for v in aes.head(e).squeeze(-1).cpu()]
        return out

    yy = xx = None
    for n, jf in enumerate(jobs):
        t0 = time.time()
        job = json.load(open(os.path.join(job_dir, jf)))
        lw, lh, mw, mh = job['lw'], job['lh'], job['mw'], job['mh']
        looks = np.fromfile(job['looks'], dtype=np.uint8)
        looks = looks[:len(looks) // (lw * lh * 3) * lw * lh * 3].reshape(-1, lh, lw, 3)
        mot = np.fromfile(job['motion'], dtype=np.uint8)
        mot = mot[:len(mot) // (mw * mh) * mw * mh].reshape(-1, mh, mw)

        # looks: aesthetic, faces, sharpness, exposure per colour frame
        ims = [Image.fromarray(f) for f in looks]
        a = aesthetic_batch(ims) if ims else []
        L = []
        for i, f in enumerate(looks):
            gray = cv2.cvtColor(f, cv2.COLOR_RGB2GRAY)
            _, peak = curate.sharpness(gray)
            mean, hi, lo, exs = curate.exposure(gray)
            fl = faces(cv2.cvtColor(f, cv2.COLOR_RGB2BGR), gray)
            L.append([round(i / job['lfps'], 3), round(a[i], 3), round(peak, 1), round(mean, 4), round(hi, 4), round(lo, 4),
                      round(exs, 3), len(fl), int(sum(1 for x in fl if x['in_focus']))])

        # motion: dense flow between grey frames, a camera model fitted to it, what is left over is action
        M = []
        if len(mot) >= 2:
            ys, xs = np.mgrid[0:mh:4, 0:mw:4]
            cx, cy = (mw - 1) / 2, (mh - 1) / 2
            X = np.stack([(xs - cx).ravel(), (ys - cy).ravel(), np.ones(xs.size)], 1)
            for i in range(1, len(mot)):
                p, c = mot[i - 1], mot[i]
                flow = cv2.calcOpticalFlowFarneback(p, c, None, 0.5, 3, 15, 3, 5, 1.2, 0)
                fx, fy = flow[::4, ::4, 0].ravel(), flow[::4, ::4, 1].ravel()
                keep = np.ones(fx.size, bool)
                for _ in range(2):                           # refit without the moving things
                    ax, *_ = np.linalg.lstsq(X[keep], fx[keep], rcond=None)
                    ay, *_ = np.linalg.lstsq(X[keep], fy[keep], rcond=None)
                    r = np.hypot(fx - X @ ax, fy - X @ ay)
                    keep = r <= max(np.quantile(r, 0.7), 0.05)
                zoom = (ax[0] + ay[1]) / 2
                diff = float(np.abs(c.astype(np.int16) - p.astype(np.int16)).mean())
                M.append([round(i / job['mfps'], 3), round(float(ax[2]), 3), round(float(ay[2]), 3), round(float(zoom), 5),
                          round(float(r.mean()), 4), round(float((r > 1.0).mean()), 4), round(diff, 2)])
        rec = dict(job['key'], looks=L, motion=M, lfps=job['lfps'], mfps=job['mfps'], mlong=MOTION_LONG,
                   lsize=[lw, lh], msize=[mw, mh])
        json.dump(rec, open(job['out'] + '.tmp', 'w'))
        os.replace(job['out'] + '.tmp', job['out'])
        for f in (job['looks'], job['motion'], os.path.join(job_dir, jf)):
            try:
                os.remove(f)
            except OSError:
                pass
        print(f"{job['key']['id']}: {len(L)} looks, {len(M)} motion, {time.time() - t0:.1f} s", flush=True)
        progress(base + n + 1, total2, cache_prog)


# ======================================================================= step 1: decode
def even(x):
    return max(2, int(round(x / 2)) * 2)


def extract(it, info, job_dir, out):
    import real_videos as rv
    src = f"{T}/{it['rel']}"
    w, h, dur = info['width'], info['height'], max(info['duration'], 0.1)
    s = LOOKS_LONG / max(w, h); lw, lh = even(w * s), even(h * s)
    s = MOTION_LONG / max(w, h); mw, mh = even(w * s), even(h * s)
    lfps = min(LOOKS_FPS, MAX_LOOKS / dur); mfps = min(MOTION_FPS, MAX_MOTION / dur)
    if lfps < LOOKS_FPS:                                 # keep the colour rate a whole fraction of the grey one
        mfps = lfps * max(1, round(mfps / lfps))
    lfps, mfps = round(lfps, 4), round(mfps, 4)
    base = os.path.join(job_dir, it['id'])
    g = (f"[0:v]fps={mfps},scale={lw}:{lh}:flags=area,{rv.color(info)}format=rgb24,split[a][b];"
         f"[a]fps={lfps}[l];[b]scale={mw}:{mh}:flags=area,format=gray[m]")
    hw = encoders.hwaccel() if info['pix_fmt'] in ('yuv420p', 'yuv420p10le', 'nv12', 'p010le') else []
    cmd = ['ffmpeg', '-v', 'error', '-y', *hw, *rv.rotate_args(it['id']), '-i', src, '-an', '-filter_complex', g,
           '-map', '[l]', '-f', 'rawvideo', base + '.looks', '-map', '[m]', '-f', 'rawvideo', base + '.motion']
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and hw:                          # GPU decode refused this file: decode on the CPU
        r = subprocess.run([c for c in cmd if c not in hw], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-300:])
    key = {'id': it['id'], 'v': RAWV, 'src_size': os.path.getsize(src), 'src_mtime': int(os.path.getmtime(src)),
           'duration': round(dur, 3), 'w': w, 'h': h, 'transfer': info['transfer'], 'fps': info['fps']}
    jd, cd = (job_dir, out) if tvconf.IN_DOCKER else ('/jobs', '/cache-clips')    # paths as the scorer sees them
    json.dump({'key': key, 'lw': lw, 'lh': lh, 'mw': mw, 'mh': mh, 'lfps': lfps, 'mfps': mfps,
               'looks': f"{jd}/{it['id']}.looks", 'motion': f"{jd}/{it['id']}.motion", 'out': f"{cd}/{it['id']}.json"}, open(base + '.job.json', 'w'))


# ======================================================================= step 3: scores
def clip(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0.0


def sharp_n(peak):
    # the photo scale (curate.sharp_score) is set at 1024 px photos; these are 512 px video frames, set on the 110 clips of 10-27
    return clip((math.log10(max(peak, 1)) - 3.3) / (4.4 - 3.3))


def looks_n(a):
    return clip((a - 3.9) / 1.5)                     # video frames score lower than photos: 3.9 -> 0, 5.4 -> 1


# calibration (px per grey frame at 256 px long side and 8 fps; long clips sampled sparser are scaled to 8 fps)
ACT_LO, ACT_HI = 0.20, 3.00      # leftover flow: 0.2 is still life, 3+ is lots going on (onboard laps 4-6)
PAN_LO, PAN_HI = 0.50, 6.0       # smooth camera travel (a pan or a drive)
JIT_LO, JIT_HI = 0.40, 5.0       # camera wiggle around its smoothed path


def series(raw):
    """Per grey frame: time, action, pan speed, wiggle, scene cut. All per 1/8 s."""
    M = raw['motion']
    if not M:
        return []
    k = 8.0 / max(raw['mfps'], 0.01)                     # bring sparse sampling to the 8 fps scale
    k = min(k, 8.0)
    n = len(M); R = max(1, int(round(raw['mfps'] * 0.5)))   # 1 s centred smoothing
    # an edit or a flash: the picture changes far more than just before and after. Its flow is noise, so it
    # neither moves the camera path nor counts as action.
    cuts = []
    for i, m in enumerate(M):
        around = [M[j][6] for j in range(max(0, i - 4), min(n, i + 5)) if j != i]
        cuts.append(m[6] > 12 and m[6] > 3 * (median(around) if around else 0))
    px = py = 0.0; path = []
    for m, cut in zip(M, cuts):
        if not cut:
            px += m[1]; py += m[2]
        path.append((px, py))
    out = []
    for i, m in enumerate(M):
        lo, hi = max(0, i - R), min(n, i + R + 1)
        sx = mean(p[0] for p in path[lo:hi]); sy = mean(p[1] for p in path[lo:hi])
        jit = math.hypot(path[i][0] - sx, path[i][1] - sy)
        pan = math.hypot(m[1], m[2]) + abs(m[3]) * MOTION_LONG / 2
        out.append([m[0], m[4] / k, pan / k, jit / math.sqrt(k), cuts[i]])
    return out


def frame_scores(raw):
    """One row per colour frame: t, looks, action, pan, steady, sharp, exposure, luma, faces, faces in focus, aesthetic."""
    S = series(raw)
    rows = []
    for l in raw['looks']:
        t = l[0]
        near = [s for s in S if abs(s[0] - t) <= 0.5 and not s[4]] or [s for s in S if abs(s[0] - t) <= 1.0] or S
        act = clip((mean(s[1] for s in near) - ACT_LO) / (ACT_HI - ACT_LO)) if near else 0.0
        pan = clip((mean(s[2] for s in near) - PAN_LO) / (PAN_HI - PAN_LO)) if near else 0.0
        steady = 1 - clip((mean(s[3] for s in near) - JIT_LO) / (JIT_HI - JIT_LO)) if near else 1.0
        rows.append(dict(t=t, looks=looks_n(l[1]), aes=l[1], act=act, pan=pan, steady=steady, sharp=sharp_n(l[2]),
                         exp=l[6], luma=l[3], hi=l[4], lo=l[5], faces=l[7], ffocus=l[8]))
    return rows


def motion_of(r):
    return max(r['act'], 0.5 * r['pan'])                 # a smooth pan shows something too, but less than action


W_LOOKS, W_MOTION, W_STEADY, W_SHARP = 0.38, 0.34, 0.18, 0.10


def fscore(r):
    q = W_LOOKS * r['looks'] + W_MOTION * motion_of(r) + W_STEADY * r['steady'] + W_SHARP * r['sharp']
    return q * (0.7 + 0.3 * r['exp'])


def best_window(rows, dur):
    if dur <= WINDOW + 0.5 or not rows:
        return 0.0, round(min(WINDOW, dur), 2)
    fs = [fscore(r) for r in rows]
    step, best, bs = 0.5, 0.0, -1.0
    s = 0.0
    while s + WINDOW <= dur + 1e-6:
        v = [f for r, f in zip(rows, fs) if s <= r['t'] < s + WINDOW]
        if v:
            m = mean(v)
            if m > bs + 1e-9:
                bs, best = m, s
        s += step
    return round(best, 1), WINDOW


def fmt_t(s):
    s = int(round(s))
    return f'{s // 60}:{s % 60:02d}'


def score_clip(raw):
    dur = raw['duration']
    rows = frame_scores(raw)
    if not rows:
        return dict(score=0, looks=0, motion=0, steady=0, sharp=0, exposure=0, faces=0, duration=round(dur, 2),
                    best={'start_s': 0.0, 'length_s': round(min(WINDOW, dur), 2)}, why='Could not be read',
                    auto_hide=True, frames=[])
    st, ln = best_window(rows, dur)
    win = [r for r in rows if st <= r['t'] < st + ln] or rows
    looks = mean(r['looks'] for r in win); aes = mean(r['aes'] for r in win)
    motion = mean(motion_of(r) for r in win); act = mean(r['act'] for r in win)
    steady = mean(r['steady'] for r in win); sharp = mean(r['sharp'] for r in win); exp = mean(r['exp'] for r in win)
    luma = median(r['luma'] for r in win)
    fc = sorted(r['faces'] for r in win); faces = fc[int(len(fc) * 0.75)] if fc else 0
    q = (W_LOOKS * looks + W_MOTION * motion + W_STEADY * steady + W_SHARP * sharp) * (0.7 + 0.3 * exp)
    if faces and sum(r['ffocus'] for r in win) >= 0.5 * sum(r['faces'] for r in win):
        q += 0.03
    # what makes a clip worse to watch
    short = dur < 3 and 0.35 or dur < 5 and 0.75 or dur < 8 and 0.9 or 1.0
    static = motion < 0.08 and 0.65 or motion < 0.18 and 0.85 or 1.0
    shaky = steady < 0.25 and 0.6 or steady < 0.45 and 0.8 or 1.0
    dark = luma < 0.06 and 0.4 or 1.0
    score = round(100 * clip(q * short * static * shaky * dark), 1)

    hide = []
    if dur < 3: hide.append(f'under 3 seconds')
    if luma < 0.06: hide.append('almost black')
    if steady < 0.15: hide.append('very shaky')
    if motion < 0.03 or (motion < 0.05 and dur < 15): hide.append('nothing happens')
    if score < 18 and not hide: hide.append('low score')

    bits = []
    bits.append('very steady' if steady >= 0.85 else 'steady' if steady >= 0.65 else 'a little shaky' if steady >= 0.45
                else 'shaky' if steady >= 0.2 else 'very shaky')
    bits.append('lots going on' if motion >= 0.6 else 'some action' if motion >= 0.3 else 'a little movement' if motion >= 0.1
                else 'nothing much happens')
    if act < 0.15 and motion >= 0.15:
        bits[-1] = 'camera moves, little action' if motion < 0.3 else 'moving camera'
    bits.append('great looking' if aes >= 5.1 else 'good looking' if aes >= 4.75 else 'plain looking' if aes >= 4.35 else 'dull looking')
    if luma < 0.06: bits.append('almost black')
    elif exp < 0.6: bits.append('too dark' if luma < 0.3 else 'too bright' if luma > 0.6 else 'odd exposure')
    if sharp < 0.3: bits.append('blurry')
    if faces: bits.append('a face in it' if faces == 1 else f'{faces} faces')
    if dur < 3: bits.append(f'very short ({dur:.1f} s)')
    elif dur < 8: bits.append(f'short ({dur:.0f} s)')
    if ln < dur - 0.5 and st >= 1: bits.append(f'best 30 s starts at {fmt_t(st)}')
    why = ', '.join(bits)
    why = why[0].upper() + why[1:]
    if hide: why = 'Kept off the TV: ' + ', '.join(hide) + '. ' + why

    step = max(1, math.ceil(len(rows) / 60))
    frames = [[round(r['t'], 1), round(r['aes'], 2)] for r in rows[::step]]
    return dict(score=score, looks=round(aes, 2), motion=round(100 * motion), steady=round(100 * steady),
                sharp=round(100 * sharp), exposure=round(100 * exp), faces=int(faces), duration=round(dur, 2),
                best={'start_s': st, 'length_s': ln}, why=why, auto_hide=bool(hide), frames=frames)


# ======================================================================= main
def main():
    if '--gpu' in sys.argv:
        return gpu_stage(sys.argv[sys.argv.index('--gpu') + 1])
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import real_videos as rv
    cache = f'{T}/real/clipcache'; jobs = f'{cache}/jobs'
    os.makedirs(jobs, exist_ok=True)
    for f in os.listdir(jobs):                                # leftovers of a run that was stopped
        os.remove(os.path.join(jobs, f))
    clips = [it for it in json.load(open(f'{T}/real/items.json')) if it['kind'] == 'video']
    only = set(filter(None, os.environ.get('CLIPS_ONLY', '').split(',')))   # for testing a few
    if only:
        clips = [it for it in clips if it['id'] in only]

    def fresh(it):
        src = f"{T}/{it['rel']}"
        try:
            c = json.load(open(f"{cache}/{it['id']}.json"))
            return (c.get('v') == RAWV and c.get('src_size') == os.path.getsize(src)
                    and c.get('src_mtime') == int(os.path.getmtime(src)))
        except (OSError, ValueError):
            return False

    todo = [] if '--rescore' in sys.argv else [it for it in clips if os.path.exists(f"{T}/{it['rel']}") and not fresh(it)]
    print(f'{len(clips)} clips, {len(todo)} to measure', flush=True)
    if todo:
        done = [0]

        def one(it):
            try:
                extract(it, rv.probe(f"{T}/{it['rel']}", it['id']), jobs, cache)
            except Exception as e:                       # one bad file must not stop the rest
                print('clip failed', it['id'], e, flush=True)
            done[0] += 1; progress(done[0], 2 * len(todo))

        # decode in small batches so the raw frames never take much disk
        for i in range(0, len(todo), 12):
            batch = todo[i:i + 12]
            with ThreadPoolExecutor(4) as ex:
                list(ex.map(one, batch))
            uid = os.getuid(); gid = os.getgid()
            if tvconf.IN_DOCKER:                          # the TV Frame image has the scorer's models and libraries
                r = subprocess.run([sys.executable, os.path.abspath(__file__), '--gpu', jobs],
                                   env=dict(os.environ, CLIPS_TOTAL=str(2 * len(todo)), CLIPS_BASE=str(len(todo) + i)))
            else:
                r = subprocess.run(['docker', 'run', '--rm', *tvconf.DOCKER_GPU,
                                    '-u', f'{uid}:{gid}', '-e', 'HOME=/tmp', '-w', '/work',
                                    '-e', f'CLIPS_TOTAL={2 * len(todo)}', '-e', f'CLIPS_BASE={len(todo) + i}',
                                    '-v', f'{T}/curate:/work:ro', '-v', f'{T}/tools:/tools:ro', '-v', f'{jobs}:/jobs',
                                    '-v', f'{cache}:/cache-clips', '-v', f"{os.path.expanduser('~/.cache')}:/cache",
                                    'tvframe-curate:local', 'python', '/tools/clip_score.py', '--gpu', '/jobs'])
            if r.returncode != 0:
                print('clip scoring container failed', r.returncode, flush=True)
            for f in os.listdir(jobs):
                os.remove(os.path.join(jobs, f))

    out = {}
    for it in clips:
        try:
            raw = json.load(open(f"{cache}/{it['id']}.json"))
        except (OSError, ValueError):
            continue
        out[it['id']] = score_clip(raw)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    res = {'generated': datetime.now(ZoneInfo(tvconf.TZ)).isoformat(timespec='seconds'), 'clips': out}
    p = f'{T}/real/clip_scores.json'
    json.dump(res, open(p + '.tmp', 'w'), indent=1)
    os.replace(p + '.tmp', p)
    hidden = sum(1 for c in out.values() if c['auto_hide'])
    print(f'{len(out)} clips scored, {hidden} kept off the TV', flush=True)


if __name__ == '__main__':
    main()
