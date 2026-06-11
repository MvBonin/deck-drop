#!/usr/bin/env bash
# Set up the DeckDrop systemd user service.
#
# Works regardless of whether DeckDrop is installed via pipx, AppImage, or Flatpak.
# Run once after installation; re-run after moving an AppImage to a new location.
#
# Usage:
#   bash packaging/service-setup.sh              # install & enable
#   bash packaging/service-setup.sh --uninstall  # remove service
#
# To force AppImage mode with a specific path:
#   APPIMAGE=/path/to/DeckDrop.AppImage bash packaging/service-setup.sh

set -euo pipefail

UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/deckdrop.service"
SERVICE="deckdrop"

# ── Uninstall ─────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--uninstall" ]]; then
    echo "Disabling DeckDrop service…"
    systemctl --user disable --now "$SERVICE" 2>/dev/null || true
    rm -f "$UNIT_FILE"
    systemctl --user daemon-reload
    echo "Service removed."
    exit 0
fi

# ── Detect install type ───────────────────────────────────────────────────────
if flatpak list 2>/dev/null | grep -q "com.deckdrop.DeckDrop"; then
    INSTALL_TYPE="flatpak"
    EXEC_START="/usr/bin/flatpak run com.deckdrop.DeckDrop --headless"
elif [ -n "${APPIMAGE:-}" ]; then
    INSTALL_TYPE="appimage"
    EXEC_START="${APPIMAGE} --headless"
elif appimage_found=$(find "$HOME" -maxdepth 4 -name "DeckDrop-*.AppImage" 2>/dev/null | sort -V | tail -1) && [ -n "$appimage_found" ]; then
    INSTALL_TYPE="appimage"
    EXEC_START="${appimage_found} --headless"
else
    INSTALL_TYPE="pipx"
    EXEC_START="%h/.local/bin/deckdrop --headless"
fi

# ── Write unit file ───────────────────────────────────────────────────────────
echo "Install type: $INSTALL_TYPE"
echo "ExecStart:    $EXEC_START"

APPIMAGE_ENV=""
if [ "$INSTALL_TYPE" = "appimage" ]; then
    APPIMAGE_PATH="${EXEC_START% --headless}"
    APPIMAGE_ENV="Environment=APPIMAGE=${APPIMAGE_PATH}"
fi

mkdir -p "$UNIT_DIR"
cat > "$UNIT_FILE" << EOF
[Unit]
Description=DeckDrop LAN Game Sharing
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${EXEC_START}
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
${APPIMAGE_ENV}

[Install]
WantedBy=default.target
EOF

# Let the user service run after reboot without an active login session.
loginctl enable-linger "$USER" 2>/dev/null || true

systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE"

echo ""
echo "DeckDrop service enabled and running."
echo "Check status:  systemctl --user status $SERVICE"
echo "Disable:       bash packaging/service-setup.sh --uninstall"
