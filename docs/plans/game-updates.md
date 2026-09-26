# DeckDrop – Spiel-Updates & Versionen im Netzwerk

## Kontext

Heute kann DeckDrop ein Spiel nur **einmal komplett** übertragen. Wird das Spiel danach auf PC1
gepatcht, passiert Folgendes:

- DeckDrop merkt nichts. PC1 seedet weiter mit `seed_mode` (libtorrent prüft nicht) → Empfänger
  bekommen kaputte Pieces bzw. Hash-Fehler.
- Auf dem Steam Deck gibt es keinen Weg, nur die geänderten Daten nachzuladen.
- **Bestehender Bug:** Ein heruntergeladenes Spiel bekommt eine **neue** Spiel-ID
  (`transfer.py::_register_downloaded_game` → `game_mod.create_new`). PC1 und Deck wissen also
  nicht, dass es dasselbe Spiel ist. Auch `added_by` wird mit dem Namen des Empfängers
  überschrieben.
- **Bestehender Bug:** Der Torrent-Wurzelordner ist der Ordnername des Hosts, `dest_path` ist aber
  `download_dir / <Spielname>`. Weichen beide ab, landen die Dateien im falschen Ordner.

**Ziel:** Veränderungen werden erkannt, ein verändertes Spiel ist nicht mehr teilbar, bis der
Nutzer es als **Update** (Versionsname + Notiz) veröffentlicht. Andere Geräte sehen das Update mit
allen Infos (Ersteller, wer aktualisiert hat, Version, Notiz, Verlauf) und können es übernehmen:
Unveränderte Dateien bleiben liegen, geladen wird nur, was sich geändert hat (libtorrent
Piece-Ebene). Beim Erst-Download sieht man alle Versionen im Netz und wählt eine aus.

---

## Getroffene Entscheidungen (bitte bei Freigabe prüfen)

1. **Nur Änderungen an verteilten Dateien machen ein Spiel „verändert“.** Neue Dateien (Spielstände,
   Logs, Shader-Cache, Configs) blockieren das Teilen **nicht**. Sie werden nur als Hinweis gezeigt
   und können beim Veröffentlichen eines Updates mit aufgenommen werden.
2. **Jeder darf ein Update veröffentlichen** (PC1 oder Deck). Die Revisionsnummer wird hochgezählt;
   haben zwei Geräte unabhängig voneinander ein Update gemacht, erscheinen beide als eigene
   Version zur Auswahl.
3. **Updates werden direkt im Spielordner eingespielt** (kein zweiter Ordner, spart Platz auf dem
   Deck). Während das Update läuft, ist das Spiel als „wird aktualisiert“ markiert. Nach einem
   Neustart läuft es weiter.
4. **Keine automatischen Updates.** Der Nutzer sieht „Update verfügbar“ und entscheidet selbst.
5. Die bestehende Feldbezeichnung `game.version` (Metadaten-Zähler, wird bei jeder Bearbeitung
   erhöht und für den Metadaten-Sync genutzt) bleibt **unverändert**. Die Inhaltsversion ist neu und
   lebt in `[content]`. In der UI wird nur die Inhaltsversion als „Version“ angezeigt.
6. **Umsetzung in Phasen**, jede Phase einzeln mergebar. Phase 6 (Schnellpfad) und Phase 7
   (Reparieren) sind optional.

---

## Grundidee (wie es technisch „schlau“ wird)

1. **Manifest = Wahrheit über den Inhalt.** Pro Datei: relativer Pfad, blake2b-Hash, Größe (steht
   schon teilweise in `deckdrop.toml [files]`). Daraus ergibt sich `content_hash` (Hash über das
   sortierte Manifest). Er identifiziert eine Version **unabhängig** von Ordnername, Piece-Größe oder
   libtorrent-Version.
2. **Lokaler Snapshot** (`size`, `mtime_ns` pro Datei, außerhalb des Spielordners) → Änderungen
   werden mit reinem `stat()` erkannt (schnell). Nur wenn sich allein die mtime geändert hat, wird
   die eine Datei nachgehasht, um Fehlalarme zu vermeiden.
3. **Hybride v1+v2-Torrents mit Datei-Ausrichtung** (libtorrent ≥ 2.0 Standard): Jede Datei beginnt
   auf einer Piece-Grenze. Unveränderte Dateien haben dadurch **identische Pieces**, egal was sich
   an anderen Dateien ändert.
4. **Update einspielen = neuen Torrent auf den bestehenden Ordner zeigen lassen.** libtorrent prüft
   die vorhandenen Daten und lädt **nur fehlende/abweichende Pieces**. Das ist Delta-Übertragung
   auf Piece-Ebene, auch innerhalb großer geänderter Dateien. Vorher: verschobene Dateien lokal
   verschieben/kopieren, zu große Dateien kürzen. Danach: entfernte Dateien löschen.
5. **Schnellpfad (Phase 6):** Pieces unveränderter Dateien werden per `have_pieces` als vorhanden
   markiert (ohne Hashen), Pieces geänderter Dateien werden selbst per SHA1 gegen die Piece-Hashes
   geprüft. So wird nur geladen, was wirklich anders ist, ohne das ganze Spiel neu zu prüfen.
6. **Swarm:** Alle Peers mit derselben Version (`content_hash` + `info_hash`) werden gleichzeitig
   verbunden (Multi-Source).

---

## Datenmodell

### `deckdrop.toml` (im Spielordner, wird mit Peers geteilt) – Erweiterung

```toml
[game]            # unverändert (id, name, version=Metadaten-Zähler, added_by, ...)

[content]
revision = 2                      # int, startet bei 1
version_label = "1.6.8"           # Freitext, optional ("" → UI zeigt "Rev. 2")
note = "Patch 1.6.8 + Hotfix"     # Update-Notiz, optional
created_by = "alice"              # wer das Spiel ursprünglich geteilt hat
created_at = "2026-09-01T10:00:00+00:00"
updated_by = "bob"                # wer diese Revision veröffentlicht hat
updated_at = "2026-09-26T12:00:00+00:00"
content_hash = "3f9a…"            # 32 hex, siehe content.compute_content_hash
ignore = ["saves/**"]             # zusätzliche Ignore-Muster (fnmatch auf POSIX-relpath)

[[history]]                        # älteste zuerst, letzte = aktuelle Revision
revision = 1
version_label = ""
note = "Erstveröffentlichung"
by = "alice"
at = "2026-09-01T10:00:00+00:00"
content_hash = "…"

[files]   # relpath → blake2b hex (existiert schon)
[sizes]   # relpath → int (neu)
```

**Legacy-Laden** (kein `[content]`): `revision=1`, `version_label=""`, `note=""`,
`created_by=game.added_by`, `created_at=game.added_at`, `updated_by=created_by`,
`updated_at=created_at`, `content_hash=""`, `ignore=[]`, `history=[]`. Fehlt `[sizes]` →
leeres Dict (wird beim nächsten Scan/Hash gefüllt). **Nicht** beim Laden zurückschreiben, erst wenn
ohnehin gespeichert wird.

### Lokaler Zustand (NICHT geteilt): `~/.local/share/deckdrop/content/<game_id>.json`

Pfad als `Config.content_state_dir` (analog `resume_dir`: `torrent_cache.parent / "content"`).

```json
{
  "state": "clean",                 // clean | modified | hashing | publishing | updating
  "snapshot": {"bin/game.exe": [123456, 1727350000000000000]},
  "summary": {"changed": 0, "removed": 0, "added": 0},
  "last_scan": 1727350000.0,
  "pending_update": null            // während Update: {"download_id", "target_manifest", "old_manifest"}
}
```

