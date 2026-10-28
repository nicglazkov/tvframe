#!/usr/bin/env python3
"""TV Frame photo curation.

Scores every photo in one or more folders, groups near-duplicates, and writes
out/scores.json with a 0..100 show weight per photo. Then runs the slideshow
sampler for a simulated evening and writes out/sim.json.

Usage (inside the tvframe-curate container, see run.sh):
    python curate.py /photos/raw /photos/dups --out /work/out --meta /work/sample_meta.json

Re-runnable: point it at any folder of JPEG/PNG/HEIC-free images.
"""
import argparse, glob, hashlib, json, math, os, sys, time
from datetime import date, datetime

import cv2
import numpy as np
from PIL import Image, ImageOps, ExifTags

import sampler

MODELS = os.environ.get("TVF_MODELS", "/work/models")
NORM_LONG = 1024          # all technical metrics run at this long side
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff")


# ---------------------------------------------------------------- loading
def load_image(path):
    im = Image.open(path)
    exif = im.getexif()
    date_taken = None
    try:
        sub = exif.get_ifd(0x8769)
        raw = sub.get(36867) or exif.get(306)
        if raw:
            date_taken = datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S").date().isoformat()
    except Exception:
        date_taken = None
    im = ImageOps.exif_transpose(im).convert("RGB")
    return im, date_taken


def resize_long(im, long_side):
    w, h = im.size
    s = long_side / max(w, h)
    if s >= 1:
        return im.copy()
    return im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)


# ---------------------------------------------------------------- technical
def sharpness(gray):
    """Laplacian variance at the normalized size.
    Global value plus the mean of the sharpest 15% of tiles, so a sharp subject
    on a soft background still counts as sharp."""
    lap = cv2.Laplacian(gray, cv2.CV_64F, ksize=3)
    g = float(lap.var())
    h, w = gray.shape
    ty, tx = 8, 8
    tiles = []
    for i in range(ty):
        for j in range(tx):
            t = lap[i * h // ty:(i + 1) * h // ty, j * w // tx:(j + 1) * w // tx]
            tiles.append(t.var())
    tiles = np.sort(np.array(tiles))[::-1]
    k = max(1, int(len(tiles) * 0.15))
    peak = float(tiles[:k].mean())
    return g, peak


def sharp_score(peak):
    # log scale at 1024 px long side: ~50 is clearly blurred, ~5000+ is crisp
    return float(np.clip((math.log10(max(peak, 1)) - 1.7) / (3.7 - 1.7), 0, 1))


def exposure(gray):
    mean = float(gray.mean()) / 255
    hi = float((gray >= 250).mean())
    lo = float((gray <= 5).mean())
    # penalize clipping beyond a little, and very dark or very bright averages
    pen = 0.0
    pen += max(0, hi - 0.01) * 4
    pen += max(0, lo - 0.02) * 3
    pen += max(0, abs(mean - 0.45) - 0.25) * 2
    return mean, hi, lo, float(np.clip(1 - pen, 0, 1))


def noise_sigma(gray_native):
    """Immerkaer (1996) noise estimate on a native-resolution center crop, measured only on
    the flattest 40% of pixels so fine texture (grass, gravel) is not mistaken for noise."""
    h, w = gray_native.shape
    c = 768
    y0, x0 = max(0, h // 2 - c // 2), max(0, w // 2 - c // 2)
    g = gray_native[y0:y0 + c, x0:x0 + c].astype(np.float64)
    M = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float64)
    conv = np.abs(cv2.filter2D(g, -1, M))[2:-2, 2:-2]
    gx = cv2.Sobel(g, cv2.CV_64F, 1, 0, ksize=3)[2:-2, 2:-2]
    gy = cv2.Sobel(g, cv2.CV_64F, 0, 1, ksize=3)[2:-2, 2:-2]
    mag = np.hypot(gx, gy)
    mask = mag <= np.quantile(mag, 0.4)
    if mask.sum() < 1000:
        mask = np.ones_like(mag, dtype=bool)
    return float(conv[mask].mean() * math.sqrt(0.5 * math.pi) / 6)


def noise_score(sigma):
    # sigma < 2 is clean, > 8 is visibly noisy
    return float(np.clip(1 - (sigma - 2) / 6, 0, 1))


# ---------------------------------------------------------------- palette
def palette(im_small, k=5):
    a = np.asarray(im_small.resize((96, 96), Image.BILINEAR), dtype=np.uint8)
    lab = cv2.cvtColor(a, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    cv2.setRNGSeed(7)
    _, labels, centers = cv2.kmeans(lab, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=k) / len(labels)
    order = np.argsort(-counts)
    rgb = cv2.cvtColor(centers[order].reshape(1, -1, 3).astype(np.uint8), cv2.COLOR_LAB2RGB).reshape(-1, 3)
    hexes = ["#%02x%02x%02x" % tuple(int(v) for v in c) for c in rgb]
    shares = [round(float(counts[i]), 3) for i in order]
    # matting / background color: the most common color, darkened and desaturated a bit
    L, A, B = centers[order[0]]
    mat = np.array([[[min(L, 60.0), 128 + (A - 128) * 0.6, 128 + (B - 128) * 0.6]]], dtype=np.uint8)
    m = cv2.cvtColor(mat, cv2.COLOR_LAB2RGB)[0, 0]
    return hexes, shares, "#%02x%02x%02x" % tuple(int(v) for v in m)


# ---------------------------------------------------------------- saliency / focal point
def spectral_residual(gray):
    """Hou & Zhang 2007 spectral residual saliency at 64 px."""
    g = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA).astype(np.float32)
    f = np.fft.fft2(g)
    logamp = np.log(np.abs(f) + 1e-8)
    phase = np.angle(f)
    resid = logamp - cv2.blur(logamp, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(resid + 1j * phase))) ** 2
    sal = cv2.GaussianBlur(sal.astype(np.float32), (9, 9), 2.5)
    return sal / (sal.max() + 1e-8)


