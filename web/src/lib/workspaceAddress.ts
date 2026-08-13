export type WorkspaceSelection =
  | { kind: "none" }
  | { kind: "chief" }
  | { kind: "ticket"; id: string }
  | { kind: "item"; id: string };

function decodeSegment(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

export function parseWorkspaceAddress(hash: string): WorkspaceSelection | null {
  const path = hash.replace(/^#/, "").split("?", 1)[0];
  const segments = path.split("/").filter(Boolean);
  if (segments[0] !== "workspace") return null;
  if (segments.length === 1) return { kind: "none" };
  if (segments[1] === "chief-of-staff" && segments.length === 2) {
    return { kind: "chief" };
  }
  if (segments[1] === "item" && segments[2] && segments.length === 3) {
    return { kind: "item", id: decodeSegment(segments[2]) };
  }
  if (segments[1] === "item") return null;
  if (segments.length === 2) {
    return { kind: "ticket", id: decodeSegment(segments[1]) };
  }
  return null;
}

export function workspaceAddress(selection: WorkspaceSelection): string {
  if (selection.kind === "none") return "#/workspace";
  if (selection.kind === "chief") return "#/workspace/chief-of-staff";
  if (selection.kind === "item") {
    return `#/workspace/item/${encodeURIComponent(selection.id)}`;
  }
  return `#/workspace/${encodeURIComponent(selection.id)}`;
}

export function workspaceSelectionKey(selection: WorkspaceSelection): string {
  return selection.kind === "none" ? "workspace" : `workspace/${selection.kind}/${selection.kind === "chief" ? "chief-of-staff" : selection.id}`;
}
