import { html } from 'htm/preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import { api } from '../api.js';

export function EditGame({ game, onClose, onSaved }) {
  const [name, setName]         = useState(game.name || '');
  const [platform, setPlatform] = useState(game.platform || 'any');
  const [appId, setAppId]       = useState(game.steam_app_id ? String(game.steam_app_id) : '');
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState('');
  const firstRef = useRef(null);

  useEffect(() => { firstRef.current?.focus(); }, []);

  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setLoading(true);
    setError('');
    try {
      const body = { name: name.trim(), platform };
      if (appId) body.steam_app_id = parseInt(appId, 10);
      const updated = await api.patchGame(game.id, body);
      onSaved(updated);
    } catch (err) {
      setError(err.body || 'Unbekannter Fehler.');
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
              type="number"
              placeholder="413150"
              value=${appId}
              onInput=${e => setAppId(e.target.value)}
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
