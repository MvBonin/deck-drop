import { html } from 'htm/preact';
import { useState, useEffect } from 'preact/hooks';
import { api, fmtBytes } from '../api.js';
import { formatApiError } from '../errors.js';
import { isDebug, setDebug } from '../debug.js';
import { AppUpdate } from './AppUpdate.js';
import { t, LANGUAGES, getLanguage, setLanguage } from '../i18n/index.js';

export function Settings({ showToast }) {
  const [cfg, setCfg]         = useState(null);
  const [saving, setSaving]   = useState(false);
  const [showExit, setShowExit] = useState(false);
  const [exiting, setExiting]   = useState(false);
  const [svc, setSvc]           = useState(null);
  const [svcLoading, setSvcLoading] = useState(false);
  const [debugOn, setDebugOn]   = useState(isDebug());

  const toggleDebug = (on) => {
    setDebug(on);
    setDebugOn(on);
    showToast(on ? t('settings.debugOn') : t('settings.debugOff'));
  };

  const load = async () => {
    try {
      const [settings, service] = await Promise.all([api.settings(), api.serviceStatus().catch(() => null)]);
      setCfg(settings);
      setSvc(service);
    } catch {}
  };
  useEffect(() => { load(); }, []);

  const update = (key, val) => setCfg(c => ({ ...c, [key]: val }));

  const confirmExit = async () => {
    setExiting(true);
    try {
      await api.shutdown();
      showToast(t('settings.quitting'));
    } catch (err) {
      showToast(t('settings.quitFailed', { error: formatApiError(err, 'settings') }));
      setExiting(false);
      setShowExit(false);
    }
  };

  const enableService = async () => {
    setSvcLoading(true);
    try {
      const result = await api.enableService();
      setSvc(result);
      showToast(t('settings.serviceInstalled'));
    } catch (err) {
      showToast(t('common.error', { error: formatApiError(err, 'service') }));
    } finally {
      setSvcLoading(false);
    }
  };

  const disableService = async () => {
    setSvcLoading(true);
    try {
      const result = await api.disableService();
      setSvc(result);
      showToast(t('settings.serviceDisabled'));
    } catch (err) {
      showToast(t('common.error', { error: formatApiError(err, 'service') }));
    } finally {
      setSvcLoading(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      await api.saveSettings({
        user_name:         cfg.user_name,
        download_dir:      cfg.download_dir,
        max_upload_speed:  cfg.max_upload_speed,
        max_download_speed:cfg.max_download_speed,
      });
      showToast(t('settings.saved'));
    } catch (err) {
      showToast(t('settings.saveFailed', { error: formatApiError(err, 'settings') }));
    } finally {
      setSaving(false);
    }
  };

  // Applied right away (not with "Save"): the whole UI re-renders in the new language.
  const changeLanguage = async (lang) => {
    try {
      await api.saveSettings({ language: lang });
      setLanguage(lang);
      showToast(t('settings.languageSaved'));
    } catch (err) {
      showToast(t('settings.saveFailed', { error: formatApiError(err, 'settings') }));
    }
  };

  if (!cfg) return html`<div class="empty-state"><div class="spinner"></div></div>`;

  return html`
    <div class="view">
      <${AppUpdate} title=${t('settings.title')} showToast=${showToast} />

      <p class="settings-section-title">${t('settings.profile')}</p>
      <div class="settings-section">
        <div class="settings-row">
          <div>
            <div class="settings-row-label">${t('settings.language')}${getLanguage() !== 'en' ? ' / Language' : ''}</div>
            <div class="settings-row-sub">${t('settings.languageSub')}</div>
          </div>
          <select
            class="form-input"
            style="max-width:200px"
            aria-label=${t('settings.language')}
            value=${getLanguage()}
            onChange=${e => changeLanguage(e.target.value)}
          >
            ${LANGUAGES.map(l => html`<option key=${l.code} value=${l.code}>${l.label}</option>`)}
          </select>
        </div>
        <div class="settings-row">
          <div>
            <div class="settings-row-label">${t('settings.name')}</div>
            <div class="settings-row-sub">${t('settings.nameSub')}</div>
          </div>
          <input
            class="form-input"
            style="max-width:200px"
            value=${cfg.user_name}
            onInput=${e => update('user_name', e.target.value)}
          />
        </div>
        <div class="settings-row">
          <div>
            <div class="settings-row-label">${t('settings.peerId')}</div>
            <div class="settings-row-sub">${t('settings.peerIdSub')}</div>
          </div>
          <code style="font-size:11px;color:var(--text-dim)">${cfg.peer_id.slice(0,16)}…</code>
        </div>
      </div>

      <p class="settings-section-title">${t('settings.files')}</p>
      <div class="settings-section">
        <div class="settings-row">
          <div>
            <div class="settings-row-label">${t('settings.downloadDir')}</div>
            <div class="settings-row-sub">${t('settings.downloadDirSub')}</div>
          </div>
          <input
            class="form-input"
            style="max-width:240px;font-size:12px"
            value=${cfg.download_dir}
            onInput=${e => update('download_dir', e.target.value)}
          />
        </div>
      </div>

      <p class="settings-section-title">${t('settings.transfer')}</p>
      <div class="settings-section">
        <div class="settings-row">
          <div>
            <div class="settings-row-label">${t('settings.uploadLimit')}</div>
            <div class="settings-row-sub">${t('settings.limitSub')}</div>
          </div>
          <input
            class="form-input"
            style="max-width:100px"
            type="number"
            min="0"
            value=${cfg.max_upload_speed}
            onInput=${e => update('max_upload_speed', parseInt(e.target.value)||0)}
          />
        </div>
        <div class="settings-row">
          <div>
            <div class="settings-row-label">${t('settings.downloadLimit')}</div>
            <div class="settings-row-sub">${t('settings.limitSub')}</div>
          </div>
          <input
            class="form-input"
            style="max-width:100px"
            type="number"
            min="0"
            value=${cfg.max_download_speed}
            onInput=${e => update('max_download_speed', parseInt(e.target.value)||0)}
          />
        </div>
      </div>

      <p class="settings-section-title">${t('settings.network')}</p>
      <div class="settings-section">
        <div class="settings-row">
          <div class="settings-row-label">${t('settings.apiPort')}</div>
          <code style="color:var(--text-dim)">${cfg.port}</code>
        </div>
        <div class="settings-row">
          <div class="settings-row-label">${t('settings.sharingPort')}</div>
          <code style="color:var(--text-dim)">${cfg.torrent_port}</code>
        </div>
      </div>

      <p style="font-size:12px;color:var(--text-dim);padding:4px 20px 12px;line-height:1.6;margin:0">
        ${t('settings.lanNote', { port: cfg.port, torrentPort: cfg.torrent_port })}
      </p>

      <p class="settings-section-title">${t('settings.application')}</p>
      ${svc && html`
        <div class="settings-section">
          <div class="settings-row">
            <div>
              <div class="settings-row-label">${t('settings.service')}</div>
              <div class="settings-row-sub">${t('settings.serviceSub')}</div>
            </div>
            <div style="display:flex;align-items:center;gap:10px;flex-shrink:0">
              <span style="font-size:12px;color:${svc.active ? 'var(--success)' : 'var(--text-dim)'}">
                ${svc.active ? t('settings.active') : t('settings.inactive')}
              </span>
              ${svc.enabled
                ? html`<button
                    type="button"
                    class="btn btn-ghost"
                    style="padding:4px 12px;font-size:12px"
                    onClick=${disableService}
                    disabled=${svcLoading}
                  >${svcLoading ? html`<span class="spinner"></span>` : t('settings.disable')}</button>`
                : html`<button
                    type="button"
                    class="btn btn-primary"
                    style="padding:4px 12px;font-size:12px"
                    onClick=${enableService}
                    disabled=${svcLoading}
                  >${svcLoading ? html`<span class="spinner"></span>` : t('settings.installService')}</button>`
              }
            </div>
          </div>
        </div>
      `}
      <div class="settings-section">
        <div class="settings-row">
          <div>
            <div class="settings-row-label">${t('settings.debugMode')}</div>
            <div class="settings-row-sub" dangerouslySetInnerHTML=${{ __html: t('settings.debugModeSub') }}></div>
          </div>
          <label class="toggle">
            <input
              type="checkbox"
              checked=${debugOn}
              onChange=${e => toggleDebug(e.target.checked)}
              aria-label=${t('settings.debugMode')}
            />
            <span class="toggle-track"></span>
          </label>
        </div>
      </div>
      <div class="settings-section">
        <button
          type="button"
          class="btn btn-danger"
          style="width:100%"
          onClick=${() => setShowExit(true)}
        >
          ${t('settings.quit')}
        </button>
      </div>

      <div style="padding:16px 20px">
        <button class="btn btn-primary" onClick=${save} disabled=${saving}>
          ${saving ? html`<span class="spinner"></span>` : t('common.save')}
        </button>
      </div>

      ${showExit && html`
        <div
          class="overlay"
          onClick=${e => e.target === e.currentTarget && !exiting && setShowExit(false)}
          role="dialog"
          aria-modal="true"
          aria-label=${t('settings.quit')}
        >
          <div class="dialog">
            <div class="dialog-title">${t('settings.quitConfirm')}</div>
            <p class="dialog-text">
              ${t('settings.quitText')}
            </p>
            <div class="dialog-actions">
              <button
                type="button"
                class="btn btn-ghost"
                onClick=${() => setShowExit(false)}
                disabled=${exiting}
              >
                ${t('common.cancel')}
              </button>
              <button
                type="button"
                class="btn btn-danger"
                onClick=${confirmExit}
                disabled=${exiting}
              >
                ${exiting ? html`<span class="spinner"></span>` : t('settings.quitButton')}
              </button>
            </div>
          </div>
        </div>
      `}
    </div>`;
}
