#!/bin/bash
# Start the configured screensaver by shortening the ambient timer, then restore it.
. "$(dirname "$0")/conf.sh"
ORIG=${ORIG:-600000}
$A shell settings put system screen_off_timeout 15000
for i in $(seq 1 30); do
  sleep 2
  d=$($A shell dumpsys dreams | grep -m1 "mCurrentDream")
  case "$d" in *null*) ;; *) echo "dreaming after $((i*2))s: $d"; break;; esac
done
$A shell settings put system screen_off_timeout $ORIG
echo "restored screen_off_timeout=$($A shell settings get system screen_off_timeout)"
