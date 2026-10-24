import { html } from 'htm/preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import { api } from '../api.js';
import { formatApiError } from '../errors.js';
import { GameCard } from './GameCard.js';
import { AddGame } from './AddGame.js';
import { EditGame } from './EditGame.js';
import { Comments } from './Comments.js';
import { PublishUpdate } from './PublishUpdate.js';
import { UpdateGame } from './UpdateGame.js';
import { RepairGame } from './RepairGame.js';
import { useGridNav } from '../app.js';
import { t } from '../i18n/index.js';

export function MyGames({ wsEvent, showToast }) {
  const [games, setGames]       = useState([]);
  const [prepById, setPrepById] = useState({});
  const [updateProgress, setUpdateProgress] = useState({});
  const [loading, setLoading]   = useState(true);
  const [showAdd, setShowAdd]         = useState(false);
  const [editGame, setEditGame]       = useState(null);
  const [commentsGame, setCommentsGame] = useState(null);
  const [publishGame, setPublishGame] = useState(null);
  const [updateGame, setUpdateGame]   = useState(null);
  const [repairGame, setRepairGame]   = useState(null);
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

  useEffect(() => {
    load();
    api.scanGames().catch(() => {});
  }, []);

  // Reload when a download truly completes (libtorrent finished + full bytes)
  useEffect(() => {
    if (wsEvent?.event !== 'download_complete') return;
    const total = wsEvent.data?.total_bytes ?? 0;
    const downloaded = wsEvent.data?.downloaded_bytes ?? 0;
    if (total > 0 && downloaded >= total) load();
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
      showToast(t('myGames.readyToShare'));
    }
    if (wsEvent.event === 'torrent_prep_error') {
      setPrepById(p => { const n = { ...p }; delete n[id]; return n; });
      const err = wsEvent.data.error || t('common.unknownError');
      setGames(gs => gs.map(g => g.id === id
        ? { ...g, torrent_preparing: false, torrent_prep_error: err }
        : g));
      showToast(t('myGames.hashFailed', { error: err }));
    }
    if (wsEvent.event === 'game_content_state') {
      const { state, summary } = wsEvent.data;
      const hashing = state === 'hashing' || state === 'publishing';
      if (hashing) setPrepById(p => ({ ...p, [id]: p[id] ?? 0 }));
      setGames(gs => gs.map(g => g.id === id
        ? {
            ...g,
            content_state: state,
            change_summary: summary ?? g.change_summary,
            shareable: state === 'clean' && g.has_torrent,
            torrent_preparing: hashing ? true : g.torrent_preparing,
            torrent_prep_progress: hashing ? (g.torrent_prep_progress ?? 0) : g.torrent_prep_progress,
          }
        : g));
    }
    if (wsEvent.event === 'content_publish_progress') {
      setPrepById(p => ({ ...p, [id]: wsEvent.data.progress ?? 0 }));
      setGames(gs => gs.map(g => g.id === id
        ? { ...g, torrent_preparing: true, torrent_prep_progress: wsEvent.data.progress ?? 0 }
        : g));
    }
    if (wsEvent.event === 'content_publish_complete') {
      if (wsEvent.data.unchanged) {
        setPrepById(p => { const n = { ...p }; delete n[id]; return n; });
        setGames(gs => gs.map(g => g.id === id
          ? { ...g, torrent_preparing: false, torrent_prep_progress: null }
          : g));
      }
      if (!wsEvent.data.unchanged) {
        showToast(t('myGames.updatePublished', { version: wsEvent.data.version_label || '' }).replace(/\s+/g, ' '));
      } else if (wsEvent.data.label_updated) {
        showToast(t('myGames.versionSaved', { version: wsEvent.data.version_label || '' }).replace(/\s+/g, ' '));
      }
      load();
    }
    if (wsEvent.event === 'content_publish_error') {
      setPrepById(p => { const n = { ...p }; delete n[id]; return n; });
      showToast(t('myGames.publishFailed', { error: wsEvent.data.error || t('common.unknownError') }));
      load();
    }
    if (wsEvent.event === 'download_progress') {
      // Harmless to record progress for a non-updating game id here – GameCard
      // only renders it while content_state === 'updating'.
      setUpdateProgress(p => ({
        ...p,
        [id]: {
          progress: wsEvent.data.progress ?? 0,
          phase: wsEvent.data.phase,
          phase_progress: wsEvent.data.phase_progress ?? 0,
        },
      }));
    }
    if (wsEvent.event === 'game_updated') {
      setUpdateProgress(p => { const n = { ...p }; delete n[id]; return n; });
      showToast(t('myGames.updateApplied', { version: wsEvent.data.version_label || '' }).replace(/\s+/g, ' '));
      load();
    }
  }, [wsEvent]);

  // Poll while any game is still preparing (e.g. after page reload)
  useEffect(() => {
    const needsPoll = games.some(g =>
      g.torrent_preparing || g.content_state === 'hashing' || g.content_state === 'publishing'
    );
    if (!needsPoll) return;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [games]);

  const onAdded = (game) => {
    setGames(g => [...g, { ...game, torrent_preparing: !game.has_torrent, torrent_prep_progress: 0 }]);
    if (!game.has_torrent) setPrepById(p => ({ ...p, [game.id]: 0 }));
    setShowAdd(false);
    showToast(t('myGames.added', { name: game.name }));
  };

  const onRemove = async (game) => {
    if (!confirm(t('myGames.removeConfirm', { name: game.name }))) return;
    try {
      await api.removeGame(game.id);
      setGames(g => g.filter(x => x.id !== game.id));
      showToast(t('myGames.removed', { name: game.name }));
    } catch (err) {
      showToast(t('common.error', { error: formatApiError(err, 'game') }));
    }
  };

  const onSaved = (updated) => {
    setGames(gs => gs.map(g => g.id === updated.id ? updated : g));
    setEditGame(null);
    showToast(t('myGames.saved', { name: updated.name }));
  };

  const onGameUpdated = (updated) => {
    setGames(gs => gs.map(g => g.id === updated.id ? updated : g));
    setEditGame(eg => (eg?.id === updated.id ? updated : eg));
  };

  const onRestore = async (game) => {
    if (!confirm(t('myGames.restoreConfirm', { name: game.name }))) return;
    try {
      await api.startUpdate(game.id, game.content_hash);
      showToast(t('myGames.restoring'));
    } catch (err) {
      showToast(t('common.error', { error: formatApiError(err, 'game') }));
    }
  };

  return html`
    <div class="view">
      <div class="view-header">
        <div>
          <div class="view-title">${t('myGames.title')}</div>
          <div class="view-subtitle">${t('myGames.shared', { count: games.length })}</div>
        </div>
      </div>

      ${loading
        ? html`<div class="empty-state"><div class="spinner"></div></div>`
        : games.length === 0
          ? html`<div class="empty-state">
              <div class="empty-icon">🎮</div>
              <div class="empty-title">${t('myGames.emptyTitle')}</div>
              <div class="empty-sub">${t('myGames.emptySub')}</div>
            </div>`
          : html`<div class="card-grid" ref=${gridRef} role="list">
              ${games.map(g => html`
                <${GameCard}
                  key=${g.id}
                  game=${g}
                  mode="own"
                  prepProgress=${prepById[g.id]}
                  updateProgress=${updateProgress[g.id]}
                  onAction=${() => onRemove(g)}
                  onEdit=${() => setEditGame(g)}
                  onComments=${() => setCommentsGame(g)}
                  onPublish=${() => setPublishGame(g)}
                  onUpdate=${() => setUpdateGame(g)}
                  onRepair=${() => setRepairGame(g)}
                  onRestore=${() => onRestore(g)}
                />
              `)}
            </div>`
      }

      <button
        class="fab"
        onClick=${() => setShowAdd(true)}
        title=${t('myGames.add')}
        aria-label=${t('myGames.add')}
      >+</button>

      ${showAdd && html`<${AddGame} onClose=${() => setShowAdd(false)} onAdded=${onAdded} />`}
      ${editGame && html`<${EditGame} game=${editGame} onClose=${() => setEditGame(null)} onSaved=${onSaved} onGameUpdated=${onGameUpdated} />`}
      ${commentsGame && html`<${Comments} game=${commentsGame} onClose=${() => setCommentsGame(null)} />`}
      ${publishGame && html`
        <${PublishUpdate}
          game=${publishGame}
          onClose=${() => setPublishGame(null)}
          onPublished=${() => { setPublishGame(null); showToast(t('myGames.updatePreparing')); }}
        />
      `}
      ${repairGame && html`
        <${RepairGame}
          game=${repairGame}
          onClose=${() => setRepairGame(null)}
          onStarted=${() => { setRepairGame(null); showToast(t('myGames.repairStarted')); load(); }}
        />
      `}
      ${updateGame && html`
        <${UpdateGame}
          game=${updateGame}
          onClose=${() => setUpdateGame(null)}
          onStarted=${() => { setUpdateGame(null); showToast(t('myGames.updateStarted')); load(); }}
        />
      `}
    </div>`;
}
