import { redirectedSettingsPath } from "./settings/address";

/** Resolve supported historical addresses without losing Config detail selections. */
export function canonicalShellHash(hash: string): string {
  const queryIndex = hash.indexOf("?");
  const path = queryIndex < 0 ? hash : hash.slice(0, queryIndex);
  const query = queryIndex < 0 ? "" : hash.slice(queryIndex);
  if (path.startsWith("#/workspace/item/")) return canonicalShellHash(path.replace("#/workspace/item/", "#/workspace/manager/") + query);
  if (new URLSearchParams(query.slice(1)).get("view") === "items") {
    const params = new URLSearchParams(query.slice(1));
    params.set("view", "managers");
    return canonicalShellHash(path + "?" + params);
  }
  if (/^#\/backlog(?:\/|$)/.test(path)) return "#/workspace";
  const settings = redirectedSettingsPath(path);
  if (settings !== null) return canonicalShellHash(settings + query);
  if (path === "#/agents/chief-of-staff") return "#/workspace/chief-of-staff";
  if (path === "#/agents") return "#/workspace";
  return hash;
}
