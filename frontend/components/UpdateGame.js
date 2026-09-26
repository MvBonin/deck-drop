import { html } from 'htm/preact';
import { useState, useEffect } from 'preact/hooks';
import { api, fmtBytes } from '../api.js';
import { formatApiError } from '../errors.js';
import { VersionList } from './VersionList.js';

/**
 * "Update übernehmen" dialog (Phase 5). Also used for the Phase 7 "restore"
 * action: the caller can pre-select the game's own content_hash as
 * `restoreKey`, which is a valid `version_key` for POST /update when the game
 * is `modified` (see docs/plans/game-updates.md "Phase 7").
 */
export function UpdateGame({ game, onClose, onStarted, restoreKey }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [chosenKey, setChosenKey] = useState(restoreKey || null);
  const [showHistory, setShowHistory] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const d = await api.getUpdates(game.id);
        if (cancelled) return;
        setData(d);
        if (!restoreKey) {
          const versions = d.versions || [];
          const firstNewer = versions.find(v => v.is_newer && v.shareable && v.has_torrent);
          setChosenKey(firstNewer?.version_key ?? versions[0]?.version_key ?? null);
        }
      } catch (err) {
        if (!cancelled) setLoadError(formatApiError(err, 'game'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [game.id]);

  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  const versions = data?.versions || [];
  const chosen = versions.find(v => v.version_key === chosenKey);
  const modified = game.content_state === 'modified';

  const submit = async () => {
    if (!chosenKey) return;
    setSubmitting(true);
    setError('');
    try {
      const dl = await api.startUpdate(game.id, chosenKey);
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
      aria-label="Update übernehmen"
    >
      <div class="dialog">
        <div class="dialog-title">Update – ${game.name}</div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:10px">
          Installiert: ${game.version_label || `Rev. ${game.revision ?? 1}`}
          ${game.created_by && html` · erstellt von ${game.created_by}`}
        </div>

        ${modified && html`
          <div style="font-size:12px;color:var(--danger);background:rgba(220,60,60,0.12);border:1px solid var(--danger);border-radius:8px;padding:8px 10px;margin-bottom:10px">
            ⚠ Lokale Änderungen an diesem Spiel werden überschrieben.
          </div>
        `}

        ${loading
          ? html`<div class="empty-state"><div class="spinner"></div></div>`
          : loadError
            ? html`<p style="color:var(--danger);font-size:13px">${loadError}</p>`
            : versions.length === 0
              ? html`<p class="dialog-text">Keine Version im Netzwerk gefunden.</p>`
              : html`
                <${VersionList} versions=${versions} value=${chosenKey} onChange=${setChosenKey} />
                ${chosen && html`
                  <div style="font-size:13px;color:var(--text-dim);margin-top:10px">
                    Zu laden: ${chosen.download_estimate != null
                      ? html`ca. ${fmtBytes(chosen.download_estimate)}${chosen.size_bytes ? ` von ${fmtBytes(chosen.size_bytes)}` : ''}`
                      : 'wird beim Prüfen ermittelt'}
                  </div>
                `}
                ${chosen?.history?.length > 0 && html`
                  <div style="margin-top:8px">
                    <button
                      type="button"
                      class="btn btn-ghost"
                      style="padding:4px 8px;font-size:12px"
                      onClick=${() => setShowHistory(s => !s)}
                    >${showHistory ? 'Verlauf ▲' : 'Verlauf ▾'}</button>
                    ${showHistory && html`
                      <div style="max-height:160px;overflow-y:auto;font-size:12px;margin-top:6px;display:flex;flex-direction:column;gap:6px">
                        ${chosen.history.slice().reverse().map(h => html`
                          <div key=${h.revision}>
                            <strong>${h.version_label || `Rev. ${h.revision}`}</strong>
                            <span style="color:var(--text-dim)"> · ${h.by}</span>
                            ${h.note && html`<div style="color:var(--text-dim)">${h.note}</div>`}
                          </div>
                        `)}
                      </div>
                    `}
                  </div>
                `}
              `
        }

        ${error && html`<p style="color:var(--danger);font-size:13px;margin-top:10px">${error}</p>`}

        <div class="dialog-actions">
          <button type="button" class="btn btn-ghost" onClick=${onClose} disabled=${submitting}>Abbrechen</button>
          <button
            type="button"
            class="btn btn-primary"
            disabled=${submitting || loading || !chosenKey}
            onClick=${submit}
          >${submitting ? html`<span class="spinner"></span>` : 'Aktualisieren'}</button>
        </div>
      </div>
    </div>`;
}
