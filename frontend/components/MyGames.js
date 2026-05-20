import { html } from 'htm/preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import { api } from '../api.js';
import { formatApiError } from '../errors.js';
import { GameCard } from './GameCard.js';
import { AddGame } from './AddGame.js';
import { EditGame } from './EditGame.js';
import { useGridNav } from '../app.js';

export function MyGames({ wsEvent, showToast }) {
  const [games, setGames]       = useState([]);
  const [prepById, setPrepById] = useState({});
  const [loading, setLoading]   = useState(true);
  const [showAdd, setShowAdd]   = useState(false);
  const [editGame, setEditGame] = useState(null);
  const gridRef = useRef(null);
  useGridNav(gridRef);

  const syncPrepFromGames = (list) => {
    const next = {};
    for (const g of list) {
      if (g.torrent_preparing) {
        next[g.id] = g.torrent_prep_progress ?? 0;
      }
    }
    setPrepById(next);
  };

  const load = async () => {
    try {
      const list = await api.games();
      setGames(list);
      syncPrepFromGames(list);
    } catch {}
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  // Reload when a download completes
  useEffect(() => {
    if (wsEvent?.event === 'download_complete') load();
  }, [wsEvent]);

  useEffect(() => {
    if (!wsEvent?.data?.game_id) return;
    const id = wsEvent.data.game_id;
    if (wsEvent.event === 'torrent_prep_started') {
      setPrepById(p => ({ ...p, [id]: 0 }));
      setGames(gs => gs.map(g => g.id === id
        ? { ...g, torrent_preparing: true, torrent_prep_progress: 0, torrent_prep_error: null }
        : g));
    }
    if (wsEvent.event === 'torrent_prep_progress') {
      const progress = wsEvent.data.progress ?? 0;
      setPrepById(p => ({ ...p, [id]: progress }));
      setGames(gs => gs.map(g => g.id === id
        ? { ...g, torrent_preparing: true, torrent_prep_progress: progress }
        : g));
    }
    if (wsEvent.event === 'torrent_prep_complete') {
      setPrepById(p => { const n = { ...p }; delete n[id]; return n; });
      setGames(gs => gs.map(g => g.id === id
        ? { ...g, has_torrent: true, torrent_preparing: false, torrent_prep_progress: null, torrent_prep_error: null }
        : g));
      showToast('Spiel ist bereit zum Teilen');
    }
    if (wsEvent.event === 'torrent_prep_error') {
      setPrepById(p => { const n = { ...p }; delete n[id]; return n; });
      const err = wsEvent.data.error || 'Unbekannter Fehler';
      setGames(gs => gs.map(g => g.id === id
        ? { ...g, torrent_preparing: false, torrent_prep_error: err }
        : g));
      showToast(`Game-Hashes fehlgeschlagen: ${err}`);
    }
  }, [wsEvent]);

  // Poll while any game is still preparing (e.g. after page reload)
  useEffect(() => {
    const needsPoll = games.some(g => g.torrent_preparing);
    if (!needsPoll) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [games]);

  const onAdded = (game) => {
    setGames(g => [...g, { ...game, torrent_preparing: !game.has_torrent, torrent_prep_progress: 0 }]);
    if (!game.has_torrent) setPrepById(p => ({ ...p, [game.id]: 0 }));
    setShowAdd(false);
    showToast(`„${game.name}" hinzugefügt – Game-Hashes werden berechnet…`);
  };

  const onRemove = async (game) => {
    if (!confirm(`„${game.name}" aus DeckDrop entfernen? Dateien bleiben erhalten.`)) return;
    try {
      await api.removeGame(game.id);
      setGames(g => g.filter(x => x.id !== game.id));
      showToast(`„${game.name}" entfernt`);
    } catch (err) {
      showToast(`Fehler: ${formatApiError(err, 'game')}`);
    }
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
                  prepProgress=${prepById[g.id]}
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
