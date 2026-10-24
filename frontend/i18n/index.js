// UI translations. The language lives in the server config (GET /api/status →
// `language`); app.js calls setLanguage() once it is known and re-renders on
// onLanguageChange().
//
// Adding a language: create <code>.js next to en.js with the same keys
// (tests/test_i18n.py checks that), import it below and add it to LANGUAGES.
// The backend has its own catalog in deckdrop/core/i18n.py.
//
// Usage:
//   t('nav.games')                         → "My Games"
//   t('downloads.eta', { time: '3 min' })  → "{time} left" with {time} filled
//   t('network.games', { count: 2 })       → uses 'network.games_one' /
//                                            'network.games_other' if present

import en from './en.js';
import de from './de.js';

export const DEFAULT_LANGUAGE = 'en';

// `label` is shown in the language picker, always in its own language.
export const LANGUAGES = [
  { code: 'en', label: 'English', locale: 'en-US' },
  { code: 'de', label: 'Deutsch', locale: 'de-DE' },
];

const CATALOGS = { en, de };
const EVENT = 'deckdrop-language';

let current = DEFAULT_LANGUAGE;

export function getLanguage() {
  return current;
}

/** BCP 47 locale for toLocale*String() of the current language. */
export function getLocale() {
  return (LANGUAGES.find(l => l.code === current) || LANGUAGES[0]).locale;
}

export function setLanguage(lang) {
  const next = CATALOGS[lang] ? lang : DEFAULT_LANGUAGE;
  document.documentElement.lang = next;
  if (next === current) return;
  current = next;
  window.dispatchEvent(new CustomEvent(EVENT, { detail: next }));
}

export function onLanguageChange(cb) {
  const handler = (e) => cb(e.detail);
  window.addEventListener(EVENT, handler);
  return () => window.removeEventListener(EVENT, handler);
}

function lookup(key) {
  const cat = CATALOGS[current];
  if (cat && key in cat) return cat[key];
  if (key in CATALOGS[DEFAULT_LANGUAGE]) return CATALOGS[DEFAULT_LANGUAGE][key];
  return undefined;
}

export function t(key, params) {
  let text;
  if (params && typeof params.count === 'number') {
    text = lookup(`${key}_${params.count === 1 ? 'one' : 'other'}`);
  }
  if (text === undefined) text = lookup(key);
  if (text === undefined) return key;
  if (!params) return text;
  return text.replace(/\{(\w+)\}/g, (m, name) => (name in params ? String(params[name]) : m));
}
