#!/bin/bash
# Sync every album in albums.txt, then re-run the TV Frame curation on them.
#
# Usage:  ./sync.sh                 sync all albums, then curate
#         ./sync.sh --no-curate     sync only
#         ALBUMS=/path/list.txt ./sync.sh
#
# albums.txt, one album per line:   <name>  <share link>
#   name is a short folder name (letters, digits, - or _). Lines starting with # are skipped.
#   A line with only a link uses the link's last part as the name.
# Photos land in ~/tvframe/photos/albums/<name>/ (one folder per album).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ALBUMS="${ALBUMS:-$HERE/albums.txt}"
DEST_ROOT="${DEST_ROOT:-$HOME/tvframe/photos/albums}"
CURATE="${CURATE:-$HOME/tvframe/curate/run.sh}"
# album scores go to their own folder so the preview site's sample scores in curate/out stay as they are
export OUT="${OUT:-$HOME/tvframe/curate/out-albums}"
DELAY="${DELAY:-1}"
DO_CURATE=1
[ "${1:-}" = "--no-curate" ] && DO_CURATE=0

mkdir -p "$HERE/logs" "$DEST_ROOT"
LOG="$HERE/logs/sync-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

# one sync at a time
exec 9>"$HERE/.sync.lock"
if ! flock -n 9; then echo "another sync is running, stopping"; exit 0; fi

[ -f "$ALBUMS" ] || { echo "no album list at $ALBUMS"; exit 2; }

echo "sync started $(date)"
DIRS=(); FAILED=0
while read -r a b _; do
  [ -z "${a:-}" ] && continue
  case "$a" in \#*) continue ;; esac
  if [ -n "${b:-}" ]; then name="$a"; link="$b"; else link="$a"; name="${a##*/}"; name="${name%%\?*}"; fi
  if ! [[ "$name" =~ ^[A-Za-z0-9_-]+$ ]]; then echo "skipping bad name: $name"; FAILED=1; continue; fi
  echo "--- $name"
  python3 "$HERE/gpalbum.py" "$link" "$DEST_ROOT/$name" --delay "$DELAY"
  rc=$?
  if [ $rc -ne 0 ]; then echo "    $name finished with code $rc"; FAILED=1; fi
  [ -d "$DEST_ROOT/$name" ] && DIRS+=("albums/$name")
  sleep "$DELAY"
done < "$ALBUMS"

if [ $DO_CURATE -eq 1 ] && [ ${#DIRS[@]} -gt 0 ]; then
  echo "--- curating ${DIRS[*]}"
  "$CURATE" "${DIRS[@]}" || { echo "curation failed"; FAILED=1; }
fi
echo "sync finished $(date), log: $LOG"
exit $FAILED
