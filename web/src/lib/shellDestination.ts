import { canonicalShellHash } from "./shellRouting";
import { impliedWorkspaceView, parseWorkspaceAddress, workspaceAddress, type WorkspaceAddress } from "./workspaceAddress";
import { parseUtilityPath } from "./utilityAddress";

type DestinationName = "workspace" | "review" | "ideas" | "feedback" | "settings" | "backends" | "preview" | "vps" | "dev" | "unknown";
/** One parsed address for the persistent shell. Pane keys never key the shell itself. */
export type ShellDestination = {
  [Name in DestinationName]: { name: Name; params: Record<string, string>; key: string; hash: string; workspace?: WorkspaceAddress }
}[DestinationName];

export function parseShellDestination(incoming: string): ShellDestination {
  const hash = canonicalShellHash(incoming);
  const routeText = hash.replace(/^#/, "");
  const queryIndex = routeText.indexOf("?");
  const path = queryIndex >= 0 ? routeText.slice(0, queryIndex) : routeText;
  const query = queryIndex >= 0 ? routeText.slice(queryIndex) : "";
  const segments = path.split("/").filter(Boolean);
  const name = segments[0] || "workspace";
  const params: Record<string, string> = {};
  const unknown = (): ShellDestination => ({ name: "unknown", params: {}, key: "unknown", hash });
  const workspace = (address: WorkspaceAddress, target = hash): ShellDestination => ({ name: "workspace", params: {}, key: "workspace", workspace: address, hash: target });
  const redirect = (selection: WorkspaceAddress["selection"]): ShellDestination => workspace({ selection, view: impliedWorkspaceView(selection), openFile: null }, workspaceAddress(selection));
  if (!segments.length || name === "chief" || name === "day" && segments.length === 1) return redirect({ kind: "chief" });
  if (name === "board") return redirect({ kind: "none" });
  if (name === "ticket" && segments[1]) {
    let id = segments[1];
    try { id = decodeURIComponent(id); } catch { /* Keep malformed segments intact. */ }
    return redirect({ kind: "ticket", id });
  }
  if (name === "workspace") {
    const address = parseWorkspaceAddress(hash);
    if (!address) return unknown();
    return workspace(address);
  }
  if (name === "dev") {
    if (segments.length !== 2 || segments[1] !== "goal-components") return unknown();
    params.sub = segments[1];
  } else if (name === "settings" || name === "backends") {
    const placement = parseUtilityPath(name === "settings" ? "settings" : "usage", segments.slice(1));
    if (!placement) return unknown();
    const detail = placement.utility === "settings" ? placement.settings : placement.utility === "usage" ? placement.usage : null;
    if (!detail) return unknown();
    if ("section" in detail) params.section = detail.section;
    if ("tab" in detail) params.tab = detail.tab;
    params.roleKind = detail.roleKind;
    if (detail.roleId) params.id = detail.roleId;
  } else if (!["review", "ideas", "feedback", "preview", "vps"].includes(name) || segments.length > 1) return unknown();
  const key = name === "backends" ? "backends" : `${segments.join("/")}${query}`;
  return { name: name as Exclude<DestinationName, "workspace">, params, key, hash };
}
