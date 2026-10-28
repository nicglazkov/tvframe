#!/usr/bin/env python3
"""Download a public Google Photos shared album to a folder, and keep it in sync.

Usage:
    gpalbum.py <album-link> <out-dir> [--delay 1.0] [--no-video] [--list-only]

<album-link> is a share link: https://photos.app.goo.gl/... or
https://photos.google.com/share/<id>?key=<key>. No login or cookies are used.

What it does:
  1. Opens the share page and reads the first page of items from the embedded data.
  2. Asks for the rest with the page's own "next page" call (snAcKc, ~300 items each).
  3. Downloads each new item: photos with "=d" (the original file, EXIF kept),
     videos with "=dv" (the original video file).
  4. Keeps <out-dir>/manifest.json. Re-runs only fetch new items. Items gone from
     the album are marked removed and their files moved to <out-dir>/_removed/.

Python standard library only.
"""
import argparse, hashlib, json, os, random, re, shutil, sys, time
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
PAGE_RPC = "snAcKc"
VIDEO_KEY = "76647426"      # present in an item's extras dict only for videos
MOTION_KEY = "146008172"    # present for motion photos (still + short clip)
MANIFEST = "manifest.json"
EXIT_BAD_LINK, EXIT_NETWORK, EXIT_SUSPICIOUS = 2, 3, 4


class BadLink(Exception):
    pass


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ http
def http(url, data=None, headers=None, retries=4, timeout=60, stream_to=None):
    """GET/POST with retries on 429, 5xx and network errors.
    Returns (final_url, headers, body_bytes) or, with stream_to, writes the body
    to that path and returns (final_url, headers, bytes_written)."""
    h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"}
    h.update(headers or {})
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if stream_to is None:
                    return r.geturl(), r.headers, r.read()
                n = 0
                with open(stream_to, "wb") as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        n += len(chunk)
                cl = r.headers.get("Content-Length")
                if cl and int(cl) != n:
                    raise IOError(f"short read: {n} of {cl} bytes")
                return r.geturl(), r.headers, n
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = delay * (2 ** attempt) + random.random()
                ra = e.headers.get("Retry-After") if e.headers else None
                if ra and ra.isdigit():
                    wait = max(wait, int(ra))
                log(f"  HTTP {e.code}, retrying in {wait:.0f}s")
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, IOError) as e:
            if attempt < retries:
                wait = delay * (2 ** attempt) + random.random()
                log(f"  {type(e).__name__}: {e}, retrying in {wait:.0f}s")
                time.sleep(wait)
                continue
            raise


# ------------------------------------------------------------------ parsing
CALLBACK_RE = re.compile(r"AF_initDataCallback\(\{key: '(ds:\d+)'.*?data:(.*?), sideChannel: \{\}\}\);</script>", re.S)


def find_album_data(html):
    """Return the embedded data block that describes the album:
    [?, items|None, next_token, [album_id, title, [times...], ...], ...]."""
    for m in CALLBACK_RE.finditer(html):
        try:
            d = json.loads(m.group(2))
        except ValueError:
            continue
        if (isinstance(d, list) and len(d) >= 4 and isinstance(d[3], list) and d[3]
                and isinstance(d[3][0], str) and d[3][0].startswith("AF1Qip")):
            return d
    return None


def wiz(html, key):
    m = re.search(r'"%s":"([^"]*)"' % key, html)
    return m.group(1) if m else None


def ms_to_iso(ms, tz_ms=None):
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    tz = timezone(timedelta(milliseconds=tz_ms)) if isinstance(tz_ms, (int, float)) else timezone.utc
    return datetime.fromtimestamp(ms / 1000, tz).isoformat(timespec="seconds")


