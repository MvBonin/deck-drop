import { html } from 'htm/preact';
import { useState, useEffect } from 'preact/hooks';
import { api, fmtBytes } from '../api.js';
import { formatApiError } from '../errors.js';

export function Settings({ showToast }) {
  const [cfg, setCfg]         = useState(null);
  const [saving, setSaving]   = useState(false);
  const [showExit, setShowExit] = useState(false);
  const [exiting, setExiting]   = useState(false);
  const [svc, setSvc]           = useState(null);
  const [svcLoading, setSvcLoading] = useState(false);

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
      showToast('DeckDrop wird beendet…');
    } catch (err) {
      showToast(`Beenden fehlgeschlagen: ${formatApiError(err, 'settings')}`);
      setExiting(false);
      setShowExit(false);
    }
  };

  const enableService = async () => {
    setSvcLoading(true);
    try {
      const result = await api.enableService();
      setSvc(result);
      showToast('Service installiert und gestartet ✓');
    } catch (err) {
      showToast(`Fehler: ${formatApiError(err, 'service')}`);
    } finally {
      setSvcLoading(false);
    }
  };

  const disableService = async () => {
    setSvcLoading(true);
    try {
      const result = await api.disableService();
      setSvc(result);
      showToast('Service deaktiviert');
    } catch (err) {
      showToast(`Fehler: ${formatApiError(err, 'service')}`);
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
      showToast('Einstellungen gespeichert ✓');
    } catch (err) {
      showToast(`Fehler beim Speichern: ${formatApiError(err, 'settings')}`);
    } finally {
      setSaving(false);
    }
  };

  if (!cfg) return html`<div class="empty-state"><div class="spinner"></div></div>`;

  return html`
    <div class="view">
      <div class="view-header">
        <div class="view-title">Einstellungen</div>
      </div>

      <p class="settings-section-title">Profil</p>
      <div class="settings-section">
        <div class="settings-row">
          <div>
            <div class="settings-row-label">Name im Netzwerk</div>
            <div class="settings-row-sub">Sichtbar für andere Peers</div>
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
            <div class="settings-row-label">Peer-ID</div>
            <div class="settings-row-sub">Eindeutige Gerätekennung</div>
          </div>
          <code style="font-size:11px;color:var(--text-dim)">${cfg.peer_id.slice(0,16)}…</code>
        </div>
      </div>

      <p class="settings-section-title">Dateien</p>
      <div class="settings-section">
        <div class="settings-row">
          <div>
            <div class="settings-row-label">Download-Ordner</div>
            <div class="settings-row-sub">Neue Spiele werden hier gespeichert</div>
          </div>
          <input
            class="form-input"
            style="max-width:240px;font-size:12px"
            value=${cfg.download_dir}
            onInput=${e => update('download_dir', e.target.value)}
          />
        </div>
      </div>

      <p class="settings-section-title">Transfer</p>
      <div class="settings-section">
        <div class="settings-row">
          <div>
            <div class="settings-row-label">Upload-Limit</div>
            <div class="settings-row-sub">0 = unbegrenzt (KB/s)</div>
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
            <div class="settings-row-label">Download-Limit</div>
            <div class="settings-row-sub">0 = unbegrenzt (KB/s)</div>
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

      <p class="settings-section-title">Netzwerk</p>
      <div class="settings-section">
        <div class="settings-row">
          <div class="settings-row-label">API-Port</div>
          <code style="color:var(--text-dim)">${cfg.port}</code>
        </div>
        <div class="settings-row">
          <div class="settings-row-label">Sharing-Port</div>
          <code style="color:var(--text-dim)">${cfg.torrent_port}</code>
        </div>
      </div>

      <p style="font-size:12px;color:var(--text-dim);padding:4px 20px 12px;line-height:1.6;margin:0">
        🔒 DeckDrop kommuniziert nur im lokalen Netzwerk. DHT und externe Tracker sind
        deaktiviert – Ports ${cfg.port} und ${cfg.torrent_port} werden ausschließlich für
        LAN-Peers (inkl. Tailscale) verwendet.
      </p>

      <p class="settings-section-title">Anwendung</p>
      ${svc && html`
        <div class="settings-section">
          <div class="settings-row">
            <div>
              <div class="settings-row-label">Als Service starten</div>
              <div class="settings-row-sub">DeckDrop automatisch im Hintergrund starten (systemd)</div>
            </div>
            <div style="display:flex;align-items:center;gap:10px;flex-shrink:0">
              <span style="font-size:12px;color:${svc.active ? 'var(--success)' : 'var(--text-dim)'}">
                ${svc.active ? '● Aktiv' : '○ Inaktiv'}
              </span>
              ${svc.enabled
                ? html`<button
                    type="button"
                    class="btn btn-ghost"
                    style="padding:4px 12px;font-size:12px"
                    onClick=${disableService}
                    disabled=${svcLoading}
                  >${svcLoading ? html`<span class="spinner"></span>` : 'Deaktivieren'}</button>`
                : html`<button
                    type="button"
                    class="btn btn-primary"
                    style="padding:4px 12px;font-size:12px"
                    onClick=${enableService}
                    disabled=${svcLoading}
                  >${svcLoading ? html`<span class="spinner"></span>` : 'Als Service installieren'}</button>`
              }
            </div>
          </div>
        </div>
      `}
      <div class="settings-section">
        <button
          type="button"
          class="btn btn-danger"
          style="width:100%"
          onClick=${() => setShowExit(true)}
        >
          DeckDrop beenden
        </button>
      </div>

      <div style="padding:16px 20px">
        <button class="btn btn-primary" onClick=${save} disabled=${saving}>
          ${saving ? html`<span class="spinner"></span>` : 'Speichern'}
        </button>
      </div>

      ${showExit && html`
        <div
          class="overlay"
          onClick=${e => e.target === e.currentTarget && !exiting && setShowExit(false)}
          role="dialog"
          aria-modal="true"
          aria-label="DeckDrop beenden"
        >
          <div class="dialog">
            <div class="dialog-title">DeckDrop beenden?</div>
            <p class="dialog-text">
              Server und LAN-Freigabe werden gestoppt. Laufende Downloads werden abgebrochen.
            </p>
            <div class="dialog-actions">
              <button
                type="button"
                class="btn btn-ghost"
                onClick=${() => setShowExit(false)}
                disabled=${exiting}
              >
                Abbrechen
              </button>
              <button
                type="button"
                class="btn btn-danger"
                onClick=${confirmExit}
                disabled=${exiting}
              >
                ${exiting ? html`<span class="spinner"></span>` : 'Beenden'}
              </button>
            </div>
          </div>
        </div>
      `}
    </div>`;
}
