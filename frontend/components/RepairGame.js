import { html } from 'htm/preact';
import { useState, useEffect } from 'preact/hooks';
import { api } from '../api.js';
import { formatApiError } from '../errors.js';
import { VersionList } from './VersionList.js';

/**
 * "Reparieren": pick a version and the peer to take it from. DeckDrop then
 * fetches that version's torrent, rechecks every file against it (like
 * "Recheck" in a BitTorrent client) and downloads whatever differs.
 */
export function RepairGame({ game, onClose, onStarted }) {
  const [versions, setVersions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [versionKey, setVersionKey] = useState(null);
  const [peerId, setPeerId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const d = await api.getUpdates(game.id);
        if (cancelled) return;
        const list = (d.versions || []).filter(v => v.shareable && v.has_torrent);
        setVersions(list);
        // Default: the installed version, else the newest one.
        const current = list.find(v => v.is_current) || list[0];
        setVersionKey(current?.version_key ?? null);
      } catch (err) {
        if (!cancelled) setLoadError(formatApiError(err, 'game'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [game.id]);

  const chosen = versions.find(v => v.version_key === versionKey);
  const peers = chosen?.peers || [];

  useEffect(() => {
    setPeerId(peers[0]?.peer_id ?? '');
  }, [versionKey, versions.length]);

  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  const submit = async () => {
    if (!versionKey) return;
    setSubmitting(true);
    setError('');
    try {
      const dl = await api.repairGame(game.id, versionKey, peerId || null);
      onStarted?.(dl);
    } catch (err) {
      setError(formatApiError(err, 'game'));
      setSubmitting(false);
    }
  };

  return html`
    <div
      class="overlay"
      onClick=${e => e.target === e.currentTarget && onClose()}
      role="dialog"
      aria-modal="true"
      aria-label="Spiel reparieren"
    >
      <div class="dialog">
        <div class="dialog-title">Reparieren – ${game.name}</div>
        <p class="dialog-text" style="margin-top:0">
          Alle Dateien werden gegen die gewählte Version geprüft (Recheck).
          Was fehlt oder abweicht, wird neu geladen.
        </p>

        ${loading
          ? html`<div class="empty-state"><div class="spinner"></div></div>`
          : loadError
            ? html`<p style="color:var(--danger);font-size:13px">${loadError}</p>`
            : versions.length === 0
              ? html`<p class="dialog-text">Keine Version dieses Spiels im Netzwerk gefunden.</p>`
              : html`
                <${VersionList} versions=${versions} value=${versionKey} onChange=${setVersionKey} />
                <label style="display:block;margin-top:12px;font-size:13px;color:var(--text-dim)">
                  Quelle
                  <select
                    class="form-input"
                    style="margin-top:4px"
                    value=${peerId}
                    onChange=${e => setPeerId(e.target.value)}
                    aria-label="Quelle"
                  >
                    ${peers.map(p => html`<option key=${p.peer_id} value=${p.peer_id}>${p.peer_name}</option>`)}
                  </select>
                </label>
              `
        }

        ${error && html`<p style="color:var(--danger);font-size:13px;margin-top:10px">${error}</p>`}

        <div class="dialog-actions">
          <button type="button" class="btn btn-ghost" onClick=${onClose} disabled=${submitting}>Abbrechen</button>
          <button
            type="button"
            class="btn btn-primary"
            disabled=${submitting || loading || !versionKey}
            onClick=${submit}
          >${submitting ? html`<span class="spinner"></span>` : 'Reparieren'}</button>
        </div>
      </div>
    </div>`;
}
