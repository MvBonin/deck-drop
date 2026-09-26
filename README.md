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

## Updates

DeckDrop tracks the content of every shared game folder and notices when it changes on disk
(a patch, a manual file edit, ...). A changed game is **locked from sharing** until you publish
it as an update, so peers never receive half-patched or corrupted data:

1. Patch/change a shared game normally (e.g. on your PC via Steam). DeckDrop's "Meine Spiele"
   view shows a **"Verändert"** badge on the next scan (periodic, or right after you open the
   view).
2. Click **"Update veröffentlichen…"**, give the update an optional version label and note, and
   confirm. DeckDrop hashes only what changed and re-shares the game.
3. Other devices that already have this game (PC or Steam Deck) see an **"Update verfügbar"**
   badge with the version label. Updating downloads only the pieces that actually differ –
   unchanged files are never re-transferred – and keeps any local-only files (save games, config,
   shader cache, ...) untouched.
4. A first-time download instead shows every version currently shared on the network, so you can
   pick a specific one (e.g. to match a friend's modded copy).
5. If a game is falsely marked "Verändert" (e.g. after restoring a backup), the same update flow
   with the game's own version acts as an in-place repair against the network's copy.
6. On the Steam Deck, the Decky plugin's Quick-Access panel shows how many of your games have an
   update available, without opening the DeckDrop UI.

No update is ever applied automatically – you always start it from the UI.

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
