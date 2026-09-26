import { html } from 'htm/preact';
import { useState } from 'preact/hooks';
import { fmtBytes } from '../api.js';

const PLACEHOLDER_STYLE = 'display:none;position:absolute;inset:0;align-items:center;justify-content:center;font-size:52px;font-weight:800;color:var(--accent);pointer-events:none';

function CoverImage({ game, mode = 'own' }) {
  const initial = (game.name || '?')[0].toUpperCase();
  const [steamFailed, setSteamFailed] = useState(false);

  if (game.has_local_cover) {
    const coverSrc = mode === 'network' && game.peer_id
      ? `/api/peers/${game.peer_id}/games/${game.id}/cover`
      : `/api/games/${game.id}/cover`;
    return html`
      <div class="card-cover">
        <img
          src=${coverSrc}
          alt=${game.name}
          onError=${e => { e.target.style.display='none'; e.target.parentNode.querySelector('.placeholder-text').style.display='flex'; }}
        />
        <span class="placeholder-text" style=${PLACEHOLDER_STYLE}>${initial}</span>
      </div>`;
  }
  if (game.steam_app_id && !steamFailed) {
    return html`
      <div class="card-cover">
        <img
          src="https://cdn.cloudflare.steamstatic.com/steam/apps/${game.steam_app_id}/library_600x900.jpg"
          alt=${game.name}
          onError=${() => setSteamFailed(true)}
        />
        <span class="placeholder-text" style=${PLACEHOLDER_STYLE}>${initial}</span>
      </div>`;
  }
  return html`<div class="card-cover-placeholder">${initial}</div>`;
}

