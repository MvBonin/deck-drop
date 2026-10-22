# DeckDrop veröffentlichen

DeckDrop prüft Updates gegen ein **signiertes Release-Manifest** (`release.json` +
`release.json.sig`, Ed25519). Ohne gültige Signatur ersetzt die App nichts – egal ob
das Update von GitHub oder von einem anderen Gerät im LAN kommt.

## Einmalig: Signaturschlüssel einrichten

1. Schlüssel erzeugen (auf deinem Rechner, nicht im Repo):

   ```bash
   python packaging/release_tool.py generate-key ~/deckdrop-release.key
   ```

   Ausgegeben wird der **öffentliche** Schlüssel (64 Hex-Zeichen).
2. Öffentlichen Schlüssel in `deckdrop/core/release_key.py` eintragen:

   ```python
   PUBLIC_KEYS: list[str] = ["<öffentlicher Schlüssel>"]
   ```

   committen und mergen. Erst Versionen *mit* diesem Schlüssel können Updates prüfen.
3. Den **privaten** Schlüssel (Inhalt von `~/deckdrop-release.key`) auf GitHub unter
   *Settings → Secrets and variables → Actions* als `DECKDROP_SIGNING_KEY` speichern.
   Die Datei danach sicher aufbewahren (Passwort-Manager) – wer sie hat, kann Updates
   signieren.

## Jedes Release

1. Version in `deckdrop/__init__.py` erhöhen (einzige Versionsquelle), mergen.
2. Tag pushen:

   ```bash
   git tag v2.1.0 && git push origin v2.1.0
   ```

3. Der Workflow `.github/workflows/release.yml` prüft Tag ↔ Version, baut die AppImage,
   schreibt und signiert `release.json` und veröffentlicht das Release mit:
   `DeckDrop-<version>-x86_64.AppImage`, `release.json`, `release.json.sig`, `SHA256SUMS`.

Fehlt das Secret oder passt der Schlüssel nicht zu `PUBLIC_KEYS`, bricht der Workflow ab –
es entsteht nie ein unsigniertes Release.

## In der App

- *Einstellungen → DeckDrop-Version → Nach Updates suchen*: fragt nach einer Warnung
  GitHub an (nur dann).
- Neuere, signierte Versionen auf anderen Geräten im LAN werden automatisch angezeigt
  („Neuere Version … im Netzwerk gefunden – bei …“) und lassen sich von dort installieren.
- AppImage: „Jetzt aktualisieren“ ersetzt die Datei am selben Pfad (alte bleibt als
  `.bak`), „Neu starten“ startet die neue Version; „Vorherige Version wiederherstellen“
  tauscht zurück. Andere Installationsarten bekommen einen Hinweis und den Download-Link.
