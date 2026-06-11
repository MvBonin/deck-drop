#!/usr/bin/env bash
set -e
echo "=== DeckDrop Installer ==="

if ! command -v pipx &>/dev/null; then
    python3 -m pip install --user pipx
    python3 -m pipx ensurepath
fi

pipx install .

bash packaging/service-setup.sh

APPDIR="$HOME/.local/share/applications"
mkdir -p "$APPDIR"
cp packaging/deckdrop.desktop "$APPDIR/deckdrop.desktop"

ICONDIR="$HOME/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$ICONDIR"
cp packaging/deckdrop.svg "$ICONDIR/deckdrop.svg"

echo ""
echo "DeckDrop wurde installiert und gestartet."
echo "Öffne http://localhost:7373 im Browser."
echo ""
echo "Gaming Mode Shortcut:"
echo "  Füge 'packaging/deckdrop-kiosk.desktop' als Non-Steam-Spiel hinzu."
