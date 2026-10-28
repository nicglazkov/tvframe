#!/bin/bash
# Rebuild the site's real-photo set end to end on the server. Safe to rerun: geocoding is cached,
# image and video outputs are rewritten or skipped.
set -euo pipefail
cd ~/tvframe
python3 tools/real_meta.py
docker run --rm -u "$(id -u):$(id -g)" -v "$HOME/tvframe":/work -w /work tvframe-media:heif python tools/real_media.py
python3 tools/real_videos.py
python3 -c 'import json;it=json.load(open("real/items.json"));json.dump({i["id"]:{"date":i["date"]} for i in it if i["kind"]=="photo" and i.get("date")},open("real/curate_meta.json","w"),indent=1)'
OUT="$HOME/tvframe/curate/out-real" META="$HOME/tvframe/real/curate_meta.json" ./curate/run.sh real > real/curate.log 2>&1
python3 tools/fetch_live.py
python3 tools/sync_data.py