def parse_item(it):
    """One media item from the album data. Layout (observed 2026-10):
    [id, [base_url, width, height, ...], capture_ms, dedup_key, tz_offset_ms,
     added_ms, [owner_id], [...], ?, {extras}]"""
    extras = it[-1] if isinstance(it[-1], dict) else {}
    media = it[1]
    rec = {
        "id": it[0],
        "base_url": media[0],
        "width": media[1],
        "height": media[2],
        "type": "video" if VIDEO_KEY in extras else "photo",
        "capture_time": ms_to_iso(it[2], it[4] if len(it) > 4 else None),
        "capture_ms": it[2] if isinstance(it[2], (int, float)) else None,
        "added_time": ms_to_iso(it[5]) if len(it) > 5 else None,
    }
    if rec["type"] == "video":
        v = extras.get(VIDEO_KEY) or []
        rec["duration_s"] = round(v[0] / 1000, 2) if v and isinstance(v[0], (int, float)) else None
    if MOTION_KEY in extras:
        rec["motion_photo"] = True
    return rec


# ------------------------------------------------------------------ listing
def resolve(link):
    p = urllib.parse.urlparse(link.strip())
    if p.scheme not in ("http", "https") or p.netloc not in ("photos.app.goo.gl", "goo.gl", "photos.google.com"):
        raise BadLink(f"not a Google Photos share link: {link}")
    try:
        final, _, body = http(link, retries=2)
    except urllib.error.HTTPError as e:
        raise BadLink(f"link did not open (HTTP {e.code}). It may be deleted or mistyped: {link}")
    fp = urllib.parse.urlparse(final)
    if fp.netloc != "photos.google.com" or not fp.path.startswith("/share/"):
        raise BadLink(f"link did not lead to a shared album (ended at {fp.netloc}{fp.path}). "
                      "Sharing may be turned off for it.")
    html = body.decode("utf-8", "replace")
    data = find_album_data(html)
    if data is None:
        raise BadLink("the page opened but has no album data. The link may be for a single photo, "
                      "or Google changed the page layout.")
    key = urllib.parse.parse_qs(fp.query).get("key", [None])[0]
    return {
        "final_url": final,
        "album_id": data[3][0],
        "title": data[3][1] if len(data[3]) > 1 and isinstance(data[3][1], str) else "",
        "key": key,
        "source_path": fp.path,
        "sid": wiz(html, "FdrFJe"),
        "bl": wiz(html, "cfb2h"),
        "expected_count": data[3][21] if len(data[3]) > 21 and isinstance(data[3][21], int) else None,
        "first_items": data[1] or [],
        "next_token": data[2] or None,
    }


