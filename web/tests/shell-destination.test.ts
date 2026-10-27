import { describe, expect, it } from "vitest";
import { parseShellDestination } from "../src/lib/shellDestination";

describe("persistent shell addresses", () => {
  it.each([
    ["#/config/chief-of-staff", "#/settings/config/chief-of-staff", "settings", { section: "config", roleKind: "agent", id: "chief_of_staff" }],
    ["#/vps", "#/vps", "vps", {}],
  ])("normalizes %s without losing its selection", (input, hash, name, params) => {
    const destination = parseShellDestination(input);
    expect(destination).toMatchObject({ hash, name, params });
    expect(parseShellDestination(destination.hash)).toEqual(destination);
  });

  it("retains existing utility aliases and their view and encoded detail state", () => {
    const hash = "#/workspace/utility/settings/config/workers/type%2Fwith%20space?view=managers";
    expect(parseShellDestination(hash)).toMatchObject({ name: "workspace", hash, workspace: {
      view: "managers", selection: { kind: "utility", utility: "settings", path: ["config", "workers", "type%2Fwith%20space"] }
    } });
  });

  it("retains Usage's mount key across tabs, and changes Settings detail keys", () => {
    expect(parseShellDestination("#/backends/tokens").key).toBe(parseShellDestination("#/backends/cost").key);
    expect(parseShellDestination("#/settings/config/chief-of-staff").key)
      .not.toBe(parseShellDestination("#/settings/config/workers/coding").key);
  });

  it("preserves preview and workspace open-file query state", () => {
    const query = "?source=ticket&ticket=t_1&path=artifacts%2Freport.html";
    expect(parseShellDestination("#/preview" + query)).toMatchObject({ name: "preview", hash: "#/preview" + query });
    const input = "#/workspace/t_1" + query;
    expect(parseShellDestination(input)).toMatchObject({ name: "workspace", hash: input, workspace: { openFile: { kind: "ticket-file", ticketId: "t_1", path: "artifacts/report.html" } } });
  });

  it.each(["#/settings/measure-sources", "#/workspace/utility/settings/measure-sources", "#/settings/unknown", "#/backends/unknown", "#/vps/unknown", "#/review/unknown", "#/workspace/utility/usage/unknown", "#/day/2026-07-29", "#/sprint"])("keeps an unavailable destination explicit: %s", hash => {
    expect(parseShellDestination(hash).name).toBe("unknown");
  });
});
