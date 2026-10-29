# shellcheck shell=bash
# Settings for the shell tools: sourced, not run. Sets $T, where TV Frame lives (this checkout), and reads the
# home's settings from ~/.config/tvframe/config.env (see config.example.env and server/tvconf.py).
umask 022                                        # some Docker setups start containers with 0000
T="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF="${TVFRAME_CONFIG:-$HOME/.config/tvframe/config.env}"
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"
# Steps that need the image libraries or the photo scorer run in their own containers, unless this already is
# the TV Frame image (compose.yaml), which has everything.
in_box() {   # in_box <image> <command...>: run a command from the checkout, in that image if needed
  local img="$1"; shift
  if [ "${TVFRAME_IN_DOCKER:-}" = 1 ]; then (cd "$T" && "$@")
  else docker run --rm -u "$(id -u):$(id -g)" -v "$T":/work -w /work "$img" "$@"; fi
}