### Standard-Ignore-Muster (`core/content.py::DEFAULT_IGNORE`)

`*.log`, `*.tmp`, `*.dxvk-cache`, `*.vkd3d-proton.cache*`, `Thumbs.db`, `.DS_Store`, `desktop.ini`
plus alles aus `integrity.TORRENT_SKIP_FILENAMES`.
**Regel:** Ignorierte Dateien werden nie erkannt, nie verteilt und **nie** bei einem Update gelöscht.

---

## Neue/geänderte Module – Überblick

| Datei | Änderung |
|---|---|
| `deckdrop/core/content.py` | **NEU.** Reine Funktionen (ohne libtorrent, ohne I/O-Seiteneffekte außer Lesen): Manifest, `content_hash`, Ignore, Snapshot-Vergleich, Diff, lokaler Vorbereitungsplan, Piece-Mathematik |
| `deckdrop/core/content_tracker.py` | **NEU.** `ContentTracker`: Zustand pro Spiel, Scan-Thread, Publish-Job, persistiert JSON, sendet WS-Events (Muster wie `torrent_prep.py`: Thread + `_emit`) |
| `deckdrop/core/game.py` | `ContentInfo`, `HistoryEntry`, `GameInfo.content`, `GameInfo.history`, `GameInfo.sizes`, Laden/Speichern, `manifest_dict()`, `apply_manifest()` |
| `deckdrop/core/config.py` | `content_state_dir`, `content_scan_interval` (Default 300 s) |
| `deckdrop/core/torrent.py` | `create_torrent_data(game_path, files=None, piece_size=None)`, Piece-Größen-Regel, `retarget_root(lt, ti, folder_name)` |
| `deckdrop/core/torrent_prep.py` | Torrent aus Manifest-Dateiliste bauen; Prep auch für heruntergeladene Spiele erlauben, wenn explizit angestoßen |
| `deckdrop/api/state.py` | `AppState.content: ContentTracker` |
| `deckdrop/api/routes/games.py` | Neue Felder in `GameOut`; neue Endpunkte (siehe unten); `/magnet` verweigert bei nicht teilbar |
| `deckdrop/api/routes/downloads.py` | `version_key` beim Start; Manifest + .torrent holen; mehrere Peers |
| `deckdrop/network/peer_registry.py` | Netzwerk-Gruppierung nach ID + Versionen; Legacy-ID-Verknüpfung |
| `deckdrop/network/transfer.py` | `kind="update"`, Torrent-Bytes statt Magnet, Root-Umbenennung, Finalisieren eines Updates, Multi-Peer |
| `deckdrop/network/resume.py` | `ResumeStore.save_manifest/load_manifest/save_torrent` |
| `deckdrop/main.py` | Tracker erzeugen, Baseline + Scan-Loop starten, Legacy-Migration |
| `frontend/api.js` | Neue Aufrufe |
| `frontend/components/GameCard.js` | Badges/Buttons: Verändert, Update verfügbar, Wird aktualisiert, Versionsanzeige |
| `frontend/components/PublishUpdate.js` | **NEU.** Dialog „Update veröffentlichen“ |
| `frontend/components/VersionList.js` | **NEU.** Versionsliste (wiederverwendet für Erst-Download + Update) |
| `frontend/components/UpdateGame.js` | **NEU.** Dialog „Update übernehmen“ |
| `frontend/components/MyGames.js`, `Network.js`, `Downloads.js` | Einbindung |

---

## Phase 0 – libtorrent-API prüfen (Spike, ~1 h)

CI hat **kein** libtorrent. Deshalb muss alles libtorrent-Spezifische dünn bleiben, die Logik
selbst liegt in `content.py` (testbar ohne libtorrent).

Lokal `pip install libtorrent` (≥ 2.0.9) und ein Wegwerf-Skript im Scratchpad ausführen, das prüft
und die Ergebnisse als Kommentar oben in `core/torrent.py` festhält:

- `lt.create_torrent(fs, piece_size)` erzeugt standardmäßig hybride Torrents:
  `lt.torrent_info(...).info_hashes().has_v2()` ist `True`; Pad-Dateien vorhanden
  (`fs.file_flags(i) & lt.file_storage.flag_pad_file`).
- Jede Nicht-Pad-Datei beginnt auf Piece-Grenze: `fs.file_offset(i) % ti.piece_length() == 0`.
- `ti.rename_file(i, new_path)` vor `add_torrent` funktioniert; `handle.rename_file(i, path)` nach
  Metadaten-Empfang ebenso.
- `ti.hash_for_piece(p)` liefert den v1-SHA1 (20 Bytes); `ti.piece_size(p)`.
- `params.have_pieces = [bool, ...]` wird akzeptiert (sonst: Phase 6 entfällt, Fallback nutzen).

---

## Phase 1 – Datenmodell, Manifest, ID-Fix (kein sichtbares Verhalten)

### 1.1 `core/content.py` (neu, reine Funktionen)

```python
DEFAULT_IGNORE: tuple[str, ...]

def is_ignored(rel: str, patterns: Iterable[str]) -> bool
    # rel = POSIX relpath; TORRENT_SKIP_FILENAMES auf basename prüfen,
    # dann fnmatch.fnmatchcase(rel, p) or fnmatchcase(basename, p) für DEFAULT_IGNORE + patterns.
    # Muster mit "/**" am Ende matchen ganze Ordner (rel.startswith(prefix + "/")).

def iter_content_files(root: Path, patterns) -> list[str]
    # sortierte relpaths aller Dateien unter root, die nicht ignoriert sind. Symlinks nicht folgen.

def compute_content_hash(files: dict[str, str], sizes: dict[str, int]) -> str
    # blake2b(digest_size=16) über "\n".join(f"{rel}\t{files[rel]}\t{sizes.get(rel, -1)}"
    #   for rel in sorted(files)).hexdigest(); "" wenn files leer.

def take_snapshot(root: Path, rels: Iterable[str]) -> dict[str, list[int]]
    # rel → [st_size, st_mtime_ns]; fehlende Dateien weglassen.

@dataclass
class ScanResult:
    changed: list[str]; removed: list[str]; added: list[str]
    mtime_only: list[str]   # Größe gleich, mtime anders → Aufrufer hasht nach

def compare_snapshot(root, manifest_files: dict[str,str], snapshot, patterns) -> ScanResult
    # Für jede Datei im Manifest: fehlt → removed; Größe ≠ Snapshot → changed;
    #   mtime ≠ Snapshot → mtime_only. Dateien auf Platte, nicht im Manifest, nicht ignoriert → added.

@dataclass
class ManifestDiff:
    unchanged: list[str]; changed: list[str]; added: list[str]; removed: list[str]
    moved: dict[str, str]          # new_rel → old_rel (gleicher Hash, anderer Pfad)
    download_estimate: int | None  # Summe sizes der zu ladenden Dateien; None wenn Hashes fehlen

def diff_manifests(old_files, old_sizes, new_files, new_sizes) -> ManifestDiff

@dataclass
class LocalPrepPlan:
    moves: list[tuple[str, str]]   # (old_rel, new_rel) – old_rel ist im neuen Manifest NICHT mehr drin
    copies: list[tuple[str, str]]  # (old_rel, new_rel) – old_rel bleibt im neuen Manifest
    truncates: list[tuple[str, int]]  # (rel, new_size) – lokale Datei größer als Zielgröße
    deletes_after: list[str]       # im alten, nicht im neuen Manifest, nicht ignoriert, nicht verschoben

def plan_local_prep(root, diff, new_sizes, new_ignore) -> LocalPrepPlan
def apply_local_prep(root, plan) -> None      # moves via os.replace, copies via shutil.copy2,
                                              # truncates via os.truncate; Zielordner anlegen
def delete_removed(root, rels) -> None         # löscht Dateien, danach leere Ordner (bottom-up)
```

