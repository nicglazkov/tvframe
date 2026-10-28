#!/bin/sh
# 8-bit HEVC copies of the clips and the loop video, for decoders without HEVC Main10
# (the Android TV emulator; the real Streamer should not need them). NVENC, no audio.
set -e
cd "$(dirname "$0")"
SET=${TVFRAME_SET:-$(cat photoset 2>/dev/null || echo unsplash)}
if [ "$SET" = real ]; then C=cache-real; V=videos-real; else C=cache; V=videos; fi
mkdir -p $C/video8
for f in "$HOME"/tvframe/photos/$V/tv/*.mp4; do
  [ -e "$f" ] || continue
  out="$C/video8/$(basename "$f")"
  [ -s "$out" ] && [ "$out" -nt "$f" ] && continue
  ffmpeg -hide_banner -loglevel error -y -i "$f" -an -vf format=yuv420p -c:v hevc_nvenc -profile:v main \
    -preset p5 -rc vbr -cq 24 -b:v 25M -maxrate 40M -bufsize 50M -tag:v hvc1 -movflags +faststart "$out.tmp.mp4"
  mv "$out.tmp.mp4" "$out"; echo "made $out"
done
if [ -s $C/loop/loop.mp4 ] && [ ! $C/loop/loop8.mp4 -nt $C/loop/loop.mp4 ]; then
  ffmpeg -hide_banner -loglevel error -y -i $C/loop/loop.mp4 -an -vf format=yuv420p -c:v hevc_nvenc -profile:v main \
    -preset p5 -rc vbr -cq 24 -b:v 25M -maxrate 40M -bufsize 50M -tag:v hvc1 -movflags +faststart $C/loop/loop8.tmp.mp4
  mv $C/loop/loop8.tmp.mp4 $C/loop/loop8.mp4; echo "made $C/loop/loop8.mp4"
fi
