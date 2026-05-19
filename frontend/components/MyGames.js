import { html } from 'htm/preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import { api } from '../api.js';
import { GameCard } from './GameCard.js';
import { AddGame } from './AddGame.js';
import { EditGame } from './EditGame.js';
import { useGridNav } from '../app.js';

export function MyGames({ wsEvent, showToast }) {
  const [games, setGames]       = useState([]);
  const [loading, setLoading]   = useState(true);
  const [showAdd, setShowAdd]   = useState(false);
  const [editGame, setEditGame] = useState(null);
  const gridRef = useRef(null);
  useGridNav(gridRef);

  const load = async () => {
    try { setGames(await api.games()); }
    catch {}
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  // Reload when a download completes
  useEffect(() => {
    if (wsEvent?.event === 'download_complete') load();
  }, [wsEvent]);

  const onAdded = (game) => {
    setGames(g => [...g, game]);
    setShowAdd(false);
    showToast(`„${game.name}" hinzugefügt`);
  };

  const onRemove = async (game) => {
    if (!confirm(`„${game.name}" aus DeckDrop entfernen? Dateien bleiben erhalten.`)) return;
    try {
      await api.removeGame(game.id);
      setGames(g => g.filter(x => x.id !== game.id));
      showToast(`„${game.name}" entfernt`);
    } catch {}
  };

  const onSaved = (updated) => {
    setGames(gs => gs.map(g => g.id === updated.id ? updated : g));
    setEditGame(null);
    showToast(`„${updated.name}" gespeichert`);
  };

  return html`
    <div class="view">
      <div class="view-header">
        <div>
          <div class="view-title">Meine Spiele</div>
          <div class="view-subtitle">${games.length} Spiel${games.length !== 1 ? 'e' : ''} geteilt</div>
        </div>
      </div>

      ${loading
        ? html`<div class="empty-state"><div class="spinner"></div></div>`
        : games.length === 0
          ? html`<div class="empty-state">
              <div class="empty-icon">🎮</div>
              <div class="empty-title">Noch keine Spiele</div>
              <div class="empty-sub">Füge deinen ersten Spielordner hinzu, um ihn im LAN zu teilen.</div>
            </div>`
          : html`<div class="card-grid" ref=${gridRef} role="list">
              ${games.map(g => html`
                <${GameCard}
                  key=${g.id}
                  game=${g}
                  mode="own"
                  onAction=${() => onRemove(g)}
                  onEdit=${() => setEditGame(g)}
                />
              `)}
            </div>`
      }

      <button
        class="fab"
        onClick=${() => setShowAdd(true)}
        title="Spiel hinzufügen"
        aria-label="Spiel hinzufügen"
      >+</button>

      ${showAdd && html`<${AddGame} onClose=${() => setShowAdd(false)} onAdded=${onAdded} />`}
      ${editGame && html`<${EditGame} game=${editGame} onClose=${() => setEditGame(null)} onSaved=${onSaved} />`}
    </div>`;
}
