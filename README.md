# DeckDrop

LAN-only game sharing for Steam Deck and Linux. Share your game library with friends on the same network – no internet, no accounts, no trackers.

## Features

- **Peer discovery** via mDNS – devices appear automatically on the same Wi-Fi
- **P2P transfers** via libtorrent (LAN-optimised, DHT disabled)
- **Controller navigation** – fully usable in Steam Deck Gaming Mode
- **Dark UI** accessible from any browser on the network (`http://<device-ip>:7373`)
- **Steam cover art** pulled automatically by App ID
- **Integrity checking** via Blake2b hashes
- No accounts, no internet required, no DRM

## Requirements

- Python 3.11+
- `libtorrent` Python bindings (optional – transfers disabled without it)

## Install

```bash
pipx install git+https://github.com/mvbonin/deck-drop.git
```

Or clone and install in development mode:

```bash
git clone https://github.com/mvbonin/deck-drop.git
cd deck-drop
pip install -e ".[dev]"
```

## Usage

```bash
deckdrop          # start server, open http://localhost:7373
deckdrop --port 8080 --open   # custom port + auto-open browser
```

On Steam Deck: install the `.desktop` shortcut via `packaging/install.sh` to launch from Gaming Mode.

### AppImage (alles in einer Datei)

```bash
bash packaging/build-appimage.sh
chmod +x DeckDrop-*-x86_64.AppImage
./DeckDrop-*-x86_64.AppImage          # Server + Browser
./DeckDrop-*-x86_64.AppImage --kiosk  # Gaming Mode (Chromium Vollbild)
```

Enthält Python 3.12, alle Abhängigkeiten und libtorrent. Auf dem Steam Deck als Non-Steam-Spiel hinzufügen.

## Development

```bash
pip install -e ".[dev]"
pytest            # run tests
ruff check .      # lint
ruff format .     # format
```

## Project layout

```
deckdrop/
  core/         config, game metadata, hashing, torrent generation
  network/      mDNS discovery, peer registry, libtorrent transfers
  api/          FastAPI server + REST routes + WebSocket
frontend/       Preact UI (no build step – CDN imports)
tests/          pytest test suite
packaging/      systemd service, AppImage builder, .desktop files
```

## Configuration

Config lives at `~/.config/deckdrop/config.toml` and is editable via the Settings view in the UI.

Default ports: API `7373`, torrent `7374`.

## Security model

- LAN only – no port forwarding needed
- No authentication (trust your local network, like Samba)
- File integrity verified via libtorrent piece hashes + optional Blake2b check
- No external tracker, no DHT, no internet traffic

## License

MIT