def focal_from_saliency(sal):
    h, w = sal.shape
    thr = np.quantile(sal, 0.95)
    m = np.where(sal >= thr, sal, 0)
    ys, xs = np.mgrid[0:h, 0:w]
    tot = m.sum() + 1e-8
    x = float((m * (xs + 0.5)).sum() / tot / w)
    y = float((m * (ys + 0.5)).sum() / tot / h)
    # pull 30% toward center: spectral residual likes edges and texture
    return 0.7 * x + 0.15, 0.7 * y + 0.15


def crop_box(fx, fy, img_w, img_h, aspect=16 / 9):
    """Largest box of the given aspect that fits, centered on the focal point and clamped."""
    if img_w / img_h > aspect:
        ch, cw = img_h, img_h * aspect
    else:
        cw, ch = img_w, img_w / aspect
    x = min(max(fx * img_w - cw / 2, 0), img_w - cw)
    y = min(max(fy * img_h - ch / 2, 0), img_h - ch)
    return [round(x / img_w, 4), round(y / img_h, 4), round(cw / img_w, 4), round(ch / img_h, 4)]


# ---------------------------------------------------------------- faces
class Faces:
    def __init__(self):
        p = os.path.join(MODELS, "face_detection_yunet_2023mar.onnx")
        self.det = cv2.FaceDetectorYN.create(p, "", (320, 320), 0.8, 0.3, 50)

    def __call__(self, bgr, gray):
        h, w = bgr.shape[:2]
        self.det.setInputSize((w, h))
        _, res = self.det.detect(bgr)
        faces = []
        if res is None:
            return faces
        for r in res:
            x, y, fw, fh, score = float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[14])
            if fw * fh < (0.0004 * w * h):     # ignore tiny background faces
                continue
            x0, y0 = int(max(0, x)), int(max(0, y))
            x1, y1 = int(min(w, x + fw)), int(min(h, y + fh))
            crop = gray[y0:y1, x0:x1]
            if crop.size < 64:
                continue
            crop = cv2.resize(crop, (128, 128), interpolation=cv2.INTER_AREA)
            fs = float(cv2.Laplacian(crop, cv2.CV_64F).var())
            faces.append(dict(box=[round(x / w, 4), round(y / h, 4), round(fw / w, 4), round(fh / h, 4)],
                              conf=round(score, 3), sharpness=round(fs, 1), in_focus=bool(fs >= 60)))
        return faces


