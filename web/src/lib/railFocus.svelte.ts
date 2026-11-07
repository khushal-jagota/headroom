/* Focus (owner): a switch at the foot of the sidebar. While it is on, the sidebar shows only the
 * work that needs the person: the groups of Tickets an agent is doing or an agent must approve
 * leave the sidebar (pages keep their full lists). The choice is kept for the next visit. */
const KEY = "panels.rail-focus";

function stored(): boolean {
  try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
}

export const railFocus = $state({ on: stored() });

export function setRailFocus(on: boolean): void {
  railFocus.on = on;
  try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* the switch still works this visit */ }
}

/** The Ticket groups Focus takes out of the sidebar. */
export const FOCUS_HIDDEN_GROUPS: ReadonlySet<string> = new Set(["agent", "status_awaiting_approval"]);