### 1.2 `core/game.py`

- `@dataclass ContentInfo` (Felder wie `[content]` oben, inkl. `ignore: list[str]`) und
  `@dataclass HistoryEntry(revision, version_label, note, by, at, content_hash)`.
- `GameInfo` erhält `content: ContentInfo`, `history: list[HistoryEntry]`, `sizes: dict[str,int]`.
- `to_dict()` schreibt `[content]`, `[[history]]` (nur wenn nicht leer), `[sizes]` (nur wenn nicht leer).
- `load_from_path()` liest sie inkl. Legacy-Defaults (siehe oben).
- `create_new(...)` setzt `content = ContentInfo(revision=1, created_by=added_by, created_at=now, updated_by=added_by, updated_at=now)`.
- `manifest_dict(g) -> dict`: das JSON, das `/manifest` ausliefert:
  `{"id","name","platform","steam_app_id","description","launch_exe","launch_args","runner",
    "added_by","added_at","content": {...}, "history": [...], "files": {...}, "sizes": {...},
    "info_hash": g.torrent.info_hash}`.
- `apply_manifest(g, m, *, keep_local_meta: bool)`: übernimmt `content`, `history`, `files`, `sizes`;
  bei `keep_local_meta=False` (Erst-Download) auch `id`, `name`, `platform`, Steam-Felder,
  `description`, `launch_exe`, `added_by`, `added_at`.

### 1.3 ID-Fix beim Download (`transfer.py::_register_downloaded_game`)

- Neues Spiel wird **mit der Host-ID** angelegt: `info = game_mod.create_new(...)`, danach
  `info.id = h.game_id`. Wenn ein Manifest für den Download gespeichert ist (Phase 4), stattdessen
  `apply_manifest(info, manifest, keep_local_meta=False)`.
- `added_by` bleibt der Host-Ersteller (aus Manifest bzw. `remote_game["added_by"]`).
- Test: `tests/test_download_keeps_id.py` – nach Registrierung gilt `load_from_path(dest).id == h.game_id`.

### 1.4 Legacy-Verknüpfung (bereits heruntergeladene Spiele mit falscher ID)

In `peer_registry._sync_from_peer`: Für ein Remote-Spiel `rg` ohne lokalen Treffer per ID suchen wir
ein lokales Spiel `l` mit `l.origin.peer_id == peer_id`, `l.name == rg["name"]` und
`abs(l.size_bytes - rg["size_bytes"]) < 1 MiB`, dessen ID beim Peer **nicht** vorkommt. Treffer →
`relink_game_id(l, rg["id"])` (neue Funktion in `core/library.py`): ID in toml setzen, speichern,
`torrent_cache/<alt>.torrent` → `<neu>.torrent` umbenennen (falls vorhanden und Ziel fehlt),
Content-JSON umbenennen, Library neu indizieren, Log-Zeile. Test mit zwei Libraries.

### 1.5 Ordnername-Fix (Torrent-Root ≠ Zielordner)

`core/torrent.py`:

```python
def retarget_root(lt, ti, folder_name: str) -> None:
    """Rename the torrent's top-level folder so files land in <save_path>/<folder_name>/..."""
    fs = ti.files()
    for i in range(fs.num_files()):
        old = fs.file_path(i)              # "HostFolder/sub/file.bin"
        parts = old.replace("\\", "/").split("/", 1)
        if parts[0] != folder_name:
            ti.rename_file(i, folder_name + ("/" + parts[1] if len(parts) > 1 else ""))
```

Verwenden in: `TransferManager.seed_from_cache` (`folder_name = game_path.name`), überall wo ein
Torrent aus Bytes/Datei hinzugefügt wird (`resume.params_from_torrent_file` bekommt Parameter
`folder_name`), und für Magnet-Downloads in `_on_metadata_received` über `handle.rename_file`.
Die Umbenennung ändert den `info_hash` **nicht**.

### 1.6 Tests Phase 1

`tests/test_content.py` (ohne libtorrent): `is_ignored`, `compute_content_hash` (Reihenfolge egal,
Pfad-/Größenänderung ändert Hash), `compare_snapshot` (changed/removed/added/mtime_only, ignorierte
Dateien unsichtbar), `diff_manifests` (inkl. moved, estimate), `plan_local_prep` + `apply_local_prep`
+ `delete_removed` auf `tmp_path`. `tests/test_game.py` erweitern: Legacy-toml lädt Defaults;
Roundtrip `[content]`, `[[history]]`, `[sizes]`.

### Abweichungen Phase 0/1 (umgesetzt 2026-09-26)

- **Phase 0 bestätigt** mit libtorrent 2.1.1.0 (pip-Wheel): hybride v1+v2-Torrents per Default,
  Pad-Dateien vorhanden, Datei-Ausrichtung auf Piece-Grenzen, `rename_file` (Torrent-Info und
  Handle) funktioniert, `hash_for_piece`/`piece_size` wie erwartet, **`params.have_pieces`
  funktioniert** → Phase 6 ist möglich. Siehe Kommentarblock oben in `deckdrop/core/torrent.py`
  für Details. Einzige Abweichung: `ti.files()`, `create_torrent(fs, piece_size)` und
  `rename_file()` sind in 2.1.1 als deprecated markiert (funktionieren aber weiterhin) – für einen
  späteren libtorrent-Major-Bump ggf. auf die Nachfolge-APIs migrieren.
- **`retarget_root`**: für den Magnet-Metadaten-Pfad (`_on_metadata_received`) wird abweichend vom
  Wortlaut ("über `handle.rename_file`") die bereits vorhandene `retarget_root(lt, ti, folder_name)`
  wiederverwendet, mit `ti = handle.torrent_file()`. Ein Test hat bestätigt, dass das Umbenennen
  dieses `torrent_info`-Objekts sich sofort auf den Handle auswirkt (gleiche zugrunde liegende
  Struktur) – ein separates `handle.rename_file(i, path)` ist dafür nicht nötig. Funktional
  identisch zum Plan, aber ohne Code-Duplikation.
- `content.diff_manifests`/`plan_local_prep`: "alte Datei bleibt im neuen Manifest" (→ `copies`)
  kann laut der hier gewählten Zuordnungslogik nur auftreten, wenn **derselbe** alte Pfad als Quelle
  für **mehrere** neue Pfade dient (doppelte Datei wird an zwei Stellen wiederhergestellt); der
  erste Treffer wird per `move` (rename) aufgelöst, jeder weitere Treffer per `copy`. Das deckt den
  in Phase 5 relevanten Fall ab, ohne die in Phase 0/1 nicht genutzten Piece-Mathematik-Funktionen
  aus Phase 6 vorwegzunehmen.
- `config.py`: Der neue `content_scan_interval` liegt unter einer neuen `[content]`-Sektion in
  `config.toml` (`scan_interval`, Default 300), nicht unter `[transfer]`, um ihn sauber von den
  Übertragungs-Einstellungen zu trennen.

---

## Phase 2 – Änderungen erkennen, veränderte Spiele sperren

### 2.1 `core/content_tracker.py`

```python
class ContentTracker:
    def __init__(self, cfg, library): ...     # lädt alle <id>.json aus cfg.content_state_dir
    def bind_loop(self, loop): ...
    def state(self, game_id) -> str          # "clean" default
    def summary(self, game_id) -> dict
    def is_shareable(self, game_id) -> bool   # state == "clean"
    def ensure_baseline(self, game_id) -> None
    def scan(self, game_id) -> str            # sync, im Worker-Thread aufrufen
    def scan_all_async(self) -> None          # startet Daemon-Thread, max. 1 gleichzeitig
    def start_periodic(self) -> None          # asyncio-Task, alle cfg.content_scan_interval s
    def set_state(self, game_id, state, **extra) -> None   # persistiert + emit "game_content_state"
```

