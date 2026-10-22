import { html } from 'htm/preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import { api, fmtBytes } from '../api.js';
import { formatApiError } from '../errors.js';

const INSTALL_HINT = {
  flatpak: 'Flatpak: neue Version von der Download-Seite installieren.',
  pipx: 'pipx: pipx install --force git+https://github.com/MvBonin/deck-drop@v',
  windows: 'Windows: neue Version von der Download-Seite herunterladen.',
};

function fmtDate(iso) {
  try { return iso ? new Date(iso).toLocaleDateString('de-DE') : ''; } catch { return ''; }
}

/** Waits until the restarted server answers again, then reloads the page. */
async function waitForRestart(oldVersion) {
  const deadline = Date.now() + 120_000;
  let wentDown = false;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 1000));
    try {
      const s = await api.status();
      if (wentDown || s.version !== oldVersion) { location.reload(); return; }
    } catch {
      wentDown = true;
    }
  }
  location.reload();
}

/**
 * "DeckDrop-Version" in Settings: GitHub is only asked when the user clicks
 * "Nach Updates suchen"; newer versions on LAN peers are listed automatically
 * (LAN only). Every source is verified against the signed release manifest.
 */
export function AppUpdate({ showToast }) {
  const [st, setSt] = useState(null);
  const [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [showNotes, setShowNotes] = useState(false);
  const timer = useRef(null);

  const load = async () => {
    try { setSt(await api.selfUpdate()); } catch {}
  };
  useEffect(() => { load(); }, []);

  // Poll while downloading/verifying.
  useEffect(() => {
    const running = st && ['downloading', 'verifying'].includes(st.install?.state);
    clearInterval(timer.current);
    if (running) timer.current = setInterval(load, 800);
    return () => clearInterval(timer.current);
  }, [st?.install?.state]);

  const check = async () => {
    if (!confirm('Achtung: Dafür wird eine Anfrage an GitHub (api.github.com) gesendet. Fortfahren?')) return;
    setChecking(true);
    try {
      const s = await api.checkSelfUpdate();
      setSt(s);
      const gh = s.github || {};
      if (gh.ok === false) showToast(gh.error || 'Prüfen fehlgeschlagen');
      else if (!gh.newer) showToast(gh.message || 'DeckDrop ist aktuell');
    } catch (err) {
      showToast(`Fehler: ${formatApiError(err, 'settings')}`);
    } finally {
      setChecking(false);
    }
  };

  const install = async (source) => {
    setBusy(true);
    try {
      setSt(await api.installSelfUpdate(source));
    } catch (err) {
      showToast(`Update nicht möglich: ${formatApiError(err, 'settings')}`);
    } finally {
      setBusy(false);
    }
  };

  const restart = async (rollback = false) => {
    if (rollback && !confirm('Zur vorherigen DeckDrop-Version zurückkehren und neu starten?')) return;
    setRestarting(true);
    try {
      if (rollback) await api.rollbackSelfUpdate();
      else await api.restartSelfUpdate();
    } catch (err) {
      showToast(`Fehler: ${formatApiError(err, 'settings')}`);
      setRestarting(false);
      return;
    }
    waitForRestart(st?.current);
  };

  if (!st) return null;
  const gh = st.github;
  const inst = st.install || { state: 'idle' };
  const installing = ['downloading', 'verifying'].includes(inst.state);
  const pct = Math.round((inst.progress || 0) * 100);
  const lan = st.lan || [];

  return html`
    <p class="settings-section-title">DeckDrop-Version</p>
    <div class="settings-section">
      <div class="settings-row">
        <div>
          <div class="settings-row-label">Installiert: ${st.current}</div>
          <div class="settings-row-sub">Achtung: Die Suche fragt GitHub an – nur auf Knopfdruck</div>
        </div>
        <button type="button" class="btn btn-secondary" style="padding:6px 12px;font-size:13px"
          onClick=${check} disabled=${checking || installing}>
          ${checking ? html`<span class="spinner"></span>` : 'Nach Updates suchen'}
        </button>
      </div>

      ${lan.map(o => html`
        <div class="settings-row" key=${o.peer_id}>
          <div>
            <div class="settings-row-label">Neuere Version ${o.version} im Netzwerk gefunden</div>
            <div class="settings-row-sub">bei ${o.peer_name}${o.size ? ` · ${fmtBytes(o.size)}` : ''} · signiert</div>
          </div>
          ${st.can_self_update && html`
            <button type="button" class="btn btn-primary" style="padding:6px 12px;font-size:13px"
              onClick=${() => install(o.peer_id)} disabled=${busy || installing || inst.state === 'ready'}>
              Von ${o.peer_name} aktualisieren
            </button>`}
        </div>
      `)}

      ${gh && gh.newer && html`
        <div class="settings-row" style="flex-wrap:wrap">
          <div style="flex:1;min-width:200px">
            <div class="settings-row-label">Version ${gh.latest} verfügbar</div>
            <div class="settings-row-sub">
              ${fmtDate(gh.published_at)}
              ${gh.verified ? ' · Signatur geprüft' : ''}
              ${gh.error ? html` · <span style="color:var(--danger)">${gh.error}</span>` : ''}
            </div>
            ${gh.notes && html`
              <button type="button" class="debug-link" onClick=${() => setShowNotes(v => !v)}>
                ${showNotes ? 'Änderungen ausblenden' : 'Änderungen anzeigen'}
              </button>
              ${showNotes && html`<pre class="update-notes">${gh.notes}</pre>`}
            `}
            ${!st.can_self_update && html`
              <div class="settings-row-sub" style="margin-top:4px">
                ${INSTALL_HINT[st.install_type] || st.self_update_hint}${st.install_type === 'pipx' ? gh.latest : ''}
              </div>`}
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            ${st.can_self_update && gh.verified && html`
              <button type="button" class="btn btn-primary" style="padding:6px 12px;font-size:13px"
                onClick=${() => install('github')} disabled=${busy || installing || inst.state === 'ready'}>
                Jetzt aktualisieren
              </button>`}
            <a class="btn btn-ghost" style="padding:6px 12px;font-size:13px" href=${gh.html_url}
              target="_blank" rel="noopener noreferrer">Download-Seite</a>
          </div>
        </div>
      `}

      ${installing && html`
        <div class="settings-row" style="display:block">
          <div class="settings-row-sub" style="margin-bottom:6px">
            ${inst.state === 'verifying' ? 'Prüfe Signatur und Prüfsumme…' : `Lade ${inst.version} von ${inst.source}… ${pct} %`}
          </div>
          <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
        </div>
      `}
      ${inst.state === 'error' && html`
        <div class="settings-row"><div class="settings-row-sub" style="color:var(--danger)">${inst.error}</div></div>
      `}
      ${inst.state === 'ready' && html`
        <div class="settings-row">
          <div class="settings-row-label">Update installiert – Neustart nötig</div>
          <button type="button" class="btn btn-primary" style="padding:6px 12px;font-size:13px"
            onClick=${() => restart(false)} disabled=${restarting}>Neu starten</button>
        </div>
      `}
      ${st.rollback_available && inst.state !== 'ready' && html`
        <div class="settings-row">
          <div class="settings-row-sub">Vorherige Version ist noch vorhanden</div>
          <button type="button" class="btn btn-ghost" style="padding:4px 10px;font-size:12px"
            onClick=${() => restart(true)} disabled=${restarting || installing}>Vorherige Version wiederherstellen</button>
        </div>
      `}
    </div>
    ${restarting && html`
      <div class="overlay" role="dialog" aria-modal="true" aria-label="Neustart">
        <div class="dialog" style="text-align:center">
          <div class="dialog-title">DeckDrop startet neu…</div>
          <div class="spinner" style="margin:16px auto"></div>
        </div>
      </div>
    `}`;
}
