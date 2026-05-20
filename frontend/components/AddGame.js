import { html } from 'htm/preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import { api } from '../api.js';
import { formatApiError } from '../errors.js';

export function AddGame({ onClose, onAdded }) {
  const [path, setPath]           = useState('');
  const [name, setName]           = useState('');
  const [platform, setPlatform]   = useState('any');
  const [appId, setAppId]         = useState('');
  const [needsWizard, setNeeds]   = useState(false);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState('');
  const firstRef = useRef(null);

  // Focus trap: focus first element on open
  useEffect(() => { firstRef.current?.focus(); }, []);

  // Close on Escape
  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

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
        // Kein deckdrop.toml → Wizard anzeigen
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
    <div class="overlay" onClick=${e => e.target === e.currentTarget && onClose()} role="dialog" aria-modal="true" aria-label="Spiel hinzufügen">
      <div class="dialog">
        <div class="dialog-title">${needsWizard ? 'Spiel einrichten' : 'Spiel hinzufügen'}</div>

        <form onSubmit=${submit} style="display:flex;flex-direction:column;gap:14px">
          <div class="form-group">
            <label class="form-label">Spielordner-Pfad</label>
            <input
              ref=${firstRef}
              class="form-input"
              type="text"
              placeholder="/home/user/Games/MeinSpiel"
              value=${path}
              onInput=${e => { setPath(e.target.value); setNeeds(false); setError(''); }}
              disabled=${loading}
            />
          </div>

          ${needsWizard && html`
            <p style="font-size:13px;color:var(--text-dim)">
              Kein <code>deckdrop.toml</code> gefunden. Bitte ergänze die Spielinfos:
            </p>
            <div class="form-group">
              <label class="form-label">Spielname *</label>
              <input
                class="form-input"
                type="text"
                placeholder="Stardew Valley"
                value=${name}
                onInput=${e => setName(e.target.value)}
                required
              />
            </div>
            <div class="form-group">
              <label class="form-label">Plattform</label>
              <select class="form-select" value=${platform} onChange=${e => setPlatform(e.target.value)}>
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
              />
            </div>
          `}

          ${error && html`<p style="color:var(--danger);font-size:13px">${error}</p>`}

          <div class="dialog-actions">
            <button type="button" class="btn btn-ghost" onClick=${onClose}>Abbrechen</button>
            <button type="submit" class="btn btn-primary" disabled=${!canSubmit}>
              ${loading ? html`<span class="spinner"></span>` : (needsWizard ? 'Einrichten' : 'Hinzufügen')}
            </button>
          </div>
        </form>
      </div>
    </div>`;
}