# ---------------------------------------------------------------- aesthetics
class Aesthetic:
    """CLIP ViT-L/14 (OpenAI weights via open_clip) + LAION improved aesthetic predictor
    (sac+logos+ava1-l14-linearMSE). One forward pass gives both the embedding used for
    near-duplicate grouping and the aesthetic score (roughly 1..10, most photos 4..7)."""

    def __init__(self, device):
        import torch, open_clip
        self.torch = torch
        self.device = device
        self.model, _, self.prep = open_clip.create_model_and_transforms(
            "ViT-L-14", pretrained="openai", device=device,
            cache_dir=os.path.join(MODELS, "open_clip"))
        self.model.eval()
        if device == "cuda":
            self.model = self.model.half()
        nn = torch.nn
        self.head = nn.Sequential(
            nn.Linear(768, 1024), nn.Dropout(0.2), nn.Linear(1024, 128), nn.Dropout(0.2),
            nn.Linear(128, 64), nn.Dropout(0.1), nn.Linear(64, 16), nn.Linear(16, 1))
        sd = torch.load(os.path.join(MODELS, "sac+logos+ava1-l14-linearMSE.pth"), map_location="cpu")
        sd = {k.replace("layers.", ""): v for k, v in sd.items()}
        self.head.load_state_dict(sd)
        self.head.to(device).eval()

    def __call__(self, im):
        torch = self.torch
        with torch.no_grad():
            x = self.prep(im).unsqueeze(0).to(self.device)
            if self.device == "cuda":
                x = x.half()
            e = self.model.encode_image(x).float()
            e = e / e.norm(dim=-1, keepdim=True)
            a = float(self.head(e).item())
        return e[0].cpu().numpy(), a


# ---------------------------------------------------------------- clustering
def thumb_vec(gray):
    """32x32 zero-mean unit-length thumbnail: layout and tones, blind to fine detail."""
    t = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float64).ravel()
    t -= t.mean()
    return t / (np.linalg.norm(t) + 1e-8)


def cluster(embs, thumbs, thr, thr_loose, thumb_thr):
    """Grouping of near-duplicates. Two photos match when their CLIP cosine is >= thr,
    or when CLIP is >= thr_loose AND their tiny thumbnails line up (thumb corr >= thumb_thr).
    The second test catches blurred or re-exposed copies without merging two different
    shots of the same scene. Groups merge strongest match first, and only while every
    photo in the merged group is still at least thr_loose from every other one, so a
    chain of look-alikes (A like B like C) can't pull unrelated landscapes together."""
    n = len(embs)
    E = np.stack(embs)
    S = E @ E.T
    T = np.stack(thumbs)
    C = T @ T.T
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    members = {i: [i] for i in range(n)}
    ii, jj = np.triu_indices(n, 1)
    ok = (S[ii, jj] >= thr) | ((S[ii, jj] >= thr_loose) & (C[ii, jj] >= thumb_thr))
    ii, jj = ii[ok], jj[ok]
    for k in np.argsort(-S[ii, jj], kind="stable"):
        a, b = find(ii[k]), find(jj[k])
        if a == b or S[np.ix_(members[a], members[b])].min() < thr_loose:
            continue
        parent[a] = b
        members[b] += members.pop(a)
    roots = {}
    lab = []
    for i in range(n):
        r = find(i)
        roots.setdefault(r, len(roots))
        lab.append(roots[r])
    return lab, S, C


# ---------------------------------------------------------------- weight + reason
def aesthetic_norm(a):
    return float(np.clip((a - 4.0) / 3.0, 0, 1))      # 4 -> 0, 7 -> 1


def quality(rec):
    q = (0.55 * rec["aesthetic_norm"] + 0.25 * rec["sharpness_score"]
         + 0.12 * rec["exposure_score"] + 0.08 * rec["noise_score"])
    nf = rec["faces"]["count"]
    if nf:
        q += 0.05 if rec["faces"]["all_in_focus"] else -0.10
    return float(np.clip(q, 0, 1))


