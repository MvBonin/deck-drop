import { html } from 'htm/preact';
import { useState, useEffect } from 'preact/hooks';
import { api, fmtBytes, fmtSpeed } from '../api.js';
import { formatApiError, formatTransferError } from '../errors.js';

const STATUS_LABEL = {
  queued:      '⏳ Wartend',
  downloading: '⬇ Lädt',
  seeding:     '↑ Seeding',
  done:        '✓ Fertig',
  error:       '⚠ Fehler',
  paused:      '⏸ Pausiert',
};

const STATUS_COLOR = {
  done:  'var(--success)',
  error: 'var(--danger)',
  seeding: 'var(--success)',
  paused: 'var(--text-dim)',
};

function progressPct(dl) {
  if (dl.total_bytes > 0) {
    return Math.min(100, Math.round((dl.downloaded_bytes / dl.total_bytes) * 100));
  }
  return Math.round((dl.progress || 0) * 100);
}

function DownloadRow({ dl, onPause, onResume, onRetry, onRemove }) {
  const pct = progressPct(dl);
  const fillClass = dl.status === 'done' || dl.status === 'seeding' ? 'done'
                  : dl.status === 'error' ? 'error'
                  : dl.status === 'paused' ? '' : '';
  const statusColor = STATUS_COLOR[dl.status] || 'var(--text-dim)';
  const canPause = dl.status === 'downloading' || dl.status === 'queued';
  const canResume = dl.status === 'paused';
  const canRetry = dl.status === 'error';
  const canRemove = true;

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

      <div class="progress-bar" role="progressbar" aria-valuenow=${pct} aria-valuemin="0" aria-valuemax="100" aria-label="${pct}%">
        <div class="progress-fill ${fillClass}" style="width:${pct}%"></div>
      </div>

      <div class="download-stats">
        <span><span class="stat-val">${pct}%</span></span>
        ${dl.status === 'downloading' && html`
          <span>↓ <span class="stat-val">${fmtSpeed(dl.speed_bytes_sec)}</span></span>
          <span><span class="stat-val">${dl.num_peers}</span> Peer${dl.num_peers !== 1 ? 's' : ''}</span>
        `}
        <span>${fmtBytes(dl.downloaded_bytes)} / ${fmtBytes(dl.total_bytes)}</span>
        ${dl.error && html`<span style="color:var(--danger)">${formatTransferError(dl.error)}</span>`}
      </div>
    </div>`;
}

export function Downloads({ wsEvent, showToast }) {
  const [downloads, setDownloads] = useState([]);
  const [loading, setLoading]     = useState(true);
  const [removeTarget, setRemoveTarget] = useState(null);

  const load = async () => {
    try { setDownloads(await api.downloads()); }
    catch {}
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  useEffect(() => {
    if (!wsEvent) return;
    if (wsEvent.event === 'download_progress' || wsEvent.event === 'download_error') {
      setDownloads(prev => {
        const exists = prev.find(d => d.id === wsEvent.data.id);
        if (exists) {
          return prev.map(d => d.id === wsEvent.data.id ? { ...d, ...wsEvent.data } : d);
        }
        return [...prev, wsEvent.data];
      });
    }
    if (wsEvent.event === 'download_complete') {
      setDownloads(prev =>
        prev.map(d => d.id === wsEvent.data.id ? { ...d, status: 'done', progress: 1 } : d)
      );
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

  const active = downloads.filter(d => !['done', 'seeding'].includes(d.status));
  const done   = downloads.filter(d => d.status === 'done' || d.status === 'seeding');

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
        : downloads.length === 0
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
              ${done.length > 0 && active.length > 0 && html`<hr style="border-color:var(--surface-2);margin:4px 0"/>`}
              ${done.map(d => html`
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
