/** Where a Settings page can be, and the address of each.
 *
 * Settings has seven sections in three groups. Agents and Workers also have a detail page
 * for one row. Every other place reads this one list, so the rail, the router and the
 * search index cannot disagree about which sections exist.
 */

export type SettingsSection =
  | "general"
  | "notifications"
  | "agents"
  | "workers"
  | "machines"
  | "backends"
  | "diagnostics";

export type SettingsSectionDefinition = { key: SettingsSection; label: string };

export const SETTINGS_GROUPS: readonly { label: string; sections: readonly SettingsSectionDefinition[] }[] = [
  { label: "Panels", sections: [{ key: "general", label: "General" }, { key: "notifications", label: "Notifications" }] },
  { label: "Agents", sections: [{ key: "agents", label: "Agents" }, { key: "workers", label: "Workers" }] },
  { label: "System", sections: [{ key: "machines", label: "Machines" }, { key: "backends", label: "Backends" }, { key: "diagnostics", label: "Diagnostics" }] }
];

export const SETTINGS_SECTIONS: readonly SettingsSectionDefinition[] = SETTINGS_GROUPS.flatMap(group => group.sections);

export function settingsSectionLabel(section: SettingsSection): string {
  return SETTINGS_SECTIONS.find(item => item.key === section)?.label ?? section;
}

/** The three Agents detail pages, by the address segment each one uses. */
export const SETTINGS_AGENT_IDS = ["chief-of-staff", "manager", "worker-skill"] as const;
export type SettingsAgentId = (typeof SETTINGS_AGENT_IDS)[number];

/** A parsed Settings address.
 *
 * `chosen` is false only for bare `#/settings`. The desktop shows General there, and a phone
 * shows the section list, which is its first screen.
 */
export type SettingsPlace = {
  section: SettingsSection;
  detail: string | null;
  chosen: boolean;
};

function isSection(value: string): value is SettingsSection {
  return SETTINGS_SECTIONS.some(item => item.key === value);
}

/** Read the path after `#/settings`. Returns null for a place Settings does not have. */
export function parseSettingsPath(path: readonly string[]): SettingsPlace | null {
  if (path.length === 0) return { section: "general", detail: null, chosen: false };
  const [section, detail, ...rest] = path;
  if (!isSection(section) || rest.length > 0) return null;
  if (detail === undefined) return { section, detail: null, chosen: true };
  if (section === "agents") {
    return (SETTINGS_AGENT_IDS as readonly string[]).includes(detail) ? { section, detail, chosen: true } : null;
  }
  if (section === "workers" && detail) {
    let workerType = detail;
    try { workerType = decodeURIComponent(detail); } catch { /* Keep a malformed segment as it is. */ }
    return { section, detail: workerType, chosen: true };
  }
  return null;
}

export function settingsHref(section: SettingsSection | null, detail?: string | null): string {
  if (section === null) return "#/settings";
  if (!detail) return `#/settings/${section}`;
  return `#/settings/${section}/${section === "workers" ? encodeURIComponent(detail) : detail}`;
}

/** The new address for a Settings or Config address from before the redesign, or null.
 *
 * `path` is the hash path without its query. Config's detail pages move to Agents and
 * Workers. Its index, which also held the sidebar setting, moves to General. A Config
 * detail that never existed becomes `#/settings/config/...`, which stays unknown.
 */
export function redirectedSettingsPath(path: string): string | null {
  const config = (detail: string, index: string): string => {
    if (detail === "") return index;
    if (detail === "/chief-of-staff" || detail === "/manager" || detail === "/worker-skill") return `#/settings/agents${detail}`;
    if (/^\/workers\/[^/]+$/.test(detail)) return `#/settings${detail}`;
    return `#/settings/config${detail}`;
  };
  const changed = (next: string): string | null => (next === path ? null : next);
  let match = path.match(/^#\/(?:settings\/)?config(\/.*)?$/);
  if (match) return changed(config(match[1] ?? "", "#/settings/general"));
  match = path.match(/^#\/backends\/config(\/.*)?$/);
  if (match) return config(match[1] ?? "", "#/settings/agents");
  // Settings used to open inside the Workspace pane as well. It has one home now.
  match = path.match(/^#\/workspace\/utility\/settings(\/.*)?$/);
  if (match) return redirectedSettingsPath(`#/settings${match[1] ?? ""}`) ?? `#/settings${match[1] ?? ""}`;
  match = path.match(/^#\/workspace\/utility\/usage\/config(\/.*)?$/);
  if (match) return config(match[1] ?? "", "#/settings/agents");
  if (path === "#/settings/vps" || path === "#/vps") return "#/settings/diagnostics";
  if (path === "#/notifications") return "#/settings/notifications";
  if (path === "#/workers") return "#/settings/workers";
  if (/^#\/workers\/[^/]+$/.test(path)) return path.replace("#/workers", "#/settings/workers");
  if (/^#\/agents\/workers\/[^/]+$/.test(path)) return path.replace("#/agents", "#/settings");
  if (path === "#/agents/worker-skill") return "#/settings/agents/worker-skill";
  return null;
}
