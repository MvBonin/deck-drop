import { html } from 'htm/preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import { api, fmtBytes } from '../api.js';
import { formatApiError } from '../errors.js';

export function PublishUpdate({ game, onClose, onPublished }) {
  const [versionLabel, setVersionLabel] = useState(game.version_label || '');
  const [note, setNote]                 = useState('');
  const [changes, setChanges]           = useState(null);
  const [loadingChanges, setLoadingChanges] = useState(true);
  const [includedAdded, setIncludedAdded]   = useState({});
  const [showDetails, setShowDetails]   = useState(false);
  const [submitting, setSubmitting]     = useState(false);
  const [error, setError]               = useState('');
  const firstRef = useRef(null);

  useEffect(() => { firstRef.current?.focus(); }, []);

  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.getChanges(game.id);
        if (cancelled) return;
        setChanges(data);
        const included = {};
        for (const a of data.added || []) included[a.path] = true;
        setIncludedAdded(included);
      } catch (err) {
        if (!cancelled) setError(formatApiError(err, 'game'));
      } finally {
        if (!cancelled) setLoadingChanges(false);
      }
    })();
    return () => { cancelled = true; };
  }, [game.id]);

  const toggleAdded = (path) => {
    setIncludedAdded((m) => ({ ...m, [path]: !m[path] }));
  };

  const submit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      const exclude = Object.entries(includedAdded)
        .filter(([, included]) => !included)
        .map(([path]) => path);
      const result = await api.publishUpdate(game.id, {
        version_label: versionLabel.trim(),
        note: note.trim(),
        exclude,
      });
      onPublished?.(result);
    } catch (err) {
      setError(formatApiError(err, 'game'));
      setSubmitting(false);
    }
  };

  const changed = changes?.changed || [];
  const removed = changes?.removed || [];
  const added = changes?.added || [];
  const summaryText = loadingChanges
    ? 'Änderungen werden ermittelt…'
    : `${changed.length} geändert · ${added.length} neu · ${removed.length} entfernt`;

  return html`
    <div class="overlay" onClick=${e => e.target === e.currentTarget && onClose()} role="dialog" aria-modal="true" aria-label="Update veröffentlichen">
      <div class="dialog">
        <div class="dialog-title">Update veröffentlichen – ${game.name}</div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:10px">
          Aktuell: ${game.version_label || `Rev. ${game.revision ?? 1}`}
          ${game.created_by && html` · erstellt von ${game.created_by}`}
        </div>

        <form onSubmit=${submit} style="display:flex;flex-direction:column;gap:14px">
          <div class="form-group">
            <div style="font-size:13px;color:var(--text-dim)">${summaryText}</div>
            ${(added.length > 0 || removed.length > 0 || changed.length > 0) && html`
              <button
                type="button"
                class="btn btn-ghost"
                style="align-self:flex-start;padding:4px 8px;font-size:12px;margin-top:4px"
                onClick=${() => setShowDetails(s => !s)}
              >${showDetails ? 'Details ▲' : 'Details ▾'}</button>
            `}
            ${showDetails && html`
              <div style="max-height:200px;overflow-y:auto;font-size:12px;margin-top:6px;display:flex;flex-direction:column;gap:4px">
                ${changed.map(c => html`
                  <div>~ ${c.path} <span style="color:var(--text-dim)">(${fmtBytes(c.old_size || 0)} → ${fmtBytes(c.new_size || 0)})</span></div>
                `)}
                ${removed.map(r => html`
                  <div style="color:var(--danger)">− ${r.path} <span style="color:var(--text-dim)">(${fmtBytes(r.size || 0)})</span></div>
                `)}
                ${added.map(a => html`
                  <label style="display:flex;align-items:center;gap:6px">
                    <input
                      type="checkbox"
                      checked=${!!includedAdded[a.path]}
                      onChange=${() => toggleAdded(a.path)}
                    />
                    <span>+ ${a.path} <span style="color:var(--text-dim)">(${fmtBytes(a.size || 0)})</span></span>
                  </label>
                `)}
                ${added.length > 0 && html`
                  <div style="font-size:11px;color:var(--text-dim);margin-top:2px">
                    Abgewählt = lokale Datei, nicht teilen (z. B. Spielstände).
                  </div>
                `}
              </div>
            `}
          </div>

          <div class="form-group">
            <label class="form-label">Version</label>
            <input
              ref=${firstRef}
              class="form-input"
              type="text"
              placeholder="1.6.8"
              value=${versionLabel}
              onInput=${e => setVersionLabel(e.target.value)}
              disabled=${submitting}
            />
          </div>

          <div class="form-group">
            <label class="form-label">Notiz (optional)</label>
            <textarea
              class="form-input"
              rows="2"
              placeholder="Patch 1.6.8, Crash-Fix"
              value=${note}
              onInput=${e => setNote(e.target.value)}
              disabled=${submitting}
              style="resize:vertical"
            />
          </div>

          ${error && html`<p style="color:var(--danger);font-size:13px">${error}</p>`}

          <div class="dialog-actions">
            <button type="button" class="btn btn-ghost" onClick=${onClose} disabled=${submitting}>Abbrechen</button>
            <button type="submit" class="btn btn-primary" disabled=${submitting || loadingChanges}>
              ${submitting ? html`<span class="spinner"></span>` : 'Veröffentlichen'}
            </button>
          </div>
        </form>
      </div>
    </div>`;
}
