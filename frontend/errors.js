import { t } from './i18n/index.js';

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
    if (/peer/i.test(detail)) return t('errors.peerGone');
    if (/game/i.test(detail) || /spiel/i.test(detail)) return t('errors.hostGameGone');
    return detail || t('errors.gameOrPeerNotFound');
  },
  409: () => t('errors.hostHashing'),
  502: (detail) => {
    const base = t('errors.hostUnreachable');
    return detail ? `${base} (${detail})` : base;
  },
  503: (detail) => {
    if (/libtorrent/i.test(detail)) return t('errors.noLibtorrent');
    return detail || t('errors.serviceUnavailable');
  },
  500: (detail) => {
    if (!detail || /^internal server error$/i.test(detail)) return t('errors.internal');
    return t('errors.downloadNotStarted', { detail });
  },
};

const SETTINGS_BY_STATUS = {
  403: () => t('errors.localOnly'),
  503: (detail) => detail || t('errors.settingsNotSaved'),
};

const GAME_BY_STATUS = {
  400: (detail) => detail || t('errors.invalidPath'),
  404: () => t('errors.gameNotFound'),
  422: (detail) => detail || t('errors.requiredFields'),
  500: (detail) => detail || t('errors.gameNotSaved'),
};

const CONTEXT_MAP = {
  download: DOWNLOAD_BY_STATUS,
  settings: SETTINGS_BY_STATUS,
  game: GAME_BY_STATUS,
};

/**
 * Turn a failed api.req() error into a short user message (current language).
 * Server details already come translated (deckdrop/core/i18n.py).
 */
export function formatApiError(err, context = 'download') {
  if (!err || !err.status) {
    return t('errors.noApi');
  }
  const detail = parseFastApiDetail(err.body);
  const byStatus = CONTEXT_MAP[context] || DOWNLOAD_BY_STATUS;
  const mapper = byStatus[err.status];
  if (mapper) return mapper(detail);
  return detail || t('errors.http', { status: err.status });
}

// Raw libtorrent text → message key. Errors the server produced itself are
// already translated and pass through unchanged.
const TRANSFER_PATTERNS = [
  [/no such file|filesystem/i, 'errors.transfer.pathNotFound'],
  [/timed out|timeout/i, 'errors.transfer.timeout'],
  [/connection refused/i, 'errors.transfer.refused'],
  [/parse_magnet_uri/i, 'errors.transfer.badMagnet'],
  [/0 peers|no peers|num_peers.*0/i, 'errors.transfer.noPeers'],
];

/**
 * Friendly text for libtorrent / transfer runtime errors.
 */
export function formatTransferError(raw) {
  if (!raw || typeof raw !== 'string') return t('errors.transfer.unknown');
  const lower = raw.toLowerCase();
  for (const [re, key] of TRANSFER_PATTERNS) {
    if (re.test(lower)) return t(key);
  }
  if (raw.length > 120) return `${raw.slice(0, 117)}…`;
  return raw;
}

// Fallback only: the server sends `error_hint` (translated) with every error.
const TRANSFER_HINT_PATTERNS = [
  [/hash|piece/i, 'errors.hint.checkHostFiles'],
  [/no space|disk/i, 'errors.hint.freeSpace'],
  [/0 peers|no peers/i, 'errors.hint.keepHostOnline'],
  [/timed out|timeout/i, 'errors.hint.timeout'],
  [/connection refused/i, 'errors.hint.firewall'],
  [/no such file|filesystem/i, 'errors.hint.restartDownload'],
];

/**
 * Actionable hint for a transfer error (API error_hint or derived from the raw message).
 */
export function getTransferHint(dl) {
  if (dl?.error_hint) return dl.error_hint;
  const lower = (dl?.error || '').toLowerCase();
  for (const [re, key] of TRANSFER_HINT_PATTERNS) {
    if (re.test(lower)) return t(key);
  }
  return t('errors.hint.default');
}