def reason(rec, cluster_size):
    bits = []
    s = rec["sharpness_score"]
    bits.append("very sharp" if s > 0.8 else "sharp" if s > 0.55 else "a bit soft" if s > 0.3 else "blurry")
    a = rec["aesthetic"]
    bits.append("strong aesthetic" if a >= 6.3 else "good aesthetic" if a >= 5.6 else "plain aesthetic" if a >= 4.8 else "weak aesthetic")
    if rec["exposure_score"] < 0.7:
        if rec["clip_highlights"] > 0.03:
            bits.append("blown highlights")
        elif rec["clip_shadows"] > 0.05:
            bits.append("crushed shadows")
        else:
            bits.append("off exposure")
    if rec["noise_score"] < 0.6:
        bits.append("noisy")
    nf = rec["faces"]["count"]
    if nf:
        bits.append(f"{nf} face{'s' if nf > 1 else ''}" + (" in focus" if rec["faces"]["all_in_focus"] else ", not all in focus"))
    if cluster_size > 1:
        bits.append(f"best of {cluster_size} near-duplicates" if rec["is_rep"] else f"near-duplicate of {rec['rep_id']}, hidden")
    return ", ".join(bits)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Score and curate a photo folder for TV Frame.")
    ap.add_argument("dirs", nargs="+", help="photo folders")
    ap.add_argument("--out", default="out")
    ap.add_argument("--meta", help="optional JSON: {id: {favorite: bool, date: 'YYYY-MM-DD'}}")
    ap.add_argument("--dup-threshold", type=float, default=0.95, help="CLIP cosine that alone means near-duplicate")
    ap.add_argument("--dup-loose", type=float, default=0.88, help="lower CLIP cosine allowed when thumbnails also match")
    ap.add_argument("--thumb-threshold", type=float, default=0.85, help="32x32 thumbnail correlation for the loose test")
    ap.add_argument("--today", default=date.today().isoformat())
    ap.add_argument("--sim-hours", type=float, default=15)
    ap.add_argument("--slide-seconds", type=float, default=20)
    ap.add_argument("--cpu", action="store_true", help="force CPU")
    args = ap.parse_args()

    import torch
    device = "cuda" if (torch.cuda.is_available() and not args.cpu) else "cpu"
    print(f"device: {device}" + (f" ({torch.cuda.get_device_name(0)})" if device == "cuda" else ""), flush=True)

    files = []
    for d in args.dirs:
        files += sorted(p for p in glob.glob(os.path.join(d, "*")) if p.lower().endswith(IMG_EXT))
    meta = json.load(open(args.meta)) if args.meta and os.path.exists(args.meta) else {}

    t0 = time.time()
    aes = Aesthetic(device)
    faces = Faces()
    t_load = time.time() - t0
    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()

    recs, embs, thumbs, timings = [], [], [], []
    for p in files:
        pid = os.path.splitext(os.path.basename(p))[0]
        tt = {}
        t = time.time()
        im, exif_date = load_image(p)
        W, H = im.size
        small = resize_long(im, NORM_LONG)
        tt["decode"] = time.time() - t

        t = time.time()
        gray = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2GRAY)
        gnat = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2GRAY)
        g_var, peak = sharpness(gray)
        mean, hi, lo, exp_s = exposure(gray)
        sig = noise_sigma(gnat)
        hexes, shares, mat = palette(small)
        sal = spectral_residual(gray)
        tt["technical"] = time.time() - t

        t = time.time()
        fl = faces(cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2BGR), gray)
        tt["faces"] = time.time() - t

        t = time.time()
        emb, a = aes(small)
        if device == "cuda":
            torch.cuda.synchronize()
        tt["clip_aesthetic"] = time.time() - t

        if fl:
            # faces win: weight each face by area, then nudge slightly toward saliency
            ws = np.array([f["box"][2] * f["box"][3] for f in fl])
            fx = float(sum(w * (f["box"][0] + f["box"][2] / 2) for w, f in zip(ws, fl)) / ws.sum())
            fy = float(sum(w * (f["box"][1] + f["box"][3] * 0.45) for w, f in zip(ws, fl)) / ws.sum())
            focal_src = "faces"
        else:
            fx, fy = focal_from_saliency(sal)
            focal_src = "saliency"

        m = meta.get(pid, {})
        rec = dict(
            id=pid, file=os.path.abspath(p), width=W, height=H,
            orientation="landscape" if W > H * 1.05 else "portrait" if H > W * 1.05 else "square",
            aspect=round(W / H, 4),
            date=exif_date or m.get("date"), date_source="exif" if exif_date else ("synthetic" if m.get("date") else None),
            favorite=bool(m.get("favorite", False)),
            sharpness_laplacian_var=round(g_var, 1), sharpness_peak_tiles=round(peak, 1),
            sharpness_score=round(sharp_score(peak), 3),
            mean_luma=round(mean, 3), clip_highlights=round(hi, 4), clip_shadows=round(lo, 4),
            exposure_score=round(exp_s, 3),
            noise_sigma=round(sig, 2), noise_score=round(noise_score(sig), 3),
            aesthetic=round(a, 3), aesthetic_norm=round(aesthetic_norm(a), 3),
            faces=dict(count=len(fl), all_in_focus=all(f["in_focus"] for f in fl) if fl else None, list=fl),
            palette=hexes, palette_share=shares, mat_color=mat,
            focal_point=[round(fx, 4), round(fy, 4)], focal_source=focal_src,
            crop_16x9=crop_box(fx, fy, W, H),
            timing_ms={k: round(v * 1000, 1) for k, v in tt.items()},
        )
        rec["quality"] = round(quality(rec), 4)
        recs.append(rec)
        embs.append(emb)
        thumbs.append(thumb_vec(gray))
        timings.append(sum(tt.values()))
        print(f"{pid:>22}  aes {a:5.2f}  sharp {rec['sharpness_score']:.2f}  exp {exp_s:.2f}  "
              f"noise {sig:4.1f}  faces {len(fl)}  {sum(tt.values())*1000:6.0f} ms", flush=True)

    gpu_peak = torch.cuda.max_memory_allocated() / 2**20 if device == "cuda" else 0
    gpu_reserved = torch.cuda.max_memory_reserved() / 2**20 if device == "cuda" else 0

    # near-duplicates
    labels, S, C = cluster(embs, thumbs, args.dup_threshold, args.dup_loose, args.thumb_threshold)
    groups = {}
    for r, l in zip(recs, labels):
        groups.setdefault(l, []).append(r)
    for l, members in groups.items():
        # best = highest quality; more pixels breaks near-ties (an original beats its crop)
        best = max(members, key=lambda r: r["quality"] + 0.02 * math.log2(r["width"] * r["height"]))
        for r in members:
            r["cluster_id"] = int(l)
            r["cluster_size"] = len(members)
            r["is_rep"] = r is best
            r["rep_id"] = best["id"]

    # show weight: quality curve, near-dupes pushed way down (not deleted)
    for r in recs:
        w = 100 * r["quality"] ** 1.3
        if not r["is_rep"]:
            w *= 0.05
        r["weight"] = round(float(w), 2)
        r["reason"] = reason(r, r["cluster_size"])

    # closest pairs for transparency (helps pick the threshold)
    n = len(recs)
    pairs = sorted(((float(S[i, j]), float(C[i, j]), recs[i]["id"], recs[j]["id"]) for i in range(n) for j in range(i + 1, n)), reverse=True)[:15]

    out_l = {r["id"]: r["cluster_id"] for r in recs}
    os.makedirs(args.out, exist_ok=True)
    out = dict(
        generated=datetime.now().isoformat(timespec="seconds"),
        device=device, gpu=torch.cuda.get_device_name(0) if device == "cuda" else None,
        models=dict(clip="ViT-L-14 openai (open_clip)", aesthetic="LAION improved aesthetic predictor sac+logos+ava1-l14-linearMSE",
                    faces="OpenCV YuNet 2023mar", saliency="spectral residual (Hou and Zhang 2007)"),
        dup_rule=dict(clip_cos=args.dup_threshold, or_clip_cos=args.dup_loose, and_thumb_corr=args.thumb_threshold),
        closest_pairs=[dict(clip_cos=round(c, 4), thumb_corr=round(t, 4), a=a, b=b,
                            same_cluster=out_l[a] == out_l[b]) for c, t, a, b in pairs],
        perf=dict(photos=n, model_load_s=round(t_load, 2), total_s=round(sum(timings), 2),
                  per_photo_ms_mean=round(1000 * float(np.mean(timings)), 1),
                  per_photo_ms_median=round(1000 * float(np.median(timings)), 1),
                  per_photo_ms_max=round(1000 * float(np.max(timings)), 1),
                  stage_ms_mean={k: round(float(np.mean([r["timing_ms"][k] for r in recs])), 1) for k in recs[0]["timing_ms"]},
                  gpu_peak_allocated_mib=round(gpu_peak, 1), gpu_peak_reserved_mib=round(gpu_reserved, 1)),
        photos={r["id"]: r for r in recs},
    )
    json.dump(out, open(os.path.join(args.out, "scores.json"), "w"), indent=1)
    np.save(os.path.join(args.out, "embeddings.npy"), np.stack(embs))
    print(f"wrote {os.path.join(args.out, 'scores.json')}  ({n} photos, {len(groups)} clusters)")
    print(json.dumps(out["perf"], indent=1))

    sim = sampler.simulate(list(out["photos"].values()), hours=args.sim_hours,
                           slide_s=args.slide_seconds, today=args.today)
    big = sampler.simulate(sampler.synthetic_library(), hours=args.sim_hours,
                           slide_s=args.slide_seconds, today=args.today)
    sim["synthetic_800_photo_library"] = dict(note="same sampler on a made-up 800 photo library with a wider spread of weights",
                                              settings=big["settings"], summary=big["summary"])
    json.dump(sim, open(os.path.join(args.out, "sim.json"), "w"), indent=1)
    print(json.dumps(sim["summary"], indent=1))


if __name__ == "__main__":
    main()
