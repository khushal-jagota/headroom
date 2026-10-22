import { RAIL_MOTION } from '../workspaceRailMotion';

export const CONVERSATION_MOVE_MS = 240;
export const CONVERSATION_CARD_MS = 150;
type Move = { position: boolean; start: number; duration: number; apply: (fraction: number) => void; done?: () => void; finish: (completed: boolean) => void };
const surfaces = new WeakMap<HTMLElement, ReturnType<typeof createSurfaceMotion>>();

/** Size writes and the reader's position share one frame, in that order. */
function createSurfaceMotion(pane: HTMLElement) {
  const moves = new Map<object, Move>();
  const followers = new Set<(keys: readonly object[]) => void>();
  let frame = 0;
  function mark() { pane.toggleAttribute?.("data-conversation-motion", moves.size > 0); }
  function step(now: number) {
    frame = 0;
    const keys = [...moves.keys()];
    for (const [key, move] of [...moves].sort((a, b) => Number(a[1].position) - Number(b[1].position))) {
      if (moves.get(key) !== move) continue;
      const fraction = Math.min(1, Math.max(0, (now - move.start) / move.duration));
      move.apply(fraction);
      if (fraction === 1 && moves.get(key) === move) { moves.delete(key); move.done?.(); move.finish(true); }
    }
    mark();
    for (const follow of followers) follow(keys);
    if (moves.size) frame = requestAnimationFrame(step);
  }
  return {
    move(key: object, apply: (fraction: number) => void, duration = CONVERSATION_MOVE_MS, done?: () => void, position = false) {
      moves.get(key)?.finish(false);
      moves.delete(key);
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        apply(1); for (const follow of followers) follow([key]); done?.(); return Promise.resolve(true);
      }
      const result = new Promise<boolean>(finish => moves.set(key, { position, start: performance.now(), duration, apply, done, finish }));
      mark();
      apply(0);
      if (!frame) frame = requestAnimationFrame(step);
      return result;
    },
    cancel(key: object) { moves.get(key)?.finish(false); moves.delete(key); mark(); },
    following(callback: (keys: readonly object[]) => void) { followers.add(callback); return () => followers.delete(callback); },
    active(key: object) { return moves.has(key); }
  };
}
export function conversationMotion(element: HTMLElement) {
  const pane = element.closest<HTMLElement>('[data-conversation-pane]') ?? element;
  let motion = surfaces.get(pane);
  if (!motion) { motion = createSurfaceMotion(pane); surfaces.set(pane, motion); }
  return motion;
}

/** A plain outer window can reach zero; its child owns all padding and borders. */
export function conversationHeight(windowElement: HTMLElement) {
  const content = windowElement.firstElementChild as HTMLElement | null;
  if (!content) return {};
  const motion = conversationMotion(windowElement);
  let height = content.getBoundingClientRect().height;
  windowElement.style.height = `${height}px`;
  windowElement.style.overflow = 'clip';
  const observer = new ResizeObserver(() => {
    const wanted = content.getBoundingClientRect().height;
    const from = windowElement.getBoundingClientRect().height;
    if (Math.abs(wanted - height) < .1) return;
    height = wanted;
    motion.move(windowElement, fraction => {
      windowElement.style.height = `${from + (wanted - from) * fraction}px`;
    });
  });
  observer.observe(content);
  return { destroy() { observer.disconnect(); motion.cancel(windowElement); } };
}

const exitOpacity = new WeakMap<HTMLElement, { value: string; priority: string; release: () => void }>();
function restoreExitOpacity(element: HTMLElement): void {
  const original = exitOpacity.get(element);
  if (!original) return;
  original.release();
  if (original.value) element.style.setProperty('opacity', original.value, original.priority);
  else element.style.removeProperty('opacity');
  exitOpacity.delete(element);
}

export async function fadeConversationExit(element: HTMLElement | null, signal?: AbortSignal): Promise<void> {
  if (!element || signal?.aborted || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  restoreExitOpacity(element);
  const animation = element.animate([{ opacity: 1 }, { opacity: 0 }], {
    duration: RAIL_MOTION.exit, easing: RAIL_MOTION.leave, fill: 'forwards'
  });
  const abort = () => { animation.cancel(); restoreExitOpacity(element); };
  exitOpacity.set(element, {
    value: element.style.getPropertyValue('opacity'), priority: element.style.getPropertyPriority('opacity'),
    release: () => signal?.removeEventListener('abort', abort)
  });
  signal?.addEventListener('abort', abort, { once: true });
  try {
    await animation.finished;
    if (!signal?.aborted) element.style.opacity = '0';
  } catch { restoreExitOpacity(element); }
  finally { animation.cancel(); }
}

/** Close the row only after its ink leaves. The negative margin consumes its flex gap. */
export async function collapseConversationRow(element: HTMLElement, signal?: AbortSignal): Promise<() => void> {
  if (signal?.aborted) { restoreExitOpacity(element); return () => undefined; }
  const row = element.closest<HTMLElement>('[data-conversation-view-key]') ?? element;
  const original = row.style.cssText;
  const style = getComputedStyle(row);
  const height = row.getBoundingClientRect().height;
  const paddingTop = Number.parseFloat(style.paddingTop) || 0;
  const paddingBottom = Number.parseFloat(style.paddingBottom) || 0;
  const margin = Number.parseFloat(style.marginBottom) || 0;
  const content = row.closest<HTMLElement>('[data-conversation-content]');
  const gap = content ? Number.parseFloat(getComputedStyle(content).rowGap) || 0 : 0;
  const motion = conversationMotion(row);
  let restored = false;
  const restore = () => {
    if (restored) return;
    restored = true;
    signal?.removeEventListener('abort', restore);
    motion.cancel(row);
    row.style.cssText = original;
    restoreExitOpacity(element);
  };
  // The collapse now owns restoration, including the fade's original opacity.
  exitOpacity.get(element)?.release();
  signal?.addEventListener('abort', restore, { once: true });
  row.style.overflow = 'clip';
  row.style.boxSizing = 'border-box';
  row.style.minHeight = '0';
  const completed = await motion.move(row, fraction => {
    if (signal?.aborted) return;
    row.style.height = `${height * (1 - fraction)}px`;
    row.style.paddingTop = `${paddingTop * (1 - fraction)}px`;
    row.style.paddingBottom = `${paddingBottom * (1 - fraction)}px`;
    row.style.marginBottom = `${margin - gap * fraction}px`;
  });
  if (!completed) restore();
  return restore;
}
