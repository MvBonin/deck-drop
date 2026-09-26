import { html } from 'htm/preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import { api, fmtBytes } from '../api.js';

const POLL_MS = 3000;

function fmtTime(ts) {
  if (!ts) return '–';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function HashEvent({ ev, gameName }) {
  const [open, setOpen] = useState(false);
  const hasFiles = ev.files && ev.files.length > 0;
  return html`
    <div class="debug-event">
      <div class="debug-event-head" onClick=${() => hasFiles && setOpen(o => !o)}>
        <span class="debug-time">${fmtTime(ev.ts)}</span>
        <span class=${'debug-kind debug-kind-' + ev.kind}>${ev.kind}</span>
        <span class="debug-game">${gameName || ev.game_id || '–'}</span>
        <span class="debug-count">${ev.file_count} Datei${ev.file_count === 1 ? '' : 'en'}</span>
      </div>
      <div class="debug-reason">
        ${ev.reason_text}
        <code>${ev.reason}</code>
        ${ev.detail && html` · ${ev.detail}`}
      </div>
      ${hasFiles && html`
        <button type="button" class="debug-link" onClick=${() => setOpen(o => !o)}>
          ${open ? 'Dateien ausblenden' : 'Dateien anzeigen'}
        </button>
      `}
      ${open && html`
        <ul class="debug-files">
          ${ev.files.map(f => html`<li key=${f}><code>${f}</code></li>`)}
          ${ev.file_count > ev.files.length && html`<li>… und ${ev.file_count - ev.files.length} weitere</li>`}
        </ul>
      `}
    </div>`;
}

export function Debug({ wsEvent }) {
  const [data, setData]       = useState(null);
  const [error, setError]     = useState('');
  const [gameId, setGameId]   = useState('');
  const [showRaw, setShowRaw] = useState(false);
  // The filter dropdown must keep listing every game while one is selected
  // (the API then only returns that game), so remember the unfiltered list.
  const [allGames, setAllGames] = useState([]);

  const load = useCallback(async () => {
    try {
      const d = await api.debug(gameId || undefined);
      setData(d);
      if (!gameId) setAllGames(d.games.map(g => ({ id: g.id, name: g.name })));
      setError('');
    } catch (err) {
      setError(String(err?.message || err));
    }
  }, [gameId]);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (!wsEvent) return;
    if (wsEvent.event === 'debug_hash_event' || wsEvent.event === 'game_content_state') load();
  }, [wsEvent]);

  if (!data) {
    return html`
      <div class="view">
        <div class="view-header"><div class="view-title">Debug</div></div>
        ${error
          ? html`<p class="debug-empty">Fehler: ${error}</p>`
          : html`<div class="empty-state"><div class="spinner"></div></div>`}
      </div>`;
  }

  const names = Object.fromEntries(
    [...allGames, ...data.games].map(g => [g.id, g.name])
  );

  return html`
    <div class="view">
      <div class="view-header">
        <div>
          <div class="view-title">Debug</div>
          <div class="view-subtitle">DeckDrop ${data.version} · Peer ${String(data.peer_id).slice(0, 8)}…</div>
        </div>
        <select class="form-input debug-filter" value=${gameId} onChange=${e => setGameId(e.target.value)}>
          <option value="">Alle Spiele</option>
          ${gameId && !names[gameId] ? html`<option value=${gameId}>${gameId}</option>` : null}
          ${allGames.map(g => html`<option key=${g.id} value=${g.id}>${g.name}</option>`)}
        </select>
      </div>
      ${error && html`<p class="debug-empty">Aktualisieren fehlgeschlagen: ${error}</p>`}

      <p class="settings-section-title">Hash-Ereignisse (warum wird gehasht?)</p>
      <div class="settings-section debug-section">
        ${data.hash_events.length === 0
          ? html`<p class="debug-empty">Seit dem Start wurde nichts gehasht oder neu geprüft.</p>`
          : data.hash_events.map(ev => html`<${HashEvent} key=${ev.id} ev=${ev} gameName=${names[ev.game_id]} />`)}
      </div>

      <p class="settings-section-title">Spiele</p>
      <div class="settings-section debug-section">
        ${data.games.map(g => html`
          <div class="debug-game-row" key=${g.id}>
            <div class="debug-event-head">
              <strong>${g.name}</strong>
              <span class=${'debug-kind debug-state-' + g.content.state}>${g.content.state}</span>
            </div>
            <div class="debug-kv">
              <span>ID</span><code>${g.id}</code>
              <span>Revision</span><code>${g.revision}${g.version_label ? ` (${g.version_label})` : ''}</code>
              <span>Datei-Hashes</span><code>${g.has_file_hashes ? `${g.file_count} Dateien` : 'keine'}</code>
              <span>content_hash</span><code>${g.content_hash || '–'}</code>
              <span>info_hash</span><code>${g.info_hash || '–'}</code>
              <span>Herkunft</span><code>${g.origin_peer || 'lokal'}</code>
              ${g.content.pending_hash_reason && html`
                <span>Nächster Rehash</span>
                <code>${g.content.pending_hash_reason_text} (${g.content.pending_hash_reason})</code>
              `}
              ${g.content.hash_progress != null && html`
                <span>Fortschritt</span><code>${Math.round(g.content.hash_progress * 100)} %</code>
              `}
              ${g.content.summary && html`
                <span>Änderungen</span>
                <code>${g.content.summary.changed} geändert · ${g.content.summary.removed} entfernt · ${g.content.summary.added} neu</code>
              `}
              <span>Letzter Scan</span><code>${fmtTime(g.content.last_scan)}</code>
              ${g.network && html`
                <span>Im Netzwerk</span>
                <code>${g.network.same_id
                  ? `${g.network.same_id} Version(en) mit gleicher ID`
                  : 'keine Version mit gleicher ID'}${g.network.best_update
                  ? ` · Update: ${g.network.best_update.version_label || `Rev. ${g.network.best_update.revision}`}`
                  : ''}</code>
              `}
            </div>
            ${g.network && g.network.name_matches.length > 0 && !g.network.same_id && html`
              <p class="debug-warn">
                Gleichnamiges Spiel mit anderer ID im Netzwerk (${g.network.name_matches.join(', ')}) –
                wird nicht als Update erkannt, sondern als neuer Download angeboten.
              </p>
            `}
          </div>
        `)}
      </div>

      <p class="settings-section-title">Netzwerk</p>
      <div class="settings-section debug-section">
        ${(data.network_games || []).length === 0
          ? html`<p class="debug-empty">Keine Spiele von anderen Peers.</p>`
          : data.network_games.map(n => html`
            <div class="debug-game-row" key=${n.id + n.version_key}>
              <div class="debug-event-head">
                <strong>${n.name}</strong>
                <span class="debug-kind">${n.version_label || `Rev. ${n.revision}`}</span>
                ${n.installed
                  ? html`<span class="debug-kind debug-state-clean">installiert</span>`
                  : html`<span class="debug-kind">nicht installiert</span>`}
                ${n.update_available && html`<span class="debug-kind debug-kind-piece_check">Update</span>`}
              </div>
              <div class="debug-kv">
                <span>ID</span><code>${n.id}</code>
                <span>Peers</span><code>${n.peers.join(', ') || '–'}</code>
                <span>Teilbar</span><code>${n.shareable ? 'ja' : 'nein'} · Torrent ${n.has_torrent ? 'ja' : 'nein'}</code>
                <span>Größe</span><code>${fmtBytes(n.size_bytes)}</code>
              </div>
            </div>
          `)}
      </div>

      <p class="settings-section-title">Downloads</p>
      <div class="settings-section debug-section">
        ${data.downloads.length === 0
          ? html`<p class="debug-empty">Keine aktiven Downloads.</p>`
          : data.downloads.map(d => html`
            <div class="debug-game-row" key=${d.id}>
              <div class="debug-event-head">
                <strong>${d.game_name}</strong>
                <span class="debug-kind">${d.kind || 'download'}</span>
                <span class="debug-kind">${d.status}${d.phase ? ` / ${d.phase}` : ''}</span>
              </div>
              <div class="debug-kv">
                <span>Fortschritt</span><code>${Math.round((d.progress || 0) * 100)} % · ${fmtBytes(d.downloaded_bytes)} / ${fmtBytes(d.total_bytes)}</code>
                <span>Peers</span><code>${d.num_peers}</code>
                ${d.error && html`<span>Fehler</span><code>${d.error}</code>`}
              </div>
            </div>
          `)}
      </div>

      <div style="padding:4px 20px 20px">
        <button type="button" class="btn btn-ghost" onClick=${() => setShowRaw(r => !r)}>
          ${showRaw ? 'Roh-JSON ausblenden' : 'Roh-JSON anzeigen'}
        </button>
        ${showRaw && html`<pre class="debug-raw">${JSON.stringify(data, null, 2)}</pre>`}
      </div>
    </div>`;
}
