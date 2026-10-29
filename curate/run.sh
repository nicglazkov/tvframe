#!/bin/bash
# Score photos and group near-duplicates (curate.py), on the GPU when there is one.
# Usage: ./run.sh [photo dirs under photos/ ...]
#   ./run.sh                     raw dups (the sample set), results in ./out
#   ./run.sh real                the library (tools/library_sync.sh)
# Settings (environment variables, all optional):
#   OUT=/some/dir   where results go (default: ./out next to this script). Created if missing.
#                   Keep it under your home folder: the snap Docker cannot see /tmp.
#   TODAY=2026-10-24  date used for "on this day" boosts (default: today on this machine)
#   META=file.json  favorites and dates (default: sample_meta.json only for the sample set)
#   CPU=1           run without the GPU
#   TVFRAME_DOCKER_GPU  the docker run flags that reach the GPU (config.env; default --runtime=nvidia ...; none for no GPU)
set -u
cd "$(dirname "$0")" || exit 1
HERE="$PWD"
. ../tools/env.sh
if [ $# -eq 0 ]; then DIRS=(raw dups); SAMPLE=1; else DIRS=("$@"); SAMPLE=0; fi
TODAY="${TODAY:-$(date +%F)}"
OUT="${OUT:-$HERE/out}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
if [ -z "${META:-}" ] && [ $SAMPLE -eq 1 ]; then META="$HERE/sample_meta.json"; fi
echo "curating ${DIRS[*]} for $TODAY into $OUT"
# Photo paths are written as /photos/... either way (the TV Frame image links /photos to photos/ in the checkout)
ARGS=(); for d in "${DIRS[@]}"; do ARGS+=("/photos/$d"); done
{ [ -n "${CPU:-}" ] || [ "${TVFRAME_DOCKER_GPU:-}" = none ]; } && ARGS+=(--cpu)
if [ "${TVFRAME_IN_DOCKER:-}" = 1 ]; then
  [ -n "${META:-}" ] && ARGS+=(--meta "$META")
  exec python3 curate.py "${ARGS[@]}" --out "$OUT" --today "$TODAY"
fi
MOUNTS=(-v "$HERE":/work -v "$T/photos":/photos:ro -v "$OUT":/out)
if [ -n "${META:-}" ]; then
  META="$(cd "$(dirname "$META")" && pwd)/$(basename "$META")"
  MOUNTS+=(-v "$META":/meta.json:ro); ARGS+=(--meta /meta.json)
fi
# shellcheck disable=SC2206
GPU=(${TVFRAME_DOCKER_GPU:---runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all})
{ [ -n "${CPU:-}" ] || [ "${GPU[*]}" = none ]; } && GPU=()
exec docker run --rm "${GPU[@]}" -u "$(id -u):$(id -g)" -e HOME=/tmp -w /work \
  "${MOUNTS[@]}" tvframe-curate:local python curate.py "${ARGS[@]}" --out /out --today "$TODAY"
