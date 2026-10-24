import { html } from 'htm/preact';
import { fmtBytes } from '../api.js';
import { t, getLocale } from '../i18n/index.js';

function fmtDate(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString(getLocale());
  } catch {
    return '';
  }
}

/**
 * Radio list of content versions of one game, shared by the first-download
 * version picker (Network.js) and the update dialog (Phase 5, UpdateGame.js).
 *
 * `versions` follows peer_registry.all_network_games()["versions"]: sorted
 * newest first, each with version_key/revision/version_label/version_note/
 * created_by/content_updated_by/content_updated_at/size_bytes/shareable/
 * has_torrent/peers.
 */
export function VersionList({ versions, value, onChange }) {
  if (!versions || versions.length === 0) return null;

  return html`
    <div role="radiogroup" aria-label=${t('versions.choose')} style="display:flex;flex-direction:column;gap:8px">
      ${versions.map((v, i) => {
        const label = v.version_label || `Rev. ${v.revision}`;
        const disabled = !v.shareable || !v.has_torrent;
        const selected = value === v.version_key;
        const peerNames = (v.peers || []).map(p => p.peer_name).join(', ');
        const by = v.content_updated_by || v.created_by;
        const date = fmtDate(v.content_updated_at);
        const byLine = by && date ? t('versions.byOn', { by, date }) : (by || date);
        const borderColor = selected ? 'var(--accent)' : 'var(--surface-3)';
        return html`
          <label
            key=${v.version_key}
            style=${`display:flex;gap:10px;align-items:flex-start;padding:10px;border-radius:var(--radius-sm);border:1px solid ${borderColor};cursor:${disabled ? 'not-allowed' : 'pointer'};opacity:${disabled ? 0.55 : 1};background:${selected ? 'var(--surface-2)' : 'transparent'}`}
          >
            <input
              type="radio"
              name="version-list"
              value=${v.version_key}
              checked=${selected}
              disabled=${disabled}
              onChange=${() => onChange?.(v.version_key)}
              style="margin-top:3px"
            />
            <div style="flex:1;min-width:0">
              <div style="display:flex;align-items:center;gap:6px;font-weight:600">
                <span>${label}</span>
                <span style="font-weight:400;color:var(--text-dim);font-size:12px">· Rev. ${v.revision}</span>
                ${i === 0 && !disabled && html`<span class="badge badge-success">${t('versions.latest')}</span>`}
              </div>
              <div style="font-size:12px;color:var(--text-dim);margin-top:2px">
                ${byLine ? t('versions.by', { who: byLine }) : ''}
                ${peerNames ? t('versions.at', { peers: peerNames }) : ''}
                ${v.size_bytes ? html` · ${fmtBytes(v.size_bytes)}` : ''}
              </div>
              ${v.version_note && html`
                <div style="font-size:12px;color:var(--text);margin-top:4px;white-space:pre-wrap">${v.version_note}</div>
              `}
              ${disabled && html`
                <div style="font-size:11px;color:var(--danger);margin-top:4px">
                  ${t('versions.notShareable')}${v.content_updated_by ? t('versions.modifiedBy', { name: v.content_updated_by }) : ''}
                </div>
              `}
            </div>
          </label>`;
      })}
    </div>`;
}
