/**
 * Parse FastAPI error body (JSON {"detail": "..."} or plain text).
 */
export function parseFastApiDetail(body) {
  if (!body || typeof body !== 'string') return '';
  const trimmed = body.trim();
  if (!trimmed) return '';
  if (trimmed.startsWith('{')) {
    try {
      const data = JSON.parse(trimmed);
      const d = data.detail;
      if (typeof d === 'string') return d;
      if (Array.isArray(d)) {
        return d.map((e) => e.msg || String(e)).join('; ');
      }
    } catch {
      /* fall through */
    }
  }
  return trimmed;
}

const DOWNLOAD_BY_STATUS = {
  404: (detail) => {
    if (/peer/i.test(detail)) {
      return 'Peer nicht mehr im Netzwerk – Netzwerk-Tab aktualisieren.';
    }
    if (/game/i.test(detail) || /spiel/i.test(detail)) {
      return 'Spiel beim Host nicht mehr gefunden.';
    }
    return detail || 'Spiel oder Peer nicht gefunden.';
  },
  409: () =>
    'Der Host berechnet noch Game-Hashes – kurz warten und erneut versuchen.',
  502: (detail) => {
    const base = 'Host nicht erreichbar (Magnet-Link). Firewall prüfen: Port 7373.';
    return detail ? `${base} (${detail})` : base;
  },
  503: (detail) => {
    if (/libtorrent/i.test(detail)) {
      return 'Transfer nicht verfügbar – libtorrent fehlt auf diesem Gerät.';
    }
    return detail || 'Dienst vorübergehend nicht verfügbar (libtorrent).';
  },
  500: (detail) => {
    if (!detail || /^internal server error$/i.test(detail)) {
      return 'Interner Serverfehler – DeckDrop-Log prüfen oder App neu starten.';
    }
    return `Download konnte nicht gestartet werden: ${detail}`;
  },
};

const SETTINGS_BY_STATUS = {
  403: () => 'Nur von diesem Gerät aus erreichbar.',
  503: (detail) => detail || 'Einstellungen konnten nicht gespeichert werden.',
};

const GAME_BY_STATUS = {
  400: (detail) => detail || 'Ungültiger Pfad oder Ordner.',
  404: () => 'Spiel nicht gefunden.',
  422: (detail) => detail || 'Bitte alle Pflichtfelder ausfüllen.',
  500: (detail) => detail || 'Spiel konnte nicht gespeichert werden.',
};

const CONTEXT_MAP = {
  download: DOWNLOAD_BY_STATUS,
  settings: SETTINGS_BY_STATUS,
  game: GAME_BY_STATUS,
};

/**
 * Turn a failed api.req() error into a short German user message.
 */
export function formatApiError(err, context = 'download') {
  if (!err || !err.status) {
    return 'Keine Verbindung zur DeckDrop-API.';
  }
  const detail = parseFastApiDetail(err.body);
  const byStatus = CONTEXT_MAP[context] || DOWNLOAD_BY_STATUS;
  const mapper = byStatus[err.status];
  if (mapper) return mapper(detail);
  return detail || `Fehler (HTTP ${err.status}).`;
}

const TRANSFER_PATTERNS = [
  [/no such file|filesystem/i, 'Dateipfad nicht gefunden.'],
  [/timed out|timeout/i, 'Zeitüberschreitung – Host erreichbar?'],
  [/connection refused/i, 'Verbindung abgelehnt – Torrent-Port 7374 prüfen.'],
  [/parse_magnet_uri/i, 'Magnet-Link ungültig oder libtorrent-Version inkompatibel.'],
  [/0 peers|no peers|num_peers.*0/i, 'Kein Peer verbunden – Host seedet möglicherweise nicht.'],
];

/**
 * Friendly German text for libtorrent / transfer runtime errors.
 */
export function formatTransferError(raw) {
  if (!raw || typeof raw !== 'string') return 'Unbekannter Übertragungsfehler.';
  const lower = raw.toLowerCase();
  for (const [re, msg] of TRANSFER_PATTERNS) {
    if (re.test(lower)) return msg;
  }
  if (raw.length > 120) return `${raw.slice(0, 117)}…`;
  return raw;
}
