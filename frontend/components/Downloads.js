import { html } from 'htm/preact';
import { useState, useEffect } from 'preact/hooks';
import { api, fmtBytes, fmtSpeed } from '../api.js';
import { formatApiError, formatTransferError, getTransferHint } from '../errors.js';
import { t } from '../i18n/index.js';
import { Icon } from '../icons.js';

const STATUS_LABEL_KEYS = new Set([
  'queued', 'downloading', 'verifying', 'seeding', 'done', 'error', 'paused',
]);

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

/**
 * Label for a row. `status` is coarse ("Waiting" covers both waiting for the
 * host and re-checking existing files), so prefer the detailed phase – a long
 * file check used to look like a frozen download.
 */
function statusLabel(dl) {
  if (dl.phase === 'preparing') {
    // Update: reading the files already on disk to find reusable pieces.
    const pct = Math.round((dl.phase_progress || 0) * 100);
    return t('dl.checkingFiles', { pct });
  }
  if (dl.source_state && dl.kind !== 'update' && dl.num_peers === 0) {
    // the version/host is gone – the notice below explains it
    return t('dl.status.queued');
  }
  if (dl.status === 'queued' || dl.status === 'downloading') {
    if (dl.phase === 'checking') {
      const pct = Math.round((dl.phase_progress || 0) * 100);
      return t('dl.checkingFiles', { pct });
    }
    if (dl.phase === 'metadata') return t('dl.waitMetadata');
    if (dl.num_peers === 0) return t('dl.connecting');
  }
  return STATUS_LABEL_KEYS.has(dl.status) ? t(`dl.status.${dl.status}`) : dl.status;
}

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
    parts.push(t('dl.piecesMissing', { count: dl.pieces_missing }));
  }
  const remaining = dl.bytes_remaining ?? Math.max(0, dl.total_bytes - dl.downloaded_bytes);
  if (remaining > 0) {
    parts.push(t('dl.bytesMissing', { size: fmtBytes(remaining) }));
  }
  return parts.length ? parts.join(' · ') : null;
}