def fetch_page(album, token, reqid):
    inner = json.dumps([album["album_id"], token, None, album["key"]])
    freq = json.dumps([[[PAGE_RPC, inner, None, "generic"]]])
    q = {"rpcids": PAGE_RPC, "source-path": album["source_path"], "hl": "en",
         "_reqid": str(reqid), "rt": "c"}
    if album["sid"]:
        q["f.sid"] = album["sid"]
    if album["bl"]:
        q["bl"] = album["bl"]
    url = "https://photos.google.com/_/PhotosUi/data/batchexecute?" + urllib.parse.urlencode(q)
    body = urllib.parse.urlencode({"f.req": freq}).encode()
    _, _, raw = http(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Origin": "https://photos.google.com", "Referer": album["final_url"]})
    text = raw.decode("utf-8", "replace")
    # Response is chunked: ")]}'" then <length>\n<json> blocks. Find the wrb.fr row for our rpc.
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("[["):
            continue
        try:
            rows = json.loads(line)
        except ValueError:
            continue
        for row in rows:
            if isinstance(row, list) and len(row) > 2 and row[0] == "wrb.fr" and row[1] == PAGE_RPC:
                if not row[2]:
                    raise RuntimeError(f"page request returned no data (row: {json.dumps(row)[:200]})")
                d = json.loads(row[2])
                return d[1] or [], d[2] or None
    raise RuntimeError("could not read the next-page response; Google may have changed it")


def list_all(album, page_delay=1.0, max_pages=1000):
    items = list(album["first_items"])
    token, pages, reqid = album["next_token"], 1, 100001
    seen_tokens = set()
    while token and pages < max_pages:
        if token in seen_tokens:
            log("  page token repeated, stopping")
            break
        seen_tokens.add(token)
        time.sleep(page_delay)
        batch, token = fetch_page(album, token, reqid)
        reqid += 100000
        pages += 1
        items.extend(batch)
        log(f"  page {pages}: +{len(batch)} items ({len(items)} so far)")
    recs, ids = [], set()
    for it in items:
        try:
            r = parse_item(it)
        except (IndexError, TypeError):
            log(f"  skipping an item I could not read: {json.dumps(it)[:120]}")
            continue
        if r["id"] in ids:
            continue
        ids.add(r["id"])
        recs.append(r)
    return recs, pages


# ------------------------------------------------------------------ downloading
SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def filename_from(headers, rec):
    cd = headers.get("Content-Disposition", "") or ""
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", cd)
    orig = urllib.parse.unquote(m.group(1)) if m else ""
    stem, ext = os.path.splitext(orig)
    if not ext:
        ct = (headers.get("Content-Type") or "").split(";")[0].strip()
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/heic": ".heic", "image/heif": ".heif",
               "image/webp": ".webp", "image/gif": ".gif", "video/mp4": ".mp4",
               "video/quicktime": ".mov"}.get(ct, ".bin")
    stem = SAFE.sub("_", stem).strip("._") or "item"
    # the id tail keeps names unique when two items share an original name
    return f"{stem[:80]}__{rec['id'][-10:]}{ext.lower()}", orig


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(rec, out_dir):
    suffix = "=dv" if rec["type"] == "video" else "=d"
    tmp = os.path.join(out_dir, f".part-{rec['id'][-16:]}")
    try:
        final, headers, n = http(rec["base_url"] + suffix, stream_to=tmp, timeout=300)
        ct = (headers.get("Content-Type") or "").split(";")[0].strip()
        want = "video/" if rec["type"] == "video" else "image/"
        if not ct.startswith(want):
            raise RuntimeError(f"expected {want}*, got {ct or 'no type'} ({n} bytes)"
                               + (" - the video may still be processing" if rec["type"] == "video" else ""))
        name, orig = filename_from(headers, rec)
        path = os.path.join(out_dir, name)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if rec.get("capture_ms"):
        t = rec["capture_ms"] / 1000
        os.utime(path, (t, t))
    return {"filename": name, "original_name": orig, "bytes": n, "content_type": ct, "sha256": sha256(path)}


# ------------------------------------------------------------------ manifest
def load_manifest(out_dir):
    p = os.path.join(out_dir, MANIFEST)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"items": {}}


