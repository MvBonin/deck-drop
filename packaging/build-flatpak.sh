#!/usr/bin/env bash
# Build a DeckDrop Flatpak.
#
# Prerequisites:
#   flatpak flatpak-builder org.freedesktop.Platform//24.08 org.freedesktop.Sdk//24.08
#   org.freedesktop.Sdk.Extension.python3.12
#   pip install flatpak-pip-generator   (only needed to regenerate pip sources)
#
# Usage:
#   bash packaging/build-flatpak.sh [--install]
#
# Pass --install to install the resulting .flatpak into the user's Flatpak store.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$ROOT_DIR/.flatpak-build"
REPO_DIR="$ROOT_DIR/.flatpak-repo"
BUNDLE="$ROOT_DIR/DeckDrop.flatpak"
SOURCES_JSON="$SCRIPT_DIR/python3-deckdrop-deps.json"

# ── 1. Regenerate pip sources if needed ───────────────────────────────────────
if [ ! -f "$SOURCES_JSON" ]; then
    echo "python3-deckdrop-deps.json not found. Generating…"
    bash "$SCRIPT_DIR/generate-flatpak-sources.sh"
fi

# ── 2. Install required Flatpak runtimes ──────────────────────────────────────
echo "Installing Flatpak runtimes (if not present)…"
flatpak install --user --noninteractive \
    flathub org.freedesktop.Platform//24.08 \
    flathub org.freedesktop.Sdk//24.08 \
    flathub org.freedesktop.Sdk.Extension.python3.12 || true

# ── 3. Build ──────────────────────────────────────────────────────────────────
echo "Building DeckDrop Flatpak…"
flatpak-builder \
    --force-clean \
    --repo="$REPO_DIR" \
    --state-dir="$BUILD_DIR/.state" \
    "$BUILD_DIR/app" \
    "$SCRIPT_DIR/com.deckdrop.DeckDrop.json"

# ── 4. Export single-file bundle ─────────────────────────────────────────────
echo "Exporting bundle…"
flatpak build-bundle \
    "$REPO_DIR" \
    "$BUNDLE" \
    com.deckdrop.DeckDrop

echo ""
echo "Bundle: $BUNDLE"
echo ""

# ── 5. Optionally install ─────────────────────────────────────────────────────
if [[ "${1:-}" == "--install" ]]; then
    echo "Installing DeckDrop Flatpak…"
    flatpak install --user --noninteractive "$BUNDLE"
    echo "Run with: flatpak run com.deckdrop.DeckDrop"
fi
