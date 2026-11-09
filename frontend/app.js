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
import { Debug }      from './components/Debug.js';
import { isDebug, onDebugChange } from './debug.js';
import { t, setLanguage, onLanguageChange } from './i18n/index.js';
import { installSpatialNav } from './spatial.js';

// D-pad / arrow-key focus movement for every view (see spatial.js).
installSpatialNav();

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
  const [debug, setDebugState]    = useState(isDebug());
  const [lang, setLang]           = useState(null);
  const toastTimer = useRef(null);
  const showToastRef = useRef(null);

  // Settings badge: newer DeckDrop on a LAN peer (checked locally, no
  // internet) or found by the last manual GitHub check.
  const [updateAvailable, setUpdateAvailable] = useState(false);
  useEffect(() => {
    const poll = () => api.selfUpdate()
      .then(s => setUpdateAvailable(Boolean((s.lan || []).length || s.github?.newer
        || s.install?.state === 'ready')))
      .catch(() => {});
    poll();
    const t = setInterval(poll, 60_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => onLanguageChange(setLang), []);

  useEffect(() => onDebugChange((on) => {
    setDebugState(on);
    if (!on) setView(v => (v === 'debug' ? 'settings' : v));
  }), []);

  const ACTIVE_DL = new Set(['queued', 'downloading', 'verifying', 'paused', 'error', 'done']);

  const isRealDownloadComplete = (data) => {
    const total = data?.total_bytes ?? 0;
    const downloaded = data?.downloaded_bytes ?? 0;
    return total > 0 && downloaded >= total;
  };

  const mergeDownload = useCallback((dl) => {
    if (!dl?.id || !ACTIVE_DL.has(dl.status)) return;
    setDownloads(prev => {
      const exists = prev.find(d => d.id === dl.id);
      if (exists) return prev.map(d => d.id === dl.id ? { ...d, ...dl } : d);
      return [...prev, dl];
    });
  }, []);

  // Known downloads from the start – the network page greys out "Laden"
  // for games already downloading, even before the downloads page was opened.
  useEffect(() => {
    api.downloads()
      .then(list => setDownloads(list.filter(d => ACTIVE_DL.has(d.status))))
      .catch(() => {});
  }, []);

  // -- Initial status fetch --
  useEffect(() => {
    api.status()
      .then((s) => { setLanguage(s.language); setStatus(s); })
      .catch(() => setStatus({ onboarding_complete: false, name: '' }));
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
        if (msg.event === 'download_start_failed') {
          // The host never answered (or refused) – drop the placeholder row.
          setDownloads(prev => prev.filter(d => d.id !== msg.data.id));
          showToastRef.current?.(t('dl.startFailed', {
            name: msg.data.game_name,
            error: msg.data.error || t('errors.hostUnreachable'),
          }));
        }
        if (msg.event === 'download_complete' && isRealDownloadComplete(msg.data)) {
          setDownloads(prev => {
            const dl = prev.find(d => d.id === msg.data.id);
            if (!dl) return prev;
            return prev.map(d => d.id === msg.data.id
              ? { ...d, ...msg.data, status: 'done' }
              : d);
          });
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
  showToastRef.current = showToast;

  const onboardingDone = (name, language) => {
    setStatus(s => ({ ...s, onboarding_complete: true, name, language }));
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
    network:   html`<${Network}    wsEvent=${wsEvent} showToast=${showToast} onNavigate=${setView} onDownloadStarted=${mergeDownload} downloads=${downloads} />`,
    downloads: html`<${Downloads}  wsEvent=${wsEvent} showToast=${showToast} downloads=${downloads} setDownloads=${setDownloads} />`,
    settings:  html`<${Settings}   showToast=${showToast} />`,
    debug:     debug ? html`<${Debug} wsEvent=${wsEvent} />` : null,
  };

  return html`
    <div id="app-inner" key=${lang}>
      ${debug && html`<div class="debug-badge" title=${t('app.debugBadge')}>DEBUG</div>`}
      ${views[view] ?? views.games}
      <${Nav} view=${view} onNav=${setView} dlCount=${activeDlCount} debug=${debug} updateAvailable=${updateAvailable} />
      <${Toast} message=${toast} />
    </div>`;
}

render(html`<${App} />`, document.getElementById('app'));
