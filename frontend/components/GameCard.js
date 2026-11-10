import { html } from 'htm/preact';
import { useState } from 'preact/hooks';
import { fmtBytes } from '../api.js';
import { t } from '../i18n/index.js';
import { Icon } from '../icons.js';

function CoverImage({ game, mode = 'own' }) {
  const [failed, setFailed] = useState(false);
  let src = null;
  if (game.has_local_cover) {
    src = mode === 'network' && game.peer_id
      ? `/api/peers/${game.peer_id}/games/${game.id}/cover`
      : `/api/games/${game.id}/cover`;
  } else if (game.steam_app_id) {
    src = `https://cdn.cloudflare.steamstatic.com/steam/apps/${game.steam_app_id}/library_600x900.jpg`;
  }
  if (!src || failed) {
    // No cover: a quiet placeholder – the name is right below it.
    return html`<div class="card-cover-placeholder" aria-hidden="true"><${Icon} name="gamepad-2" size=${40} /></div>`;
  }
  return html`
    <div class="card-cover">
      <img src=${src} alt="" loading="lazy" onError=${() => setFailed(true)} />
    </div>`;
}

export function GameCard({
  game, mode = 'own', onAction, onEdit, onComments, onPublish, onUpdate, onRestore, onRepair, onRemove,
  disabled, downloading, prepProgress, updateProgress,
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

  // A / Enter on the card itself: download or update on the network page,
  // open the game's settings on your own games (it used to remove the game).
  const primary = mode === 'own' ? onEdit : onAction;
  const handleKey = (e) => {
    if (e.target !== e.currentTarget) return; // a button inside the card handles itself
    if ((e.key === 'Enter' || e.key === ' ') && !disabled && !downloading && !unavailable) {
      e.preventDefault();
      primary?.();
    }
  };

  let status = null;
  if (mode === 'own' && !unavailable) {
    if (updating) status = null;
    else if (ownPreparing) status = null;
    else if (modified) status = { cls: 'warn', icon: 'triangle-alert', text: t('card.modifiedNotShared') };
    else if (unverified) status = { cls: '', icon: 'hourglass', text: t('card.unverifiedNotShared') };
    else if (prepFailed) status = { cls: 'warn', icon: 'triangle-alert', text: t('card.prepFailed') };
    else if (game.shareable) status = { cls: 'ok', icon: 'circle-check', text: t('card.sharedStatus') };
    else status = { cls: '', icon: 'hourglass', text: t('card.notSharedYet') };
  }

  const stop = (fn) => (e) => { e.stopPropagation(); fn(); };

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
        <div class="card-meta">
          ${(mode === 'own' || game.version_label) && html`<span>${versionLabel} · </span>`}
          ${size}
          ${mode === 'network' && game.peer_count != null && html` · ${t('card.peers', { count: game.peer_count })}`}
          ${mode === 'network' && game.peer_count > 1 && game.peer_name && html` · ${game.peer_name}`}
          ${mode === 'own' && game.source_peer_name && html` · ${t('card.from', { peer: game.source_peer_name })}`}
          ${protondbUrl && html` · <a href=${protondbUrl} target="_blank" rel="noopener noreferrer"
              onClick=${e => e.stopPropagation()} tabIndex=${-1}>ProtonDB</a>`}
        </div>
        ${mode === 'own' && game.update_available && !updating && html`
          <div><span class="badge badge-success">
            ${t('card.updateAvailable', { version: game.update_version_label || t('card.available') })}
          </span></div>
        `}
        ${mode === 'network' && versionCount > 1 && html`
          <div><span class="badge">${t('card.versions', { count: versionCount })}</span></div>
        `}
        ${mode === 'network' && shortDesc && html`<div class="card-desc">${shortDesc}</div>`}
        ${status && html`
          <div class=${'card-status ' + status.cls}><${Icon} name=${status.icon} size=${16} />${status.text}</div>
        `}
        ${unavailable && html`<div class="card-status"><${Icon} name="cloud-off" size=${16} />${t('card.unavailable')}</div>`}
        ${hostPreparing && html`
          <div class="card-prep"><div class="card-prep-label">${t('card.hashing')}</div></div>
        `}
        ${ownPreparing && html`
          <div class="card-prep">
            <div class="card-prep-label">${t('card.hashingPct', { pct: prepPct })}</div>
            <div class="progress-bar" role="progressbar" aria-valuenow=${prepPct} aria-valuemin="0" aria-valuemax="100">
              <div class="progress-fill" style="width:${prepPct}%"></div>
            </div>
          </div>
        `}
        ${updating && html`
          <div class="card-prep">
            <div class="card-prep-label">${updatePreparing
              ? t('card.updatingCheck', { pct: updatePct })
              : t('card.updating', { pct: updatePct })}</div>
            <div class="progress-bar" role="progressbar" aria-valuenow=${updatePct} aria-valuemin="0" aria-valuemax="100">
              <div class="progress-fill" style="width:${updatePct}%"></div>
            </div>
            <div class="card-prep-label">${t('card.dontLaunch')}</div>
          </div>
        `}

        <div class="card-extra">
          ${modified && onPublish && !updating && html`
            <button class="btn btn-primary btn-sm" onClick=${stop(onPublish)}>${t('card.publish')}</button>
          `}
          ${game.update_available && onUpdate && !updating && html`
            <button class="btn btn-secondary btn-sm" onClick=${stop(onUpdate)}>${t('card.updateButton')}</button>
          `}
          ${updating && game.update_restart_available && onUpdate && html`
            <button class="btn btn-secondary btn-sm" onClick=${stop(onUpdate)} title=${t('card.restartUpdateTitle')}>
              ${t('card.restartUpdate', { version: game.update_restart_label || t('card.newVersion') })}
            </button>
          `}
          ${game.restore_available && onRestore && !updating && html`
            <button class="btn btn-danger btn-sm" onClick=${stop(onRestore)}>${t('card.discard')}</button>
          `}
        </div>

        <div class="card-actions">
          ${mode === 'network' && !unavailable && html`
            <button
              class=${'btn btn-sm ' + (installedNoUpdate ? 'btn-secondary' : 'btn-primary')}
              onClick=${stop(onAction)}
              disabled=${disabled || downloading || hostPreparing || installedNoUpdate}
            >
              ${downloading
                ? html`<${Icon} name="download" size=${18} />${t('card.downloading')}`
                : hostPreparing
                ? t('card.pleaseWait')
                : installedWithUpdate
                  ? html`<${Icon} name="refresh-cw" size=${18} />${t('card.update')}`
                  : installedNoUpdate
                    ? html`<${Icon} name="check" size=${18} />${t('card.installed')}`
                    : html`<${Icon} name="download" size=${18} />${t('card.download')}`}
            </button>
          `}
          ${mode === 'own' && html`<span style="flex:1"></span>`}
          ${onComments && html`
            <button class="btn btn-icon" onClick=${stop(onComments)}
              title=${t('card.comments')} aria-label=${t('card.showComments')}>
              <${Icon} name="message-square" />
            </button>
          `}
          ${mode === 'own' && onRepair && game.local_share === false && html`
            <button class="btn btn-icon" onClick=${stop(onRepair)}
              title=${t('card.repair')} aria-label=${t('card.repairGame')}>
              <${Icon} name="wrench" />
            </button>
          `}
          ${mode === 'own' && onEdit && html`
            <button class="btn btn-icon" onClick=${stop(onEdit)}
              title=${t('card.edit')} aria-label=${t('card.editGame')}>
              <${Icon} name="pencil" />
            </button>
          `}
          ${mode === 'own' && onRemove && html`
            <button class="btn btn-icon" onClick=${stop(onRemove)}
              title=${t('card.remove')} aria-label=${t('card.removeGame')}>
              <${Icon} name="trash-2" />
            </button>
          `}
        </div>
      </div>
    </div>`;
}
