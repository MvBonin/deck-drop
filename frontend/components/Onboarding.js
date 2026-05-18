import { html } from 'htm/preact';
import { useState } from 'preact/hooks';
import { api } from '../api.js';

export function Onboarding({ onDone }) {
  const [name, setName]       = useState('');
  const [agreed, setAgreed]   = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState('');

  const canSubmit = name.trim().length >= 2 && agreed && !loading;

  const submit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;
    setLoading(true);
    setError('');
    try {
      await api.saveSettings({ user_name: name.trim(), onboarding_complete: true });
      onDone(name.trim());
    } catch {
      setError('Fehler beim Speichern. Bitte erneut versuchen.');
      setLoading(false);
    }
  };

  const handleKey = (e) => {
    if (e.key === 'Enter') submit(e);
  };

  return html`
    <div class="onboarding">
      <div class="onboarding-card">
        <div>
          <div class="onboarding-logo">DeckDrop</div>
          <div class="onboarding-sub">LAN-Spieleübertragung für Steam Deck</div>
        </div>

        <form onSubmit=${submit} style="display:flex;flex-direction:column;gap:16px">
          <div class="form-group">
            <label class="form-label" for="ob-name">Dein Name im Netzwerk</label>
            <input
              id="ob-name"
              class="form-input"
              type="text"
              placeholder="z.B. SteamDeck-Lars"
              value=${name}
              onInput=${e => setName(e.target.value)}
              onKeyDown=${handleKey}
              maxLength=${40}
              autofocus
              required
            />
          </div>

          <label class="form-checkbox">
            <input
              type="checkbox"
              checked=${agreed}
              onChange=${e => setAgreed(e.target.checked)}
            />
            <span style="font-size:13px;color:var(--text-dim)">
              Ich teile nur Spiele, bei denen mir das gestattet ist,
              und bestätige, dass alle Kopien bei mir liegen.
            </span>
          </label>

          ${error && html`<p style="color:var(--danger);font-size:13px">${error}</p>`}

          <button class="btn btn-primary" type="submit" disabled=${!canSubmit}>
            ${loading ? html`<span class="spinner"></span>` : 'Loslegen →'}
          </button>
        </form>
      </div>
    </div>`;
}
