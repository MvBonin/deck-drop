import { html } from 'htm/preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import { api } from '../api.js';
import { t } from '../i18n/index.js';

const PLATFORM_ICON = { windows: '🪟', linux: '🐧', mac: '🍎' };

/**
 * Search online metadata (Steam store) for a game and let the user pick one
 * match – or explicitly continue without metadata.
 *
 * onPick(meta) is called with
 *   { steam_app_id, name, description, suggested_platform, cover_url }
 * or with null for "no metadata".
 */
export function MetadataPicker({ query, onPick, selectedId, autoSearch = true }) {
  const [term, setTerm]         = useState(query || '');
  const [results, setResults]   = useState(null);
  const [searching, setSearching] = useState(false);
  const [picking, setPicking]   = useState(0);
  const [error, setError]       = useState('');
  const lastAuto = useRef('');

  const run = async (q) => {
    const value = (q ?? term).trim();
    if (!value) { setResults([]); return; }
    setSearching(true);
    setError('');
    try {
      const data = await api.searchMetadata(value);
      setResults(data.results || []);
    } catch (err) {
      setResults(null);
      setError(t('meta.searchFailed'));
    } finally {
      setSearching(false);
    }
  };

  // Search once for the name the dialog suggested (folder name) – debounced,
  // so typing a path doesn't fire a request per keystroke.
  useEffect(() => {
    if (!autoSearch) return;
    const q = (query || '').trim();
    if (!q || q === lastAuto.current) return;
    const id = setTimeout(() => {
      lastAuto.current = q;
      setTerm(q);
      run(q);
    }, 350);
    return () => clearTimeout(id);
  }, [query, autoSearch]);

  const pick = async (r) => {
    setPicking(r.steam_app_id);
    setError('');
    let meta = {
      steam_app_id: r.steam_app_id,
      name: r.name,
      description: '',
      suggested_platform: r.platforms?.includes('linux') ? 'linux'
        : (r.platforms?.length === 1 && r.platforms[0] === 'windows') ? 'windows' : 'any',
      cover_url: r.cover_url,
    };
    try {
      const d = await api.metadataDetails(r.steam_app_id);
      meta = { ...meta, ...d, name: d.name || r.name };
    } catch (_) {
      // Details are a bonus – the search result alone is a valid pick.
    } finally {
      setPicking(0);
    }
    onPick(meta);
  };

  const onKey = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); run(); }
  };

  return html`
    <div class="meta-picker">
      <div class="input-row">
        <input
          class="form-input"
          type="search"
          placeholder=${t('meta.searchPlaceholder')}
          value=${term}
          onInput=${e => setTerm(e.target.value)}
          onKeyDown=${onKey}
          aria-label=${t('meta.searchPlaceholder')}
        />
        <button type="button" class="btn btn-ghost" onClick=${() => run()} disabled=${searching || !term.trim()}>
          ${searching ? html`<span class="spinner"></span>` : t('meta.search')}
        </button>
      </div>

      ${error && html`<p class="meta-error">${error}</p>`}

      ${results && html`
        <div class="meta-results" role="listbox" aria-label=${t('meta.results')}>
          ${results.length === 0 && html`<div class="meta-empty">${t('meta.noResults')}</div>`}
          ${results.map(r => html`
            <button
              type="button"
              role="option"
              aria-selected=${selectedId === r.steam_app_id}
              class=${'meta-item' + (selectedId === r.steam_app_id ? ' selected' : '')}
              onClick=${() => pick(r)}
              disabled=${!!picking}
            >
              <img class="meta-thumb" src=${r.thumb_url} alt="" loading="lazy"
                onError=${e => { e.target.style.visibility = 'hidden'; }} />
              <span class="meta-name">${r.name}</span>
              <span class="meta-platforms">
                ${picking === r.steam_app_id
                  ? html`<span class="spinner"></span>`
                  : (r.platforms || []).map(p => html`<span title=${p}>${PLATFORM_ICON[p] || p}</span>`)}
              </span>
            </button>`)}
        </div>`}

      <button type="button" class="btn btn-ghost btn-sm meta-none" onClick=${() => onPick(null)}>
        ${t('meta.none')}
      </button>
    </div>`;
}

/** Compact line for an already chosen match ("✓ Celeste · Steam 504230 – change"). */
export function MetadataChoice({ meta, onChange }) {
  return html`
    <div class="meta-choice">
      ${meta
        ? html`
          <img class="meta-thumb" src=${`https://cdn.cloudflare.steamstatic.com/steam/apps/${meta.steam_app_id}/capsule_231x87.jpg`} alt=""
            onError=${e => { e.target.style.visibility = 'hidden'; }} />
          <span class="meta-name">✓ ${meta.name} <span class="meta-sub">Steam ${meta.steam_app_id}</span></span>`
        : html`<span class="meta-name meta-sub">${t('meta.noneChosen')}</span>`}
      <button type="button" class="btn btn-ghost btn-sm" onClick=${onChange}>${t('meta.change')}</button>
    </div>`;
}
