import { html } from 'htm/preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import { api } from '../api.js';
import { formatApiError } from '../errors.js';

export function EditGame({ game, onClose, onSaved, onGameUpdated }) {
  const [name, setName]             = useState(game.name || '');
  const [platform, setPlatform]     = useState(game.platform || 'any');
  const [appId, setAppId]           = useState(game.steam_app_id ? String(game.steam_app_id) : '');
  const [description, setDesc]      = useState(game.description || '');
  const [launchExe, setLaunchExe]   = useState(game.launch_exe || '');
  const [launchArgs, setLaunchArgs] = useState(game.launch_args || '');
  const [runner, setRunner]         = useState(game.runner || '');
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

  const searchCover = async () => {
    setSearching(true);
    setError('');
    try {
      const res = await api.searchCover(game.id);
      setAppId(String(res.steam_app_id));
      if (res.cover_downloaded) {
        const updated = await api.getGame(game.id);
        onGameUpdated?.(updated);
      } else {
        setError('App-ID gefunden, aber kein Steam-Cover verfügbar');
      }
    } catch (err) {
      setError('Keine Cover Art auf Steam gefunden');
    } finally {
      setSearching(false);
    }
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
    <div class="overlay" onClick=${e => e.target === e.currentTarget && onClose()} role="dialog" aria-modal="true" aria-label="Spiel bearbeiten">
      <div class="dialog">
        <div class="dialog-title">Spiel bearbeiten</div>

        <form onSubmit=${submit} style="display:flex;flex-direction:column;gap:14px">
          <div class="form-group">
            <label class="form-label">Spielname *</label>
            <input
              ref=${firstRef}
              class="form-input"
              type="text"
              placeholder="Spielname"
              value=${name}
              onInput=${e => setName(e.target.value)}
              disabled=${loading}
              required
            />
          </div>

          <div class="form-group">
            <label class="form-label">Plattform</label>
            <select class="form-select" value=${platform} onChange=${e => setPlatform(e.target.value)} disabled=${loading}>
              <option value="linux">Linux</option>
              <option value="windows">Windows</option>
              <option value="any">Beliebig</option>
            </select>
          </div>

          <div class="form-group">
            <label class="form-label">Steam App-ID (optional, für Cover)</label>
            <input
              class="form-input"
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              placeholder="413150"
              value=${appId}
              onInput=${e => setAppId(e.target.value.replace(/\D/g, ''))}
              disabled=${loading || searching}
            />
            <button
              type="button"
              class="btn btn-ghost"
              style="align-self:flex-start;padding:8px 14px;font-size:13px"
              onClick=${searchCover}
              disabled=${loading || searching}
              title="Spielname auf Steam durchsuchen und erste Treffer-App-ID übernehmen"
            >
              ${searching ? html`<span class="spinner"></span>` : '🔍 Auf Steam suchen'}
            </button>
            <div style="font-size:11px;color:var(--muted);margin-top:2px;line-height:1.45">
              „Suchen“ oder Speichern mit App-ID lädt das Cover von Steam als
              <code>deckdrop.jpg</code> in den Spielordner (ohne Torrent neu zu bauen).
            </div>
          </div>

          <div class="form-group">
            <label class="form-label">Beschreibung (optional)</label>
            <textarea
              class="form-input"
              rows="3"
              placeholder="Kurze Beschreibung des Spiels…"
              value=${description}
              onInput=${e => setDesc(e.target.value)}
              disabled=${loading}
              style="resize:vertical"
            />
          </div>

          <div class="form-group">
            <label class="form-label">Launch-Exe (optional, für Steam-Shortcut)</label>
            <input
              class="form-input"
              type="text"
              placeholder="/pfad/zum/spiel.exe"
              value=${launchExe}
              onInput=${e => setLaunchExe(e.target.value)}
              disabled=${loading}
            />
          </div>

          <div class="form-group">
            <label class="form-label">Launch-Argumente (optional)</label>
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
            <label class="form-label">Proton / Runner (optional)</label>
            <input
              class="form-input"
              type="text"
              placeholder="Proton 9.0"
              value=${runner}
              onInput=${e => setRunner(e.target.value)}
              disabled=${loading}
            />
          </div>

          ${error && html`<p style="color:var(--danger);font-size:13px">${error}</p>`}

          <div class="dialog-actions">
            <button type="button" class="btn btn-ghost" onClick=${onClose}>Abbrechen</button>
            <button type="submit" class="btn btn-primary" disabled=${!name.trim() || loading}>
              ${loading ? html`<span class="spinner"></span>` : 'Speichern'}
            </button>
          </div>
        </form>
      </div>
    </div>`;
}
