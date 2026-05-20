#!/usr/bin/env bash
# Generate python3-deckdrop-deps.json for Flatpak build.
# Requires: flatpak-pip-generator (pip install flatpak-pip-generator)
#
# Usage:  cd packaging && bash generate-flatpak-sources.sh
#
# The generated file is committed to the repo so CI / flatpak-builder
# can build offline. Re-run whenever flatpak-requirements.txt changes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT="$SCRIPT_DIR/python3-deckdrop-deps.json"
REQS="$SCRIPT_DIR/flatpak-requirements.txt"

if ! command -v flatpak-pip-generator &>/dev/null; then
    echo "flatpak-pip-generator not found. Install with:"
    echo "  pip install flatpak-pip-generator"
    exit 1
fi

echo "Generating Flatpak Python sources…"
flatpak-pip-generator \
    --python-version 3.12 \
    --runtime org.freedesktop.Platform//24.08 \
    --output "$OUTPUT" \
    --requirements-file "$REQS"

echo "Generated: $OUTPUT"
echo "Commit this file alongside com.deckdrop.DeckDrop.json"
