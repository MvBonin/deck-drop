# DeckDrop – Implementierungsplan

## Kernprinzipien

- **Nur LAN** – kein Traffic nach außen, kein DHT, kein STUN/TURN, kein externer Tracker
- **Gamer-first** – Card-Layout, dunkles Theme, Controller-Navigation (Steam Deck Gaming Mode)
- **Peer-to-peer** – libtorrent mit LSD (Local Service Discovery via Multicast), kein Internet-Seeding
- **Kein DRM** – nur Integritätsprüfung via Blake2b
- **Erster Start** – Nutzer gibt Namen ein und bestätigt, dass er nur Spiele teilt, zu denen er berechtigt ist

---

## Tech-Stack

| Schicht | Technologie | Begründung |
|---|---|---|
| Backend | Python ≥ 3.11 + FastAPI + uvicorn | Async, schnell, gute Doku |
| P2P-Transfer | libtorrent (Python-Bindings) | Bewährt, LAN-tauglich, multi-peer nativ |
| mDNS-Discovery | zeroconf | Pure Python, kein Setup |
| TOML | tomllib (stdlib lesen) + tomli-w (schreiben) | Kein Extra-Dep fürs Lesen |
| Hashing | hashlib blake2b (stdlib) | Schnell, keine externe Dep |
| HTTP-Client | httpx | Async, für Peer-API-Requests |
| Frontend | Preact + htm via CDN | Kein Build-Step, React-kompatible Komponenten |
| Packaging | pipx + AppImage (SteamOS/Arch), PyInstaller (Windows) | Steam Deck tauglich |

**xxhash entfernt** – blake2b aus der stdlib reicht für alle Use-Cases.

---

## Spieleordner & Konfiguration

### Standard-Download-Ordner
- Default: `~/Games/DeckDrop-Games`
- Wird beim Start nach Unterordnern mit `deckdrop.toml` gescannt
- Neue Downloads landen hier

### Einzelne Spielpfade
- Beliebige Pfade (externe HDD, andere Partitionen) per Pfad hinzufügbar
- Gespeichert in `~/.config/deckdrop/config.toml` unter `paths.game_paths`
- Beim Start geprüft: Existiert der Pfad nicht → Spiel wird ausgegraut, nicht gelöscht

### Wizard (kein deckdrop.toml vorhanden)
Wenn ein Ordner ohne `deckdrop.toml` hinzugefügt wird:
1. Name eingeben
2. Platform wählen (linux / windows / any)
3. Steam App-ID (optional, für Cover-Art)
4. DeckDrop generiert `deckdrop.toml` + startet Hashing im Hintergrund

### Spiel entfernen
- Nur aus DeckDrop entfernen (Dateien bleiben auf der Festplatte)
- Pfad aus `game_paths` entfernt, `deckdrop.toml` bleibt

---

## LAN-only: libtorrent-Konfiguration

```python
settings = {
    "enable_dht": False,       # Kein Internet-DHT
    "enable_lsd": True,        # Local Service Discovery (Multicast LAN) ✓
    "enable_upnp": False,      # Kein Port-Forwarding
    "enable_natpmp": False,    # Kein NAT-PMP
    "announce_to_all_trackers": False,
    # Keine Tracker in .torrent-Dateien
}
```

Peers werden gefunden via:
1. libtorrent LSD (Multicast `239.192.152.143:6771`) – automatisch
2. Direkte IP-Übergabe beim Download-Start (`add_peer()`) – sofort, kein Warten

---

## Verzeichnisstruktur

