# Settings for the shell tools: sourced, not run. The values live in ~/.config/tvframe/config.env (see
# config.example.env and server/tvconf.py). Sets $A, the adb command aimed at the TV.
CONF="${TVFRAME_CONFIG:-$HOME/.config/tvframe/config.env}"
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"
ADB_BIN="${ADB_BIN:-$HOME/android/sdk/platform-tools/adb}"
if [ -z "${TVFRAME_TV:-}" ]; then echo "Set TVFRAME_TV (the TV's adb address, host:port) in $CONF" >&2; exit 2; fi
A="$ADB_BIN -s $TVFRAME_TV"
