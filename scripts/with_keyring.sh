#!/usr/bin/env bash
# Run a command in a throwaway D-Bus session with an unlocked gnome-keyring: a Secret Service
# for API keys where no desktop session provides one (CI). Its keyring lives in a temporary
# XDG_DATA_HOME, so no real keyring is touched. Usage: scripts/with_keyring.sh <command> [args]
set -euo pipefail
XDG_DATA_HOME="$(mktemp -d)"
export XDG_DATA_HOME
trap 'rm -rf "$XDG_DATA_HOME"' EXIT
dbus-run-session -- bash -c \
  'echo -n ci | gnome-keyring-daemon --unlock --components=secrets >/dev/null && "$@"' \
  with_keyring "$@"