**Baseline** (`ensure_baseline`, beim Start für jedes Spiel ohne JSON und nach Hinzufügen):
- `g.files` leer → komplett hashen (`integrity.hash_file`), `files`/`sizes` füllen,
  `content_hash` berechnen, toml speichern. Ersetzt `games.py::_hash_game_files` (dort nur noch
  `content.ensure_baseline` aufrufen). Das bisherige `invalidate_torrent` bei erster Hash-Füllung
  **entfällt** (baut sonst den gerade erzeugten Torrent sofort neu).
- `g.files` vorhanden → nur `snapshot = take_snapshot(...)`, `sizes` ergänzen falls leer.
- Zustand `clean`. Während des Hashens Zustand `hashing` (nicht teilbar).

**Scan** (`scan`):
1. Zustände `hashing|publishing|updating` → nichts tun.
2. `compare_snapshot(...)`.
3. Für `mtime_only`: Datei hashen; gleich wie Manifest → Snapshot-Eintrag aktualisieren; sonst → `changed`.
4. `changed` oder `removed` nicht leer → `modified`; sonst `clean`. `summary` mit allen drei Zählern
   (auch `added`, rein informativ).
5. Übergang `clean → modified`: `transfer.drop_seed(id)`, WS-Event `game_content_state`,
   `peer_registry`-Peers merken es beim nächsten Poll (über `shareable=false`).
6. Übergang `modified → clean` (Nutzer hat Änderungen rückgängig gemacht): `transfer.seed_from_cache`
   erneut aufrufen.

**Auslöser:**
- App-Start (nach `library.reload`, in `main.py` Lifespan): `ensure_baseline` für alle, danach `scan_all_async`.
- Periodisch alle 300 s.
- `POST /api/games/scan` – ruft die UI beim Öffnen von „Meine Spiele“ auf (nach Patch auf PC1
  sieht man die Änderung sofort).
- **Vor dem Ausliefern** von `/magnet`, `/torrent`, `/manifest`: `scan(game_id)` synchron (nur `stat`,
  schnell) → verhindert, dass je veränderte Daten verteilt werden.

### 2.2 Sperre durchsetzen

- `GameOut` neue Felder: `content_state`, `shareable`, `change_summary`, `revision`,
  `version_label`, `version_note`, `created_by`, `content_updated_by`, `content_updated_at`,
  `content_hash`.
  `shareable = has_torrent and tracker.is_shareable(id)`.
- `/magnet` (+ später `/torrent`, `/manifest`): nicht teilbar → `409` mit
  `"Spiel wurde verändert – erst als Update veröffentlichen."`.
- `TransferManager.seed_all_shared`/`seed_from_cache`: nur seeden, wenn teilbar.
- `peer_registry._needs_fast_refresh`: Spiele mit `content_state == "modified"` **nicht** als
  „wartet auf Torrent“ zählen (sonst 3-s-Polling für immer).

### 2.3 UI (Phase 2)

- `GameCard` (mode `own`): bei `content_state === 'modified'` oranges Badge **„Verändert“** und
  Hinweis „Wird nicht geteilt“. Unter dem Namen die Version: `version_label || 'Rev. ' + revision`.
- `MyGames.js`: beim Mount `api.scanGames()`; WS-Event `game_content_state` → Spiel neu laden.

### 2.4 Tests Phase 2

`tests/test_content_tracker.py`: Baseline setzt clean; Datei ändern (Größe) → modified +
`drop_seed` aufgerufen (MagicMock-Transfer); nur `os.utime` → bleibt clean; neue Datei → clean mit
`added=1`; Datei in `saves/` bei `ignore=["saves/**"]` → unsichtbar; `/magnet` liefert 409 wenn modified.

---

## Phase 3 – Update veröffentlichen

### 3.1 Torrent aus dem Manifest bauen

`core/torrent.py::create_torrent_data(game_path, files: list[str] | None = None, piece_size: int | None = None, on_progress=None)`:
- `files` = Relpaths aus dem Manifest (sortiert). `None` → bisheriges Verhalten (`iter_torrent_files`).
- Piece-Größe: `choose_piece_size(total_bytes)` → `1 MiB` bis < 2 GiB, `2 MiB` bis < 16 GiB,
  sonst `4 MiB`. Kleine Pieces = feineres Delta bei Updates. Gilt nur für neu gebaute Torrents;
  bestehende Caches bleiben (kein Massen-Rebuild).
- **Niemals** `v1_only` setzen (Datei-Ausrichtung ist Pflicht für Delta-Updates).
- `torrent_prep._prepare`: ruft `create_torrent_data(g.path, files=sorted(g.files) or None, ...)`.
- `GameOut.from_info`: `preparing` ist auch für heruntergeladene Spiele `True`, wenn
  `torrent_prep.is_preparing(id)` (heute nur für lokal hinzugefügte).

### 3.2 Publish-Job (`ContentTracker.publish`)

`publish(game_id, version_label: str, note: str, exclude: list[str]) -> None` startet Thread:

1. Zustand `publishing`, `drop_seed`.
2. Dateiliste = `iter_content_files(root, content.ignore + exclude)`.
3. Pro Datei: Snapshot-Treffer (Größe + mtime gleich) **und** Hash im Manifest → Hash übernehmen;
   sonst hashen. Fortschritt per `_emit("content_publish_progress", {game_id, progress})`.
4. Neuer `content_hash`. Gleich wie alt → keine neue Revision, Snapshot aktualisieren, Zustand
   `clean`, Event `content_publish_complete` mit `unchanged: true`, **fertig**.
5. Sonst: `revision += 1` (bzw. `max(history.revision)+1`), `version_label`, `note`,
   `updated_by = cfg.user_name`, `updated_at = now`, `content.ignore += exclude` (exakte Relpaths),
   `history.append(HistoryEntry(...))`, `files`/`sizes`/`size_bytes` setzen, `game_mod.save`.
6. Snapshot neu schreiben, Zustand `clean`.
7. `torrent_prep.invalidate_torrent(game_id)` (baut neuen Torrent → neuer `info_hash`, seedet,
   `peer_registry.trigger_refresh`).
8. Event `content_publish_complete {game_id, revision, version_label}`. Fehler → Zustand
   `modified`, Event `content_publish_error`.

### 3.3 API (local_only)

- `POST /api/games/scan` → `{game_id: state}` für alle (Scan im Thread, Antwort nach Abschluss).
- `POST /api/games/{id}/scan` → `GameOut`.
- `GET /api/games/{id}/changes` → `{"changed":[{path,old_size,new_size}], "removed":[{path,size}], "added":[{path,size}], "revision", "version_label"}` (führt `scan` aus; Listen max. 500 Einträge + `truncated: bool`).
- `POST /api/games/{id}/publish` Body `{version_label: str = "", note: str = "", exclude: list[str] = []}` → `202`.
  `400`, wenn Zustand `hashing|publishing|updating`. `exclude` darf nur Pfade enthalten, die im
  Spielordner liegen (kein `..`, kein absoluter Pfad → sonst `400`).

### 3.4 UI: `PublishUpdate.js`

Öffnet sich über den Button **„Update veröffentlichen…“** auf der Karte (bei `modified`) und im
`EditGame`-Dialog (immer, z. B. um nur neue Dateien aufzunehmen).

