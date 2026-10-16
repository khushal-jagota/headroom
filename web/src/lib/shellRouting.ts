/** Resolve supported historical addresses without losing Config detail selections. */
export function canonicalShellHash(hash: string): string {
  if (/^#\/backlog(?:[/?]|$)/.test(hash)) return "#/workspace";
  if (hash === "#/notifications") return "#/settings/notifications";
  if (hash === "#/scheduled-tasks") return "#/settings/scheduled-tasks";
  if (/^#\/config(?:[/?]|$)/.test(hash)) return hash.replace("#/config", "#/settings/config");
  if (/^#\/workers(?:[/?]|$)/.test(hash)) return hash.replace("#/workers", "#/settings/config/workers").replace(/\/workers$/, "");
  if (hash === "#/agents/worker-skill") return "#/settings/config/worker-skill";
  if (/^#\/agents\/workers\/[^/]+$/.test(hash)) return hash.replace("#/agents", "#/settings/config");
  if (hash === "#/agents/chief-of-staff") return "#/workspace/chief-of-staff";
  if (hash === "#/agents") return "#/workspace";
  return hash;
}