def save_manifest(out_dir, man):
    p = os.path.join(out_dir, MANIFEST)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(man, f, indent=1, sort_keys=True)
    os.replace(tmp, p)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Download and sync a public Google Photos shared album.")
    ap.add_argument("link")
    ap.add_argument("out_dir")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between downloads (default 1)")
    ap.add_argument("--no-video", action="store_true", help="skip videos")
    ap.add_argument("--list-only", action="store_true", help="list items as JSON on stdout, download nothing")
    ap.add_argument("--max-pages", type=int, default=1000)
    ap.add_argument("--allow-empty", action="store_true",
                    help="accept an empty listing even if the manifest had items (normally refused as a likely error)")
    a = ap.parse_args()

    t0 = time.time()
    try:
        album = resolve(a.link)
        log(f"album: {album['title'] or '(untitled)'}  id {album['album_id'][:16]}...")
        recs, pages = list_all(album, page_delay=a.delay, max_pages=a.max_pages)
    except BadLink as e:
        log(f"error: {e}")
        return EXIT_BAD_LINK
    except (urllib.error.URLError, RuntimeError, OSError) as e:
        log(f"error while listing the album: {e}")
        return EXIT_NETWORK
    n_vid = sum(r["type"] == "video" for r in recs)
    log(f"listed {len(recs)} items ({len(recs) - n_vid} photos, {n_vid} videos) in {pages} page(s)")
    exp = album["expected_count"]
    short = exp is not None and len(recs) < exp
    if short:
        log(f"warning: the album says it has {exp} items but only {len(recs)} were listed. "
            "Nothing will be marked removed this run.")

    if a.list_only:
        json.dump({"title": album["title"], "album_id": album["album_id"], "pages": pages,
                   "expected_count": exp,
                   "items": recs}, sys.stdout, indent=1)
        print()
        return 0

    os.makedirs(a.out_dir, exist_ok=True)
    man = load_manifest(a.out_dir)
    items = man.setdefault("items", {})
    if man.get("album_id") and man["album_id"] != album["album_id"]:
        log(f"error: {a.out_dir} holds a different album ({man.get('title') or man['album_id'][:16]}). "
            "Use a separate folder for each album.")
        return EXIT_BAD_LINK
    live_before = sum(1 for v in items.values() if v.get("status") == "present")
    if not recs and live_before and not a.allow_empty:
        log(f"error: the album now lists 0 items but {live_before} were here last time. "
            "Not marking them removed. Re-run with --allow-empty if the album really is empty.")
        return EXIT_SUSPICIOUS
    man.update({"link": a.link, "album_id": album["album_id"], "title": album["title"],
                "expected_count": exp, "listed_count": len(recs), "last_sync": now_iso()})

    current = {r["id"] for r in recs}
    stats = {"new": 0, "kept": 0, "failed": 0, "removed": 0, "skipped_video": 0, "restored": 0}

    # removed items: mark and move the file out of the folder the curator reads
    for iid, v in items.items():
        if short:
            break
        if iid not in current and v.get("status") in ("present", "failed"):
            v["status"], v["removed_at"] = "removed", now_iso()
            f = v.get("filename")
            if f and os.path.exists(os.path.join(a.out_dir, f)):
                os.makedirs(os.path.join(a.out_dir, "_removed"), exist_ok=True)
                shutil.move(os.path.join(a.out_dir, f), os.path.join(a.out_dir, "_removed", f))
            stats["removed"] += 1

    first = True
    for r in recs:
        old = items.get(r["id"], {})
        if r["type"] == "video" and a.no_video:
            stats["skipped_video"] += 1
            continue
        have = old.get("filename") and os.path.exists(os.path.join(a.out_dir, old["filename"]))
        if old.get("status") == "removed" and not have and old.get("filename"):
            # came back into the album: restore from _removed if we still have it
            src = os.path.join(a.out_dir, "_removed", old["filename"])
            if os.path.exists(src):
                shutil.move(src, os.path.join(a.out_dir, old["filename"]))
                have = True
                stats["restored"] += 1
        meta = {k: r[k] for k in r if k != "capture_ms"}
        if have:
            old.update(meta)
            old["status"], old["last_seen"] = "present", now_iso()
            old.pop("removed_at", None)
            items[r["id"]] = old
            stats["kept"] += 1
            continue
        if not first:
            time.sleep(a.delay)
        first = False
        try:
            info = download(r, a.out_dir)
        except Exception as e:  # keep going; it will be retried next run
            log(f"  failed {r['type']} {r['id'][-10:]}: {e}")
            old.update(meta)
            old.update({"status": "failed", "error": str(e)[:300], "last_seen": now_iso()})
            items[r["id"]] = old
            stats["failed"] += 1
            continue
        old.update(meta)
        old.update(info)
        old.update({"status": "present", "last_seen": now_iso()})
        old.setdefault("first_seen", now_iso())
        old.pop("error", None)
        old.pop("removed_at", None)
        items[r["id"]] = old
        stats["new"] += 1
        log(f"  got {r['type']} {info['filename']} ({info['bytes'] / 1e6:.1f} MB)")
        if stats["new"] % 20 == 0:
            save_manifest(a.out_dir, man)

    man["last_stats"] = stats
    save_manifest(a.out_dir, man)
    log(f"done in {time.time() - t0:.1f}s: {stats['new']} new, {stats['kept']} already here, "
        f"{stats['removed']} removed, {stats['failed']} failed"
        + (f", {stats['skipped_video']} videos skipped" if stats["skipped_video"] else ""))
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
