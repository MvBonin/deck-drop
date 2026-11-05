"""Server-side translations for user-visible messages (API errors, transfer
errors, self-update and debug texts).

The language is the one stored in the config (`[user] language`); the API
calls `set_language()` at startup and whenever the setting changes.

Adding a language: add its code to SUPPORTED_LANGUAGES and a full catalog to
MESSAGES (tests/test_i18n.py checks that every catalog has the same keys).
The web UI has its own catalogs in frontend/i18n/.
"""

from __future__ import annotations

import string

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "de")

_current = DEFAULT_LANGUAGE


def set_language(lang: str) -> None:
    global _current
    _current = lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def get_language() -> str:
    return _current


def t(key: str, lang: str | None = None, **params: object) -> str:
    """Translate `key` into the current (or given) language, `{name}` filled from params."""
    lang = lang or _current
    text = MESSAGES.get(lang, {}).get(key) or MESSAGES[DEFAULT_LANGUAGE].get(key) or key
    if params:
        try:
            return text.format(**params)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def matches(message: str, key: str) -> bool:
    """True if `message` was produced by t(key) in any language.

    Compares the fixed text before the first placeholder, so messages with
    parameters (an address, a version) are recognised too.
    """
    lower = message.lower()
    for catalog in MESSAGES.values():
        template = catalog.get(key)
        if not template:
            continue
        prefix = next(iter(string.Formatter().parse(template)), ("", None, None, None))[0]
        prefix = prefix.strip().lower()
        if prefix and lower.startswith(prefix):
            return True
    return False


MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        # -- API errors --
        "err.local_only": "This endpoint is only available from localhost",
        "err.game_not_found": "Game not found",
        "err.folder_not_found": "Folder not found: {path}",
        "err.folder_no_access": "No access to folder: {path}",
        "err.peer_not_found": "Peer not found",
        "err.peer_id_not_found": "Peer {peer_id} not found",
        "err.peer_game_not_found": "Game not found on this peer",
        "err.peer_game_id_not_found": "Game {game_id} not found on peer {peer_id}",
        "err.download_not_found": "Download not found",
        "err.download_resume_failed": "Download not found or could not be resumed",
        "err.download_retry_failed": "Download not found or retry failed",
        "err.download_start_failed": "Download could not be started: {error}",
        "err.version_not_found": "Version {version} not found",
        "err.version_no_peer": "Version {version} not found or no peer online",
        "err.peer_version_gone": "The selected peer no longer offers this version",
        "err.already_installed": "Game is already installed – use Update.",
        "err.transfer_unavailable": "Transfer unavailable (libtorrent not installed)",
        "err.magnet_http": "Could not fetch magnet link from host (HTTP {status}): {detail}",
        "err.magnet_fetch": "Could not fetch magnet link from host: {error}",
        "err.magnet_timeout": "Magnet link: host is taking too long to prepare the torrent. "
        "Wait for the host and try again. ({detail})",
        "err.comments_http": "Could not fetch comments from peer (HTTP {status}): {detail}",
        "err.comments_fetch": "Could not fetch comments from peer: {error}",
        "err.no_peer_cover": "Peer has no cover",
        "err.cover_fetch": "Could not fetch cover from peer: {error}",
        "err.not_a_directory": "Path is not a directory: {path}",
        "err.toml_load_failed": "Failed to load deckdrop.toml",
        "err.comment_empty": "Comment must not be empty",
        "err.no_local_cover": "No local cover found",
        "err.steam_search_failed": "Steam search failed: {error}",
        "err.steam_no_match": "No Steam match for this game name",
        "err.game_modified": "Game has been modified – publish it as an update first.",
        "err.torrent_build_failed": "Torrent could not be created: {error}",
        "err.torrent_preparing": "Torrent is being prepared – please wait a moment and try again.",
        "err.invalid_path": "Invalid path: {path}",
        "err.game_busy": "Game is busy right now ({state})",
        "err.repair_own_game": "Your own games cannot be repaired",
        "err.hash_cancel_failed": "Hash calculation could not be cancelled",
        "err.peer_files_unavailable": "Could not fetch manifest or torrent file from peer",
        "err.update_start_failed": "Update could not be started: {error}",
        "err.update_no_status": "Update started, but status is not available",
        "err.update_already_running": "This update is already running.",
        "err.no_checked_update": "No verified update from GitHub – check for updates first.",
        "err.no_signed_package": "No signed DeckDrop package available",
        "err.peer_no_update": "Peer does not offer a DeckDrop update.",
        "err.appimage_unknown": "AppImage path unknown – service cannot be set up",
        # -- transfer errors (shown on download cards) --
        "transfer.path_not_found": "File path not found.",
        "transfer.timeout": "Timed out – is the host reachable?",
        "transfer.refused": "Connection refused – check torrent port 7374.",
        "transfer.bad_magnet": "Magnet link could not be read.",
        "transfer.lt_incompatible": "libtorrent API incompatible – update the app.",
        "transfer.hash_failed": "Checksum failed – file damaged or incomplete.",
        "transfer.piece_failed": "Corrupt data block – download incomplete.",
        "transfer.no_space": "Not enough disk space.",
        "transfer.disk_error": "Disk error while writing.",
        "transfer.no_peers": "No peer connected.",
        "transfer.unknown": "Unknown transfer error.",
        "transfer.no_metadata": "Host is not sending torrent data – share the game again on the "
        "host (rebuild torrent), then “Retry”.",
        "transfer.no_host_connection": "No connection to host ({address}). Is DeckDrop running "
        "there and port 7374 open? Try “Retry”.",
        "transfer.unknown_address": "unknown",
        "transfer.midstall": "No data from host for 5 minutes – check the host, "
        "the download keeps trying.",
        "transfer.endgame_missing": "Host is not sending the last pieces – rebuild the torrent on "
        "the host (metadata changed?). Try “Retry”.",
        "transfer.endgame_stall": "Download is stuck on the last pieces – "
        "is the host reachable and online? Try “Retry”.",
        # -- hints below transfer errors --
        "hint.default": "Try “Retry” or check the connection to the host.",
        "hint.no_connection": "Turn on the host, start DeckDrop there, open port 7374.",
        "hint.no_metadata": "Share the game again on the host, then “Retry”.",
        "hint.no_data": "Keep the host online; the download reconnects by itself.",
        "hint.check_host_files": "Try again; check the files on the host.",
        "hint.free_space": "Free up disk space, then “Retry”.",
        "hint.disk": "Check disk space and permissions.",
        "hint.keep_host_online": "Keep the host online and try again.",
        "hint.timeout": "Host reachable? Wait a moment, then “Retry”.",
        "hint.firewall": "Firewall: check torrent port 7374 on the host.",
        "hint.restart_download": "Remove the download and start it again.",
        # -- self-update --
        "update.no_release": "No release on GitHub yet.",
        "update.invalid_version": "Not a valid version: {version}",
        "update.no_key": "No signing key built in – updates cannot be verified.",
        "update.bad_signature": "Invalid signature.",
        "update.foreign_signature": (
            "Invalid signature – update does not come from the DeckDrop project."
        ),
        "update.foreign_manifest": "Manifest does not belong to DeckDrop.",
        "update.manifest_no_version": "Manifest has no valid version.",
        "update.manifest_no_appimage": "Manifest contains no AppImage.",
        "update.appimage_only": "Only the AppImage can update itself.",
        "update.no_write_access": "No write permission for {path}.",
        "update.unsigned_release": "Release is not signed (release.json missing).",
        "update.version_mismatch": "Manifest version does not match the release.",
        "update.github_unreachable": "GitHub not reachable: {error}",
        "update.already_running": "An update is already running.",
        "update.not_newer": "Offered version is not newer than the installed one.",
        "update.too_large": "Download is larger than stated in the manifest.",
        "update.checksum_mismatch": (
            "Checksum does not match the signed manifest – nothing replaced."
        ),
        "update.incomplete": "Download incomplete – nothing replaced.",
        # -- debug log (Debug tab) --
        "debug.kind.blake2b": "DeckDrop hashes files (blake2b)",
        "debug.kind.piece_check": "Check local pieces (SHA1)",
        "debug.kind.recheck": "libtorrent re-checks files",
        "debug.kind.manifest_check": "Compare manifest ↔ torrent (without reading)",
        "debug.kind.torrent_build": "Build torrent (reads all files)",
        "debug.kind.relink": "Game ID aligned with the host (legacy download)",
        "debug.kind.update": "Update",
        "debug.reason.new_local_game": "New local game – no file hashes yet",
        "debug.reason.manifest_mismatch": "Manifest does not match the torrent – hashes are "
        "recomputed locally",
        "debug.reason.manifest_ok": "Manifest matches the torrent – no rehash needed",
        "debug.reason.mtime_changed": "Modification time changed, size unchanged – content is "
        "being checked",
        "debug.reason.publish": "Publish update – changed files are being hashed",
        "debug.reason.download_without_manifest": "Download without manifest (old peer or "
        "fetching the manifest failed)",
        "debug.reason.update_reuse_local": "Update: reuse existing bytes of changed/moved files",
        "debug.reason.update_without_baseline": "Update without old file hashes – existing files "
        "are checked piece by piece once",
        "debug.reason.update_started": "Caught up: hashing had been cancelled for an update",
        "debug.reason.interrupted": "Caught up: hashing was interrupted by quit/restart",
        "debug.reason.relinked": "Legacy ID corrected – check continues under the new ID",
        "debug.reason.stall": "Download stuck – one-time re-check",
        "debug.reason.repair": "Repair – all files are checked against the torrent, missing "
        "data is downloaded",
        "debug.reason.update_replaced": "Running update replaced (new version or new torrent on "
        "the host) – downloaded data is reused",
        "debug.reason.torrent_rebuilt": "Torrent rebuilt – seed re-checks files",
        "debug.reason.torrent_missing": "No torrent yet – building it from the files",
        "debug.reason.torrent_forced": "Torrent is being rebuilt (content or metadata changed)",
        "debug.reason.unknown": "Unknown",
        "debug.detail.manifest_ok": "{count} files",
        "debug.detail.reused": "{count} file(s) taken over without rehash",
        "debug.detail.fast_path_failed": "failed, libtorrent checks by itself: {error}",
    },
    "de": {
        # -- API errors --
        "err.local_only": "Nur von diesem Gerät aus erreichbar",
        "err.game_not_found": "Spiel nicht gefunden",
        "err.folder_not_found": "Ordner nicht gefunden: {path}",
        "err.folder_no_access": "Kein Zugriff auf Ordner: {path}",
        "err.peer_not_found": "Peer nicht gefunden",
        "err.peer_id_not_found": "Peer {peer_id} nicht gefunden",
        "err.peer_game_not_found": "Spiel beim Peer nicht gefunden",
        "err.peer_game_id_not_found": "Spiel {game_id} beim Peer {peer_id} nicht gefunden",
        "err.download_not_found": "Download nicht gefunden",
        "err.download_resume_failed": (
            "Download nicht gefunden oder konnte nicht fortgesetzt werden"
        ),
        "err.download_retry_failed": "Download nicht gefunden oder Wiederholung fehlgeschlagen",
        "err.download_start_failed": "Download konnte nicht gestartet werden: {error}",
        "err.version_not_found": "Version {version} nicht gefunden",
        "err.version_no_peer": "Version {version} nicht gefunden oder kein Peer online",
        "err.peer_version_gone": "Gewählter Peer bietet diese Version nicht (mehr) an",
        "err.already_installed": "Spiel ist schon installiert – Update verwenden.",
        "err.transfer_unavailable": "Transfer nicht verfügbar (libtorrent nicht installiert)",
        "err.magnet_http": "Magnet-Link vom Host nicht abrufbar (HTTP {status}): {detail}",
        "err.magnet_fetch": "Magnet-Link vom Host nicht abrufbar: {error}",
        "err.magnet_timeout": "Magnet-Link: Host braucht zu lange für Torrent-Vorbereitung. "
        "Am Host warten und erneut versuchen. ({detail})",
        "err.comments_http": "Kommentare vom Peer nicht abrufbar (HTTP {status}): {detail}",
        "err.comments_fetch": "Kommentare vom Peer nicht abrufbar: {error}",
        "err.no_peer_cover": "Kein Cover beim Peer",
        "err.cover_fetch": "Cover vom Peer nicht abrufbar: {error}",
        "err.not_a_directory": "Pfad ist kein Ordner: {path}",
        "err.toml_load_failed": "deckdrop.toml konnte nicht geladen werden",
        "err.comment_empty": "Kommentar darf nicht leer sein",
        "err.no_local_cover": "Kein lokales Cover gefunden",
        "err.steam_search_failed": "Steam-Suche fehlgeschlagen: {error}",
        "err.steam_no_match": "Kein Steam-Treffer für diesen Spielnamen",
        "err.game_modified": "Spiel wurde verändert – erst als Update veröffentlichen.",
        "err.torrent_build_failed": "Torrent konnte nicht erzeugt werden: {error}",
        "err.torrent_preparing": (
            "Torrent wird vorbereitet – bitte kurz warten und erneut versuchen."
        ),
        "err.invalid_path": "Ungültiger Pfad: {path}",
        "err.game_busy": "Spiel ist gerade beschäftigt ({state})",
        "err.repair_own_game": "Eigene Spiele können nicht repariert werden",
        "err.hash_cancel_failed": "Hash-Berechnung ließ sich nicht abbrechen",
        "err.peer_files_unavailable": "Manifest oder Torrent-Datei vom Peer nicht abrufbar",
        "err.update_start_failed": "Update konnte nicht gestartet werden: {error}",
        "err.update_no_status": "Update gestartet, aber Status nicht verfügbar",
        "err.update_already_running": "Dieses Update läuft bereits.",
        "err.no_checked_update": "Kein geprüftes Update von GitHub – erst suchen.",
        "err.no_signed_package": "Kein signiertes DeckDrop-Paket verfügbar",
        "err.peer_no_update": "Peer bietet kein DeckDrop-Update an.",
        "err.appimage_unknown": "AppImage-Pfad unbekannt – Service kann nicht eingerichtet werden",
        # -- transfer errors (shown on download cards) --
        "transfer.path_not_found": "Dateipfad nicht gefunden.",
        "transfer.timeout": "Zeitüberschreitung – Host erreichbar?",
        "transfer.refused": "Verbindung abgelehnt – Torrent-Port 7374 prüfen.",
        "transfer.bad_magnet": "Magnet-Link konnte nicht gelesen werden.",
        "transfer.lt_incompatible": "libtorrent-API inkompatibel – App aktualisieren.",
        "transfer.hash_failed": "Prüfsumme fehlgeschlagen – Datei beschädigt oder unvollständig.",
        "transfer.piece_failed": "Datenblock fehlerhaft – Download unvollständig.",
        "transfer.no_space": "Nicht genug Speicherplatz.",
        "transfer.disk_error": "Festplattenfehler beim Schreiben.",
        "transfer.no_peers": "Kein Peer verbunden.",
        "transfer.unknown": "Unbekannter Übertragungsfehler.",
        "transfer.no_metadata": "Host liefert keine Torrent-Daten – am Host das Spiel neu "
        "freigeben (Torrent neu erstellen), dann „Erneut“.",
        "transfer.no_host_connection": "Keine Verbindung zum Host ({address}). Läuft DeckDrop "
        "dort und ist Port 7374 offen? „Erneut“ versuchen.",
        "transfer.unknown_address": "unbekannt",
        "transfer.midstall": "Seit 5 Minuten keine Daten vom Host – Host prüfen, "
        "der Download versucht es weiter.",
        "transfer.endgame_missing": "Host liefert letzte Daten nicht – auf dem Host Torrent neu "
        "erstellen (Metadaten geändert?). „Erneut“ versuchen.",
        "transfer.endgame_stall": "Download hängt bei den letzten Daten – "
        "Host erreichbar und online? „Erneut“ versuchen.",
        # -- hints below transfer errors --
        "hint.default": "„Erneut“ versuchen oder Host-Verbindung prüfen.",
        "hint.no_connection": "Host einschalten, DeckDrop dort starten, Port 7374 freigeben.",
        "hint.no_metadata": "Am Host das Spiel neu freigeben, dann „Erneut“.",
        "hint.no_data": "Host online lassen; der Download verbindet sich selbst neu.",
        "hint.check_host_files": "Erneut versuchen; Host-Dateien prüfen.",
        "hint.free_space": "Speicherplatz freigeben, dann „Erneut“.",
        "hint.disk": "Speicherplatz und Berechtigungen prüfen.",
        "hint.keep_host_online": "Host online lassen und erneut versuchen.",
        "hint.timeout": "Host erreichbar? Kurz warten und „Erneut“.",
        "hint.firewall": "Firewall: Torrent-Port 7374 am Host prüfen.",
        "hint.restart_download": "Download entfernen und neu starten.",
        # -- self-update --
        "update.no_release": "Noch kein Release auf GitHub.",
        "update.invalid_version": "Keine gültige Version: {version}",
        "update.no_key": "Kein Signaturschlüssel eingebaut – Updates können nicht geprüft werden.",
        "update.bad_signature": "Signatur ungültig.",
        "update.foreign_signature": "Signatur ungültig – Update stammt nicht vom DeckDrop-Projekt.",
        "update.foreign_manifest": "Manifest gehört nicht zu DeckDrop.",
        "update.manifest_no_version": "Manifest ohne gültige Version.",
        "update.manifest_no_appimage": "Manifest enthält keine AppImage.",
        "update.appimage_only": "Nur die AppImage kann sich selbst aktualisieren.",
        "update.no_write_access": "Kein Schreibrecht für {path}.",
        "update.unsigned_release": "Release ist nicht signiert (release.json fehlt).",
        "update.version_mismatch": "Manifest-Version passt nicht zum Release.",
        "update.github_unreachable": "GitHub nicht erreichbar: {error}",
        "update.already_running": "Ein Update läuft bereits.",
        "update.not_newer": "Angebotene Version ist nicht neuer als die installierte.",
        "update.too_large": "Download größer als im Manifest angegeben.",
        "update.checksum_mismatch": (
            "Prüfsumme passt nicht zum signierten Manifest – nichts ersetzt."
        ),
        "update.incomplete": "Download unvollständig – nichts ersetzt.",
        # -- debug log (Debug tab) --
        "debug.kind.blake2b": "DeckDrop hasht Dateien (blake2b)",
        "debug.kind.piece_check": "Lokale Pieces prüfen (SHA1)",
        "debug.kind.recheck": "libtorrent prüft Dateien neu",
        "debug.kind.manifest_check": "Manifest ↔ Torrent abgleichen (ohne Lesen)",
        "debug.kind.torrent_build": "Torrent erstellen (liest alle Dateien)",
        "debug.kind.relink": "Spiel-ID an den Host angeglichen (Legacy-Download)",
        "debug.kind.update": "Update",
        "debug.reason.new_local_game": "Neues lokales Spiel – noch keine Datei-Hashes",
        "debug.reason.manifest_mismatch": "Manifest passt nicht zum Torrent – Hashes werden "
        "lokal neu berechnet",
        "debug.reason.manifest_ok": "Manifest passt zum Torrent – kein Rehash nötig",
        "debug.reason.mtime_changed": "Änderungszeit geändert, Größe gleich – Inhalt wird geprüft",
        "debug.reason.publish": "Update veröffentlichen – geänderte Dateien werden gehasht",
        "debug.reason.download_without_manifest": "Download ohne Manifest (alter Peer oder "
        "Manifest-Abruf fehlgeschlagen)",
        "debug.reason.update_reuse_local": "Update: vorhandene Bytes geänderter/verschobener "
        "Dateien wiederverwenden",
        "debug.reason.update_without_baseline": "Update ohne alte Datei-Hashes – vorhandene "
        "Dateien werden einmal pieceweise geprüft",
        "debug.reason.update_started": "Nachgeholt: Hash war für ein Update abgebrochen worden",
        "debug.reason.interrupted": "Nachgeholt: Hash wurde durch Beenden/Neustart unterbrochen",
        "debug.reason.relinked": "Legacy-ID korrigiert – Prüfung läuft unter der neuen ID weiter",
        "debug.reason.stall": "Download hängt – einmalige Neuprüfung",
        "debug.reason.repair": "Reparieren – alle Dateien werden gegen den Torrent geprüft, "
        "Fehlendes geladen",
        "debug.reason.update_replaced": "Laufendes Update ersetzt (neue Version oder neuer "
        "Torrent beim Host) – Geladenes wird wiederverwendet",
        "debug.reason.torrent_rebuilt": "Torrent neu erstellt – Seed prüft Dateien neu",
        "debug.reason.torrent_missing": "Kein Torrent vorhanden – wird aus den Dateien erstellt",
        "debug.reason.torrent_forced": "Torrent wird neu erstellt (Inhalt oder Metadaten geändert)",
        "debug.reason.unknown": "Unbekannt",
        "debug.detail.manifest_ok": "{count} Dateien",
        "debug.detail.reused": "{count} Datei(en) ohne Rehash übernommen",
        "debug.detail.fast_path_failed": "fehlgeschlagen, libtorrent prüft selbst: {error}",
    },
}
