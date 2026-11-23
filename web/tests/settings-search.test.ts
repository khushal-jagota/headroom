import { describe, expect, it } from "vitest";
import { buildSettingsSearchEntries, searchSettings } from "../src/lib/settings/searchIndex";

// Search is the only way to reach a row without knowing its section. A row the index
// leaves out, or a result that opens the wrong page, cannot be seen any other way.
const entries = buildSettingsSearchEntries({
  goalSidebarRows: true,
  notificationSubjects: [{ key: "tickets", label: "Tickets", types: [
    { id: "awaiting_approval", label: "Needs approval", description: "A Ticket has a proposal waiting for your decision.", enabled: false }
  ] }],
  agents: [{ id: "manager", name: "Manager", value: "Opus 5.5 · medium" }],
  workers: [{ workerType: "coding", label: "Coding", value: "GPT-6.1-Sol · medium" }],
  machines: [{ machineId: "mac-1", name: "Mac 1", status: "Available" }],
  backends: [{ key: "claude", name: "Claude", status: "2.1.322 · 2.1.330 available" }],
  diagnostics: [{ key: "memory", label: "Memory" }]
});

describe("Settings search", () => {
  it("finds a row in every section and opens it where it lives", () => {
    const cases: [string, string, string | null, string][] = [
      ["sidebar", "#/settings/general", "general:goal-sidebar-rows", "General › Sidebar · On"],
      ["approval", "#/settings/notifications", "notifications:tickets:awaiting_approval", "Notifications › Tickets · Off"],
      ["manager", "#/settings/agents", "agents:manager", "Agents · Opus 5.5 · medium"],
      ["coding", "#/settings/workers", "workers:coding", "Workers · GPT-6.1-Sol · medium"],
      ["mac 1", "#/settings/machines", "machines:mac-1", "Machines · Available"],
      ["claude", "#/settings/backends", "backends:claude", "Backends · 2.1.322 · 2.1.330 available"],
      ["memory", "#/settings/diagnostics", "diagnostics:memory", "Diagnostics"]
    ];
    for (const [query, href, rowId, where] of cases) {
      expect(searchSettings(entries, query)[0], query).toEqual(expect.objectContaining({ href, rowId, where }));
    }
  });

  it("needs every word, matches descriptions, and puts label matches first", () => {
    expect(searchSettings(entries, "proposal decision").map(hit => hit.label)).toEqual(["Needs approval"]);
    expect(searchSettings(entries, "tickets approval").map(hit => hit.label)).toEqual(["Needs approval"]);
    const workers = searchSettings(entries, "workers");
    expect(workers[0]).toMatchObject({ label: "Workers", rowId: null });
    expect(searchSettings(entries, "  ")).toEqual([]);
    expect(searchSettings(entries, "nothing like this")).toEqual([]);
  });
});
