#!/usr/bin/env bash
# Flatpak wrapper: ensure XDG_RUNTIME_DIR is set for D-Bus (needed by Avahi/mDNS).
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
exec deckdrop "$@"