```
┌ Update veröffentlichen – Stardew Valley ───────────────┐
│ Aktuell: 1.6.7 (Rev. 3) · erstellt von Alice            │
│ Änderungen: 12 geändert · 3 neu · 1 entfernt  [Details ▾]│
│   (Details: Liste; neue Dateien mit Checkbox,           │
│    abgewählt = "lokale Datei, nicht teilen")            │
│ Version   [ 1.6.8                    ]                  │
│ Notiz     [ Patch 1.6.8, Crash-Fix    ]                 │
│                  [Abbrechen]  [Veröffentlichen]         │
└──────────────────────────────────────────────────────────┘
```

- Standard: alle neuen Dateien sind angehakt (einfaches, vorhersehbares Verhalten; der Nutzer sieht
  die Liste und hakt z. B. Spielstände ab → diese landen als `exclude` in `content.ignore`).
- Nach dem Absenden: Karte zeigt „Update wird vorbereitet… x %“ (Events `content_publish_progress`,
  danach die bestehenden `torrent_prep_*`-Events). Toast „Update 1.6.8 veröffentlicht“.
- Controller-bedienbar (Tab-Reihenfolge, Enter), wie die bestehenden Dialoge (`EditGame.js` als Vorlage).

### 3.5 Tests Phase 3

`tests/test_publish.py`: Datei ändern → publish → `revision == 2`, `history[-1].note`, neuer
`content_hash`, `invalidate_torrent` aufgerufen (monkeypatch), Zustand clean. Nur mtime geändert →
`unchanged`. `exclude` landet in `content.ignore`, Datei nicht mehr im Manifest. API 400 bei Pfad `../x`.
libtorrent-Test (`pytest.importorskip("libtorrent")`): `create_torrent_data(files=[...])` enthält nur
diese Dateien, `has_v2()` ist True, Nicht-Pad-Dateien piece-ausgerichtet.

### Abweichungen Phase 2/3 (umgesetzt 2026-09-26)

- **`ContentTracker` ist eine Instanz, nicht das Modul-Singleton-Muster von `torrent_prep.py`.**
  `AppState.content: ContentTracker | None` mit einer `get_content_tracker()`-Methode, die bei
  `None` lazy eine neue Instanz aus `self.cfg`/`self.library` erzeugt. Dadurch bleibt
  `app_state.init(cfg, library)` (ohne `content`-Argument) für alle bestehenden Tests
  rückwärtskompatibel, und `deckdrop/api/routes/games.py` sowie
  `TransferManager._is_shareable` rufen immer `s.get_content_tracker()` statt `s.content` direkt.
  `main.py` erzeugt den Tracker weiterhin explizit und übergibt ihn an `app_state.init`, damit
  Baseline/Scan-Loop/`bind_loop` am echten, langlebigen Objekt hängen.
- **`scan()`/`ensure_baseline()`/`publish()` greifen für Seed-Auf/Abbau lazy per
  `from deckdrop.api import state as app_state` auf `TransferManager` zu** (gleiches
  Fail-open-Muster wie in `transfer.py::_registry_peer_address`), statt eine `transfer`-Referenz
  im Konstruktor zu verlangen – vermeidet einen Import-Zyklus und deckt sich mit den
  Testerwartungen aus 2.4 (MagicMock-Transfer via `app_state.init(..., transfer=mock)`).
- **Detaillierte Änderungslisten** (`changed`/`removed`/`added`-Pfade, nicht nur die Zähler aus
  `summary`) werden zusätzlich im persistierten Zustand gespeichert (`ContentTracker.change_lists`)
  statt bei jedem `GET /changes`-Aufruf erneut aus dem Snapshot rekonstruiert zu werden – vermeidet
  eine zweite, potenziell abweichende Reklassifizierung von `mtime_only`-Dateien.
- **`publish()` löst zusätzlich eine neue Revision aus, wenn sich nur `content.ignore` ändert**
  (neuer `exclude`, aber zufällig gleicher `content_hash`), nicht nur bei geändertem Hash – sonst
  könnte ein Nutzer eine Datei aus dem Teilen nehmen, ohne dass sich am veröffentlichten Zustand
  sichtbar etwas ändert.
- `POST /api/games/scan` überspringt heruntergeladene Spiele (`origin.peer_id`/`peer_name` gesetzt)
  – deren lokales Hashing/Tracking ist Teil von Phase 4/5, noch nicht Bestandteil dieses Plans.
- `create_torrent_data(files=...)` bekommt zusätzlich `piece_size` als expliziten Override-Parameter
  (Default: `choose_piece_size(total_bytes)`), wie in 3.1 beschrieben, aber nicht separat
  aufgelistet – nötig, damit spätere Phasen (Delta-Update) exakt dieselbe Piece-Größe wie beim
  letzten Build erzwingen können, falls das je gebraucht wird.
- **Korrektur (umgesetzt mit Phase 4, 2026-09-26):** Die oben genannte Ausnahme
  ("`POST /api/games/scan` überspringt heruntergeladene Spiele") wird zurückgenommen. Laut
  Entscheidung 2 darf jeder ein Update veröffentlichen und heruntergeladene Kopien werden ebenfalls
  geseedet, also müssen sie genauso erkannt/gesperrt werden wie lokal geteilte Spiele.
  `scan_all_games` scannt jetzt alle Spiele ohne `origin`-Ausnahme, und
  `TransferManager._register_downloaded_game` ruft nach der Registrierung
  `ContentTracker.ensure_baseline` auf (Snapshot, falls das Manifest schon Hashes lieferte; sonst
  volles Hashen wie bei einem lokal hinzugefügten Spiel). Tests:
  `tests/test_downloaded_game_scan.py`.
- **Korrektur (umgesetzt mit Phase 4, 2026-09-26):** Die Baseline-Hash-Runde für alle Spiele beim
  Start (`main.py` Lifespan) lief bisher synchron und konnte den Serverstart bei einer großen
  Bibliothek/langsamer SD-Karte blockieren. Sie läuft jetzt in einem eigenen Daemon-Thread
  (`deckdrop.main._baseline_all_then_scan`, extrahiert als modulweite Funktion, damit sie ohne
  laufenden Server testbar ist); jedes Spiel bleibt währenddessen im Zustand `hashing` (nicht
  teilbar) – das leistet `ContentTracker.ensure_baseline` bereits von sich aus. Test:
  `tests/test_main_baseline_thread.py`.

---

## Phase 4 – Versionen im Netzwerk + Erst-Download mit Versionswahl

### 4.1 Peer-Endpunkte (öffentlich, ohne `local_only`)

- `GET /api/games/{id}/manifest` → `game_mod.manifest_dict(g)`; 409 wenn nicht teilbar.
- `GET /api/games/{id}/torrent` → `Response(content=cache.read_bytes(), media_type="application/x-bittorrent")`;
  409 wenn nicht teilbar oder in Vorbereitung (wie `/magnet`, inkl. `schedule_prepare`).
- Beide machen vorher `content.scan(id)`.

### 4.2 Netzwerk-Gruppierung (`peer_registry.all_network_games`)

Neu nach **Spiel-ID** gruppieren (heute Name+Größe). Innerhalb eines Spiels nach
`version_key = content_hash or info_hash or f"legacy:{peer_id}"` gruppieren:

```python
{
  **primary_game_fields,              # vom Peer der empfohlenen Version (wie bisher)
  "versions": [                       # sortiert: revision desc, content_updated_at desc
    {"version_key", "revision", "version_label", "version_note",
     "created_by", "content_updated_by", "content_updated_at", "size_bytes",
     "info_hash", "shareable", "has_torrent",
     "peers": [{"peer_id", "peer_name"}]}
  ],
  "version_count": int,               # nur teilbare Versionen
  "peer_count": int, "peer_names": [...],
  "installed": bool,                  # gleiche ID lokal vorhanden
  "local_revision": int | None, "local_version_key": str | None,
  "update_available": bool,           # es gibt teilbare Version mit revision > local_revision
}
```

