/** One row that Settings search can find. */
export type SettingsSearchEntry = {
  /** The `rowId` of the row to scroll to and flash, or null for a whole page. */
  rowId: string | null;
  href: string;
  label: string;
  /** The section path and the current value, shown under the label. */
  where: string;
  /** Extra words that find the row but are not shown. */
  keywords: string;
};

export type SettingsSearchHit = Omit<SettingsSearchEntry, "keywords">;

/** The entries that match every word of `query`, labels that match first.
 *
 * A word matches the label, the section path and value, or the keywords. Entries that
 * would show the same line twice are kept once.
 */
export function searchSettings(entries: readonly SettingsSearchEntry[], query: string): SettingsSearchHit[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  const words = needle.split(/\s+/);
  const seen = new Set<string>();
  const matches: { hit: SettingsSearchHit; labelMatch: boolean; order: number }[] = [];
  entries.forEach((entry, order) => {
    const haystack = `${entry.label} ${entry.where} ${entry.keywords}`.toLowerCase();
    if (!words.every(word => haystack.includes(word))) return;
    const key = `${entry.href}|${entry.label}|${entry.where}`;
    if (seen.has(key)) return;
    seen.add(key);
    const { keywords: _keywords, ...hit } = entry;
    matches.push({ hit, labelMatch: entry.label.toLowerCase().includes(needle), order });
  });
  matches.sort((left, right) => Number(right.labelMatch) - Number(left.labelMatch) || left.order - right.order);
  return matches.map(match => match.hit);
}

/** The facts search lists, each already in the words its page shows. A section whose
 *  read has not arrived yet passes nothing and is found by its title alone. */
export type SettingsSearchFacts = {
  goalSidebarRows: boolean;
  notificationSubjects?: readonly { key: string; label: string; types: readonly { id: string; label: string; description: string; enabled: boolean }[] }[];
  /** `model` is absent for the Worker skill, which has none. */
  agents?: readonly { id: string; name: string; value: string; model?: string; description?: string }[];
  workers?: readonly { workerType: string; label: string; value: string; stages?: string }[];
  machines?: readonly { machineId: string; name: string; status: string }[];
  backends?: readonly { key: string; name: string; status: string }[];
  diagnostics: readonly { key: string; label: string }[];
};

const onOff = (on: boolean): string => (on ? "On" : "Off");
const where = (path: string[], value?: string): string => (value ? `${path.join(" › ")} · ${value}` : path.join(" › "));

/** Every page, row, Agent and Worker that Settings search can find, in rail order. */
export function buildSettingsSearchEntries(facts: SettingsSearchFacts): SettingsSearchEntry[] {
  const entries: SettingsSearchEntry[] = [];
  const page = (section: string, label: string): void => {
    entries.push({ rowId: null, href: `#/settings/${section}`, label, where: "Settings", keywords: "" });
  };

  page("general", "General");
  entries.push({ rowId: "general:goal-sidebar-rows", href: "#/settings/general", label: "Show goals in sidebar rows",
    where: where(["General", "Sidebar"], onOff(facts.goalSidebarRows)), keywords: "This browser only." });

  page("notifications", "Notifications");
  entries.push({ rowId: "notifications:device", href: "#/settings/notifications", label: "Push notifications",
    where: where(["Notifications", "This device"]), keywords: "turn on off push browser" });
  for (const subject of facts.notificationSubjects ?? []) {
    for (const type of subject.types) {
      entries.push({ rowId: `notifications:${subject.key}:${type.id}`, href: "#/settings/notifications", label: type.label,
        where: where(["Notifications", subject.label], onOff(type.enabled)), keywords: type.description });
    }
  }

  page("agents", "Agents");
  for (const agent of facts.agents ?? []) {
    entries.push({ rowId: `agents:${agent.id}`, href: "#/settings/agents", label: agent.name,
      where: where(["Agents"], agent.value), keywords: "agent model skill instructions" });
    const detail = `#/settings/agents/${agent.id}`;
    if (agent.model !== undefined) {
      entries.push({ rowId: `agents:${agent.id}:model`, href: detail, label: "Model",
        where: where(["Agents", agent.name], agent.model), keywords: "effort backend" });
    }
    entries.push({ rowId: `agents:${agent.id}:description`, href: detail, label: "Description",
      where: where(["Agents", agent.name, "Skill"]), keywords: `skill ${agent.description ?? ""}` });
  }

  page("workers", "Workers");
  for (const worker of facts.workers ?? []) {
    entries.push({ rowId: `workers:${worker.workerType}`, href: "#/settings/workers", label: worker.label,
      where: where(["Workers"], worker.value), keywords: `worker model workflow stages ${worker.workerType} ${worker.stages ?? ""}` });
    entries.push({ rowId: `workers:${worker.workerType}:model`, href: `#/settings/workers/${encodeURIComponent(worker.workerType)}`, label: "Model",
      where: where(["Workers", worker.label], worker.value), keywords: "effort backend new tickets" });
  }

  page("machines", "Machines");
  for (const machine of facts.machines ?? []) {
    entries.push({ rowId: `machines:${machine.machineId}`, href: "#/settings/machines", label: machine.name,
      where: where(["Machines"], machine.status), keywords: "machine destination" });
  }

  page("backends", "Backends");
  for (const backend of facts.backends ?? []) {
    entries.push({ rowId: `backends:${backend.key}`, href: "#/settings/backends", label: backend.name,
      where: where(["Backends"], backend.status), keywords: "backend version update sign in" });
  }

  page("diagnostics", "Diagnostics");
  for (const panel of facts.diagnostics) {
    entries.push({ rowId: `diagnostics:${panel.key}`, href: "#/settings/diagnostics", label: panel.label,
      where: where(["Diagnostics"]), keywords: "diagnostics vps" });
  }
  return entries;
}
