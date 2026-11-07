import { useLayoutEffect, useRef } from 'preact/hooks';

/**
 * Controller/keyboard behaviour for a dialog rendered inline by its parent
 * (version picker, "remove download?", "quit DeckDrop?"):
 *   - Escape (B on the Steam Deck) closes it,
 *   - on open the focus moves into it ([data-autofocus], else its first
 *     button), so A can't trigger the control behind the overlay again,
 *   - on close the focus returns to the control that opened it.
 *
 * Layout effect on purpose: it runs right after the dialog is in the DOM, so
 * a B pressed immediately after opening is not lost (see the d-pad test).
 */
export function useDialogKeys(open, onClose) {
  const close = useRef(onClose);
  close.current = onClose;

  useLayoutEffect(() => {
    if (!open) return undefined;
    const opener = document.activeElement;
    const overlays = document.querySelectorAll('.overlay');
    const overlay = overlays[overlays.length - 1];
    const first = overlay?.querySelector('[data-autofocus]') || overlay?.querySelector('button');
    first?.focus({ preventScroll: true });

    const onKey = (e) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      close.current?.();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      if (opener && opener !== document.body && opener.isConnected) {
        opener.focus({ preventScroll: true });
      }
    };
  }, [open]);
}
