# DeckDrop

**Move your games between your Steam Deck, your PC and your friends' devices – over your own
network, at LAN speed.**

Your PC already has that 80 GB game installed. Your Deck doesn't. Instead of downloading it again
over a slow connection, DeckDrop copies it straight from the PC to the Deck on your home Wi-Fi or
LAN. Same at a LAN party: one person has the game and the update, everyone else pulls it locally.

> [!IMPORTANT]
> **Only for legal copies.** DeckDrop is for moving games you are allowed to copy: your own
> games between your own devices, DRM-free games whose license allows it, or games
> your friends also own. It is **not** for piracy or for handing out games
> people haven't bought. You are responsible for respecting each game's license and the store's
> terms.

> [!NOTE]
> **LAN only.** DeckDrop works only between devices on the same local network. There is no
> internet sharing, no public tracker, no DHT and no port forwarding. Game files never leave your
> network.

## What it does

- **Finds your devices automatically.** Open DeckDrop on two devices on the same network and
  they see each other. There is nothing to set up and no account to create.
- **Fast local transfers.** Games are sent peer-to-peer (BitTorrent protocol, LAN-only). If
  several devices have the game, you download from all of them at once.
- **Made for the Deck.** You can control everything with the controller in Gaming Mode. A
  fullscreen kiosk mode and a Decky plugin are included.
- **Covers included.** Cover art is loaded from Steam automatically.
- **Updates without re-downloading.** When you patch a game on one device, the others only fetch
  the parts that changed (see below).
- **Safe copies.** Every transfer is hash-checked. Half-patched or broken games are never
  shared.
- **Works from any browser.** The UI also runs from any browser on your network:
  `http://<device-ip>:7373`.

## Install

### AppImage (recommended – Steam Deck and Linux)

In Desktop Mode, open a terminal (Konsole) and run:

```bash
mkdir -p ~/Applications && curl -fL -o ~/Applications/DeckDrop.AppImage \
  https://github.com/MvBonin/deck-drop/releases/latest/download/DeckDrop-x86_64.AppImage \
  && chmod +x ~/Applications/DeckDrop.AppImage
~/Applications/DeckDrop.AppImage
```

DeckDrop starts and opens in your browser. Everything it needs is inside the AppImage
(Python, libtorrent, the UI).

**In Gaming Mode:** add `~/Applications/DeckDrop.AppImage` as a Non-Steam Game and set its launch
options to `--kiosk`. It then opens as a fullscreen app.

**Updating:** *Einstellungen → Nach Updates suchen*. The AppImage replaces itself after checking
its signature and checksum. The UI is currently in German.

### pipx

```bash
pipx install git+https://github.com/mvbonin/deck-drop.git
deckdrop
```

## How to share a game

1. Start DeckDrop on both devices, on the same Wi-Fi or LAN.
2. On the device that has the game, open **Meine Spiele** → **Spiel hinzufügen** and pick its install folder.
3. On the other device, open **Netzwerk**. The game shows up there. Click download.

That's it. Both devices need to be online at the same time, since nothing is stored in a cloud.

## Game updates

DeckDrop notices when the files of a shared game change (a patch, a mod, a manual edit). It then
**stops sharing that game** until you publish the change, so nobody ever gets a half-patched copy.

1. Patch the game as usual (e.g. let Steam update it on your PC). On the next scan, **Meine
   Spiele** shows a **"Verändert"** badge.
2. Click **"Update veröffentlichen…"**, optionally add a version label and a note, and confirm.
   DeckDrop only re-hashes what changed.
3. Devices that already have the game see **"Update verfügbar"**. Updating downloads only the
   pieces that actually differ. Your saves, config and shader cache stay untouched.
4. Downloading a game for the first time lists every version shared on the network. You can pick
   one, e.g. to match a friend's modded copy.
5. A game wrongly marked "Verändert" (e.g. after restoring a backup) can be repaired in place
   with the same flow, using the network's copy.
6. On the Steam Deck, the Decky plugin's Quick Access panel shows how many games have an update
   waiting.

Nothing is ever updated automatically. You always start it yourself.

## Network and privacy

- **Game data stays on your LAN.** Devices find each other via mDNS and transfer via libtorrent
  with DHT, UPnP and NAT-PMP switched off. There is no tracker and nothing to forward on your
  router.
- **The only internet requests** are cover art and the Steam store lookup (by game name), plus the
  update check, which only runs when you start it.
- **No login.** Anyone on your network can open the UI, the same trust model as a shared
  network folder. Only run it on networks you trust, not on public Wi-Fi.

Default ports: UI/API `7373`, transfers `7374`. Config: `~/.config/deckdrop/config.toml`, also
editable in the settings view.

## Command line

```bash
deckdrop               # start and open the UI in your browser
deckdrop --kiosk       # fullscreen app window (Gaming Mode, needs Chromium)
deckdrop --headless    # server only, no browser
deckdrop --port 8080   # different port
```

## For developers

```bash
git clone https://github.com/mvbonin/deck-drop.git
cd deck-drop
pip install -e ".[dev]"
pytest            # tests
ruff check .      # lint
ruff format .     # format
```

Build the AppImage yourself:

```bash
bash packaging/build-appimage.sh
./DeckDrop-*-x86_64.AppImage
```

Project layout:

```
deckdrop/
  core/         config, game metadata, hashing, torrent generation
  network/      mDNS discovery, peer registry, libtorrent transfers
  api/          FastAPI server + REST routes + WebSocket
frontend/       Preact UI (no build step, vendored dependencies)
decky-plugin/   Decky Loader plugin for Gaming Mode
tests/          pytest test suite
packaging/      AppImage builder, systemd service, .desktop files
```

Translations: the UI is available in English (default) and German; the
language is picked on first start and can be changed in Settings. Strings live
in `frontend/i18n/<lang>.js` (web UI), `deckdrop/core/i18n.py` (server
messages) and `decky-plugin/src/index.tsx` (Gaming Mode panel). To add a
language, add a catalog in each place and register its code
(`LANGUAGES` / `SUPPORTED_LANGUAGES`); `tests/test_i18n.py` checks that no key
is missing.

Releases: see [docs/RELEASING.md](docs/RELEASING.md).

## License

MIT. DeckDrop's license covers this software only. It does not give you any rights to the games
you share with it.
