import { html } from 'htm/preact';
import { fmtBytes } from '../api.js';

function CoverImage({ game }) {
  const initial = (game.name || '?')[0].toUpperCase();
  if (game.steam_app_id) {
    return html`
      <div class="card-cover">
        <img
          src="https://cdn.cloudflare.steamstatic.com/steam/apps/${game.steam_app_id}/library_600x900.jpg"
          alt=${game.name}
          onError=${e => { e.target.style.display='none'; e.target.parentNode.querySelector('.placeholder-text').style.display='flex'; }}
        />
        <span class="placeholder-text" style="display:none;position:absolute;inset:0;align-items:center;justify-content:center;font-size:52px;font-weight:800;color:var(--accent)">${initial}</span>
      </div>`;
  }
  return html`<div class="card-cover-placeholder">${initial}</div>`;
}

export function GameCard({ game, mode = 'own', onAction, onEdit, disabled }) {
  const unavailable = mode === 'own' && !game.available;
  const size = game.size_bytes ? fmtBytes(game.size_bytes) : '–';

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
      <${CoverImage} game=${game} />
      <div class="card-body">
        <div class="card-name">${game.name}</div>
        <div class="card-meta">${size}${game.peer_name ? html` · <span>${game.peer_name}</span>` : ''}</div>
        ${unavailable
          ? html`<span class="unavailable-chip">Nicht verfügbar</span>`
          : html`<div style="display:flex;gap:6px;align-items:center">
              <button
                class=${'btn ' + (mode === 'own' ? 'btn-secondary' : 'btn-primary')}
                style=${mode === 'own' && onEdit ? 'flex:1' : ''}
                onClick=${onAction}
                disabled=${disabled}
                tabIndex=${-1}
              >
                ${mode === 'own' ? '✓ Geteilt' : '↓ Laden'}
              </button>
              ${mode === 'own' && onEdit && html`
                <button
                  class="btn btn-ghost"
                  style="padding:6px 10px;font-size:15px"
                  onClick=${onEdit}
                  tabIndex=${-1}
                  title="Bearbeiten"
                  aria-label="Spiel bearbeiten"
                >✎</button>
              `}
            </div>`
        }
      </div>
    </div>`;
}
