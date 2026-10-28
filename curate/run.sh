#!/bin/bash
# Run the curation on the server's GPU.
# Usage: ./run.sh [photo dirs under ~/tvframe/photos ...]
#   ./run.sh                     raw dups (the sample set), results in ./out
#   ./run.sh albums/family       one synced album
# Settings (environment variables, all optional):
#   OUT=/some/dir   where results go (default: ./out next to this script). Created if missing.
#                   Keep it under your home folder: the snap Docker on the server cannot see /tmp.
#   TODAY=2026-10-24  date used for "on this day" boosts (default: today on this machine)
#   META=file.json  favorites and dates (default: sample_meta.json only for the sample set)
#   CPU=1           run without the GPU
# Note: on this host "--gpus all" fails (CDI mode); "--runtime=nvidia" works.
set -u
cd "$(dirname "$0")"
HERE="$PWD"
if [ $# -eq 0 ]; then DIRS=(raw dups); SAMPLE=1; else DIRS=("$@"); SAMPLE=0; fi
TODAY="${TODAY:-$(date +%F)}"
OUT="${OUT:-$HERE/out}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
if [ -z "${META:-}" ] && [ $SAMPLE -eq 1 ]; then META="$HERE/sample_meta.json"; fi
ARGS=(); for d in "${DIRS[@]}"; do ARGS+=("/photos/$d"); done
MOUNTS=(-v "$HERE":/work -v "$HOME/tvframe/photos":/photos:ro -v "$OUT":/out)
if [ -n "${META:-}" ]; then
  META="$(cd "$(dirname "$META")" && pwd)/$(basename "$META")"
  MOUNTS+=(-v "$META":/meta.json:ro); ARGS+=(--meta /meta.json)
fi
GPU=(--runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all)
[ -n "${CPU:-}" ] && GPU=() && ARGS+=(--cpu)
echo "curating ${DIRS[*]} for $TODAY into $OUT"
exec docker run --rm "${GPU[@]}" -u "$(id -u):$(id -g)" -e HOME=/tmp -w /work \
  "${MOUNTS[@]}" tvframe-curate:local python curate.py "${ARGS[@]}" --out /out --today "$TODAY"
