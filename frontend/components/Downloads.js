import { html } from 'htm/preact';
import { useState, useEffect } from 'preact/hooks';
import { api, fmtBytes, fmtSpeed } from '../api.js';

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
};

function DownloadRow({ dl, onCancel }) {
  const pct = Math.round(dl.progress * 100);
  const fillClass = dl.status === 'done' || dl.status === 'seeding' ? 'done'
                  : dl.status === 'error' ? 'error' : '';
  const statusColor = STATUS_COLOR[dl.status] || 'var(--text-dim)';

  return html`
    <div class="download-row" tabIndex=${0} data-card>
      <div class="download-header">
        <div>
          <div class="download-name">${dl.game_name}</div>
          <div class="download-peer">von ${dl.peer_name}</div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;flex-shrink:0">
          <span style="font-size:13px;font-weight:600;color:${statusColor}">${STATUS_LABEL[dl.status] || dl.status}</span>
          ${dl.status !== 'done' && dl.status !== 'seeding' && html`
            <button class="btn-icon btn-danger" onClick=${() => onCancel(dl.id)} title="Abbrechen" aria-label="Download abbrechen">✕</button>
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
        ${dl.error && html`<span style="color:var(--danger)">${dl.error}</span>`}
      </div>
    </div>`;
}

export function Downloads({ wsEvent, showToast }) {
  const [downloads, setDownloads] = useState([]);
  const [loading, setLoading]     = useState(true);

  const load = async () => {
    try { setDownloads(await api.downloads()); }
    catch {}
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  // Live updates from WS
  useEffect(() => {
    if (!wsEvent) return;
    if (wsEvent.event === 'download_progress') {
      setDownloads(prev =>
        prev.map(d => d.id === wsEvent.data.id ? { ...d, ...wsEvent.data } : d)
      );
    }
    if (wsEvent.event === 'download_complete') {
      setDownloads(prev =>
        prev.map(d => d.id === wsEvent.data.id ? { ...d, status: 'done', progress: 1 } : d)
      );
    }
    if (wsEvent.event === 'download_error') {
      setDownloads(prev =>
        prev.map(d => d.id === wsEvent.data.id ? { ...d, status: 'error', error: wsEvent.data.error } : d)
      );
    }
  }, [wsEvent]);

  const cancel = async (id) => {
    try {
      await api.cancelDl(id);
      setDownloads(prev => prev.filter(d => d.id !== id));
      showToast('Download abgebrochen');
    } catch {}
  };

  const active = downloads.filter(d => d.status !== 'done' && d.status !== 'seeding');
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
              ${active.map(d => html`<${DownloadRow} key=${d.id} dl=${d} onCancel=${cancel} />`)}
              ${done.length > 0 && active.length > 0 && html`<hr style="border-color:var(--surface-2);margin:4px 0"/>`}
              ${done.map(d => html`<${DownloadRow} key=${d.id} dl=${d} onCancel=${cancel} />`)}
            </div>
          `
      }
    </div>`;
}
