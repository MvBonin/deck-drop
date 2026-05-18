import { html } from 'htm/preact';

const TABS = [
  { id: 'games',     label: 'Meine Spiele', icon: Icons.games },
  { id: 'network',   label: 'Netzwerk',     icon: Icons.network },
  { id: 'downloads', label: 'Downloads',    icon: Icons.downloads },
  { id: 'settings',  label: 'Einstellungen',icon: Icons.settings },
];

function Icons() {}
Icons.games     = () => html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 3H8L6 7h12z"/></svg>`;
Icons.network   = () => html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2a10 10 0 0 1 0 20"/><path d="M12 2a10 10 0 0 0 0 20"/><line x1="2" y1="12" x2="22" y2="12"/></svg>`;
Icons.downloads = () => html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`;
Icons.settings  = () => html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`;

export function Nav({ view, onNav, dlCount }) {
  const handleKey = (e, id) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); focusNext(e.currentTarget, 1); }
    if (e.key === 'ArrowLeft')  { e.preventDefault(); focusNext(e.currentTarget, -1); }
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onNav(id); }
  };

  return html`
    <nav class="nav" role="tablist" aria-label="Hauptnavigation">
      ${TABS.map(tab => html`
        <button
          key=${tab.id}
          class=${'nav-tab' + (view === tab.id ? ' active' : '')}
          role="tab"
          aria-selected=${view === tab.id}
          tabIndex=${view === tab.id ? 0 : -1}
          onClick=${() => onNav(tab.id)}
          onKeyDown=${e => handleKey(e, tab.id)}
        >
          <${tab.icon} />
          ${tab.label}
          ${tab.id === 'downloads' && dlCount > 0
            ? html`<span class="nav-badge" aria-label="${dlCount} aktive Downloads">${dlCount}</span>`
            : null}
        </button>
      `)}
    </nav>`;
}

function focusNext(current, dir) {
  const tabs = [...current.closest('nav').querySelectorAll('.nav-tab')];
  const i = tabs.indexOf(current);
  const next = tabs[(i + dir + tabs.length) % tabs.length];
  next?.focus();
  next?.click();
}
