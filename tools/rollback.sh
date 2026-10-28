#!/bin/bash
# Puts the Streamer back the way it was before TV Frame was deployed:
#   stock Backdrop screensaver, Google TV home (launcherx), our accessibility service removed.
# Values come from device/originals-production.txt. Add --uninstall to remove the app as well.
set -u
. "$(dirname "$0")/conf.sh"
$ADB_BIN connect "$TVFRAME_TV" >/dev/null 2>&1
O="$HOME/tvframe/device/originals-production.txt"
SVC=app.tvframe/app.tvframe.HomeKeyService
sh() { $A shell "$@" | tr -d '\r'; }
orig() { grep -m1 "^$1=" "$O" | cut -d= -f2-; }

# screensaver
sh settings put secure screensaver_components "$(orig screensaver_components)"
sh settings put secure screensaver_enabled "$(orig screensaver_enabled)"
sh settings put system screen_off_timeout "$(orig screen_off_timeout)"

# accessibility: drop only our entry; if nothing else is left, restore the original (unset) state
cur=$(sh settings get secure enabled_accessibility_services)
rest=$(echo "$cur" | tr ':' '\n' | grep -v -x "$SVC" | grep -v -x app.tvframe/app.tvframe.StarKeyService | grep -v -x null | grep -v '^$' | paste -sd: -)
if [ -z "$rest" ]; then
  if [ "$(orig enabled_accessibility_services)" = "null" ]; then sh settings delete secure enabled_accessibility_services >/dev/null
  else sh settings put secure enabled_accessibility_services "$(orig enabled_accessibility_services)"; fi
  sh settings put secure accessibility_enabled "$(orig accessibility_enabled)"
else
  sh settings put secure enabled_accessibility_services "$rest"
fi

# home
sh cmd role add-role-holder android.app.role.HOME "$(orig HOME_role)" 0

if [ "${1:-}" = "--uninstall" ]; then
  sh pm uninstall app.tvframe
fi
sh input keyevent KEYCODE_HOME

echo "screensaver:   $(sh settings get secure screensaver_components)"
echo "accessibility: $(sh settings get secure enabled_accessibility_services) (enabled=$(sh settings get secure accessibility_enabled))"
echo "home role:     $(sh cmd role get-role-holders android.app.role.HOME)"
