import { html } from 'htm/preact';
import { useState, useEffect } from 'preact/hooks';
import { api } from '../api.js';
import { formatApiError } from '../errors.js';
import { GameCard } from './GameCard.js';
import { Comments } from './Comments.js';
import { VersionList } from './VersionList.js';
import { UpdateGame } from './UpdateGame.js';
import { t } from '../i18n/index.js';
import { Icon } from '../icons.js';
import { useDialogKeys } from '../hooks.js';

// Download states that mean "this game is already on its way" – starting it
// again would only create a second download into the same folder.
const BUSY_DL = new Set(['queued', 'downloading', 'verifying', 'paused', 'error']);

export function Network({ wsEvent, onNavigate, showToast, onDownloadStarted, downloads = [] }) {
  const [games, setGames]     = useState([]);
  const [peers, setPeers]     = useState([]);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(null); // game_id being started
  const [commentsGame, setCommentsGame] = useState(null);
  const [versionGame, setVersionGame] = useState(null); // game whose version picker is open
  const [chosenVersionKey, setChosenVersionKey] = useState(null);
  useDialogKeys(!!versionGame, () => setVersionGame(null));
  const [updateGame, setUpdateGame] = useState(null); // locally installed game to update
  const [query, setQuery]     = useState('');

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
    if (
      wsEvent?.event === 'peer_online' ||
      wsEvent?.event === 'peer_offline' ||
      wsEvent?.event === 'peer_games_updated'
    ) load();
  }, [wsEvent]);

  // Poll while a host is still preparing games (hashes / magnet)
  useEffect(() => {
    const waiting = games.some(g => !g.has_torrent && !g.torrent_prep_error);
    if (!waiting) return;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [games]);

  const busyGameIds = new Set(
    downloads.filter(d => BUSY_DL.has(d.status)).map(d => d.game_id),
  );

  const startDownload = async (game, versionKey) => {
    if (starting || busyGameIds.has(game.id)) return;
    setStarting(`${game.id}:${game.peer_id}`);
    try {
      const body = { peer_id: game.peer_id, game_id: game.id };
      if (versionKey) body.version_key = versionKey;
      // Answers right away (the backend fetches from the host in the
      // background), so the row is there when the downloads page opens.
      const dl = await api.startDl(body);
      onDownloadStarted?.(dl);
      onNavigate('downloads');
      showToast(t('net.downloadStarted', { name: game.name }));
    } catch (err) {
      showToast(t('common.error', { error: formatApiError(err, 'download') }));
    } finally {
      setStarting(null);
    }
  };

  const openUpdate = async (game) => {
    try {
      // The network entry's fields describe the remote peer, not our local
      // install – fetch our own game record so UpdateGame's warning/version
      // header reflect the local content_state.
      const local = await api.getGame(game.id);
      setUpdateGame(local);
    } catch {
      setUpdateGame(game);
    }
  };

  const handleAction = (game) => {
    if (game.installed) {
      if (game.update_available) openUpdate(game);
      return;
    }
    if ((game.version_count ?? 1) > 1) {
      setChosenVersionKey(game.versions?.[0]?.version_key ?? null);
      setVersionGame(game);
      return;
    }
    startDownload(game, game.versions?.[0]?.version_key);
  };

  const onlineCount = peers.length;
  const filtered = query.trim()
    ? games.filter(g => g.name.toLowerCase().includes(query.trim().toLowerCase()))
    : games;

  return html`
    <div class="view">
      <div class="view-header">
        <div>
          <h1 class="view-title">${t('net.title')}</h1>
          <div class="view-subtitle">${t('net.peersOnline', { count: onlineCount })}</div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">
          ${peers.map(p => html`
            <span key=${p.peer_id} class="peer-chip">
              <span class="peer-dot"></span>${p.name}${typeof p.game_count === 'number' ? html` <span style="color:var(--text-dim)">${p.game_count}</span>` : null}
            </span>`
          )}
        </div>
      </div>

      ${!loading && games.length > 0 && html`
        <div class="search-bar">
          <input
            type="search"
            class="search-input"
            placeholder=${t('net.search')}
            value=${query}
            onInput=${e => setQuery(e.target.value)}
            aria-label=${t('net.searchLabel')}
          />
        </div>
      `}

      ${loading
        ? html`<div class="empty-state"><div class="spinner"></div></div>`
        : games.length === 0
          ? html`<div class="empty-state">
              <div class="empty-icon"><${Icon} name="radio-tower" /></div>
              <div class="empty-title">${onlineCount > 0 ? t('net.noGames') : t('net.noPeers')}</div>
              <div class="empty-sub">${onlineCount > 0
                ? t('net.noGamesSub')
                : t('net.noPeersSub')}</div>
            </div>`
          : filtered.length === 0
            ? html`<div class="empty-state">
                <div class="empty-icon"><${Icon} name="search" /></div>
                <div class="empty-title">${t('net.noMatches')}</div>
                <div class="empty-sub">${t('net.noMatchesSub', { query })}</div>
              </div>`
            : html`<div class="card-grid" role="list">
                ${filtered.map(g => html`
                  <${GameCard}
                    key=${g.id + g.peer_id}
                    game=${g}
                    mode="network"
                    disabled=${starting === `${g.id}:${g.peer_id}`}
                    downloading=${busyGameIds.has(g.id) || starting === `${g.id}:${g.peer_id}`}
                    onAction=${() => handleAction(g)}
                    onComments=${() => setCommentsGame(g)}
                  />
                `)}
              </div>`
      }

      ${commentsGame && html`
        <${Comments}
          game=${commentsGame}
          peerId=${commentsGame.peer_id}
          readOnly
          onClose=${() => setCommentsGame(null)}
        />
      `}

      ${versionGame && html`
        <div
          class="overlay"
          onClick=${e => e.target === e.currentTarget && setVersionGame(null)}
          role="dialog"
          aria-modal="true"
          aria-label=${t('net.chooseVersion')}
        >
          <div class="dialog">
            <div class="dialog-title">${t('net.chooseVersionFor', { name: versionGame.name })}</div>
            <${VersionList}
              versions=${versionGame.versions || []}
              value=${chosenVersionKey}
              onChange=${setChosenVersionKey}
            />
            <div class="dialog-actions">
              <button class="btn btn-ghost" onClick=${() => setVersionGame(null)}>${t('common.cancel')}</button>
              <button
                class="btn btn-primary"
                disabled=${!chosenVersionKey}
                onClick=${() => {
                  const g = versionGame;
                  const key = chosenVersionKey;
                  setVersionGame(null);
                  startDownload(g, key);
                }}
              >${t('net.downloadVersion')}</button>
            </div>
          </div>
        </div>
      `}

      ${updateGame && html`
        <${UpdateGame}
          game=${updateGame}
          onClose=${() => setUpdateGame(null)}
          onStarted=${(dl) => {
            const name = updateGame.name;
            setUpdateGame(null);
            onDownloadStarted?.(dl);
            onNavigate('downloads');
            showToast(t('net.updateStarted', { name }));
          }}
        />
      `}
    </div>`;
}
