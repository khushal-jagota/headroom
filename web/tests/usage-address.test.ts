import { describe, expect, it } from "vitest";
import { parseConfigSelection } from "../src/lib/shellRouting";
import { parseUsageSelection } from "../src/lib/usageAddress";
import { prefixHref } from "../src/lib/utilityAddress";

describe("reloadable Usage tabs", () => {
  it("opens Limits by default and gives each tab an explicit address", () => {
    const pageHref = prefixHref("#/backends");
    expect(parseUsageSelection([])).toEqual({ tab: "limits", roleKind: "index" });
    for (const tab of ["tokens", "cost", "limits", "config"] as const) {
      expect(pageHref([tab])).toBe(`#/backends/${tab}`);
      expect(parseUsageSelection([tab])).toEqual({ tab, roleKind: "index" });
    }
  });

  it.each([
    [[], { roleKind: "index" }],
    [["chief-of-staff"], { roleKind: "agent", roleId: "chief_of_staff" }],
    [["worker-skill"], { roleKind: "skill", roleId: "panels-worker" }],
    [["sprint-item-supervisor"], { roleKind: "skill", roleId: "panels-sprint-item-supervisor" }],
    [["workers", "type%2Fwith%20space"], { roleKind: "worker", roleId: "type/with space" }],
    [["workers", "%broken"], { roleKind: "worker", roleId: "%broken" }]
  ])("uses the same Config selection for Usage and Settings: %j", (suffix, selection) => {
    expect(parseConfigSelection(suffix as string[])).toEqual(selection);
    expect(parseUsageSelection(["config", ...suffix as string[]])).toEqual({ tab: "config", ...selection });
  });

  it.each([
    ["unknown"], ["tokens", "detail"], ["limits", "config"], ["config", "unknown"],
    ["config", "workers"], ["config", "workers", "coding", "extra"],
    ["config", "worker-skill", "extra"]
  ])("leaves an unsupported Usage address explicit: %j", (...segments) => {
    expect(parseUsageSelection(segments)).toBeNull();
  });
});
