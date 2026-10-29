#!/bin/bash
# Rebuild the TV photos from library/ in this checkout: the "TV Photos" share (an SMB share of that folder) and
# uploads from the photos page land there. Run by tools/library_watch.py when the folder changes, or by hand.
# Steps: EXIF and places, web and 4K images, clip scoring, videos, drop outputs of deleted files, scoring, site data,
# TV frames, playlist. The TV picks the new playlist up on its next start or within 30 minutes.
# Each step is written to ~/.cache/tvframe-progress.json for the photo manager's progress panel (photos_api.py /progress).
set -euo pipefail
. "$(dirname "$0")/env.sh"
cd "$T"
mkdir -p "$HOME/.cache"
exec 9>"$HOME/.cache/tvframe-library.lock"
flock 9          # waits for a running sync, or for photos_api.py rebuilding the playlist
t0=$(date +%s)
PROG="$HOME/.cache/tvframe-progress.json"; DONE=""; ts=$t0; cur=""
step() {   # step <key>: close the current step (its time goes in "done") and start the next
  local now; now=$(date +%s)
  [ -n "$cur" ] && DONE="${DONE:+$DONE,}{\"k\":\"$cur\",\"s\":$((now - ts))}"
  cur="$1"; ts=$now
  printf '{"pid":%d,"started":%d,"step":"%s","step_started":%d,"done":[%s]}\n' $$ "$t0" "$cur" "$ts" "$DONE" > "$PROG.tmp"
  mv "$PROG.tmp" "$PROG"
}
trap '[ "$cur" != done ] && step failed' EXIT
step meta;    python3 tools/real_meta.py; python3 tools/library_prune.py
python3 tools/real_exif.py > real/exif.log 2>&1 || echo "exif failed (real/exif.log)"   # focal length, aperture, shutter, ISO for the details panel (real/exif.json)
step media;   in_box tvframe-media:heif python3 tools/real_media.py
step clips;   python3 tools/clip_score.py > real/clip_score.log 2>&1 || echo "clip scoring failed (real/clip_score.log)"   # what is in each clip, its best 30 s (real/clip_scores.json)
step videos;  python3 tools/real_videos.py
python3 tools/clip_look.py > real/clip_look.log 2>&1 || echo "clip look failed (real/clip_look.log)"   # how each clip was shot, its colour, the settings line (real/clip_look.json)
python3 tools/clip_loops.py > real/clip_loops.log 2>&1 || echo "clip loops failed (real/clip_loops.log)"   # seamless loops + first-frame posters (photos/videos-real/tvloop)
step scoring; python3 tools/curate_meta.py
OUT="$T/curate/out-real" META="$T/real/curate_meta.json" ./curate/run.sh real > real/curate.log 2>&1
python3 tools/sync_data.py
step frames;  TVFRAME_SET=real python3 server/prepare.py > real/prepare.log 2>&1
step maps;    python3 tools/maps.py || true          # small maps for the TV details panel (no GPS, no map)
step playlist; TVFRAME_SET=real python3 server/prod_build.py
step done; cp "$PROG" "$HOME/.cache/tvframe-progress-last.json"
echo "library sync done in $(( $(date +%s) - t0 )) s"
