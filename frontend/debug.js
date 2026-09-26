// Debug mode, like Odoo's `?debug=1`: `?debug=1` turns it on, `?debug=0`
// turns it off, the choice is remembered per browser (localStorage). The
// Settings toggle does the same for devices without a URL bar (Steam Deck).

const KEY = 'deckdrop.debug';
const EVENT = 'deckdrop-debug';

function readStored() {
  try { return localStorage.getItem(KEY) === '1'; } catch { return false; }
}

function store(on) {
  try {
    if (on) localStorage.setItem(KEY, '1');
    else localStorage.removeItem(KEY);
  } catch {}
}

let enabled = (() => {
  const param = new URLSearchParams(location.search).get('debug');
  if (param !== null) {
    const on = param !== '0' && param !== 'false' && param !== '';
    store(on);
    return on;
  }
  return readStored();
})();

export function isDebug() {
  return enabled;
}

export function setDebug(on) {
  enabled = !!on;
  store(enabled);
  // Keep the URL in sync so a reload/bookmark shows the same mode.
  try {
    const url = new URL(location.href);
    if (enabled) url.searchParams.set('debug', '1');
    else url.searchParams.delete('debug');
    history.replaceState(null, '', url);
  } catch {}
  window.dispatchEvent(new CustomEvent(EVENT, { detail: enabled }));
}

export function onDebugChange(fn) {
  const handler = (e) => fn(e.detail);
  window.addEventListener(EVENT, handler);
  return () => window.removeEventListener(EVENT, handler);
}
