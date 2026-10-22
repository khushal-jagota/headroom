import type { LayoutRectangle, VisibleHostBounds } from './contracts';

/** The visible intersection, expressed as insets within the host. */
export function visibleHostBounds(host: LayoutRectangle, viewport: LayoutRectangle): VisibleHostBounds {
  const top = Math.min(host.height, Math.max(0, viewport.top - host.top));
  const left = Math.min(host.width, Math.max(0, viewport.left - host.left));
  const bottom = Math.min(host.height, Math.max(0, host.top + host.height - viewport.top - viewport.height));
  const right = Math.min(host.width, Math.max(0, host.left + host.width - viewport.left - viewport.width));
  return { top, bottom, left, right, height: Math.max(0, host.height - top - bottom), width: Math.max(0, host.width - left - right) };
}

/** Collapsing the card must never remove scroll range and clamp a reader's position. */
export function retainClearance(previous: number, occupied: number): number {
  return Math.max(previous, Math.ceil(occupied), 0);
}
