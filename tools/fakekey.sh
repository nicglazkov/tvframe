#!/bin/bash
# Press keys on a virtual USB remote (real input path, unlike `input keyevent`).
# Usage: fakekey.sh <linux key code> [...]   e.g. 172 = HOME, 258 = BUTTON_3 (YouTube), 158 = BACK
. "$(dirname "$0")/conf.sh"
{
  echo '{"id":1,"command":"register","name":"Probe Remote","vid":4660,"pid":22136,"bus":"usb","configuration":[{"type":100,"data":[1]},{"type":101,"data":[158,172,258,259]}]}'
  echo '{"id":1,"command":"delay","duration":1500}'
  for k in "$@"; do
    echo "{\"id\":1,\"command\":\"inject\",\"events\":[1,$k,1,0,0,0]}"
    echo "{\"id\":1,\"command\":\"delay\",\"duration\":${HOLD:-80}}"
    echo "{\"id\":1,\"command\":\"inject\",\"events\":[1,$k,0,0,0,0]}"
    echo '{"id":1,"command":"delay","duration":3000}'
  done
} | $A shell uinput - 2>&1 | grep -v -E "^$" | tail -3
