import {
  previewQuery,
  targetFromPreviewQuery,
  type ManagedFileTarget
} from "./filePreview";

export type WorkspaceView = "tickets" | "items";

export type WorkspaceSelection =
  | { kind: "none" }
  | { kind: "chief" }
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
  if (selection.kind === "none") return "tickets";
  if (selection.kind === "ticket" && !selection.openedFromItemId) return "tickets";
  return "items";
}

function parseSelection(segments: string[]): WorkspaceSelection | null {
  if (segments.length === 1) return { kind: "none" };
  if (segments[1] === "chief-of-staff" && segments.length === 2) {
    return { kind: "chief" };
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
  if (selection.kind === "item") {
    return `#/workspace/item/${encodeURIComponent(selection.id)}`;
  }
  if (selection.openedFromItemId) {
    return `#/workspace/item/${encodeURIComponent(selection.openedFromItemId)}/${encodeURIComponent(selection.id)}`;
  }
  return `#/workspace/${encodeURIComponent(selection.id)}`;
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
    chiefMarked: selection.kind === "chief"
  };
}