Empfohlene Version (`primary`) = erste **teilbare** Version mit `has_torrent`. Nicht teilbare
Varianten erscheinen in `versions` mit `shareable=false` (UI zeigt „bei Alice verändert, noch nicht
veröffentlicht“), sind aber nicht wählbar. `_games_changed` zusätzlich auf `content_hash`,
`content_state`, `revision` prüfen.

Hilfsfunktion `peer_registry.peers_for_version(game_id, version_key) -> list[PeerEntry]`
(nur online, teilbar, `has_torrent`).

### 4.3 Download-Start mit Version (`routes/downloads.py`)

- `StartDownloadRequest` + `version_key: str | None = None`. Ohne → empfohlene Version (heutiges
  Verhalten bleibt für alte Clients).
- Spiel mit gleicher ID ist lokal vorhanden → `409 "Spiel ist schon installiert – Update verwenden."`.
- Ablauf: Peers der Version bestimmen → vom ersten erreichbaren **Manifest** (`/manifest`) und
  **Torrent** (`/torrent`) holen. `/torrent` 404/Fehler (alter Peer) → Fallback auf `/magnet` wie heute.
  Manifest-Fehler (alter Peer) → ohne Manifest weiter (Legacy-Registrierung).
- `transfer.start_download(..., torrent_bytes=..., manifest=..., extra_peer_ids=[...], expected_content_hash=...)`.

### 4.4 `TransferManager` Erweiterungen

- `_PersistedRecord` + Felder (alle mit Default, damit alte `downloads-state.json` lädt):
  `kind: str = "new"` (`"new" | "update"`), `extra_peer_ids: list[str]`, `expected_content_hash: str = ""`,
  `local_game_path: str = ""` (nur update).
- `ResumeStore`: `save_manifest(download_id, dict)`, `load_manifest(download_id)`,
  `save_torrent(download_id, bytes)` (legt die Datei so ab, dass `find_metadata` sie findet → dann greift
  der bestehende „resume > torrent > magnet“-Pfad in `_params_for_record` automatisch). `discard`
  löscht auch das Manifest.
- `_params_for_record`: nach dem Laden einer `torrent_info` immer `retarget_root(lt, ti, dest.name)`.
- Verbindung: `connect_peer` zum Hauptpeer **und** zu allen `extra_peer_ids` (Adresse über
  `_registry_peer_address`), sowohl in `start_download` als auch in `_reattach_download` und
  `_ensure_peer_connection`.
- `_maybe_upgrade_from_peer`: nur noch upgraden, wenn der Remote-`content_hash` leer (Legacy) **oder**
  gleich `rec.expected_content_hash` ist (Torrent nur neu gebaut, Inhalt gleich). Sonst nicht
  upgraden – der Nutzer hat eine bestimmte Version gewählt.
- `_register_downloaded_game`: Manifest vorhanden → `apply_manifest(keep_local_meta=False)`, ID = Host-ID,
  `origin` setzen, speichern; `content.ensure_baseline` → nur Snapshot (Hashes kommen aus Manifest).
  Optional: `integrity.verify_files` nur wenn `cfg`-Flag gesetzt (Standard aus, libtorrent hat schon geprüft).

### 4.5 UI

- `VersionList.js` (neu, wiederverwendbar): Radio-Liste, pro Eintrag:
  **Version** (`version_label || Rev. N`) · „von *updated_by* am *Datum*“ · Notiz (2 Zeilen,
  aufklappbar) · „bei Alice, Bob“ · Größe. Nicht teilbare Einträge ausgegraut mit Grund.
  Erste teilbare Version vorausgewählt und mit „Neueste“ markiert.
- `Network.js`: eine Karte pro Spiel. Chip **„3 Versionen“**, wenn `version_count > 1`.
  Klick auf „↓ Laden“: bei genau einer Version sofort wie heute; bei mehreren Dialog mit `VersionList`
  + Button „Diese Version laden“ → `api.startDl({peer_id, game_id, version_key})`.
  Ist das Spiel installiert: Button „✓ Installiert“ (deaktiviert) bzw. **„⟳ Update“** bei
  `update_available` (öffnet `UpdateGame`, Phase 5).
- Karte zeigt „Erstellt von *created_by*“ in der Meta-Zeile.

### 4.6 Tests Phase 4

`tests/test_network_versions.py`: drei Peers, zwei mit gleichem `content_hash`, einer mit neuerer
Revision → eine Gruppe, zwei Versionen, `peers` korrekt, Sortierung, `update_available` bei lokaler
älterer Revision. `tests/test_api_manifest.py`: `/manifest` und `/torrent` (Cache-Datei im tmp) liefern
Daten; 409 wenn modified. Download-Start mit unbekanntem `version_key` → 404; installiertes Spiel → 409.

### Abweichungen Phase 4 (umgesetzt 2026-09-26)

- **`all_network_games` gruppiert weiterhin nach der Spiel-`id`**, wie in 4.2 beschrieben (nicht
  mehr nach Name+Größe wie vor Phase 4). Der bestehende Test
  `test_peer_registry.py::test_all_network_games_groups_same_title` prüfte explizit das alte
  Name+Größe-Verhalten mit zwei unterschiedlichen IDs; er wurde durch
  `test_all_network_games_groups_same_id` (gleiche ID, verschiedene Peers → eine Version) und
  `test_all_network_games_different_ids_not_grouped` (gleicher Name+Größe, verschiedene ID → zwei
  Gruppen) ersetzt – das ist die vom Plan geforderte Verhaltensänderung, kein Bug.
- **`peers_for_version`/`best_update_for` filtern zusätzlich auf `has_torrent`**, nicht nur
  `shareable` – eine Version ohne bereits gebauten Torrent kann nicht heruntergeladen werden, auch
  wenn sie inhaltlich "sauber" (shareable) ist (z. B. direkt nach `publish()`, bevor
  `torrent_prep` fertig ist).
- **`/api/games/{id}/torrent` löst wie `/magnet` `schedule_prepare` aus**, wenn kein Cache-File
  existiert, statt nur 404 zurückzugeben – ein Peer, der eine gerade veröffentlichte Version zum
  ersten Mal abruft, soll die Vorbereitung anstoßen statt ins Leere zu laufen (409 + Empfehlung
  "kurz warten", wie beim bestehenden `/magnet`-Verhalten).
- **`POST /api/download` fällt bei Version-spezifischen Downloads einzeln auf Magnet zurück**, wenn
  `/torrent` beim gewählten Peer 404/Fehler liefert (alter Peer ohne Phase-4-Endpunkte) – Manifest
  und Torrent-Bytes werden dafür unabhängig voneinander per Best-Effort geholt
  (`_fetch_peer_manifest`/`_fetch_peer_torrent`, beide geben bei jedem Fehler `None` zurück statt zu
  werfen); nur wenn am Ende kein `torrent_bytes` vorliegt, greift der bisherige
  `_fetch_magnet_from_peer`-Pfad (der bei einem echten Fehler weiterhin eine `HTTPException` wirft).
  Das deckt „Manifest ja, Torrent nein“ und umgekehrt ab, ohne dass ein alter Peer den ganzen
  Download-Start scheitern lässt.
- **`ResumeStore.save_torrent` ist ein dünner Alias auf `save_metadata`** (identischer Dateiname/-ort,
  den `find_metadata` erwartet) – im Plan als eigene Methode aufgeführt, aber inhaltlich exakt das,
  was `save_metadata` schon für den "Metadaten aus eingehendem Download cachen"-Fall tut. Ein
  eigener Speicherort hätte `_params_for_record`s bestehende resume>torrent>magnet-Priorität
  duplizieren müssen.
