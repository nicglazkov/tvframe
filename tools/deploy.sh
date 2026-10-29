#!/bin/bash
# Makes TV Frame (Glance) the TV's screensaver, and with TVFRAME_QUIET_HOME=1 (the default) its home screen too.
# Every value it changes is recorded first in device/originals-production.txt; tools/rollback.sh undoes it all.
set -u
. "$(dirname "$0")/conf.sh"
$ADB_BIN connect "$TVFRAME_TV" >/dev/null 2>&1
SVC=app.tvframe/app.tvframe.HomeKeyService
sh() { $A shell "$@" | tr -d '\r'; }

if [ "${1:-}" = "--install" ]; then
  # Installing kills a running screensaver, and a killed dream puts the Streamer to sleep, which turns the
  # TV off over HDMI-CEC. If the TV was on (dreaming or awake), wake it and put Glance back up afterwards.
  was=$(sh dumpsys power | grep -o "mWakefulness=[A-Za-z]*" | cut -d= -f2)
  $A install -r "${TVFRAME_APK:-$T/android/app/build/outputs/apk/release/app-release.apk}" || exit 1
  if [ "$was" = "Dreaming" ] || [ "$was" = "Awake" ]; then
    sleep 2; sh input keyevent KEYCODE_WAKEUP; sleep 4; sh am start -n app.tvframe/.ShowActivity >/dev/null
    echo "TV was $was: woke it and reopened Glance (the screensaver takes over after 5 idle minutes)"
  fi
fi

# 0. the TV's own settings, once, before anything changes them: tools/rollback.sh puts these back
O="$T/device/originals-production.txt"
if [ ! -s "$O" ]; then
  mkdir -p "$(dirname "$O")"
  {
    echo "date=$(date -Iseconds)"
    for k in enabled_accessibility_services accessibility_enabled screensaver_components screensaver_enabled; do
      echo "$k=$(sh settings get secure $k)"
    done
    echo "screen_off_timeout=$(sh settings get system screen_off_timeout)"
    echo "sleep_timeout=$(sh settings get secure sleep_timeout)"
    echo "HOME_role=$(sh cmd role get-role-holders android.app.role.HOME)"
  } > "$O"
  echo "saved the TV's own settings in $O"
fi

# 1. screensaver, after 5 idle minutes (the stock value is kept in originals-production.txt)
sh settings put secure screensaver_components app.tvframe/.FrameDreamService
sh settings put secure screensaver_enabled 1
sh settings put system screen_off_timeout 300000

# 2. Home button: Quiet Home is the home screen. Our accessibility service catches the
#    Home key and swaps out Google TV home when it comes up by itself; launcherx stays installed and enabled,
#    so casting, voice and the YouTube/Netflix buttons are untouched. Added next to any other enabled service.
cur=$(sh settings get secure enabled_accessibility_services)
rest=$(echo "$cur" | tr ":" "\n" | grep -v -x "$SVC" | grep -v -x app.tvframe/app.tvframe.StarKeyService | grep -v -x null | grep -v "^$" | paste -sd: -)
if [ "${TVFRAME_QUIET_HOME:-1}" != 0 ]; then
  sh settings put secure enabled_accessibility_services "$(echo "$rest:$SVC" | sed "s/^://")"
  sh settings put secure accessibility_enabled 1
elif [ "$rest" != "$(echo "$cur" | sed "s/^null$//")" ]; then
  sh settings put secure enabled_accessibility_services "${rest:-null}"        # the quiet home screen was turned off
fi

# 3. HOME role stays with launcherx: its HOME filter has priority 2, which beats the role (tested 2026-10-24).

echo "screensaver:   $(sh settings get secure screensaver_components)"
echo "accessibility: $(sh settings get secure enabled_accessibility_services) (enabled=$(sh settings get secure accessibility_enabled))"
echo "home role:     $(sh cmd role get-role-holders android.app.role.HOME)"
echo "launcherx:     $(sh pm list packages -e com.google.android.apps.tv.launcherx | head -1) (enabled)"
