/* Focus (owner): a switch at the foot of the sidebar. While it is on, the sidebar shows only the
 * work that needs the person: the groups of Tickets an agent is doing or an agent must approve
 * leave the sidebar, except a Ticket with a message for the person, which stays in its group
 * (pages keep their full lists). The choice is kept for the next visit. */
const KEY = "panels.rail-focus";

function stored(): boolean {
  try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
}

export const railFocus = $state({ on: stored(), changedAt: 0 });

export function setRailFocus(on: boolean): void {
  railFocus.on = on;
  railFocus.changedAt = performance.now();
  try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* the switch still works this visit */ }
}

/** The Ticket groups Focus takes out of the sidebar. */
export const FOCUS_HIDDEN_GROUPS: ReadonlySet<string> = new Set(["agent", "status_awaiting_approval"]);

/** The groups the sidebar shows. With Focus on, a hidden group keeps only its Tickets that have a
 * message for the person (the signal behind the unread dot), and goes when none is left. */
export function focusVisibleGroups<T extends { key: string; cards: { conversation_signal?: { message_for_owner?: boolean } | null }[] }>(groups: T[], on: boolean): T[] {
  if (!on) return groups;
  return groups.flatMap(group => {
    if (!FOCUS_HIDDEN_GROUPS.has(group.key)) return [group];
    const cards = group.cards.filter(card => card.conversation_signal?.message_for_owner === true);
    return cards.length ? [{ ...group, cards }] : [];
  });
}

/** How long a group takes to slide away or back: the rail's slow step right after the switch
 * flips, nothing for any other change (data updates keep the rail's own motion) or when the
 * person prefers reduced motion. */
export function focusSlideMs(): number {
  if (performance.now() - railFocus.changedAt > 600) return 0;
  if (typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches) return 0;
  return 240;
}
