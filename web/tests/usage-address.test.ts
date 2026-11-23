import { describe, expect, it } from "vitest";
import { parseUsageSelection } from "../src/lib/usageAddress";
import { prefixHref } from "../src/lib/utilityAddress";

describe("reloadable Usage tabs", () => {
  it("opens Limits by default and gives each tab an explicit address", () => {
    const pageHref = prefixHref("#/backends");
    expect(parseUsageSelection([])).toEqual({ tab: "limits" });
    for (const tab of ["tokens", "cost", "limits"] as const) {
      expect(pageHref([tab])).toBe(`#/backends/${tab}`);
      expect(parseUsageSelection([tab])).toEqual({ tab });
    }
  });

  it.each([
    ["unknown"], ["tokens", "detail"], ["limits", "config"], ["config"], ["config", "workers", "coding"]
  ])("leaves an unsupported Usage address explicit: %j", (...segments) => {
    expect(parseUsageSelection(segments)).toBeNull();
  });
});
