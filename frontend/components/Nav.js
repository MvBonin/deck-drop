import { html } from 'htm/preact';
import { t } from '../i18n/index.js';
import { Icon } from '../icons.js';

const _icon = (name) => () => html`<${Icon} name=${name} size=${24} />`;
const _gameIcon     = _icon('gamepad-2');
const _networkIcon  = _icon('radio-tower');
const _downloadIcon = _icon('download');
const _settingsIcon = _icon('settings');
const _debugIcon    = _icon('bug');

const DEBUG_TAB = { id: 'debug', labelKey: 'nav.debug', icon: _debugIcon };

const TABS = [
  { id: 'games',     labelKey: 'nav.games',     icon: _gameIcon },
  { id: 'network',   labelKey: 'nav.network',   icon: _networkIcon },
  { id: 'downloads', labelKey: 'nav.downloads', icon: _downloadIcon },
  { id: 'settings',  labelKey: 'nav.settings',  icon: _settingsIcon },
];

export function Nav({ view, onNav, dlCount, debug, updateAvailable }) {
  const tabs = debug ? [...TABS, DEBUG_TAB] : TABS;
  const handleKey = (e, id) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); focusNext(e.currentTarget, 1); }
    if (e.key === 'ArrowLeft')  { e.preventDefault(); focusNext(e.currentTarget, -1); }
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onNav(id); }
  };

  return html`
    <nav class="nav" role="tablist" aria-label=${t('nav.main')}>
      ${tabs.map(tab => html`
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
          ${t(tab.labelKey)}
          ${tab.id === 'settings' && updateAvailable
            ? html`<span class="nav-badge" aria-label=${t('nav.updateAvailable')}><${Icon} name="arrow-up" size=${14} /></span>`
            : null}
          ${tab.id === 'downloads' && dlCount > 0
            ? html`<span class="nav-badge" aria-label=${t('nav.activeDownloads', { count: dlCount })}>${dlCount}</span>`
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
