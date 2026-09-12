import { useEffect } from "react";

/**
 * The element that actually scrolls inside a pane.
 *
 * The preview scrolls its own container; the editor's scroll box is
 * CodeMirror's `.cm-scroller` one level inside the editor pane.
 */
function scrollableWithin(pane: HTMLElement | null): HTMLElement | null {
  if (!pane) return null;
  if (pane.classList.contains("cm-editor")) return pane;
  return pane.querySelector<HTMLElement>(".cm-scroller") ?? pane.querySelector<HTMLElement>(".preview-pane") ?? (pane.scrollHeight > 0 ? pane : null);
}

/** Scrollable distance, never negative (a short document has none). */
function range(element: HTMLElement): number {
  return Math.max(0, element.scrollHeight - element.clientHeight);
}

/** How often to look for the scroll boxes while they are not there yet. */
const ATTACH_RETRY_MS = 50;
/** Give up looking after this long (a pane that never mounts anything). */
const ATTACH_GIVE_UP_MS = 10_000;

/**
 * Keep the editor and the preview at the same *relative* position while both are
 * visible — in both directions, continuously.
 *
 * **Binding is retried, not done once.** The panes exist by the time the effect
 * runs, but their scroll boxes do not always: CodeMirror creates `.cm-scroller`
 * in its own effect, so on the first render of a note the editor side is still
 * empty. A one-shot lookup therefore gave up silently and the panes stayed
 * independent until something else re-ran the effect — the "sometimes it does
 * not sync right after I open a note" behaviour. The hook now keeps looking
 * until both boxes exist and re-binds whenever either box is *replaced* (a new
 * CodeMirror instance for another note).
 *
 * Relative rather than line-anchored on purpose: the editor shows source lines
 * and the preview shows laid-out blocks (headings, images, tables), so equal
 * pixel offsets do not exist. Proportional mapping tracks ordinary prose closely
 * and never "snaps" when the two sides diverge.
 *
 * Echo suppression: writing `scrollTop` fires the peer's own scroll event. The
 * element that was just written is ignored for exactly one event, so the mirror
 * cannot feed itself while a real scroll in either pane is never swallowed.
 */
export function useSyncedScroll(left: React.RefObject<HTMLElement>, right: React.RefObject<HTMLElement>, enabled: boolean, resetKey: string): void {
  useEffect(() => {
    if (!enabled) return;

    let leftScroller: HTMLElement | null = null;
    let rightScroller: HTMLElement | null = null;
    let detach: Array<() => void> = [];
    // Element whose next scroll event is our own write, not the user's.
    let echoFrom: HTMLElement | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let startedAt = Date.now();
    let stopped = false;

    const mirror = (target: HTMLElement, other: HTMLElement) => {
      const onScroll = () => {
        // Our own write coming back: consume it and stay quiet.
        if (echoFrom === target) {
          echoFrom = null;
          return;
        }
        const from = range(target);
        const to = range(other);
        // A pane that cannot scroll must not drag the other one around.
        if (from <= 0 || to <= 0) return;
        const ratio = Math.min(1, Math.max(0, target.scrollTop / from));
        const next = ratio * to;
        // Already there (both sides moved, or a rounding step): no write, so no
        // echo to suppress either.
        if (Math.abs(other.scrollTop - next) < 0.5) return;
        // Read the old value first: assigning scrollTop can dispatch the event
        // synchronously, and the guard must swallow the *new* value.
        const previous = other.scrollTop;
        other.scrollTop = next;
        echoFrom = other.scrollTop === previous ? null : other;
      };
      target.addEventListener("scroll", onScroll, { passive: true });
      return () => target.removeEventListener("scroll", onScroll);
    };

    const unbind = () => {
      for (const off of detach) off();
      detach = [];
      echoFrom = null;
      leftScroller = null;
      rightScroller = null;
    };

    /** Attach to the boxes currently in the DOM; true when both are bound. */
    const bind = (): boolean => {
      const leftElement = scrollableWithin(left.current);
      const rightElement = scrollableWithin(right.current);
      if (!leftElement || !rightElement || leftElement === rightElement) return false;
      if (leftElement === leftScroller && rightElement === rightScroller) return true;
      unbind();
      leftScroller = leftElement;
      rightScroller = rightElement;
      detach = [mirror(leftElement, rightElement), mirror(rightElement, leftElement)];
      return true;
    };

    const poll = () => {
      if (stopped) return;
      const bound = bind();
      if (!bound) {
        // Only keep retrying while something might still show up.
        if (Date.now() - startedAt > ATTACH_GIVE_UP_MS) return;
        timer = setTimeout(poll, ATTACH_RETRY_MS);
        return;
      }
      // Bound: keep a light watch so a replaced CodeMirror DOM is re-bound
      // without waiting for another render, then stop.
      startedAt = Date.now();
      timer = setTimeout(poll, ATTACH_RETRY_MS);
    };

    poll();

    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      unbind();
    };
    // `resetKey` is the open note plus the layout: switching a tab replaces the
    // CodeMirror instance, so the lookup starts over for that DOM.
  }, [enabled, resetKey, left, right]);
}