- **`_maybe_upgrade_from_peer`-Guard vergleicht `content_hash`, nicht `info_hash`**, um zu erkennen,
  ob der Peer wirklich eine andere Version veröffentlicht hat oder nur denselben Torrent neu gebaut
  hat (z. B. andere Piece-Größe) – wie in 4.4 gefordert. Ist `rec.expected_content_hash` leer (kein
  `version_key` beim Start, alte Clients/empfohlene Version), bleibt das bisherige Verhalten
  (Auto-Upgrade bei jedem neuen `info_hash`) unverändert.
- **`GameOut.update_available`/`update_version_label`** nutzen die neue
  `peer_registry.best_update_for(game_id, local_revision)` bereits jetzt (Phase 5 fordert das
  explizit erst dort), weil `GameCard.js` ohne dieses Feld kein "Update verfügbar"-Badge zeigen
  könnte; die eigentliche Update-Übernahme (`UpdateGame.js`, `POST /api/games/{id}/update`) bleibt
  Phase 5. Bis dahin öffnet der "⟳ Update"-Button in `Network.js` nur einen Toast-Hinweis und
  navigiert zu „Meine Spiele“ (Platzhalter, wie in 4.5 als Minimalversion vorgesehen).
- **`frontend/components/VersionList.js`** verwendet Inline-Styles mit den bestehenden CSS-Variablen
  (`--surface-2`, `--surface-3`, `--text-dim`, `--danger`, `--accent`) statt neuer CSS-Klassen, analog
  zu `PublishUpdate.js` – vermeidet Änderungen an `style.css` für eine erste, noch von Phase 5
  wiederverwendete Komponente.

---

## Phase 5 – Update übernehmen (Empfänger, z. B. Steam Deck)

### 5.1 API (local_only)

- `GET /api/games/{id}/updates` →
  ```json
  {"current": {"revision", "version_label", "content_hash", "state"},
   "versions": [ {..wie VersionList.., "is_newer": bool, "is_current": bool,
                  "download_estimate": int|null, "history": [..letzte 5..]} ]}
  ```
  Holt dafür pro Version einmal `/manifest` von einem Peer (asynchron, `httpx.AsyncClient`, Timeout
  5 s, In-Memory-Cache nach `version_key`), berechnet `diff_manifests(lokal, remote).download_estimate`.
- `POST /api/games/{id}/update` Body `{version_key}` → startet Update, liefert `DownloadOut` (202).
  Fehler: Zustand `hashing|publishing|updating` → 409; Version unbekannt/keine Peers → 404.

### 5.2 Ablauf `TransferManager.start_update(game, peers, torrent_bytes, manifest)`

1. **Vorbereitung (synchron, vor add_torrent):**
   - `drop_seed(game.id)`.
   - `diff = diff_manifests(game.files, game.sizes, manifest.files, manifest.sizes)`.
   - `plan = plan_local_prep(game.path, diff, manifest.sizes, manifest.content.ignore)`;
     `apply_local_prep(game.path, plan)`.
   - Tracker: `set_state(id, "updating", pending_update={download_id, target_manifest, old_manifest: {files,sizes}, deletes_after: plan.deletes_after})`.
2. **Torrent hinzufügen:** `_PersistedRecord(kind="update", dest_path=str(game.path), local_game_path=..., expected_content_hash=manifest.content.content_hash, ...)`,
   Torrent-Bytes via `ResumeStore.save_torrent`, `params.ti` + `retarget_root(ti, game.path.name)`,
   `save_path = game.path.parent`, **kein** `seed_mode`. libtorrent prüft die vorhandenen Dateien
   und lädt nur abweichende Pieces. Connect zu allen Peers der Version.
3. `incomplete_download_dest_paths()` darf Update-Ziele **nicht** enthalten (sonst verschwindet das
   Spiel aus „Meine Spiele“) → Records mit `kind == "update"` überspringen.
4. **Fertig (`_finalize_download`, Zweig `kind == "update"` → neue Methode `_finalize_update`):**
   - `delete_removed(game.path, pending.deletes_after)`.
   - `game_mod.apply_manifest(g, manifest, keep_local_meta=True)`, `origin` = Quell-Peer, `size_bytes`
     aus Summe `sizes`, speichern.
   - Torrent-Bytes nach `torrent_cache/<id>.torrent` kopieren (überschreiben), `g.torrent.info_hash`/
     `magnet` aus `make_magnet` setzen → Deck seedet **dieselbe** Version wie PC1 (gleicher Swarm).
   - Snapshot neu (`take_snapshot`), Zustand `clean`, `pending_update = None`.
   - Seed weiterlaufen lassen (bestehendes `_promote_download_to_seed`).
   - WS `game_updated {game_id, revision, version_label}`; Library reload.
5. **Neustart während Update:** Record wird wie jeder Download per Resume wieder angehängt; beim Start
   setzt der Tracker für Spiele mit `pending_update` den Zustand `updating` (kein Scan, nicht teilbar).
   Wird das Update in der Downloads-Ansicht **entfernt**: Zustand `modified` (Ordner ist gemischt), die
   Karte bietet „Update übernehmen“ erneut an. **Niemals** `delete_files=True` für Update-Records
   (Download-Entfernen-Dialog blendet die Option für `kind=update` aus; API ignoriert sie).
6. Lokal veränderte Dateien (Zustand `modified` vor dem Update) werden überschrieben – die UI warnt
   vorher (siehe 5.3).

### 5.3 UI

- `GameCard` (own): bei `update_available` grünes Badge **„Update verfügbar: 1.6.8“** + Button
  **„Aktualisieren…“**. `update_available` wird in `GameOut` aus `peer_registry` berechnet
  (neue Hilfsfunktion `peer_registry.best_update_for(game_id, local_revision)`).
  Während `updating`: Fortschrittsbalken „Wird aktualisiert… x %“ (aus `download_progress`-Events mit
  passender `game_id`), Hinweis „Spiel nicht starten“.
- `UpdateGame.js`:
  ```
  ┌ Update – Stardew Valley ─────────────────────────────┐
  │ Installiert: 1.6.7 (Rev. 3)                           │
  │ Erstellt von Alice                                    │
  │ ( ) 1.6.8 · Rev. 4 · von Bob am 26.09. · Neueste      │
  │     „Patch 1.6.8, Crash-Fix“ · bei Bob, Alice         │
  │     Zu laden: ca. 1,2 GB von 8,4 GB                   │
  │ ( ) 1.6.8-mod · Rev. 4 · von Carol …                  │
  │ ⚠ Lokale Änderungen an 3 Dateien werden überschrieben │  (nur wenn modified)
  │ Verlauf ▾ (history der gewählten Version)             │
  │                     [Abbrechen]  [Aktualisieren]       │
  └───────────────────────────────────────────────────────┘
  ```
  Nutzt `VersionList`. „Zu laden“ = `download_estimate` (`null` → „wird beim Prüfen ermittelt“).
- `Downloads.js`: Records mit `kind === 'update'` als „Update: *Name* → *version_label*“ anzeigen.
  `DownloadOut` bekommt `kind` und `target_version_label`.

### 5.4 Tests Phase 5

- `tests/test_update_flow.py` (ohne libtorrent, `TransferManager` mit gemockter Session wie in
  `test_auto_torrent_upgrade.py`): `start_update` führt Moves/Truncates aus, setzt Zustand `updating`,
  Update-Ziel nicht in `incomplete_download_dest_paths`; `_finalize_update` löscht entfernte Dateien,
  lässt ignorierte/lokale Dateien (z. B. `saves/slot1.sav`) stehen, schreibt Manifest mit
  **gleicher ID**, setzt Zustand clean, kopiert Torrent in den Cache.
