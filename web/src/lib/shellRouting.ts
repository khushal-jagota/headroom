export type ConfigSelection = {
  roleKind: "index" | "agent" | "skill" | "worker";
  roleId?: string;
};

/** The same Config editor selection works under Settings and the Usage Config tab. */
export function parseConfigSelection(detail: readonly string[]): ConfigSelection | null {
  if (detail.length === 0) return { roleKind: "index" };
  if (detail[0] === "chief-of-staff" && detail.length === 1) {
    return { roleKind: "agent", roleId: "chief_of_staff" };
  }
  if (["worker-skill", "sprint-item-supervisor"].includes(detail[0]) && detail.length === 1) {
    return {
      roleKind: "skill",
      roleId: detail[0] === "worker-skill" ? "panels-worker" : "panels-sprint-item-supervisor"
    };
  }
  if (detail[0] === "workers" && detail[1] && detail.length === 2) {
    let roleId = detail[1];
    try { roleId = decodeURIComponent(roleId); } catch { /* Preserve a malformed segment as before. */ }
    return { roleKind: "worker", roleId };
  }
  return null;
}

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
