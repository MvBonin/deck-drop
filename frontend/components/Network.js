import { html } from 'htm/preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import { api } from '../api.js';
import { GameCard } from './GameCard.js';
import { useGridNav } from '../app.js';

export function Network({ wsEvent, onNavigate, showToast }) {
  const [games, setGames]     = useState([]);
  const [peers, setPeers]     = useState([]);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(null); // game_id being started
  const [query, setQuery]     = useState('');
  const gridRef = useRef(null);
  useGridNav(gridRef);

  const load = async () => {
    try {
      const [g, p] = await Promise.all([api.netGames(), api.peers()]);
      setGames(g);
      setPeers(p.filter(p => p.online));
    } catch {}
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  // React to peer events
  useEffect(() => {
    if (wsEvent?.event === 'peer_online' || wsEvent?.event === 'peer_offline') load();
  }, [wsEvent]);

  const startDownload = async (game) => {
    setStarting(game.id);
    try {
      await api.startDl({ peer_id: game.peer_id, game_id: game.id });
      showToast(`Download gestartet: ${game.name}`);
      onNavigate('downloads');
    } catch (err) {
      showToast(`Fehler: ${err.status === 503 ? 'Transfer nicht verfügbar (libtorrent fehlt)' : 'Download fehlgeschlagen'}`);
    } finally {
      setStarting(null);
    }
  };

  const onlineCount = peers.length;
  const filtered = query.trim()
    ? games.filter(g => g.name.toLowerCase().includes(query.trim().toLowerCase()))
    : games;

  return html`
    <div class="view">
      <div class="view-header">
        <div>
          <div class="view-title">Netzwerk</div>
          <div class="view-subtitle">${onlineCount} Peer${onlineCount !== 1 ? 's' : ''} online</div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          ${peers.map(p => html`
            <span key=${p.peer_id} class="peer-chip">
              <span class="peer-dot"></span>${p.name} (${p.game_count})
            </span>`
          )}
        </div>
      </div>

      ${!loading && games.length > 0 && html`
        <div class="search-bar">
          <input
            type="search"
            class="search-input"
            placeholder="Spiele suchen…"
            value=${query}
            onInput=${e => setQuery(e.target.value)}
            aria-label="Spiele suchen"
          />
        </div>
      `}

      ${loading
        ? html`<div class="empty-state"><div class="spinner"></div></div>`
        : games.length === 0
          ? html`<div class="empty-state">
              <div class="empty-icon">📡</div>
              <div class="empty-title">Keine Peers gefunden</div>
              <div class="empty-sub">Starte DeckDrop auf anderen Geräten im gleichen WLAN.</div>
            </div>`
          : filtered.length === 0
            ? html`<div class="empty-state">
                <div class="empty-icon">🔍</div>
                <div class="empty-title">Keine Treffer</div>
                <div class="empty-sub">Keine Spiele für "${query}" gefunden.</div>
              </div>`
            : html`<div class="card-grid" ref=${gridRef} role="list">
                ${filtered.map(g => html`
                  <${GameCard}
                    key=${g.id + g.peer_id}
                    game=${g}
                    mode="network"
                    disabled=${starting === g.id}
                    onAction=${() => startDownload(g)}
                  />
                `)}
              </div>`
      }
    </div>`;
}
