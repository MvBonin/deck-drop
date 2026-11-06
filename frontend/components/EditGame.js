import { html } from 'htm/preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import { api } from '../api.js';
import { formatApiError } from '../errors.js';
import { PublishUpdate } from './PublishUpdate.js';
import { t } from '../i18n/index.js';
import { MetadataPicker } from './MetadataPicker.js';

// One-click additions to a game's ignore list. Per game on purpose: some games
// ship files with these names, so they can't be global defaults.
const IGNORE_PRESETS = [
  { key: 'shaderCache', patterns: ['**/ShaderCache/**', '**/shadercache/**', '**/*.shadercache'] },
  { key: 'unrealSaved', patterns: ['**/Saved/**'] },
];

export function EditGame({ game, onClose, onSaved, onGameUpdated }) {
  const [showPublish, setShowPublish] = useState(false);
  const [name, setName]             = useState(game.name || '');
  const [platform, setPlatform]     = useState(game.platform || 'any');
  const [appId, setAppId]           = useState(game.steam_app_id ? String(game.steam_app_id) : '');
  const [description, setDesc]      = useState(game.description || '');
  const [launchExe, setLaunchExe]   = useState(game.launch_exe || '');
  const [launchArgs, setLaunchArgs] = useState(game.launch_args || '');
  const [runner, setRunner]         = useState(game.runner || '');
  const [ignoreText, setIgnoreText] = useState((game.ignore || []).join('\n'));
  const [loading, setLoading]       = useState(false);
  const [searching, setSearching]   = useState(false);
  const [error, setError]           = useState('');
  const firstRef = useRef(null);

  useEffect(() => { firstRef.current?.focus(); }, []);

  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  const applyMeta = (m) => {
    setSearching(false);
    if (!m) return;
    setAppId(String(m.steam_app_id));
    if (m.description && !description.trim()) setDesc(m.description);
  };

  const addPreset = (patterns) => {
    const lines = ignoreText.split('\n').map(l => l.trim()).filter(Boolean);
    for (const p of patterns) if (!lines.includes(p)) lines.push(p);
    setIgnoreText(lines.join('\n'));
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setLoading(true);
    setError('');
    try {
      const body = {
        name: name.trim(),
        platform,
        description,
        launch_exe: launchExe,
        launch_args: launchArgs,
        runner,
        ignore: ignoreText.split('\n').map(l => l.trim()).filter(Boolean),
      };
      if (appId) body.steam_app_id = parseInt(appId, 10);
      const updated = await api.patchGame(game.id, body);
      onSaved(updated);
    } catch (err) {
      setError(formatApiError(err, 'game'));
    } finally {
      setLoading(false);
    }
  };

  return html`
    <div class="overlay" onClick=${e => e.target === e.currentTarget && onClose()} role="dialog" aria-modal="true" aria-label=${t('game.edit')}>
      <div class="dialog">
        <div class="dialog-title">${t('game.edit')}</div>

        <form onSubmit=${submit} style="display:flex;flex-direction:column;gap:14px">
          <div class="form-group">
            <label class="form-label">${t('game.name')}</label>
            <input
              ref=${firstRef}
              class="form-input"
              type="text"
              placeholder=${t('game.namePlaceholder')}
              value=${name}
              onInput=${e => setName(e.target.value)}
              disabled=${loading}
              required
            />
          </div>

          <div class="form-group">
            <label class="form-label">${t('game.platform')}</label>
            <select class="form-select" value=${platform} onChange=${e => setPlatform(e.target.value)} disabled=${loading}>
              <option value="linux">Linux</option>
              <option value="windows">Windows</option>
              <option value="any">${t('game.platformAny')}</option>
            </select>
          </div>

          <div class="form-group">
            <label class="form-label">${t('game.steamAppId')}</label>
            <input
              class="form-input"
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              placeholder="413150"
              value=${appId}
              onInput=${e => setAppId(e.target.value.replace(/\D/g, ''))}
              disabled=${loading}
            />
            ${searching
              ? html`<${MetadataPicker} query=${name} selectedId=${appId ? parseInt(appId, 10) : null} onPick=${applyMeta} />`
              : html`<button
                  type="button"
                  class="btn btn-ghost"
                  style="align-self:flex-start;padding:8px 14px;font-size:13px"
                  onClick=${() => setSearching(true)}
                  disabled=${loading}
                  title=${t('game.searchSteamTitle')}
                >${t('game.searchSteam')}</button>`}
            <div style="font-size:11px;color:var(--muted);margin-top:2px;line-height:1.45"
              dangerouslySetInnerHTML=${{ __html: t('game.coverNote') }}></div>
          </div>

          <div class="form-group">
            <label class="form-label">${t('game.description')}</label>
            <textarea
              class="form-input"
              rows="3"
              placeholder=${t('game.descriptionPlaceholder')}
              value=${description}
              onInput=${e => setDesc(e.target.value)}
              disabled=${loading}
              style="resize:vertical"
            />
          </div>

          <div class="form-group">
            <label class="form-label">${t('game.launchExe')}</label>
            <input
              class="form-input"
              type="text"
              placeholder=${t('game.launchExePlaceholder')}
              value=${launchExe}
              onInput=${e => setLaunchExe(e.target.value)}
              disabled=${loading}
            />
          </div>

          <div class="form-group">
            <label class="form-label">${t('game.launchArgs')}</label>
            <input
              class="form-input"
              type="text"
              placeholder="--fullscreen --no-intro"
              value=${launchArgs}
              onInput=${e => setLaunchArgs(e.target.value)}
              disabled=${loading}
            />
          </div>

          <div class="form-group">
            <label class="form-label">${t('game.runner')}</label>
            <input
              class="form-input"
              type="text"
              placeholder="Proton 9.0"
              value=${runner}
              onInput=${e => setRunner(e.target.value)}
              disabled=${loading}
            />
          </div>

          <div class="form-group">
            <label class="form-label" for="edit-ignore">${t('game.ignore')}</label>
            <textarea
              id="edit-ignore"
              class="form-input"
              rows="3"
              placeholder=${'**/ShaderCache/**\nsettings.ini'}
              value=${ignoreText}
              onInput=${e => setIgnoreText(e.target.value)}
              disabled=${loading}
              style="resize:vertical;font-family:monospace;font-size:12px"
            />
            <div style="display:flex;gap:6px;flex-wrap:wrap">
              ${IGNORE_PRESETS.map(p => html`
                <button type="button" class="btn btn-ghost btn-sm" style="width:auto"
                  onClick=${() => addPreset(p.patterns)} disabled=${loading}>+ ${t(`game.ignorePreset.${p.key}`)}</button>`)}
            </div>
            <div class="form-hint">${t('game.ignoreHint')}</div>
          </div>

          <div class="form-group">
            <label class="form-label">${t('game.content')}</label>
            <button
              type="button"
              class="btn btn-ghost"
              style="align-self:flex-start"
              onClick=${() => setShowPublish(true)}
              disabled=${loading}
            >${t('game.publish')}</button>
            <div style="font-size:11px;color:var(--muted);margin-top:2px;line-height:1.45">
              ${t('game.publishNote')}
            </div>
          </div>

          ${error && html`<p style="color:var(--danger);font-size:13px">${error}</p>`}

          <div class="dialog-actions">
            <button type="button" class="btn btn-ghost" onClick=${onClose}>${t('common.cancel')}</button>
            <button type="submit" class="btn btn-primary" disabled=${!name.trim() || loading}>
              ${loading ? html`<span class="spinner"></span>` : t('common.save')}
            </button>
          </div>
        </form>
      </div>
      ${showPublish && html`
        <${PublishUpdate}
          game=${game}
          onClose=${() => setShowPublish(false)}
          onPublished=${() => setShowPublish(false)}
        />
      `}
    </div>`;
}
