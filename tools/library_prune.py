#!/usr/bin/env python3
"""Delete build outputs of photos and videos that left the library. real/library_ids.json lists every id built
from the library; ids in it that are no longer in real/items.json get photos/real/<id>.jpg (the scoring input),
site/media/p/<id>-<size>.*, server/cache-real/{frames,kb,hevc,hevc8}/<id>.* and photos/videos-real/{tv,web,posters}/<id>.*
removed, then the ledger is rewritten. Only this script writes the ledger, so a failed sync loses nothing.
site/media/p also holds the sample photos other pages use, which is why this goes by id and never by scanning."""
import glob, json, os, re

T = os.path.expanduser("~/tvframe")
LEDGER = f"{T}/real/library_ids.json"
now = {i["id"] for i in json.load(open(f"{T}/real/items.json"))}
try:
    known = set(json.load(open(LEDGER)))
except (OSError, ValueError):
    known = set()
removed = 0
for pid in known - now:
    e = glob.escape(pid)
    files = glob.glob(f"{T}/photos/real/{e}.jpg")
    files += [f for f in glob.glob(f"{T}/site/media/p/{e}-*")
              if re.fullmatch(re.escape(pid) + r"-(bg|4k|1920|1280|720)\.(jpg|webp|avif)", os.path.basename(f))]
    for d in ("frames", "kb", "hevc", "hevc8"):
        files += glob.glob(f"{T}/server/cache-real/{d}/{e}.*")
    for d in ("tv", "web", "posters"):
        files += glob.glob(f"{T}/photos/videos-real/{d}/{e}.*")
    for f in files:
        os.remove(f)
        removed += 1
tmp = LEDGER + ".tmp"
json.dump(sorted(now), open(tmp, "w"), indent=0)
os.replace(tmp, LEDGER)
print(f"{len(known - now)} removed from the library ({removed} files cleaned up); {len(now)} in the library")
