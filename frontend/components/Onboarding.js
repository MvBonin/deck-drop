import { html } from 'htm/preact';
import { useState } from 'preact/hooks';
import { api } from '../api.js';
import { t, LANGUAGES, getLanguage, setLanguage } from '../i18n/index.js';

export function Onboarding({ onDone }) {
  const [lang, setLang]       = useState(getLanguage());
  const [name, setName]       = useState('');
  const [agreed, setAgreed]   = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState('');

  const canSubmit = name.trim().length >= 2 && agreed && !loading;

  // Switches the onboarding itself right away; saved together with the name.
  const pickLanguage = (code) => {
    setLanguage(code);
    setLang(code);
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;
    setLoading(true);
    setError('');
    try {
      await api.saveSettings({ user_name: name.trim(), language: lang, onboarding_complete: true });
      onDone(name.trim(), lang);
    } catch {
      setError(t('onboarding.saveError'));
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
          <div class="onboarding-sub">${t('onboarding.tagline')}</div>
        </div>

        <div class="form-group">
          <span class="form-label" id="ob-lang">Language / Sprache</span>
          <div class="lang-picker" role="radiogroup" aria-labelledby="ob-lang">
            ${LANGUAGES.map(l => html`
              <button
                key=${l.code}
                type="button"
                role="radio"
                aria-checked=${lang === l.code}
                class=${'btn ' + (lang === l.code ? 'btn-primary' : 'btn-ghost')}
                onClick=${() => pickLanguage(l.code)}
              >${l.label}</button>
            `)}
          </div>
        </div>

        <div style="background:rgba(99,179,237,0.08);border-left:3px solid var(--accent);border-radius:6px;padding:12px 14px;font-size:13px;color:var(--text-dim)"
          dangerouslySetInnerHTML=${{ __html: t('onboarding.lanOnly') }}>
        </div>

        <form onSubmit=${submit} style="display:flex;flex-direction:column;gap:16px">
          <div class="form-group">
            <label class="form-label" for="ob-name">${t('onboarding.nameLabel')}</label>
            <input
              id="ob-name"
              class="form-input"
              type="text"
              placeholder=${t('onboarding.namePlaceholder')}
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
              ${t('onboarding.agree')}
            </span>
          </label>

          ${error && html`<p style="color:var(--danger);font-size:13px">${error}</p>`}

          <button class="btn btn-primary" type="submit" disabled=${!canSubmit}>
            ${loading ? html`<span class="spinner"></span>` : t('onboarding.start')}
          </button>
        </form>
      </div>
    </div>`;
}
