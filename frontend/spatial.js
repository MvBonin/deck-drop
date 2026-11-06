// D-pad navigation for the Steam Deck (and any keyboard).
//
// In Gaming Mode the Deck's d-pad arrives as arrow keys and A as Enter. Tab
// order alone made half of the UI unreachable (buttons inside game cards were
// out of the tab order). This moves focus to the nearest focusable element in
// the pressed direction – the way the Steam UI itself behaves:
//   - inside an open dialog only that dialog's controls are candidates,
//   - text fields keep Left/Right for the caret; a select acts like a button,
//   - with nothing focused yet, the first press focuses the first card/control.

const FOCUSABLE = [
  'button:not([disabled])',
  'a[href]',
  'input:not([disabled]):not([type=hidden])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

const KEYS = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right' };

function scope() {
  const overlays = document.querySelectorAll('.overlay');
  return overlays.length ? overlays[overlays.length - 1] : document;
}

function visible(el) {
  if (el.closest('[inert], [aria-hidden="true"]')) return false;
  const r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) return false;
  const style = getComputedStyle(el);
  if (style.visibility === 'hidden' || style.display === 'none') return false;
  // An <input> hidden inside a custom toggle is reached through its label box.
  return r.bottom > 0 && r.top < window.innerHeight + 2000;
}

function candidates(root) {
  return [...root.querySelectorAll(FOCUSABLE)].filter(visible);
}

/** Score of moving from rect a to rect b in direction dir (lower is better, null = not in that direction). */
export function score(a, b, dir) {
  const ax = (a.left + a.right) / 2, ay = (a.top + a.bottom) / 2;
  const bx = (b.left + b.right) / 2, by = (b.top + b.bottom) / 2;
  let main, cross, overlap;
  if (dir === 'down' || dir === 'up') {
    main = dir === 'down' ? b.top - a.bottom : a.top - b.bottom;
    if ((dir === 'down' ? by - ay : ay - by) <= 1) return null;
    overlap = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    cross = overlap > 0 ? 0 : Math.abs(bx - ax);
  } else {
    main = dir === 'right' ? b.left - a.right : a.left - b.right;
    if ((dir === 'right' ? bx - ax : ax - bx) <= 1) return null;
    overlap = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    cross = overlap > 0 ? 0 : Math.abs(by - ay);
  }
  return Math.max(0, main) + cross * 2;
}

function keepsArrows(el, dir) {
  if (!el) return false;
  const tag = el.tagName;
  // A closed <select> would change its value on Up/Down and trap the d-pad;
  // it behaves like a button instead: A / Enter opens it, arrows move on.
  if (tag === 'SELECT') return false;
  if (tag === 'TEXTAREA') {
    // Up/Down move between lines, until the caret is at the start/end.
    if (dir === 'up') return el.selectionStart > 0;
    if (dir === 'down') return el.selectionEnd < el.value.length;
    return true;
  }
  if (tag === 'INPUT') {
    const type = (el.type || 'text').toLowerCase();
    if (['checkbox', 'radio', 'button', 'submit'].includes(type)) return false;
    if (type === 'range' || type === 'number') return true;
    return dir === 'left' || dir === 'right';
  }
  return el.isContentEditable;
}

function focusEl(el) {
  el.focus({ preventScroll: true });
  el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

export function installSpatialNav(doc = document) {
  doc.addEventListener('keydown', (e) => {
    const dir = KEYS[e.key];
    if (!dir || e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
    const active = doc.activeElement;
    if (keepsArrows(active, dir)) return;

    const root = scope();
    const items = candidates(root);
    if (!items.length) return;

    const hasFocus = active && active !== doc.body && root.contains(active);
    if (!hasFocus) {
      const first = root.querySelector('.view [data-card]') && root === document
        ? [...root.querySelectorAll('.view [data-card]')].find(visible)
        : null;
      e.preventDefault();
      focusEl(first || items[0]);
      return;
    }

    const from = active.getBoundingClientRect();
    let best = null, bestScore = Infinity;
    let bestField = null, bestFieldScore = Infinity; // outside a pinned action row
    for (const el of items) {
      // A card that holds the focused button stays a target: Up from its
      // buttons goes back to the card itself.
      if (el === active) continue;
      // Into a card (its buttons) only with Down; back out to it only with Up.
      if (dir !== 'down' && active.contains(el)) continue;
      if (dir !== 'up' && el.contains(active)) continue;
      const s = score(from, el.getBoundingClientRect(), dir);
      if (s === null) continue;
      if (s < bestScore) { best = el; bestScore = s; }
      if (s < bestFieldScore && !el.closest('.dialog-actions')) { bestField = el; bestFieldScore = s; }
    }
    // A dialog's action row is pinned over the scrolling form: walking down
    // visits every field first, the buttons come last.
    if ((dir === 'down' || dir === 'up') && best?.closest('.dialog-actions')
        && !active.closest('.dialog-actions') && bestField) {
      best = bestField;
    }
    if (best) {
      e.preventDefault();
      focusEl(best);
    }
  });
}
