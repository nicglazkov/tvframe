# shellcheck shell=bash disable=SC2034
# Settings for the TV tools: sourced, not run. Sets $A, the adb command aimed at the TV.
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
ADB_BIN="${ADB_BIN:-$(command -v adb || echo "$HOME/android/sdk/platform-tools/adb")}"
if [ -z "${TVFRAME_TV:-}" ]; then echo "Set TVFRAME_TV (the TV's adb address, host:port) in $CONF" >&2; exit 2; fi
A="$ADB_BIN -s $TVFRAME_TV"
