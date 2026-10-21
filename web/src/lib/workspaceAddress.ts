import {
  previewQuery,
  targetFromPreviewQuery,
  type ManagedFileTarget
} from "./filePreview";
import { isWorkspaceUtility, parseUtilityPath, type WorkspaceUtility } from "./utilityAddress";

export type WorkspaceView = "tickets" | "items";
export type { WorkspaceUtility };

export type WorkspaceSelection =
  | { kind: "none" }
  | { kind: "chief" }
  // `path` is the screen's own position, the same path its standalone page uses. Empty
  // means the screen's default, which is what a rail icon asks for.
  | { kind: "utility"; utility: WorkspaceUtility; path: string[] }
  | { kind: "ticket"; id: string; openedFromItemId?: string }
  | { kind: "item"; id: string };

// The whole address: what the pane shows, which list the rail shows, and the artifact a
// Ticket has open. Nothing else decides any of them.
export type WorkspaceAddress = {
  selection: WorkspaceSelection;
  view: WorkspaceView;
  // The file the open Ticket is showing in place of its document. Only a Ticket can have
  // one, so it is dropped from any other address.
  openFile: ManagedFileTarget | null;
};

// What the rail draws, read off the address alone.
export type WorkspaceOpening = {
  view: WorkspaceView;
  // The Item drawn open. A Ticket opened from inside an Item keeps its Item open.
  openItemId: string | null;
  // Exactly one row carries the selected mark, and it is what the pane shows.
  markedItemId: string | null;
  markedTicketId: string | null;
  chiefMarked: boolean;
  markedUtility: WorkspaceUtility | null;
  markedUtilityPath: string[];
};

function decodeSegment(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

// The empty Workspace and a Ticket that names no Item belong to Tickets. Selections
// with Item context belong to Sprint Items. An address only carries `?view=` when the
// reader asked for the other one.
export function impliedWorkspaceView(selection: WorkspaceSelection): WorkspaceView {
  if (selection.kind === "none" || selection.kind === "utility") return "tickets";
  if (selection.kind === "ticket" && !selection.openedFromItemId) return "tickets";
  return "items";
}

function parseSelection(segments: string[]): WorkspaceSelection | null {
  if (segments.length === 1) return { kind: "none" };
  if (segments[1] === "chief-of-staff" && segments.length === 2) {
    return { kind: "chief" };
  }
  if (segments[1] === "utility" && segments.length >= 3 && isWorkspaceUtility(segments[2])) {
    // Segments stay as they arrived. The screen's own parser decodes what it owns, and
    // decoding here as well would differ from how its standalone page reads the path.
    const path = segments.slice(3);
    if (parseUtilityPath(segments[2], path) === null) return null;
    return { kind: "utility", utility: segments[2], path };
  }
  if (segments[1] === "item" && segments[2]) {
    if (segments.length === 3) return { kind: "item", id: decodeSegment(segments[2]) };
    if (segments.length === 4) {
      return {
        kind: "ticket",
        id: decodeSegment(segments[3]),
        openedFromItemId: decodeSegment(segments[2])
      };
    }
    return null;
  }
  if (segments[1] === "item") return null;
  if (segments.length === 2) {
    return { kind: "ticket", id: decodeSegment(segments[1]) };
  }
  return null;
}

export function parseWorkspaceAddress(hash: string): WorkspaceAddress | null {
  const text = hash.replace(/^#/, "");
  const queryIndex = text.indexOf("?");
  const path = queryIndex >= 0 ? text.slice(0, queryIndex) : text;
  const query = queryIndex >= 0 ? text.slice(queryIndex + 1) : "";
  const segments = path.split("/").filter(Boolean);
  if (segments[0] !== "workspace") return null;
  const selection = parseSelection(segments);
  if (selection === null) return null;
  const params = new URLSearchParams(query);
  const asked = params.get("view");
  const view = asked === "tickets" || asked === "items" ? asked : null;
  const openFile = selection.kind === "ticket" ? targetFromPreviewQuery(params) : null;
  return { selection, view: view ?? impliedWorkspaceView(selection), openFile };
}

export function workspaceAddress(
  selection: WorkspaceSelection,
  view?: WorkspaceView,
  openFile?: ManagedFileTarget | null
): string {
  const path = selectionPath(selection);
  const parts: string[] = [];
  if (view && view !== impliedWorkspaceView(selection)) parts.push(`view=${view}`);
  if (openFile && selection.kind === "ticket") parts.push(previewQuery(openFile));
  return parts.length === 0 ? path : `${path}?${parts.join("&")}`;
}

function selectionPath(selection: WorkspaceSelection): string {
  if (selection.kind === "none") return "#/workspace";
  if (selection.kind === "chief") return "#/workspace/chief-of-staff";
  if (selection.kind === "utility") {
    const base = `#/workspace/utility/${selection.utility}`;
    return selection.path.length === 0 ? base : `${base}/${selection.path.join("/")}`;
  }
  if (selection.kind === "item") {
    return `#/workspace/item/${encodeURIComponent(selection.id)}`;
  }
  if (selection.openedFromItemId) {
    return `#/workspace/item/${encodeURIComponent(selection.openedFromItemId)}/${encodeURIComponent(selection.id)}`;
  }
  return `#/workspace/${encodeURIComponent(selection.id)}`;
}

/** The address this one should have been, when its bare id names an orchestrator.
 *
 * The grammar cannot tell one bare id from another, so `#/workspace/<id>` is read as a
 * Ticket. A link of that shape pointing at an orchestrator therefore opened the Ticket
 * page and said "ticket not found", while four of the six requests behind it asked Ticket
 * questions about an orchestrator id. Each answered 404 in 96 bytes and was retried a
 * second later, and then again on every later reconciliation, because a 404 carries no
 * revision and so always looks like it changed.
 *
 * The browser can tell, because the navigation publication holds that orchestrator. So
 * this is answered where the answer is known rather than guessed at in the grammar.
 *
 * The corrected address does not carry the original's view. That view was implied by the
 * misreading - a bare Ticket implies the Tickets list - so keeping it would pin the
 * orchestrator's own address to the wrong list. Returns null when there is nothing to
 * correct, which is every ordinary address.
 */
export function correctedWorkspaceAddress(
  address: WorkspaceAddress,
  namesAnOrchestrator: (id: string) => boolean
): string | null {
  const selection = address.selection;
  // An id that arrived as item/<item>/<ticket> is already unambiguous.
  if (selection.kind !== "ticket" || selection.openedFromItemId) return null;
  if (!namesAnOrchestrator(selection.id)) return null;
  return workspaceAddress({ kind: "item", id: selection.id });
}

export function whatTheAddressOpens(address: WorkspaceAddress): WorkspaceOpening {
  const selection = address.selection;
  const openedFromItemId =
    selection.kind === "ticket" ? selection.openedFromItemId ?? null : null;
  return {
    view: address.view,
    openItemId: selection.kind === "item" ? selection.id : openedFromItemId,
    markedItemId: selection.kind === "item" ? selection.id : null,
    markedTicketId: selection.kind === "ticket" ? selection.id : null,
    chiefMarked: selection.kind === "chief",
    markedUtility: selection.kind === "utility" ? selection.utility : null,
    markedUtilityPath: selection.kind === "utility" ? selection.path : []
  };
}
