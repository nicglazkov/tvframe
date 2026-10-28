#!/bin/sh
# Make the local signing key once. Keep keystore/ private; losing it means uninstall+reinstall to update.
set -e
cd "$(dirname "$0")/.."
mkdir -p keystore
[ -f keystore/tvframe-local.jks ] && { echo "key exists"; exit 0; }
PW=$(head -c 18 /dev/urandom | base64 | tr -d '/+=')
"${JAVA_HOME:-$HOME/android/jdk}/bin/keytool" -genkeypair -keystore keystore/tvframe-local.jks -storepass "$PW" \
  -keypass "$PW" -alias tvframe -keyalg RSA -keysize 3072 -validity 10000 -dname "CN=TV Frame local"
printf 'storeFile=keystore/tvframe-local.jks\nstorePassword=%s\nkeyAlias=tvframe\nkeyPassword=%s\n' "$PW" "$PW" > keystore/key.properties
chmod 600 keystore/*
echo "made keystore/tvframe-local.jks"
