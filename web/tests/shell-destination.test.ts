import { describe, expect, it } from "vitest";
import { parseShellDestination } from "../src/lib/shellDestination";

describe("persistent shell addresses", () => {
  it.each([
    ["#/config/chief-of-staff", "#/settings/agents/chief-of-staff", "settings", { section: "agents", detail: "chief-of-staff", chosen: "true" }],
    ["#/settings/backends", "#/settings/backends", "settings", { section: "backends", chosen: "true" }],
    ["#/settings", "#/settings", "settings", { section: "general" }],
    ["#/vps", "#/settings/diagnostics", "settings", { section: "diagnostics", chosen: "true" }],
    ["#/backends/cost", "#/backends/cost", "backends", { tab: "cost" }],
    ["#/workspace/goals", "#/goals", "goals", {}],
  ])("normalizes %s without losing its selection", (input, hash, name, params) => {
    const destination = parseShellDestination(input);
    expect(destination).toMatchObject({ hash, name, params });
    expect(parseShellDestination(destination.hash)).toEqual(destination);
  });

  it("retains Usage's mount key across tabs, and changes Settings detail keys", () => {
    expect(parseShellDestination("#/backends/tokens").key).toBe(parseShellDestination("#/backends/cost").key);
    expect(parseShellDestination("#/settings/agents/chief-of-staff").key)
      .not.toBe(parseShellDestination("#/settings/workers/coding").key);
  });

  it("preserves preview and workspace open-file query state", () => {
    const query = "?source=ticket&ticket=t_1&path=artifacts%2Freport.html";
    expect(parseShellDestination("#/preview" + query)).toMatchObject({ name: "preview", hash: "#/preview" + query });
    const input = "#/workspace/t_1" + query;
    expect(parseShellDestination(input)).toMatchObject({ name: "workspace", hash: input, workspace: { openFile: { kind: "ticket-file", ticketId: "t_1", path: "artifacts/report.html" } } });
  });

  it.each(["#/settings/unknown", "#/settings/config/unknown", "#/backends/config/unknown", "#/backends/unknown", "#/vps/unknown", "#/review/unknown", "#/workspace/utility/usage/unknown", "#/day/2026-07-29", "#/sprint", "#/feedback", "#/workspace/utility/feedback", "#/dev", "#/dev/goal-components", "#/dev/conversation", "#/dev/file-preview-gallery", "#/dev/goal-components/unknown"])("keeps an unavailable destination explicit: %s", hash => {
    expect(parseShellDestination(hash).name).toBe("unknown");
  });
});
