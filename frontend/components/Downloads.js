import { html } from 'htm/preact';
import { useState, useEffect } from 'preact/hooks';
import { api, fmtBytes, fmtSpeed } from '../api.js';
import { formatApiError, formatTransferError, getTransferHint } from '../errors.js';

const STATUS_LABEL = {
  queued:      '⏳ Wartend',
  downloading: '⬇ Lädt',
  verifying:   '🔍 Verifiziert',
  seeding:     '↑ Seeding',
  done:        '✓ Fertig',
  error:       '⚠ Fehler',
  paused:      '⏸ Pausiert',
};

const STATUS_COLOR = {
  done:  'var(--success)',
  error: 'var(--danger)',
  seeding: 'var(--success)',
  verifying: 'var(--accent)',
  paused: 'var(--text-dim)',
};

const ACTIVE_DOWNLOAD_STATUSES = new Set([
  'queued', 'downloading', 'verifying', 'paused', 'error', 'done',
]);

/** Progress 0–100 as float (not rounded). */
function progressPct(dl) {
  if (dl.total_bytes > 0) {
    return Math.min(100, (dl.downloaded_bytes / dl.total_bytes) * 100);
  }
  return Math.min(100, (dl.progress || 0) * 100);
}

function formatPct(pct) {
  return `${pct.toFixed(2)} %`;
}

function endSprintDetail(dl, pct) {
  const active = dl.status === 'downloading' || dl.status === 'verifying';
  if (!active || pct < 95) return null;

  const parts = [];
  if (dl.pieces_missing > 0) {
    parts.push(`${dl.pieces_missing} Piece${dl.pieces_missing !== 1 ? 's' : ''} fehlen`);
  }
  const remaining = dl.bytes_remaining ?? Math.max(0, dl.total_bytes - dl.downloaded_bytes);
  if (remaining > 0) {
    parts.push(`${fmtBytes(remaining)} fehlen`);
  }
  return parts.length ? parts.join(' · ') : null;
}

function DownloadRow({ dl, onPause, onResume, onRetry, onRemove }) {
  const pct = progressPct(dl);
  const pctLabel = formatPct(pct);
  const fillPct = Math.min(100, pct);
  const fillClass = dl.status === 'done' || dl.status === 'seeding' ? 'done'
                  : dl.status === 'error' ? 'error'
                  : dl.status === 'paused' ? '' : '';
  const statusColor = STATUS_COLOR[dl.status] || 'var(--text-dim)';
  const canPause = dl.status === 'downloading' || dl.status === 'queued';
  const canResume = dl.status === 'paused';
  const canRetry = dl.status === 'error';
  const canRemove = true;
  const sprint = endSprintDetail(dl, pct);
  const errMsg = dl.error ? formatTransferError(dl.error) : null;
  const errHint = dl.status === 'error' ? getTransferHint(dl) : null;

  return html`
    <div class="download-row" tabIndex=${0} data-card>
      <div class="download-header">
        <div>
          <div class="download-name">${dl.game_name}</div>
          <div class="download-peer">${dl.status === 'done' || dl.status === 'seeding'
            ? `Ursprünglich von ${dl.peer_name}`
            : `von ${dl.peer_name}`}</div>
        </div>
        <div style="display:flex;align-items:center;gap:6px;flex-shrink:0;flex-wrap:wrap;justify-content:flex-end">
          <span style="font-size:13px;font-weight:600;color:${statusColor}">${STATUS_LABEL[dl.status] || dl.status}</span>
          ${canPause && html`
            <button class="btn btn-ghost" style="padding:4px 10px;font-size:12px" onClick=${() => onPause(dl)}>Pause</button>
          `}
          ${canResume && html`
            <button class="btn btn-primary" style="padding:4px 10px;font-size:12px" onClick=${() => onResume(dl)}>Weiter</button>
          `}
          ${canRetry && html`
            <button class="btn btn-primary" style="padding:4px 10px;font-size:12px" onClick=${() => onRetry(dl)}>Erneut</button>
          `}
          ${canRemove && html`
            <button class="btn btn-danger" style="padding:4px 10px;font-size:12px" onClick=${() => onRemove(dl)}>Entfernen</button>
          `}
        </div>
      </div>

      ${dl.status === 'error' && errMsg && html`
        <div style="margin-bottom:8px;padding:10px 12px;border-radius:8px;background:rgba(220,60,60,0.12);border:1px solid var(--danger)">
          <div style="font-size:13px;font-weight:600;color:var(--danger)">${errMsg}</div>
          ${errHint && html`
            <div style="font-size:12px;color:var(--text-dim);margin-top:6px">Lösung: ${errHint}</div>
          `}
        </div>
      `}

      <div class="progress-bar" role="progressbar" aria-valuenow=${fillPct.toFixed(2)} aria-valuemin="0" aria-valuemax="100" aria-label="${pctLabel}">
        <div class="progress-fill ${fillClass}" style="width:${fillPct}%"></div>
      </div>

      <div class="download-stats">
        <span><span class="stat-val">${pctLabel}</span></span>
        ${(dl.status === 'downloading' || dl.status === 'verifying') && html`
          <span>↓ <span class="stat-val">${fmtSpeed(dl.speed_bytes_sec)}</span></span>
          <span><span class="stat-val">${dl.num_peers}</span> Peer${dl.num_peers !== 1 ? 's' : ''}</span>
        `}
        <span>${fmtBytes(dl.downloaded_bytes)} / ${fmtBytes(dl.total_bytes)}</span>
        ${sprint && html`<span style="color:var(--accent)">${sprint}</span>`}
        ${pct >= 99.5 && dl.bytes_remaining > 0 && (dl.status === 'downloading' || dl.status === 'verifying') && html`
          <span style="color:var(--text-dim);font-size:12px">Host-Verbindung wird erneuert…</span>
        `}
        ${dl.num_peers >= 1 && (dl.bytes_remaining ?? 0) > 0 && (dl.bytes_remaining ?? 0) < 1024 * 1024
          && (dl.status === 'downloading' || dl.status === 'verifying') && html`
          <span style="color:var(--text-dim);font-size:12px">Letzte Daten vom Host ausstehend – am Host Torrent neu erstellen, dann Download neu starten.</span>
        `}
      </div>
    </div>`;
}