```
deckdrop/
├── deckdrop/
│   ├── __init__.py
│   ├── main.py               # CLI-Einstiegspunkt
│   ├── core/
│   │   ├── config.py         # Config laden/speichern (~/.config/deckdrop/config.toml)
│   │   ├── game.py           # GameInfo, deckdrop.toml lesen/schreiben
│   │   ├── integrity.py      # Blake2b-Hashing + Verifikation
│   │   ├── library.py        # In-Memory Spielebibliothek, scannt Ordner
│   │   └── torrent.py        # .torrent erzeugen (Phase 2)
│   ├── network/
│   │   ├── discovery.py      # mDNS: Peers ankündigen + finden (Phase 2)
│   │   ├── peer_registry.py  # Bekannte Peers im RAM + TTL (Phase 2)
│   │   └── transfer.py       # libtorrent Session, Download-Manager (Phase 2)
│   └── api/
│       ├── server.py         # FastAPI App-Factory
│       ├── state.py          # Shared AppState (Config + Library)
│       ├── routes/
│       │   ├── games.py      # GET/POST/PATCH/DELETE /api/games
│       │   ├── peers.py      # GET /api/peers
│       │   ├── downloads.py  # POST /api/download, GET/DELETE /api/downloads
│       │   ├── settings.py   # GET/PUT /api/settings
│       │   └── status.py     # GET /api/status
│       └── websocket.py      # /ws Live-Updates
├── frontend/
│   ├── index.html
│   ├── app.js                # Preact + htm, Router, State
│   ├── components/
│   │   ├── GameCard.js
│   │   ├── DownloadProgress.js
│   │   ├── PeerList.js
│   │   ├── AddGameWizard.js
│   │   └── Onboarding.js     # Erster-Start-Screen
│   └── style.css
├── packaging/
│   ├── deckdrop.service      # systemd User-Service
│   ├── deckdrop.desktop
│   ├── build-appimage.sh
│   └── build-windows.bat
├── tests/
├── pyproject.toml
└── PLAN.md
```

---

## API-Endpunkte

### Öffentlich (auch von Peers abgerufen)

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/api/games` | Eigene Spieleliste |
| `GET` | `/api/games/{id}` | Spieldetails |
| `GET` | `/api/games/{id}/magnet` | Magnet-Link für libtorrent |
| `GET` | `/api/peers` | Bekannte Peers |
| `GET` | `/api/status` | Name, Peer-ID, Version |

### Lokal (nur UI)

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/api/games` | Spiel hinzufügen (Wizard wenn kein toml) |
| `PATCH` | `/api/games/{id}` | Metadaten bearbeiten |
| `DELETE` | `/api/games/{id}` | Aus DeckDrop entfernen |
| `POST` | `/api/download` | Download starten `{peer_id, game_id}` |
| `GET` | `/api/downloads` | Laufende + abgeschlossene Downloads |
| `DELETE` | `/api/downloads/{id}` | Download pausieren / abbrechen |
| `GET` | `/api/settings` | Konfiguration laden |
| `PUT` | `/api/settings` | Konfiguration speichern |
| `WS` | `/ws` | Live-Updates (Downloads, Peers, etc.) |

---

## Frontend – Anforderungen

### Ansichten
1. **Meine Spiele** – eigene Spielebibliothek, Card-Grid, ausgegraut wenn offline
2. **Netzwerk** – Spiele anderer Peers, „Herunterladen"-Button
3. **Downloads** – Fortschrittsbalken, Geschwindigkeit, Peer-Anzahl, Stop/Resume
4. **Einstellungen** – Name, Download-Ordner, Geschwindigkeitslimits
5. **Erster Start (Onboarding)** – Name eingeben + Nutzungsbedingungen bestätigen

### Steam Deck / Controller-Navigation (Pflicht)
- Alle interaktiven Elemente per Gamepad erreichbar (Tab-Reihenfolge)
- `Enter`/`A` zum Aktivieren, `B` zum Zurückgehen
- Fokus-Highlight deutlich sichtbar (kein subtiler Browser-Default)
- Keine Hover-only-Interaktionen
- Touch-Targets mindestens 200×280px für Cards
- Dunkles Theme als Standard
- Keine Bildschirmtastatur nötig – Systemtastatur des Steam Deck wird verwendet

### Cover-Art
- Steam CDN wenn App-ID bekannt: `https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/library_600x900.jpg`
- Kein Cover → Placeholder mit Spielname

---

## Datenformate

### `deckdrop.toml` (im Spielordner)

```toml
[game]
id = "a1b2c3d4"
name = "Stardew Valley"
version = 3
added_at = "2025-01-15T14:30:00Z"
added_by = "alice"
updated_at = "2025-03-20T09:00:00Z"
updated_by = "alice"
size_bytes = 1073741824
platform = "linux"

[steam]
app_id = 413150

[files]
"Stardew Valley.exe" = "a3f1..."
"Content/Maps/farm.xnb" = "b9e2..."

[torrent]
info_hash = "deadbeef..."
magnet = "magnet:?xt=urn:btih:..."
```

### `~/.config/deckdrop/config.toml`

```toml
[user]
name = "SpielerName"
peer_id = "uuid"
onboarding_complete = true

[paths]
download_dir = "~/Games/DeckDrop-Games"
torrent_cache = "~/.local/share/deckdrop/torrents"
game_paths = [
    "/mnt/extern/MyGame",
    "/home/user/Games/OtherGame"
]

[network]
port = 7373
torrent_port = 7374
announce_interval = 30

[transfer]
max_upload_speed = 0
max_download_speed = 0
max_connections = 50
seed_after_download = true
```

