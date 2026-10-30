import { html } from 'htm/preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import { api } from '../api.js';
import { formatApiError } from '../errors.js';
import { t } from '../i18n/index.js';

// Last segment of a path ("/home/me/Games/Celeste/" → "Celeste").
export function folderName(path) {
  const parts = path.trim().split(/[\\/]+/).filter(Boolean);
  const last = parts[parts.length - 1] || '';
  return /^[A-Za-z]:$/.test(last) ? '' : last;
}

function FolderBrowser({ start, onPick, onCancel }) {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState('');

  const open = async (path, fallback) => {
    setLoading(true);
    setError('');
    try {
      setData(await api.browse(path));
    } catch (err) {
      if (fallback) return open('', false);
      setError(formatApiError(err, 'fs'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { open(start, !!start); }, []);

  return html`
    <div class="folder-browser">
      ${data && html`
        <div class="folder-shortcuts">
          ${data.shortcuts.map(s => html`
            <button type="button" class="btn btn-ghost btn-sm" title=${s.path}
              onClick=${() => open(s.path)} disabled=${loading}>${s.name}</button>`)}
        </div>
        <div class="folder-current" title=${data.path}>${data.path}</div>
      `}
      <div class="folder-list">
        ${data?.parent && html`
          <button type="button" class="folder-item" onClick=${() => open(data.parent)} disabled=${loading}>
            ⬆ ${t('game.browseUp')}
          </button>`}
        ${data?.dirs.map(d => html`
          <button type="button" class="folder-item" onClick=${() => open(d.path)} disabled=${loading}>
            📁 ${d.name}
          </button>`)}
        ${data && !data.dirs.length && html`<div class="folder-empty">${t('game.browseEmpty')}</div>`}
        ${loading && !data && html`<span class="spinner"></span>`}
      </div>
      ${error && html`<p style="color:var(--danger);font-size:13px">${error}</p>`}
      <div class="dialog-actions">
        <button type="button" class="btn btn-ghost" onClick=${onCancel}>${t('common.cancel')}</button>
        <button type="button" class="btn btn-primary" disabled=${!data || loading}
          onClick=${() => onPick(data.path)}>${t('game.browseChoose')}</button>
      </div>
    </div>`;
}

export function AddGame({ onClose, onAdded }) {
  const [path, setPath]           = useState('');
  const [name, setName]           = useState('');
  const [nameTouched, setTouched] = useState(false);
  const [browsing, setBrowsing]   = useState(false);
  const [platform, setPlatform]   = useState('any');
  const [appId, setAppId]         = useState('');
  const [needsWizard, setNeeds]   = useState(false);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState('');
  const firstRef = useRef(null);

  // Focus trap: focus first element on open
  useEffect(() => { firstRef.current?.focus(); }, []);

  // Close on Escape (closes the folder browser first)
  useEffect(() => {
    const h = (e) => {
      if (e.key !== 'Escape') return;
      if (browsing) setBrowsing(false); else onClose();
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose, browsing]);

  const changePath = (value) => {
    setPath(value);
    setNeeds(false);
    setError('');
    // Default the game name to the folder name until the user types their own.
    if (!nameTouched) setName(folderName(value));
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!path.trim()) return;
    setLoading(true);
    setError('');
    try {
      const body = { path: path.trim() };
      if (needsWizard) {
        body.name = name.trim();
        body.platform = platform;
        if (appId) body.steam_app_id = parseInt(appId, 10);
      }
      const game = await api.addGame(body);
      onAdded(game);
    } catch (err) {
      if (err.status === 422 && !needsWizard) {
        // No deckdrop.toml → show the wizard
        setNeeds(true);
        setError('');
      } else {
        setError(formatApiError(err, 'game'));
      }
    } finally {
      setLoading(false);
    }
  };

  const canSubmit = path.trim() && (!needsWizard || name.trim()) && !loading;

  return html`
    <div class="overlay" onClick=${e => e.target === e.currentTarget && onClose()} role="dialog" aria-modal="true" aria-label=${t('game.add')}>
      <div class="dialog">
        <div class="dialog-title">${needsWizard ? t('game.setup') : t('game.add')}</div>

        <form onSubmit=${submit} style="display:flex;flex-direction:column;gap:14px">
          <div class="form-group">
            <label class="form-label">${t('game.folderPath')}</label>
            <div class="input-row">
              <input
                ref=${firstRef}
                class="form-input"
                type="text"
                placeholder=${t('game.folderPlaceholder')}
                value=${path}
                onInput=${e => changePath(e.target.value)}
                disabled=${loading}
              />
              <button type="button" class="btn btn-ghost" onClick=${() => setBrowsing(b => !b)}
                disabled=${loading} aria-expanded=${browsing}>${t('game.browse')}</button>
            </div>
          </div>

          ${browsing && html`
            <${FolderBrowser}
              start=${path.trim()}
              onPick=${p => { changePath(p); setBrowsing(false); }}
              onCancel=${() => setBrowsing(false)}
            />`}

          ${needsWizard && html`
            <p style="font-size:13px;color:var(--text-dim)"
              dangerouslySetInnerHTML=${{ __html: t('game.noToml') }}></p>
            <div class="form-group">
              <label class="form-label">${t('game.name')}</label>
              <input
                class="form-input"
                type="text"
                placeholder="Stardew Valley"
                value=${name}
                onInput=${e => { setName(e.target.value); setTouched(true); }}
                required
              />
            </div>
            <div class="form-group">
              <label class="form-label">${t('game.platform')}</label>
              <select class="form-select" value=${platform} onChange=${e => setPlatform(e.target.value)}>
                <option value="linux">Linux</option>
                <option value="windows">Windows</option>
                <option value="any">${t('game.platformAny')}</option>
              </select>
            </div>
            <div class="form-group">
              <label class="form-label">${t('game.steamAppId')}</label>
              <input
                class="form-input"
                type="number"
                placeholder="413150"
                value=${appId}
                onInput=${e => setAppId(e.target.value)}
              />
            </div>
          `}

          ${error && html`<p style="color:var(--danger);font-size:13px">${error}</p>`}

          ${!browsing && html`<div class="dialog-actions">
            <button type="button" class="btn btn-ghost" onClick=${onClose}>${t('common.cancel')}</button>
            <button type="submit" class="btn btn-primary" disabled=${!canSubmit}>
              ${loading ? html`<span class="spinner"></span>` : (needsWizard ? t('game.setupButton') : t('game.addButton'))}
            </button>
          </div>`}
        </form>
      </div>
    </div>`;
}