- libtorrent-Integrationstest (`importorskip`, markiert `slow`): zwei Sessions auf `127.0.0.1` mit
  verschiedenen Ports; Host-Ordner v1 → Empfänger lädt komplett; Host ändert eine Datei in der Mitte
  (gleiche Größe) und fügt eine hinzu, entfernt eine → publish → Empfänger `start_update` → nach
  Abschluss Ordner identisch zum Host (blake2b), und `total_payload_download` deutlich kleiner als
  Spielgröße (Beweis für Delta).

---

## Phase 6 (optional) – Schnellpfad ohne komplette Prüfung

Nur wenn Phase 0 bestätigt, dass `params.have_pieces` funktioniert. Sonst überspringen – Phase 5
funktioniert vollständig ohne.

- `content.py` (rein, testbar): `pieces_for_file(file_offset, file_size, piece_length) -> range`.
- `torrent.py::build_have_pieces(lt, ti, root, unchanged_rels, changed_rels) -> list[bool]`:
  - Pieces aller `unchanged` Dateien (Hash lokal == Hash neu **und** Snapshot sauber) → `True` ohne Lesen.
  - Pieces von `changed` Dateien: lokale Bytes des Pieces lesen, mit Nullen auf `ti.piece_size(p)`
    auffüllen (Pad), `hashlib.sha1(...).digest() == ti.hash_for_piece(p)` → `True`.
    Das ist die Delta-Erkennung innerhalb großer Dateien.
  - Pieces von neuen Dateien → `False`.
- In `start_update`: `params.have_pieces = build_have_pieces(...)` (in Worker-Thread, weil es liest).
  Bei jeder Exception: loggen, ohne `have_pieces` weiter (= Phase-5-Verhalten).
- Nach Abschluss (`_finalize_update`) zur Sicherheit blake2b nur der **geänderten/neuen** Dateien
  gegen das Manifest prüfen; Abweichung → `handle.force_recheck()` und Download weiterlaufen lassen.

---

## Phase 7 (optional) – Komfort

- **Aus Netzwerk wiederherstellen:** Ist ein Spiel `modified` und hat ein Peer denselben
  `content_hash` wie die lokale Revision, bietet die Karte „Änderungen verwerfen (aus Netzwerk
  wiederherstellen)“ an = `POST /api/games/{id}/update` mit der **eigenen** `version_key`. Nutzt
  denselben Update-Weg, lädt nur die veränderten Pieces.
- **Decky-Plugin:** Anzahl „Updates verfügbar“ im Quick-Access-Menü (`decky-plugin/main.py` fragt
  `/api/games` ab und zählt `update_available`).

---

## Wiederverwendung (nicht neu erfinden)

- Thread + WS-Emit-Muster: `deckdrop/core/torrent_prep.py` (`_emit`, `_lock`, `bind_loop`).
- Hashing: `deckdrop/core/integrity.py::hash_file`, `TORRENT_SKIP_FILENAMES`, `dir_size`.
- Resume/Metadaten-Priorität: `TransferManager._params_for_record` + `resume.params_from_torrent_file`.
- Seeding nach Download: `_promote_download_to_seed`, `seed_from_cache`, `drop_seed`.
- Peer-HTTP: `_peer_http_url` (in mehreren Modulen dupliziert – für neue Aufrufe die Version aus
  `network/transfer.py` importieren, keine vierte Kopie anlegen).
- Dialog-/Controller-Muster: `frontend/components/EditGame.js`, `Comments.js`; Grid-Navigation `useGridNav`.
- Testmuster ohne libtorrent: `tests/test_auto_torrent_upgrade.py` (MagicMock-Session),
  Fixtures `isolated_config`, `make_game` in `tests/conftest.py`.

## Stolperfallen für die Umsetzung

- `game.version` **nicht** für die Inhaltsversion verwenden (Metadaten-Sync hängt daran).
- `_hash_game_files` darf nach der Umstellung **nicht** mehr `invalidate_torrent` bei der ersten
  Hash-Füllung auslösen.
- Alle neuen Dataclass-Felder in `_PersistedRecord` brauchen Defaults (alte State-Dateien).
- Nie `shutil.rmtree` auf einen Update-Zielordner.
- Pfade aus Manifesten von Peers sind **nicht vertrauenswürdig**: vor jedem Schreiben/Löschen/Verschieben
  prüfen, dass `(root / rel).resolve()` innerhalb von `root.resolve()` liegt und `rel` nicht absolut ist
  (Hilfsfunktion `content.safe_join(root, rel)`, überall verwenden, eigener Test).
- Ignorierte Dateien niemals löschen.
- Nur `ruff`-konformer Code (Zeilenlänge 100), UI-Texte auf Deutsch wie im Rest der App.
- Frontend hat keinen Build-Step: neue Komponenten als ES-Module mit `htm/preact` wie die bestehenden.

## Arbeitsweise für umsetzende Agenten

- Phasen strikt der Reihe nach umsetzen (0 → 5, danach optional 6 und 7).
- **Jede Phase = eigener PR.** Im PR die erledigte Phase unten in der Checkliste abhaken.
- Vor jedem Push die Befehle aus „Verifikation“ ausführen; alle müssen grün sein.
- Bei Abweichungen vom Plan (z. B. libtorrent-API anders als in Phase 0 angenommen) diese Datei im
  selben PR anpassen und begründen.

### Checkliste

- [x] Phase 0 – libtorrent-API geprüft
- [x] Phase 1 – Datenmodell, Manifest, ID-Fix, Ordnername-Fix
- [x] Phase 2 – Änderungserkennung + Sperre
- [x] Phase 3 – Update veröffentlichen
- [x] Phase 4 – Versionen im Netzwerk + Versionswahl beim Erst-Download
- [ ] Phase 5 – Update übernehmen
- [ ] Phase 6 (optional) – Schnellpfad
- [ ] Phase 7 (optional) – Wiederherstellen, Decky

## Verifikation

Pro Phase:

```bash
pip install -e ".[dev]"
ruff check deckdrop/ tests/ && ruff format --check deckdrop/ tests/
python -m pytest --tb=short -q --ignore=tests/test_frontend.py
for f in frontend/api.js frontend/app.js frontend/components/*.js; do node --check "$f"; done
```

End-to-End (lokal, mit libtorrent, zwei Instanzen mit getrennten Configs/Ports):

1. Instanz A: Spielordner mit ein paar Dateien hinzufügen → „Geteilt“.
2. Instanz B: Netzwerk → Spiel laden → gleiche ID in beiden `deckdrop.toml`, „Erstellt von A“.
3. A: eine Datei ändern → „Meine Spiele“ öffnen → Badge „Verändert“; B sieht die Version als
   „bei A verändert“, nicht ladbar; `/api/games/{id}/magnet` bei A → 409.
4. A: „Update veröffentlichen“ mit Version „1.1“ + Notiz → nach Torrent-Prep wieder geteilt.
5. B: Karte zeigt „Update verfügbar: 1.1“ → Dialog zeigt Ersteller, Updater, Notiz, Schätzung →
   „Aktualisieren“ → Downloads zeigt nur geringe Datenmenge; danach Ordner B == Ordner A (Hashes),
   eigene Datei `saves/x.sav` in B existiert noch, gelöschte Datei ist weg.
6. B während des Updates beenden und neu starten → Update läuft weiter und wird korrekt abgeschlossen.
7. Dritte Instanz C mit einer anderen 1.1-Variante → B/neue Instanz sieht beim Erst-Download
   „2 Versionen“ und kann wählen.