---

## Implementierungs-Phasen

### Phase 1 – Core + API ✅ (in Arbeit)
- [x] Projektstruktur (`pyproject.toml`, Verzeichnisse)
- [x] `core/config.py` – Config laden/speichern
- [x] `core/game.py` – GameInfo, deckdrop.toml lesen/schreiben
- [x] `core/integrity.py` – Blake2b-Hashing
- [x] `core/library.py` – Spielebibliothek scannen
- [x] `api/server.py` – FastAPI-App-Factory
- [x] `api/routes/games.py` – vollständiges CRUD inkl. Wizard
- [x] `api/routes/peers.py` – Stub (Phase 2)
- [x] `api/routes/downloads.py` – Stub (Phase 2)
- [x] `api/routes/settings.py` + `status.py`
- [x] `api/websocket.py` – Broadcast-Infrastruktur
- [x] Tests: config, game, integrity, API games

### Phase 2 – Transfer & Discovery
- [ ] `core/torrent.py` – .torrent erzeugen via libtorrent (LAN-Settings)
- [ ] `network/discovery.py` – mDNS mit zeroconf (`_deckdrop._tcp.local.`)
- [ ] `network/peer_registry.py` – Peers mit TTL, Spielelisten cachen
- [ ] `network/transfer.py` – libtorrent Session, Download-Manager
- [ ] `api/routes/downloads.py` – echte Implementierung
- [ ] `api/routes/peers.py` – echte Implementierung
- [ ] WebSocket-Events für Download-Fortschritt
- [ ] Integrationstests (zwei lokale Instanzen)

### Phase 3 – Frontend
- [ ] `frontend/index.html` + Preact-Setup via CDN
- [ ] `style.css` – dunkles Theme, Card-Grid, CSS Custom Properties
- [ ] `Onboarding.js` – Erster-Start-Screen (Name + Zustimmung)
- [ ] `GameCard.js` – Cover, Name, Größe, Teilen/Laden-Button
- [ ] Ansicht „Meine Spiele" – Grid + Hinzufügen-Flow + Wizard
- [ ] Ansicht „Netzwerk" – Peer-Spiele
- [ ] Ansicht „Downloads" – Fortschrittsbalken, Geschwindigkeit
- [ ] Ansicht „Einstellungen"
- [ ] Controller-Navigation: Tab-Order, Fokus-Styles, Keyboard-Events

### Phase 4 – Packaging
- [ ] systemd User-Service (`packaging/deckdrop.service`)
- [ ] `.desktop`-Datei + Icon
- [ ] `pipx`-Installation testen auf SteamOS/Arch
- [ ] AppImage-Build-Script (`build-appimage.sh`)
- [ ] Windows PyInstaller-Script (`build-windows.bat`)
- [ ] Steam Deck Gaming Mode Anleitung (Chromium-Kiosk-Shortcut)

### Phase 5 – Flatpak (Bonus)
- [ ] `org.freedesktop.Platform` als Base
- [ ] libtorrent als shared-module bundeln
- [ ] mDNS in Flatpak-Sandbox testen (`--share=network` + ggf. Avahi D-Bus)
- [ ] `com.deckdrop.DeckDrop.json` Manifest

### Phase 6 – Decky Plugin (Optional)
- [ ] Plugin-Skeleton via Decky-Template
- [ ] Quick-Access-UI: aktive Downloads, Peer-Count
- [ ] Download starten aus Quick Access Menu

---

## Sicherheitsmodell

- Nur LAN, kein Port-Forwarding nötig
- Keine Authentifizierung (Vertrauen im eigenen Netz, wie Samba)
- Integrität via libtorrent-Piece-Hashes + optionalem Blake2b-Check
- Kein externer Tracker, kein Internet-DHT

---

## Offene Entscheidungen

| Thema | Status |
|---|---|
| Frontend-Framework | Preact + htm via CDN ✓ |
| Hashing | blake2b stdlib only, xxhash gestrichen ✓ |
| LAN-only Enforcement | libtorrent DHT aus, LSD an ✓ |
| Fenster-Modus | Chromium-Kiosk auf Steam Deck, Browser sonst ✓ |
| Flatpak | Phase 5, nach dem Rest ✓ |
| Windows | Nur "sollte gehen", kein aktives Testen ✓ |