function DownloadRow({ dl, onPause, onResume, onRetry, onRemove, onSwitch, switching }) {
  const isUpdate = dl.kind === 'update';
  const displayName = dl.repair
    ? t('dl.repairName', { name: dl.game_name, version: dl.target_version_label || '?' })
    : isUpdate
      ? t('dl.updateName', { name: dl.game_name, version: dl.target_version_label || '?' })
      : dl.game_name;
  const pct = progressPct(dl);
  const pctLabel = formatPct(pct);
  const fillPct = Math.min(100, pct);
  const fillClass = dl.status === 'done' || dl.status === 'seeding' ? 'done'
                  : dl.status === 'error' ? 'error'
                  : dl.status === 'paused' ? '' : '';
  const checking = dl.phase === 'checking' || dl.phase === 'preparing';
  const statusColor = checking
    ? 'var(--accent)'
    : STATUS_COLOR[dl.status] || 'var(--text-dim)';
  const canPause = dl.status === 'downloading' || dl.status === 'queued' || dl.phase === 'preparing';
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
          <div class="download-name">${displayName}</div>
          <div class="download-peer">${dl.status === 'done' || dl.status === 'seeding'
            ? t('dl.originallyFrom', { peer: dl.peer_name })
            : t('dl.from', { peer: dl.peer_name })}</div>
        </div>
        <div class="download-actions">
          <span class="download-state" style=${`color:${statusColor}`}>${statusLabel(dl)}</span>
          ${canPause && html`
            <button class="btn btn-secondary btn-sm" onClick=${() => onPause(dl)}>
              <${Icon} name="pause" size=${18} />${t('dl.pause')}
            </button>
          `}
          ${canResume && html`
            <button class="btn btn-primary btn-sm" onClick=${() => onResume(dl)}>
              <${Icon} name="play" size=${18} />${t('dl.resume')}
            </button>
          `}
          ${canRetry && html`
            <button class="btn btn-primary btn-sm" onClick=${() => onRetry(dl)}>
              <${Icon} name="refresh-cw" size=${18} />${t('dl.retry')}
            </button>
          `}
          ${canRemove && html`
            <button class="btn btn-icon" onClick=${() => onRemove(dl)} title=${t('dl.remove')} aria-label=${t('dl.remove')}>
              <${Icon} name="trash-2" />
            </button>
          `}
        </div>
      </div>

      ${dl.status === 'error' && errMsg && html`
        <div class="dl-error">
          <div style="font-size:14px;font-weight:500;color:var(--danger)">${errMsg}</div>
          ${errHint && html`
            <div style="font-size:13px;color:var(--text-dim);margin-top:6px">${t('dl.solution', { hint: errHint })}</div>
          `}
        </div>
      `}

      ${dl.kind !== 'update' && dl.source_state === 'replaced' && html`
        <div class="dl-notice">
          <div style="font-weight:500">${t('dl.versionReplaced', { version: dl.switch_version_label })}</div>
          <div style="color:var(--text-dim);margin-top:4px">${t('dl.versionReplacedHint')}</div>
          <button class="btn btn-primary btn-sm" style="width:auto;margin-top:12px"
            disabled=${switching} onClick=${() => onSwitch(dl)}>
            ${switching ? html`<span class="spinner"></span>` : t('dl.switchVersion', { version: dl.switch_version_label })}
          </button>
        </div>
      `}
      ${dl.kind !== 'update' && dl.source_state === 'waiting' && html`
        <div class="dl-notice">
          <div style="font-weight:500">${t('dl.sourceChanged')}</div>
          <div style="color:var(--text-dim);margin-top:4px">${t('dl.sourceChangedHint')}</div>
        </div>
      `}

      <div class="progress-bar" role="progressbar" aria-valuenow=${fillPct.toFixed(2)} aria-valuemin="0" aria-valuemax="100" aria-label="${pctLabel}">
        <div class="progress-fill ${fillClass}" style="width:${fillPct}%"></div>
      </div>

      <div class="download-stats">
        <span><span class="stat-val">${pctLabel}</span></span>
        ${(dl.status === 'downloading' || dl.status === 'verifying') && html`
          <span style="display:inline-flex;align-items:center;gap:4px"><${Icon} name="download" size=${14} /><span class="stat-val">${fmtSpeed(dl.speed_bytes_sec)}</span></span>
          <span dangerouslySetInnerHTML=${{ __html: t('dl.peers', { count: dl.num_peers }) }}></span>
        `}
        ${dl.total_bytes > 0
          ? html`<span>${fmtBytes(dl.downloaded_bytes)} / ${fmtBytes(dl.total_bytes)}</span>`
          : dl.downloaded_bytes > 0
            ? html`<span>${t('dl.downloaded', { size: fmtBytes(dl.downloaded_bytes) })}</span>`
            : (dl.status === 'queued' || dl.status === 'downloading')
              ? html`<span style="color:var(--text-dim)">${dl.num_peers > 0 ? t('dl.loadingMetadata') : t('dl.connectingHost')}</span>`
              : html`<span>${fmtBytes(dl.downloaded_bytes)} / ${fmtBytes(dl.total_bytes)}</span>`}
        ${checking && html`
          <span style="color:var(--text-dim);font-size:13px">${t('dl.checkingNote')}</span>
        `}
        ${dl.phase === 'downloading' && dl.stall_seconds > 20 && html`
          <span style="color:var(--text-dim);font-size:13px">${t('dl.noDataSince', { seconds: dl.stall_seconds })}</span>
        `}
        ${sprint && html`<span style="color:var(--accent)">${sprint}</span>`}
        ${pct >= 99.5 && dl.bytes_remaining > 0 && (dl.status === 'downloading' || dl.status === 'verifying') && html`
          <span style="color:var(--text-dim);font-size:13px">${t('dl.reconnecting')}</span>
        `}
        ${dl.num_peers >= 1 && (dl.bytes_remaining ?? 0) > 0 && (dl.bytes_remaining ?? 0) < 1024 * 1024
          && (dl.status === 'downloading' || dl.status === 'verifying') && html`
          <span style="color:var(--text-dim);font-size:13px">${t('dl.lastDataPending')}</span>
        `}
      </div>
    </div>`;
}

export function Downloads({ wsEvent, showToast, downloads, setDownloads }) {
  const [loading, setLoading]     = useState(true);
  const [removeTarget, setRemoveTarget] = useState(null);
  const [switchingId, setSwitchingId] = useState(null);

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
    const timer = setInterval(load, 2000);
    return () => clearInterval(timer);
  }, [downloads]);

  useEffect(() => {
    if (!wsEvent) return;
    if (wsEvent.event === 'download_torrent_upgraded') {
      setDownloads(prev =>
        prev.map(d => d.id === wsEvent.data.id
          ? { ...d, status: 'downloading', error: null, error_hint: null }
          : d)
      );
      showToast(t('dl.torrentUpgraded', { name: wsEvent.data.game_name }));
    }
    if (wsEvent.event === 'download_error') {
      showToast(t('dl.failed', { error: formatTransferError(wsEvent.data.error) }));
    }
  }, [wsEvent]);

  const pause = async (dl) => {
    try {
      const updated = await api.pauseDl(dl.id);
      setDownloads(prev => prev.map(d => d.id === dl.id ? updated : d));
      showToast(t('dl.paused', { name: dl.game_name }));
    } catch (err) {
      showToast(t('common.error', { error: formatApiError(err, 'download') }));
    }
  };

  const resume = async (dl) => {
    try {
      const updated = await api.resumeDl(dl.id);
      setDownloads(prev => prev.map(d => d.id === dl.id ? updated : d));
      showToast(t('dl.resumed', { name: dl.game_name }));
    } catch (err) {
      showToast(t('common.error', { error: formatApiError(err, 'download') }));
    }
  };

  const retry = async (dl) => {
    try {
      const updated = await api.retryDl(dl.id);
      setDownloads(prev => prev.map(d => d.id === dl.id ? updated : d));
      showToast(t('dl.retried', { name: dl.game_name }));
    } catch (err) {
      showToast(t('common.error', { error: formatApiError(err, 'download') }));
    }
  };

  const switchVersion = async (dl) => {
    setSwitchingId(dl.id);
    try {
      const updated = await api.switchDlVersion(dl.id, dl.switch_version_key);
      setDownloads(prev => prev.map(d => d.id === dl.id ? updated : d));
      showToast(t('dl.switched', { name: dl.game_name, version: dl.switch_version_label }));
    } catch (err) {
      showToast(t('common.error', { error: formatApiError(err, 'download') }));
    } finally {
      setSwitchingId(null);
    }
  };

  const confirmRemove = async (deleteFiles) => {
    const dl = removeTarget;
    if (!dl) return;
    try {
      await api.removeDl(dl.id, deleteFiles);
      setDownloads(prev => prev.filter(d => d.id !== dl.id));
      showToast(deleteFiles
        ? t('dl.removedDeleted', { name: dl.game_name })
        : t('dl.removedFromList', { name: dl.game_name }));
    } catch (err) {
      showToast(t('common.error', { error: formatApiError(err, 'download') }));
    } finally {
      setRemoveTarget(null);
    }
  };

  const active = downloads.filter(d => ACTIVE_DOWNLOAD_STATUSES.has(d.status));

  return html`
    <div class="view">
      <div class="view-header">
        <div>
          <h1 class="view-title">${t('dl.title')}</h1>
          <div class="view-subtitle">${t('dl.active', { count: active.length })}</div>
        </div>
      </div>

      ${loading
        ? html`<div class="empty-state"><div class="spinner"></div></div>`
        : active.length === 0
          ? html`<div class="empty-state">
              <div class="empty-icon"><${Icon} name="inbox" /></div>
              <div class="empty-title">${t('dl.emptyTitle')}</div>
              <div class="empty-sub">${t('dl.emptySub')}</div>
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
                  onSwitch=${switchVersion}
                  switching=${switchingId === d.id}
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
          aria-label=${t('dl.removeDialog')}
        >
          <div class="dialog">
            <div class="dialog-title">${t('dl.removeConfirm', { name: removeTarget.game_name })}</div>
            <p class="dialog-text">
              ${removeTarget.kind === 'update'
                ? t('dl.removeUpdateText')
                : t('dl.removeText')}
              ${removeTarget.dest_path ? html`<br/><br/><code style="font-size:13px;color:var(--text-dim);overflow-wrap:anywhere">${removeTarget.dest_path}</code>` : null}
            </p>
            <div class="dialog-actions" style="flex-direction:column;align-items:stretch;gap:8px">
              <button type="button" class="btn btn-ghost" onClick=${() => confirmRemove(false)}>
                ${t('dl.removeListOnly')}
              </button>
              ${removeTarget.kind !== 'update' && html`
                <button type="button" class="btn btn-danger" onClick=${() => confirmRemove(true)}>
                  ${t('dl.removeAndDelete')}
                </button>
              `}
              <button type="button" class="btn btn-ghost" onClick=${() => setRemoveTarget(null)}>
                ${t('common.cancel')}
              </button>
            </div>
          </div>
        </div>
      `}
    </div>`;
}
