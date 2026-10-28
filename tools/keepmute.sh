#!/bin/bash
# Keeps the TV muted (through Home Assistant) while you test on it. Stops when ~/.cache/keepmute.stop exists or after 3 h.
# Needs HA_URL and HA_TV_PLAYER (the TV's media_player entity) in config.env.
. "$(dirname "$0")/conf.sh"
T=$(cat "${HA_TOKEN_FILE:-$HOME/.config/tvframe/ha_token}"); H="Authorization: Bearer $T"; U=$HA_URL/api; E=$HA_TV_PLAYER
end=$(( $(date +%s) + 10800 )); rm -f ~/.cache/keepmute.stop
while [ ! -e ~/.cache/keepmute.stop ] && [ $(date +%s) -lt $end ]; do
  s=$(curl -s -m 4 -H "$H" $U/states/$E | python3 -c "import json,sys; a=json.load(sys.stdin); print(a['state'], a['attributes'].get('is_volume_muted'))" 2>/dev/null)
  if [ "$s" = "on False" ]; then
    curl -s -m 4 -o /dev/null -H "$H" -H "Content-Type: application/json" -d "{\"entity_id\":\"$E\",\"is_volume_muted\":true}" $U/services/media_player/volume_mute
    echo "$(date +%T) re-muted"
  fi
  sleep 5
done
echo "$(date +%T) keepmute stopped"