export function Downloads({ wsEvent, showToast, downloads, setDownloads }) {
  const [loading, setLoading]     = useState(true);
  const [removeTarget, setRemoveTarget] = useState(null);

  const load = async () => {
    try {
      const list = await api.downloads();
      setDownloads(list.filter(d => ACTIVE_DOWNLOAD_STATUSES.has(d.status)));
    }
    catch {}
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  // Poll while downloads are active (covers missed WebSocket updates)
  useEffect(() => {
    const active = downloads.some(d =>
      d.status === 'downloading' || d.status === 'queued' || d.status === 'verifying'
    );
    if (!active) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [downloads]);

  useEffect(() => {
    if (!wsEvent) return;
    if (wsEvent.event === 'download_torrent_upgraded') {
      setDownloads(prev =>
        prev.map(d => d.id === wsEvent.data.id
          ? { ...d, status: 'downloading', error: null, error_hint: null }
          : d)
      );
      showToast(`Torrent aktualisiert: ${wsEvent.data.game_name} – lädt nach…`);
    }
    if (wsEvent.event === 'download_error') {
      showToast(`Download fehlgeschlagen: ${formatTransferError(wsEvent.data.error)}`);
    }
  }, [wsEvent]);

  const pause = async (dl) => {
    try {
      const updated = await api.pauseDl(dl.id);
      setDownloads(prev => prev.map(d => d.id === dl.id ? updated : d));
      showToast(`Pausiert: ${dl.game_name}`);
    } catch (err) {
      showToast(`Fehler: ${formatApiError(err, 'download')}`);
    }
  };

  const resume = async (dl) => {
    try {
      const updated = await api.resumeDl(dl.id);
      setDownloads(prev => prev.map(d => d.id === dl.id ? updated : d));
      showToast(`Fortgesetzt: ${dl.game_name}`);
    } catch (err) {
      showToast(`Fehler: ${formatApiError(err, 'download')}`);
    }
  };

  const retry = async (dl) => {
    try {
      const updated = await api.retryDl(dl.id);
      setDownloads(prev => prev.map(d => d.id === dl.id ? updated : d));
      showToast(`Erneut gestartet: ${dl.game_name}`);
    } catch (err) {
      showToast(`Fehler: ${formatApiError(err, 'download')}`);
    }
  };

  const confirmRemove = async (deleteFiles) => {
    const dl = removeTarget;
    if (!dl) return;
    try {
      await api.removeDl(dl.id, deleteFiles);
      setDownloads(prev => prev.filter(d => d.id !== dl.id));
      showToast(deleteFiles ? `„${dl.game_name}" entfernt und Ordner gelöscht` : `„${dl.game_name}" aus Liste entfernt`);
    } catch (err) {
      showToast(`Fehler: ${formatApiError(err, 'download')}`);
    } finally {
      setRemoveTarget(null);
    }
  };

  const active = downloads.filter(d => ACTIVE_DOWNLOAD_STATUSES.has(d.status));

  return html`
    <div class="view">
      <div class="view-header">
        <div>
          <div class="view-title">Downloads</div>
          <div class="view-subtitle">${active.length} aktiv</div>
        </div>
      </div>

      ${loading
        ? html`<div class="empty-state"><div class="spinner"></div></div>`
        : active.length === 0
          ? html`<div class="empty-state">
              <div class="empty-icon">📥</div>
              <div class="empty-title">Keine Downloads</div>
              <div class="empty-sub">Starte einen Download über den Netzwerk-Tab.</div>
            </div>`
          : html`
            <div class="download-list">
              ${active.map(d => html`
                <${DownloadRow}
                  key=${d.id}
                  dl=${d}
                  onPause=${pause}
                  onResume=${resume}
                  onRetry=${retry}
                  onRemove=${setRemoveTarget}
                />
              `)}
            </div>
          `
      }

      ${removeTarget && html`
        <div
          class="overlay"
          onClick=${e => e.target === e.currentTarget && setRemoveTarget(null)}
          role="dialog"
          aria-modal="true"
          aria-label="Download entfernen"
        >
          <div class="dialog">
            <div class="dialog-title">„${removeTarget.game_name}" entfernen?</div>
            <p class="dialog-text">
              Der Download wird aus der Liste entfernt.
              ${removeTarget.dest_path ? html`<br/><br/><code style="font-size:11px;color:var(--text-dim)">${removeTarget.dest_path}</code>` : null}
            </p>
            <div class="dialog-actions" style="flex-direction:column;align-items:stretch;gap:8px">
              <button type="button" class="btn btn-ghost" onClick=${() => confirmRemove(false)}>
                Nur aus Liste entfernen
              </button>
              <button type="button" class="btn btn-danger" onClick=${() => confirmRemove(true)}>
                Entfernen und Ordner löschen
              </button>
              <button type="button" class="btn btn-ghost" onClick=${() => setRemoveTarget(null)}>
                Abbrechen
              </button>
            </div>
          </div>
        </div>
      `}
    </div>`;
}
