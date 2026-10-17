/** Resolve supported historical addresses without losing Config detail selections. */
export function canonicalShellHash(hash: string): string {
  const queryIndex = hash.indexOf("?");
  const path = queryIndex < 0 ? hash : hash.slice(0, queryIndex);
  const query = queryIndex < 0 ? "" : hash.slice(queryIndex);
  if (/^#\/backlog(?:\/|$)/.test(path)) return "#/workspace";
  if (path === "#/notifications") return `#/settings/notifications${query}`;
  if (path === "#/scheduled-tasks") return `#/settings/scheduled-tasks${query}`;
  if (/^#\/config(?:\/|$)/.test(path)) return path.replace("#/config", "#/settings/config") + query;
  if (path === "#/workers") return `#/settings/config${query}`;
  if (/^#\/workers\//.test(path)) return path.replace("#/workers", "#/settings/config/workers") + query;
  if (path === "#/agents/worker-skill") return `#/settings/config/worker-skill${query}`;
  if (/^#\/agents\/workers\/[^/]+$/.test(path)) return path.replace("#/agents", "#/settings/config") + query;
  if (path === "#/agents/chief-of-staff") return "#/workspace/chief-of-staff";
  if (path === "#/agents") return "#/workspace";
  return hash;
}
