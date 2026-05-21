import { html } from 'htm/preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import { api } from '../api.js';
import { formatApiError } from '../errors.js';

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = n => String(n).padStart(2, '0');
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function Comments({ game, onClose }) {
  const [comments, setComments] = useState([]);
  const [text, setText]         = useState('');
  const [loading, setLoading]   = useState(true);
  const [posting, setPosting]   = useState(false);
  const [error, setError]       = useState('');
  const textRef = useRef(null);
  const listRef = useRef(null);

  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  useEffect(() => {
    api.getComments(game.id)
      .then(setComments)
      .catch(() => setComments([]))
      .finally(() => setLoading(false));
  }, [game.id]);

  // Scroll to bottom when comments load
  useEffect(() => {
    if (!loading && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [loading, comments.length]);

  const submit = async (e) => {
    e.preventDefault();
    if (!text.trim()) return;
    setPosting(true);
    setError('');
    try {
      const comment = await api.postComment(game.id, text.trim());
      setComments(cs => [...cs, comment]);
      setText('');
      textRef.current?.focus();
    } catch (err) {
      setError(formatApiError(err, 'Kommentar'));
    } finally {
      setPosting(false);
    }
  };

  return html`
    <div class="overlay" onClick=${e => e.target === e.currentTarget && onClose()} role="dialog" aria-modal="true" aria-label="Kommentare">
      <div class="dialog" style="max-width:520px;width:100%">
        <div class="dialog-title">Kommentare · ${game.name}</div>

        <div
          ref=${listRef}
          style="max-height:340px;overflow-y:auto;display:flex;flex-direction:column;gap:12px;margin-bottom:16px;min-height:80px"
        >
          ${loading
            ? html`<div style="text-align:center;padding:24px"><span class="spinner"></span></div>`
            : comments.length === 0
              ? html`<p style="color:var(--muted);font-size:13px;text-align:center;padding:24px 0">Noch keine Kommentare. Sei der Erste!</p>`
              : comments.map(c => html`
                  <div key=${c.id} style="background:var(--surface2,#1a1a2e);border-radius:8px;padding:10px 12px">
                    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
                      <span style="font-weight:600;font-size:13px">${c.author}</span>
                      <span style="color:var(--muted);font-size:11px">${fmtDate(c.created_at)}</span>
                    </div>
                    <div style="font-size:14px;white-space:pre-wrap;word-break:break-word">${c.text}</div>
                  </div>
                `)
          }
        </div>

        <form onSubmit=${submit} style="display:flex;flex-direction:column;gap:10px">
          <textarea
            ref=${textRef}
            class="form-input"
            rows="3"
            placeholder="Kommentar schreiben…"
            value=${text}
            onInput=${e => setText(e.target.value)}
            disabled=${posting}
            style="resize:vertical"
          />
          ${error && html`<p style="color:var(--danger);font-size:13px;margin:0">${error}</p>`}
          <div class="dialog-actions">
            <button type="button" class="btn btn-ghost" onClick=${onClose}>Schließen</button>
            <button type="submit" class="btn btn-primary" disabled=${!text.trim() || posting}>
              ${posting ? html`<span class="spinner"></span>` : 'Senden'}
            </button>
          </div>
        </form>
      </div>
    </div>`;
}
