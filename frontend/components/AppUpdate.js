import { html } from 'htm/preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import { api, fmtBytes } from '../api.js';
import { formatApiError } from '../errors.js';
import { t, getLocale } from '../i18n/index.js';

const INSTALL_HINT = {
  flatpak: () => t('appUpdate.hintFlatpak'),
  pipx: () => 'pipx: pipx install --force git+https://github.com/MvBonin/deck-drop@v',
  windows: () => t('appUpdate.hintWindows'),
};

function fmtDate(iso) {
  try { return iso ? new Date(iso).toLocaleDateString(getLocale()) : ''; } catch { return ''; }
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
 * Settings header with the installed version and "Nach Updates suchen" on the
 * right; offers/progress follow below. GitHub is only asked when the user
 * clicks (after a warning); newer versions on LAN peers are listed
 * automatically (LAN only). Every source is verified against the signed
 * release manifest.
 */
export function AppUpdate({ title, showToast }) {
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
    if (!confirm(t('appUpdate.githubWarning'))) return;
    setChecking(true);
    try {
      const s = await api.checkSelfUpdate();
      setSt(s);
      const gh = s.github || {};
      if (gh.ok === false) showToast(gh.error || t('appUpdate.checkFailed'));
      else if (!gh.newer) showToast(gh.message || t('appUpdate.upToDate'));
    } catch (err) {
      showToast(t('common.error', { error: formatApiError(err, 'settings') }));
    } finally {
      setChecking(false);
    }
  };

  const install = async (source) => {
    setBusy(true);
    try {
      setSt(await api.installSelfUpdate(source));
    } catch (err) {
      showToast(t('appUpdate.installFailed', { error: formatApiError(err, 'settings') }));
    } finally {
      setBusy(false);
    }
  };

  const restart = async (rollback = false) => {
    if (rollback && !confirm(t('appUpdate.rollbackConfirm'))) return;
    setRestarting(true);
    try {
      if (rollback) await api.rollbackSelfUpdate();
      else await api.restartSelfUpdate();
    } catch (err) {
      showToast(t('common.error', { error: formatApiError(err, 'settings') }));
      setRestarting(false);
      return;
    }
    waitForRestart(st?.current);
  };

  const header = (version) => html`
    <div class="view-header">
      <div class="view-title">${title}</div>
      ${version}
    </div>`;
  if (!st) return header(null);
  const gh = st.github;
  const inst = st.install || { state: 'idle' };
  const installing = ['downloading', 'verifying'].includes(inst.state);
  const pct = Math.round((inst.progress || 0) * 100);
  const lan = st.lan || [];
  const hasDetails = lan.length > 0 || (gh && gh.newer) || inst.state !== 'idle' || st.rollback_available;

  return html`
    ${header(html`
      <div class="app-version">
        <div>
          <div class="settings-row-label">DeckDrop ${st.current}</div>
          <div class="settings-row-sub">${t('appUpdate.searchAsksGithub')}</div>
        </div>
        <button type="button" class="btn btn-secondary" style="padding:6px 12px;font-size:13px"
          onClick=${check} disabled=${checking || installing}>
          ${checking ? html`<span class="spinner"></span>` : t('appUpdate.check')}
        </button>
      </div>
    `)}
    ${hasDetails && html`
    <p class="settings-section-title">${t('appUpdate.section')}</p>
    <div class="settings-section">

      ${lan.map(o => html`
        <div class="settings-row" key=${o.peer_id}>
          <div>
            <div class="settings-row-label">${t('appUpdate.lanFound', { version: o.version })}</div>
            <div class="settings-row-sub">${t('appUpdate.lanFrom', { peer: o.peer_name })}${o.size ? ` · ${fmtBytes(o.size)}` : ''} · ${t('appUpdate.signed')}</div>
          </div>
          ${st.can_self_update && html`
            <button type="button" class="btn btn-primary" style="padding:6px 12px;font-size:13px"
              onClick=${() => install(o.peer_id)} disabled=${busy || installing || inst.state === 'ready'}>
              ${t('appUpdate.updateFrom', { peer: o.peer_name })}
            </button>`}
        </div>
      `)}

      ${gh && gh.newer && html`
        <div class="settings-row" style="flex-wrap:wrap">
          <div style="flex:1;min-width:200px">
            <div class="settings-row-label">${t('appUpdate.available', { version: gh.latest })}</div>
            <div class="settings-row-sub">
              ${fmtDate(gh.published_at)}
              ${gh.verified ? ` · ${t('appUpdate.signatureChecked')}` : ''}
              ${gh.error ? html` · <span style="color:var(--danger)">${gh.error}</span>` : ''}
            </div>
            ${gh.notes && html`
              <button type="button" class="debug-link" onClick=${() => setShowNotes(v => !v)}>
                ${showNotes ? t('appUpdate.hideNotes') : t('appUpdate.showNotes')}
              </button>
              ${showNotes && html`<pre class="update-notes">${gh.notes}</pre>`}
            `}
            ${!st.can_self_update && html`
              <div class="settings-row-sub" style="margin-top:4px">
                ${INSTALL_HINT[st.install_type]?.() || st.self_update_hint}${st.install_type === 'pipx' ? gh.latest : ''}
              </div>`}
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            ${st.can_self_update && gh.verified && html`
              <button type="button" class="btn btn-primary" style="padding:6px 12px;font-size:13px"
                onClick=${() => install('github')} disabled=${busy || installing || inst.state === 'ready'}>
                ${t('appUpdate.updateNow')}
              </button>`}
            <a class="btn btn-ghost" style="padding:6px 12px;font-size:13px" href=${gh.html_url}
              target="_blank" rel="noopener noreferrer">${t('appUpdate.downloadPage')}</a>
          </div>
        </div>
      `}

      ${installing && html`
        <div class="settings-row" style="display:block">
          <div class="settings-row-sub" style="margin-bottom:6px">
            ${inst.state === 'verifying' ? t('appUpdate.verifying') : t('appUpdate.downloading', { version: inst.version, source: inst.source, pct })}
          </div>
          <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
        </div>
      `}
      ${inst.state === 'error' && html`
        <div class="settings-row"><div class="settings-row-sub" style="color:var(--danger)">${inst.error}</div></div>
      `}
      ${inst.state === 'ready' && html`
        <div class="settings-row">
          <div class="settings-row-label">${t('appUpdate.installed')}</div>
          <button type="button" class="btn btn-primary" style="padding:6px 12px;font-size:13px"
            onClick=${() => restart(false)} disabled=${restarting}>${t('appUpdate.restart')}</button>
        </div>
      `}
      ${st.rollback_available && inst.state !== 'ready' && html`
        <div class="settings-row">
          <div class="settings-row-sub">${t('appUpdate.previousAvailable')}</div>
          <button type="button" class="btn btn-ghost" style="padding:4px 10px;font-size:12px"
            onClick=${() => restart(true)} disabled=${restarting || installing}>${t('appUpdate.rollback')}</button>
        </div>
      `}
    </div>`}
    ${restarting && html`
      <div class="overlay" role="dialog" aria-modal="true" aria-label=${t('appUpdate.restart')}>
        <div class="dialog" style="text-align:center">
          <div class="dialog-title">${t('appUpdate.restarting')}</div>
          <div class="spinner" style="margin:16px auto"></div>
        </div>
      </div>
    `}`;
}
