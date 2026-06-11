import { h, render } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import { html } from 'htm/preact';
import { api } from './api.js';
import { Onboarding } from './components/Onboarding.js';
import { Nav }        from './components/Nav.js';
import { MyGames }    from './components/MyGames.js';
import { Network }    from './components/Network.js';
import { Downloads }  from './components/Downloads.js';
import { Settings }   from './components/Settings.js';

// ── Grid navigation hook ──────────────────────────────────────────
// Arrow keys move focus between [data-card] elements inside ref.current.
export function useGridNav(ref) {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const handler = (e) => {
      if (!['ArrowRight','ArrowLeft','ArrowDown','ArrowUp'].includes(e.key)) return;
      const items = [...el.querySelectorAll('[data-card]')];
      const idx = items.indexOf(document.activeElement);
      if (idx === -1) return;
      const cols = Math.max(1, Math.round(el.offsetWidth / 220));
      const delta = { ArrowRight:1, ArrowLeft:-1, ArrowDown:cols, ArrowUp:-cols }[e.key];
      const next = items[Math.max(0, Math.min(items.length - 1, idx + delta))];
      if (next) { e.preventDefault(); next.focus(); }
    };
    el.addEventListener('keydown', handler);
    return () => el.removeEventListener('keydown', handler);
  }, []);
}

// ── Toast ─────────────────────────────────────────────────────────
function Toast({ message }) {
  return message ? html`<div class="toast" role="status">${message}</div>` : null;
}

// ── App ───────────────────────────────────────────────────────────
function App() {
  const [status, setStatus]       = useState(null);
  const [view, setView]           = useState('games');
  const [wsEvent, setWsEvent]     = useState(null);
  const [downloads, setDownloads] = useState([]);
  const [toast, setToast]         = useState('');
  const toastTimer = useRef(null);

  const ACTIVE_DL = new Set(['queued', 'downloading', 'verifying', 'paused', 'error', 'done']);

  const mergeDownload = useCallback((dl) => {
    if (!dl?.id || !ACTIVE_DL.has(dl.status)) return;
    setDownloads(prev => {
      const exists = prev.find(d => d.id === dl.id);
      if (exists) return prev.map(d => d.id === dl.id ? { ...d, ...dl } : d);
      return [...prev, dl];
    });
  }, []);

  // -- Initial status fetch --
  useEffect(() => {
    api.status().then(setStatus).catch(() => setStatus({ onboarding_complete: false, name: '' }));
  }, []);

  // -- WebSocket --
  useEffect(() => {
    let ws, retryTimer;
    function connect() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        setWsEvent(msg);
        if (msg.event === 'download_progress') {
          mergeDownload(msg.data);
        }
        if (msg.event === 'download_complete') {
          setDownloads(prev => prev.map(d => d.id === msg.data.id ? { ...d, status: 'done' } : d));
          setTimeout(() => {
            setDownloads(prev => prev.filter(d => d.id !== msg.data.id));
          }, 3000);
        }
      };
      ws.onclose = () => { retryTimer = setTimeout(connect, 3000); };
    }
    connect();
    return () => { ws?.close(); clearTimeout(retryTimer); };
  }, [mergeDownload]);

  const showToast = useCallback((msg) => {
    setToast(msg);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(''), 2200);
  }, []);

  const onboardingDone = (name) => {
    setStatus(s => ({ ...s, onboarding_complete: true, name }));
  };

  const activeDlCount = downloads.filter(d =>
    d.status === 'downloading' || d.status === 'queued' || d.status === 'verifying'
  ).length;

  if (!status) {
    return html`<div class="empty-state" style="height:100vh"><div class="spinner"></div></div>`;
  }

  if (!status.onboarding_complete) {
    return html`<${Onboarding} onDone=${onboardingDone} />`;
  }

  const views = {
    games:     html`<${MyGames}    wsEvent=${wsEvent} showToast=${showToast} />`,
    network:   html`<${Network}    wsEvent=${wsEvent} showToast=${showToast} onNavigate=${setView} onDownloadStarted=${mergeDownload} />`,
    downloads: html`<${Downloads}  wsEvent=${wsEvent} showToast=${showToast} downloads=${downloads} setDownloads=${setDownloads} />`,
    settings:  html`<${Settings}   showToast=${showToast} />`,
  };

  return html`
    <div id="app-inner">
      ${views[view]}
      <${Nav} view=${view} onNav=${setView} dlCount=${activeDlCount} />
      <${Toast} message=${toast} />
    </div>`;
}

render(html`<${App} />`, document.getElementById('app'));