export function GameCard({
  game, mode = 'own', onAction, onEdit, onComments, onPublish, onUpdate, onRestore,
  disabled, prepProgress, updateProgress,
}) {
  const unavailable = mode === 'own' && !game.available;
  const size = game.size_bytes ? fmtBytes(game.size_bytes) : '–';
  const modified = mode === 'own' && game.content_state === 'modified';
  const updating = mode === 'own' && game.content_state === 'updating';
  // No file hashes yet and no hash running (interrupted, or cancelled for an
  // update) – not shared until the next check, but not "Verändert" either.
  const unverified = mode === 'own' && game.content_state === 'unverified';
  // updateProgress: { progress, phase, phase_progress } from download_progress.
  const updatePreparing = updateProgress?.phase === 'preparing';
  const updatePct = Math.round(
    ((updatePreparing ? updateProgress?.phase_progress : updateProgress?.progress) ?? 0) * 100,
  );
  const versionLabel = game.version_label || `Rev. ${game.revision ?? 1}`;
  const hostPreparing = mode === 'network'
    && !game.has_torrent
    && !game.torrent_prep_error;
  const hashing = mode === 'own' && (
    game.content_state === 'hashing' || game.content_state === 'publishing'
  );
  const ownPreparing = mode === 'own' && (game.torrent_preparing || hashing);
  const installedNoUpdate = mode === 'network' && game.installed && !game.update_available;
  const installedWithUpdate = mode === 'network' && game.installed && game.update_available;
  const versionCount = mode === 'network' ? (game.version_count ?? 1) : 1;
  const prepPct = Math.round(
    (prepProgress ?? game.torrent_prep_progress ?? 0) * 100,
  );
  const prepFailed = mode === 'own' && !!game.torrent_prep_error;

  const protondbUrl = game.steam_app_id
    ? `https://www.protondb.com/app/${game.steam_app_id}`
    : null;

  const shortDesc = game.description
    ? (game.description.length > 80 ? game.description.slice(0, 80) + '…' : game.description)
    : null;

  const handleKey = (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && !disabled && !unavailable) {
      e.preventDefault();
      onAction?.();
    }
  };

  return html`
    <div
      class=${'game-card' + (unavailable ? ' card-unavailable' : '')}
      tabIndex=${0}
      data-card
      onKeyDown=${handleKey}
      role="article"
      aria-label=${game.name}
    >
      <${CoverImage} game=${game} mode=${mode} />
      <div class="card-body">
        <div class="card-name">${game.name}</div>
        ${(mode === 'own' || game.version_label) && html`
          <div class="card-version">
            ${versionLabel}
            ${mode === 'own' && game.update_available && !updating && html`
              <span class="badge badge-success" style="margin-left:6px">
                Update verfügbar: ${game.update_version_label || 'verfügbar'}
              </span>
            `}
          </div>
        `}
        ${mode === 'network' && versionCount > 1 && html`
          <div class="card-version"><span class="badge">${versionCount} Versionen</span></div>
        `}
        <div class="card-meta">
          ${size}
          ${mode === 'network' && game.peer_count != null && html`
            · <span>${game.peer_count} Peer${game.peer_count !== 1 ? 's' : ''}</span>
          `}
          ${mode === 'network' && game.peer_count > 1 && game.peer_name && html`
            · <span>${game.peer_name}</span>
          `}
          ${mode === 'own' && game.source_peer_name && html`
            · <span>von ${game.source_peer_name}</span>
          `}
          ${game.created_by && html`
            · <span>Erstellt von ${game.created_by}</span>
          `}
          ${protondbUrl && html`
            · <a
                href=${protondbUrl}
                target="_blank"
                rel="noopener noreferrer"
                style="color:var(--accent);text-decoration:none;font-size:11px"
                onClick=${e => e.stopPropagation()}
                tabIndex=${-1}
              >ProtonDB</a>
          `}
        </div>
        ${shortDesc && html`
          <div style="font-size:12px;color:var(--muted);margin-top:4px;line-height:1.4">${shortDesc}</div>
        `}
        ${hostPreparing && html`
          <div class="card-prep">
            <div class="card-prep-label">Game-Hashes berechnen…</div>
          </div>
        `}
        ${ownPreparing && html`
          <div class="card-prep">
            <div class="card-prep-label">Game-Hashes berechnen… ${prepPct}%</div>
            <div class="progress-bar" role="progressbar" aria-valuenow=${prepPct} aria-valuemin="0" aria-valuemax="100">
              <div class="progress-fill" style="width:${prepPct}%"></div>
            </div>
          </div>
        `}
        ${prepFailed && html`
          <span class="unavailable-chip" style="margin-bottom:6px">Vorbereitung fehlgeschlagen</span>
        `}
        ${modified && !updating && html`
          <div class="card-modified-hint">
            <span class="badge badge-warn">Verändert</span>
            <span>Wird nicht geteilt</span>
          </div>
        `}
        ${unverified && html`
          <div class="card-modified-hint">
            <span class="badge">Noch nicht geprüft</span>
            <span>Wird nicht geteilt</span>
          </div>
        `}
        ${updating && html`
          <div class="card-prep">
            <div class="card-prep-label">${updatePreparing
              ? `Wird aktualisiert… prüfe vorhandene Dateien ${updatePct}%`
              : `Wird aktualisiert… ${updatePct}%`}</div>
            <div class="progress-bar" role="progressbar" aria-valuenow=${updatePct} aria-valuemin="0" aria-valuemax="100">
              <div class="progress-fill" style="width:${updatePct}%"></div>
            </div>
            <div style="font-size:11px;color:var(--text-dim);margin-top:4px">Spiel nicht starten</div>
          </div>
        `}
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          ${unavailable
            ? html`<span class="unavailable-chip">Nicht verfügbar</span>`
            : html`<button
                class=${'btn ' + (mode === 'own' || installedNoUpdate ? 'btn-secondary' : 'btn-primary')}
                style=${(mode === 'own' || mode === 'network') && (onEdit || onComments) ? 'flex:1' : ''}
                onClick=${onAction}
                disabled=${disabled || ownPreparing || hostPreparing || installedNoUpdate || updating}
                tabIndex=${-1}
              >
                ${hostPreparing
                  ? '⏳ Bitte warten…'
                  : ownPreparing
                    ? '⏳ Hashes…'
                    : mode === 'own'
                      ? '✓ Geteilt'
                      : installedWithUpdate
                        ? '⟳ Update'
                        : installedNoUpdate
                          ? '✓ Installiert'
                          : '↓ Laden'}
              </button>`
          }
          ${(mode === 'own' || mode === 'network') && onComments && html`
            <button
              class="btn btn-ghost"
              style="padding:6px 10px;font-size:15px;min-width:36px"
              onClick=${e => { e.stopPropagation(); onComments(); }}
              tabIndex=${-1}
              title="Kommentare"
              aria-label="Kommentare anzeigen"
            >💬</button>
          `}
          ${mode === 'own' && onEdit && html`
            <button
              class="btn btn-ghost"
              style="padding:6px 10px;font-size:15px;min-width:36px"
              onClick=${onEdit}
              tabIndex=${-1}
              title="Bearbeiten"
              aria-label="Spiel bearbeiten"
            >✎</button>
          `}
        </div>
        ${modified && onPublish && !updating && html`
          <button
            class="btn btn-secondary"
            style="width:100%;margin-top:6px;font-size:13px"
            onClick=${e => { e.stopPropagation(); onPublish(); }}
            tabIndex=${-1}
          >Update veröffentlichen…</button>
        `}
        ${game.restore_available && onRestore && !updating && html`
          <button
            class="btn btn-danger"
            style="width:100%;margin-top:6px;font-size:13px"
            onClick=${e => { e.stopPropagation(); onRestore(); }}
            tabIndex=${-1}
          >Änderungen verwerfen (aus Netzwerk wiederherstellen)</button>
        `}
        ${game.update_available && onUpdate && !updating && html`
          <button
            class="btn btn-secondary"
            style="width:100%;margin-top:6px;font-size:13px"
            onClick=${e => { e.stopPropagation(); onUpdate(); }}
            tabIndex=${-1}
          >Aktualisieren…</button>
        `}
      </div>
    </div>`;
}
