import { html } from 'htm/preact';
import { useState, useEffect, useRef, useLayoutEffect } from 'preact/hooks';
import { api, fmtBytes } from '../api.js';
import { formatApiError } from '../errors.js';
import { t } from '../i18n/index.js';
import { Icon } from '../icons.js';

export function PublishUpdate({ game, onClose, onPublished }) {
  const [versionLabel, setVersionLabel] = useState(game.version_label || '');
  const [note, setNote]                 = useState('');
  const [changes, setChanges]           = useState(null);
  const [loadingChanges, setLoadingChanges] = useState(true);
  const [includedAdded, setIncludedAdded]   = useState({});
  // Changed files the user no longer wants to distribute (settings, caches
  // the game rewrites) – they leave the shared version and are ignored from
  // now on, so the next rewrite doesn't block sharing again.
  const [ignoredChanged, setIgnoredChanged] = useState({});
  const [showDetails, setShowDetails]   = useState(false);
  const [submitting, setSubmitting]     = useState(false);
  const [error, setError]               = useState('');
  const firstRef = useRef(null);

  useLayoutEffect(() => { firstRef.current?.focus(); }, []);

  useLayoutEffect(() => {
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
        const ignored = {};
        for (const c of data.changed || []) if (c.ignored) ignored[c.path] = true;
        setIgnoredChanged(ignored);
        // Few changes: show them right away, they're what the user decides on.
        const total = (data.changed?.length || 0) + (data.added?.length || 0) + (data.removed?.length || 0);
        if (total > 0 && total <= 15) setShowDetails(true);
      } catch (err) {
        if (!cancelled) setError(formatApiError(err, 'game'));
      } finally {
        if (!cancelled) setLoadingChanges(false);
      }
    })();
    return () => { cancelled = true; };
  }, [game.id]);

  const toggleIgnored = (path) => {
    setIgnoredChanged((m) => ({ ...m, [path]: !m[path] }));
  };

  const toggleAdded = (path) => {
    setIncludedAdded((m) => ({ ...m, [path]: !m[path] }));
  };

  const submit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      const exclude = [
        ...Object.entries(includedAdded).filter(([, included]) => !included).map(([path]) => path),
        ...Object.entries(ignoredChanged).filter(([, ignored]) => ignored).map(([path]) => path),
      ];
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
    ? t('publish.detecting')
    : t('publish.summary', { changed: changed.length, added: added.length, removed: removed.length });

  return html`
    <div class="overlay" onClick=${e => e.target === e.currentTarget && onClose()} role="dialog" aria-modal="true" aria-label=${t('publish.title')}>
      <div class="dialog">
        <div class="dialog-title">${t('publish.titleFor', { name: game.name })}</div>
        <div style="font-size:13px;color:var(--muted);margin-bottom:10px">
          ${t('publish.current', { version: game.version_label || `Rev. ${game.revision ?? 1}` })}
          ${game.created_by && t('publish.createdBy', { name: game.created_by })}
        </div>

        <form onSubmit=${submit} style="display:flex;flex-direction:column;gap:14px">
          <div class="form-group">
            <div style="font-size:13px;color:var(--text-dim)">${summaryText}</div>
            ${(added.length > 0 || removed.length > 0 || changed.length > 0) && html`
              <button
                type="button"
                class="btn btn-ghost"
                style="align-self:flex-start;padding:4px 8px;font-size:13px;margin-top:4px"
                onClick=${() => setShowDetails(s => !s)}
              >${t('publish.details')} <${Icon} name=${showDetails ? 'chevron-up' : 'chevron-down'} size=${16} /></button>
            `}
            ${showDetails && html`
              <div style="max-height:200px;overflow-y:auto;font-size:13px;margin-top:6px;display:flex;flex-direction:column;gap:4px">
                ${changed.map(c => html`
                  <div style="display:flex;align-items:center;gap:6px;justify-content:space-between">
                    <span style=${ignoredChanged[c.path] ? 'text-decoration:line-through;color:var(--text-dim)' : ''}>
                      ~ ${c.path} <span style="color:var(--text-dim)">(${fmtBytes(c.old_size || 0)} → ${fmtBytes(c.new_size || 0)})</span>
                    </span>
                    <label style="display:flex;align-items:center;gap:4px;flex-shrink:0;color:var(--text-dim)">
                      <input type="checkbox" checked=${!!ignoredChanged[c.path]} onChange=${() => toggleIgnored(c.path)} />
                      ${t('publish.ignore')}
                    </label>
                  </div>
                `)}
                ${changed.length > 0 && html`
                  <div style="font-size:13px;color:var(--warn);margin-top:2px">${t('publish.ignoreHint')}</div>
                `}
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
                  <div style="font-size:13px;color:var(--text-dim);margin-top:2px">
                    ${t('publish.unchecked')}
                  </div>
                `}
              </div>
            `}
          </div>

          <div class="form-group">
            <label class="form-label">${t('publish.version')}</label>
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
            <label class="form-label">${t('publish.note')}</label>
            <textarea
              class="form-input"
              rows="2"
              placeholder=${t('publish.notePlaceholder')}
              value=${note}
              onInput=${e => setNote(e.target.value)}
              disabled=${submitting}
              style="resize:vertical"
            />
          </div>

          ${error && html`<p style="color:var(--danger);font-size:13px">${error}</p>`}

          <div class="dialog-actions">
            <button type="button" class="btn btn-ghost" onClick=${onClose} disabled=${submitting}>${t('common.cancel')}</button>
            <button type="submit" class="btn btn-primary" disabled=${submitting || loadingChanges}>
              ${submitting ? html`<span class="spinner"></span>` : t('publish.submit')}
            </button>
          </div>
        </form>
      </div>
    </div>`;
}
