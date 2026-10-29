#!/bin/sh
# Installs TV Frame on this computer: gets the code, then runs ./tvframe setup, which asks a few questions.
#   curl -fsSL https://raw.githubusercontent.com/nicglazkov/tvframe/main/install.sh | sh
# TVFRAME_DIR picks the folder (default ~/tvframe). Run it again to update an install that is already there.
set -eu
DIR="${TVFRAME_DIR:-$HOME/tvframe}"
REPO="${TVFRAME_REPO:-https://github.com/nicglazkov/tvframe}"
command -v git >/dev/null || { echo "TV Frame needs git: on Ubuntu or Debian, sudo apt install git" >&2; exit 1; }
if [ -d "$DIR/.git" ]; then
  echo "Updating TV Frame in $DIR"
  git -C "$DIR" pull --ff-only -q
else
  echo "Getting TV Frame into $DIR"
  git clone -q "$REPO" "$DIR"
fi
if [ -r /dev/tty ]; then exec "$DIR/tvframe" setup "$@" </dev/tty; else exec "$DIR/tvframe" setup --yes "$@"; fi
